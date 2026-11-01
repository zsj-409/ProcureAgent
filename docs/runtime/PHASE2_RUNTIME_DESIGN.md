# Phase 2 Runtime Design — Durable Agent Runtime

- **Date**: 2026-11-01
- **Status**: DESIGN (implementation follows in Commits A→B→C)
- **Baseline HEAD**: `cb09283` (Phase 1 complete, 104 passed)

## 0. Baseline facts (measured)

| Item | Value |
|---|---|
| SQLite library | 3.45.1 |
| journal_mode | `delete` (default) — will switch to `WAL` for concurrency |
| busy_timeout | 5000 ms |
| foreign_keys | off (0) |
| RETURNING / ON CONFLICT | supported |
| StepExecution unique constraint | **none** (P0-2) |
| TaskRuntime claim | `SELECT cached → INSERT RUNNING` (non-atomic, P0-2) |
| BackgroundTaskRunner | in-process `ThreadPoolExecutor` (P0-3) |
| Idempotency key (current) | `(task_id, step_name, supplier_id)` — but only a *check*, not a *claim* |
| Checkpoint timing | after executor returns; step result persisted as COMPLETED |
| Worker lifecycle | thread pool owned by the HTTP process; no persistence |
| Duplicate-external-call paths | concurrent resume; run+resume; crash between external success and local commit |

## 1. Design goals

1. Atomic Step Claim — one logical step, one valid execution owner at a time.
2. Lease + Heartbeat + Fencing — stale workers cannot commit.
3. Durable worker scheduling — process death does not lose the task; a new
   worker finds and reclaims it.
4. External outcome reconciliation — UNKNOWN_OUTCOME is not auto-retried as a
   failure.
5. Checkpoint versioning — no reuse of stale/incompatible results.
6. Independent fault injection + cross-process chaos evaluation.

## 2. Data model (new tables, additive — existing tables untouched)

### logical_steps

| column | type | meaning |
|---|---|---|
| id | INTEGER PK | |
| task_id | TEXT | |
| plan_hash | TEXT | plan identity for this step |
| step_key | TEXT | logical step identity (`collect_<supplier>` / `score` / ...) |
| action_type | TEXT | COLLECT_SUPPLIER / SCORE / ... |
| action_input_hash | TEXT | hash of (product, quantity, supplier config) — input versioning |
| status | TEXT | PENDING / CLAIMED / SUCCEEDED / RETRY_SCHEDULED / FAILED_TERMINAL / UNKNOWN_OUTCOME |
| owner_id | TEXT | worker id holding the lease |
| lease_epoch | INTEGER | fencing token |
| lease_expires_at | DATETIME | |
| attempt_count | INTEGER | |
| result_ref | TEXT | reference to completed result |
| created_at / updated_at | DATETIME | |

**Uniqueness**: `UNIQUE(task_id, plan_hash, step_key)` — the logical step is
unique per task+plan version+step. (Not just action_type.)

### step_attempts

| column | type | meaning |
|---|---|---|
| id | INTEGER PK | |
| logical_step_id | INTEGER FK | |
| worker_id | TEXT | |
| lease_epoch | INTEGER | token used for this attempt |
| started_at / completed_at | DATETIME | |
| attempt_status | TEXT | RUNNING / SUCCEEDED / FAILED / UNKNOWN_OUTCOME |
| error_code | TEXT | |
| external_request_id | TEXT | supplier-side request id if available |
| external_idempotency_key | TEXT | |
| observed_result_hash | TEXT | |
| tool_cost | TEXT | cost/token ledger reference |

### worker_leases (or columns on logical_steps)

Ownership lives on `logical_steps` (owner_id + lease_epoch + lease_expires_at).
A separate heartbeat table is optional; Phase 2 keeps it on the step row.

## 3. State machine

```
PENDING ──claim──▶ CLAIMED(RUNNING) ──success──▶ SUCCEEDED
   │                    │   │
   │                    │   └─retryable error──▶ RETRY_SCHEDULED ──claim──▶ CLAIMED
   │                    │   └─terminal error────▶ FAILED_TERMINAL
   │                    └─lease expiry──▶ PENDING (reclaimable)
   └─(external outcome unknown)──▶ UNKNOWN_OUTCOME  (never auto-retried as failure)
```

**UNKNOWN_OUTCOME is NOT RETRYABLE** by default — it requires reconciliation
(verify external state) or human review.

## 4. Atomic claim (SQLite)

```sql
-- claim: only from PENDING or expired lease
UPDATE logical_steps
SET status='CLAIMED', owner_id=:worker, lease_epoch=lease_epoch+1,
    lease_expires_at=:expiry, attempt_count=attempt_count+1
WHERE id=:id AND (status='PENDING'
                  OR (status='CLAIMED' AND lease_expires_at < :now))
RETURNING id, lease_epoch, attempt_count;
```

- Short transaction (BEGIN IMMEDIATE / single UPDATE), commit, **then** run the
  external call. No lock held across HTTP/browser work.
