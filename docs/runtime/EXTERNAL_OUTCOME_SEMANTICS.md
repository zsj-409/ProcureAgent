# External Outcome Semantics (Phase 2)

- **Module**: `app/runtime/step_claim.py` (`UNKNOWN_OUTCOME`),
  `app/runtime/chaos.py` (`ExternalEffectLedger`)
- **V3 rule**: 不确定的外部结果不能被当成确定失败而盲目重试。

## 1. Tool capability classification

| capability | policy | example |
|---|---|---|
| READ_ONLY_RETRYABLE | bounded retry; record duplicate query count + cost; check freshness before checkpoint reuse | supplier quote GET |
| IDEMPOTENT_MUTATION | external idempotency key; on uncertainty, query status by request id | idempotent order-create |
| NON_IDEMPOTENT_MUTATION | never blind-retry; UNKNOWN_OUTCOME + human review | payment / non-idempotent order |
| UNKNOWN_CAPABILITY | treated as NON_IDEMPOTENT_MUTATION | — |

Current ProcureAgent executes **no real purchase mutations**; mock suppliers
are read-only quote queries. The harness's `ExternalEffectLedger` records
every invocation so duplicate *queries* are countable separately from
duplicate *business mutations*.

## 2. UNKNOWN_OUTCOME handling

- A step whose external call's outcome is uncertain (timeout after success,
  connection lost mid-response) → `mark_unknown` (fenced by owner+epoch).
- UNKNOWN_OUTCOME is **not** in the claim WHERE → a worker cannot auto-claim
  it and blindly re-run.
- Resolution paths: (a) reconcile by querying the external request id /
  status; (b) human review. Both are explicit, never implicit retry.

## 3. Duplicate external effect measurement

- `ExternalEffectLedger` (separate file, not the main DB) is the ground truth
  for "external executed but local commit did not".
- `duplicates_for(task_id, step_key)` returns entries that executed the same
  task+step more than once.
- Read-only quote queries are counted as **Duplicate Invocation Count**, kept
  separate from **Duplicate External Effect Rate** (business mutations).

## 4. Verified behavior (chaos tests)

- Concurrent claim (2/4/8 workers): exactly 1 external effect per step.
- Worker crash after external effect before commit: ledger shows the effect;
  recovery worker re-executes (correct — the first completion never
  committed). 2 effects total is the CORRECT count for crash+recovery, not a
  duplicate business effect.
- Stale worker fencing: the crashed worker's epoch cannot write.

## 5. Honest limitation

Fencing + ledger cannot cancel an external effect that already happened.
"DB-level exactly-once" is NOT claimed; the system guarantees *at most one
committed local result per logical step* and *visibility* of external effects.
