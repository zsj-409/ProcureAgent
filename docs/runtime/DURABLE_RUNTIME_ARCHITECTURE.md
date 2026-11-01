# Durable Runtime Architecture (Phase 2)

- **Date**: 2026-11-01
- **Commits**: `4b489b2` (A: atomic claim + fencing), `3a58603` (B: durable worker + crash recovery), (C: chaos evaluation)
- **SQLite**: 3.45.1, WAL journal mode, busy_timeout 30 s, RETURNING supported

## 1. Ownership model

```
tasks ──1:N── logical_steps ──1:N── step_attempts
```

- `logical_steps` unique on `(task_id, plan_hash, step_key)` — the logical
  step is per task+plan version, never just action_type.
- Ownership lives on the step row: `owner_id` + `lease_epoch` (fencing token)
  + `lease_expires_at`.
- `step_attempts` records every actual execution (worker, epoch, status,
  idempotency key, cost).

## 2. Atomic claim

```sql
UPDATE logical_steps
SET status='CLAIMED', owner_id=:worker, lease_epoch=lease_epoch+1,
    lease_expires_at=:expiry, attempt_count=attempt_count+1
WHERE id=:id AND (status IN ('PENDING','RETRY_SCHEDULED')
   OR (status='CLAIMED' AND lease_expires_at < :now))
RETURNING id, lease_epoch, attempt_count
```

- Single statement, autobegin transaction, commit BEFORE any external call.
- No lock held across HTTP/browser work (explicit design decision).
- `lease_epoch` increments per claim → fencing token.

## 3. Fenced terminal writes

`complete` / `fail` / `mark_unknown` all use
`WHERE id=:id AND owner_id=:worker AND lease_epoch=:epoch`.
A stale worker (wrong owner or old epoch) gets rowcount 0 → rejected.

## 4. Durable worker

- `DurableWorker` scans `find_reclaimable_tasks(now)`: non-terminal tasks with
  no live CLAIMED lease. Claims each unfinished step, executes under a
  heartbeat loop, submits fenced results.
- Ownership is DB-persisted, NOT a thread-pool Future. Process death leaves
  the task row; lease expiry makes it reclaimable by a new worker.
- Graceful stop: stop claiming, cancel scans, drain in-flight.

## 5. External outcome semantics

| capability | policy |
|---|---|
| READ_ONLY_RETRYABLE | bounded retry; duplicate query count + cost recorded; quote freshness checked |
| IDEMPOTENT_MUTATION | external idempotency key; verify by request id on uncertainty |
| NON_IDEMPOTENT_MUTATION | never blind-retry; UNKNOWN_OUTCOME + human review |
| UNKNOWN_CAPABILITY | treated as NON_IDEMPOTENT_MUTATION |

`UNKNOWN_OUTCOME` is a distinct state, NOT in the claim WHERE, NOT auto-retried
as a failure.

## 6. Checkpoint versioning

Checkpoint binds task_id + plan_hash + step_key + action_input_hash +
result_ref + status. Reuse only when ALL match. A new plan version
(`plan_hash` change) never reuses incompatible results. Quote freshness is
re-validated before trust.

## 7. Evaluation harness

`ExternalEffectLedger` (separate file, ground truth for external effects),
`FaultInjector` (boundary hooks + fault seed registry), `StateConsistencyChecker`
(cross-table audit). Real multi-process tests via subprocess; crash via
`os._exit()` at controlled boundaries.

## 8. Honest limits

- Fencing prevents stale local writes but **cannot cancel an already-emitted
  external side effect** — stated limitation.
- DB-level protection is NOT external exactly-once. Current mock suppliers are
  read-only quote GETs; their repeated invocation is counted as duplicate
  query, not duplicate business mutation.
- SQLite WAL measured: 8 concurrent claimers → exactly 1 winner, no deadlock,
  ~1-2 s wall for the 8-worker round (see CONCURRENCY_TEST_REPORT).