- `lease_epoch` increments on every claim → fencing token.
- Old worker's conditional completion:
  ```sql
  UPDATE logical_steps SET status='SUCCEEDED', result_ref=:ref
  WHERE id=:id AND owner_id=:worker AND lease_epoch=:epoch
  ```
  rowcount==0 → stale worker rejected.

## 5. Lease / heartbeat

- `lease_expires_at = now + lease_seconds` (default 30 s, bounded).
- Heartbeat: `UPDATE ... SET lease_expires_at=:new WHERE id=:id AND owner_id=:worker AND lease_epoch=:epoch` — only the current owner extends.
- Expired-lease reclaim: the claim UPDATE above accepts `CLAIMED AND lease_expires_at < now`.
- Two workers racing to reclaim an expired step: SQLite serializes the UPDATE; exactly one rowcount=1 wins.
- Clock: single injectable clock (`Clock` protocol) used by all code and tests; DB time is `datetime.now(UTC)` at write time. Fencing prevents stale local writes but **cannot cancel an already-emitted external side effect** — stated limitation.

## 6. Durable worker

- Replace the thread-pool-only ownership with a **DB-backed claimable task
  scan**: on start, worker scans `tasks` for PENDING/COLLECTING rows not owned
  by a live lease, claims them, executes, persists.
- Heartbeat loop per claimed task.
- Bounded retries with exponential backoff; terminal failure after cap.
- Graceful shutdown: stop claiming, release leases, drain in-flight.
- Idempotent resume: re-validate plan (Phase-1 gate) before executing.
- Worker ID: `worker-<host>-<pid>-<uuid>` persisted in attempts.

## 7. External outcome semantics

| capability | policy |
|---|---|
| READ_ONLY_RETRYABLE (supplier quote GET) | bounded retry; record duplicate query count + cost; check quote freshness before checkpoint reuse |
| IDEMPOTENT_MUTATION | use external idempotency key; on uncertainty query status by request id |
| NON_IDEMPOTENT_MUTATION | never blind-retry; UNKNOWN_OUTCOME + human review |
| UNKNOWN_CAPABILITY | treat as NON_IDEMPOTENT_MUTATION |

Current mock suppliers are read-only; the harness adds a mock that records
every external invocation (external_effect_ledger) to prove no duplicate
effects under concurrent/crash scenarios.

## 8. Checkpoint versioning

Checkpoint binds: task_id + plan_hash + step_key + action_input_hash +
policy_version + result_hash + status. Reuse only when ALL match. Changing
supplier/product/quantity (action_input_hash) invalidates old results.
`plan_hash` is the plan's content hash — a new plan version never reuses
incompatible results. Quote expiry: a completed quote result carries
`collected_at`; resume re-validates freshness before trust.

## 9. Fault injection matrix

| fault | injection point |
|---|---|
| BEFORE_EXTERNAL_CALL | hook before executor |
| AFTER_EXTERNAL_SUCCESS_BEFORE_COMMIT | hook after executor returns, before step commit |
| AFTER_COMMIT_BEFORE_RESPONSE | hook after commit, before orchestrator returns |
| WORKER_PROCESS_KILLED | subprocess `os._exit()` at boundary |
| LEASE_EXPIRED | short lease + delay |
| HEARTBEAT_LOST | stop heartbeat loop |
| SQLITE_BUSY | concurrent writers |
| DUPLICATE_RESUME | two resumes |
| CONCURRENT_CLAIM | N workers claim same step |
| APPROVAL_RACE | concurrent approve/reject |

External effect log is stored **separately** from the main DB (a JSON ledger
or separate SQLite file) so the harness can prove "external succeeded, local
commit did not".

## 10. Evaluation metrics

Verified Recovery Success (with n + failure distribution), Duplicate External
Effect Rate, Duplicate Invocation Count, State Consistency, Recovery Latency,
Lease Expiration Recovery, Stale Worker Commit Rejection, Checkpoint Reuse
Accuracy, Retry Cost, P50/P95 latency, Verified Task Completion Rate, Cost per
Verified Success. Independent environment reset + world hash + method-order
invariance per experiment (no cross-method DB pollution).

## 11. Migration

Additive tables only; existing `step_executions` (audit) preserved. Migration
= `CREATE TABLE IF NOT EXISTS` on startup + an idempotent upgrade test that
re-runs on an old DB and confirms no data loss.

## 12. Gate (Phase 2 → Phase 4)

1. No uncoordinated concurrent ownership of one logical step.
2. Stale fencing token cannot commit.
3. Real process kill → new process recovers.
4. UNKNOWN_OUTCOME not blindly retried.
5. Completed checkpoints not double-committed.
6. Incompatible historical checkpoints not reused.
7. DB state consistent across task/step/attempt/audit.
8. Phase 1 business contracts still hold.
9. Real multi-process + fault injection pass.
10. Reproducible manifest for reliability metrics.
11. All failures/negative results reported.
12. DB-level protection not claimed as external exactly-once.
