# UNKNOWN_OUTCOME Recovery Spec (Phase 3)

- **Module**: `app/runtime/durable_worker.py`, `app/runtime/step_claim.py`
- **Rule**: 不确定的外部结果不能被当成确定失败而盲目重试。

## 1. Outcome classification

| local knowledge | external state | action |
|---|---|---|
| request not yet dispatched | — | safe to execute normally |
| dispatched, outcome uncertain | unknown | capability-decided (see below) |
| external confirmed success | committed | complete + persist |
| external confirmed failure | not committed | fail → RETRY (if retryable) / terminal |
| external success + persisted | committed | complete (checkpoint) |

## 2. Recovery decision by capability

| capability | crash-after-dispatch policy |
|---|---|
| READ_ONLY_RETRYABLE | re-execute (bounded); count as duplicate invocation; **0 committed effect** |
| REMOTE_IDEMPOTENT | re-execute with SAME idempotency key; ledger dedups to 1 committed effect |
| RECONCILABLE_MUTATION | prefer query by external_request_id; if still uncertain → UNKNOWN_OUTCOME |
| NON_IDEMPOTENT_MUTATION | **never re-execute** → UNKNOWN_OUTCOME + human review |
| UNKNOWN_CAPABILITY | **never re-execute** → UNKNOWN_OUTCOME + human review |

## 3. Worker wiring (Phase 3)

`DurableWorker._execute_claimed`:
- On `outcome_known=False` from the executor: if `_capability_retry_safe(step_key)`
  → `fail(RETRY_SCHEDULED)` (bounded re-query); else → `mark_unknown(...)`.
- On exception after dispatch: same capability decision. A mutation tool that
  failed after dispatch is treated as possibly-committed → UNKNOWN_OUTCOME.

`_capability_retry_safe` looks up the executor's `retry_policy.retry_safe`;
unknown → False (conservative).

## 4. Saved reconciliation context

`step_attempts` retains: `attempt_status`, `error_code`,
`external_idempotency_key`, `external_request_id` (Phase 3 adds the id),
`observed_result_hash`, `tool_cost`. `mark_unknown` records `reason_code`
in the attempt row and the step's status.

## 5. Verified behavior (tests)

- NON_IDEMPOTENT crash after external commit → step UNKNOWN_OUTCOME,
  committed effect count stays 1 (no repeat). ✅
- REMOTE_IDEMPOTENT crash → recovery with same key → committed count stays 1
  (dedup), step SUCCEEDED. ✅
- READ_ONLY crash → re-query allowed; invocation=2, committed=0. ✅
