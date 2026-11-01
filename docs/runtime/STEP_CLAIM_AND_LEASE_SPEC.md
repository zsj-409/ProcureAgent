# Step Claim & Lease Spec (Phase 2)

- **Module**: `app/runtime/step_claim.py`
- **Statuses**: `PENDING`, `CLAIMED`, `SUCCEEDED`, `RETRY_SCHEDULED`,
  `FAILED_TERMINAL`, `UNKNOWN_OUTCOME`

## 1. State machine

```
PENDING ──claim──▶ CLAIMED ──success──▶ SUCCEEDED
   │                 │  │
   │                 │  └─retryable error──▶ RETRY_SCHEDULED ──claim──▶ CLAIMED
   │                 │  └─terminal error────▶ FAILED_TERMINAL (never reclaimed)
   │                 └─lease expiry──▶ reclaimable (PENDING-like)
   └─(uncertain external)──▶ UNKNOWN_OUTCOME (not auto-retried)
```

## 2. Claim rules

- Claimable: `PENDING`, `RETRY_SCHEDULED`, or `CLAIMED` with expired lease.
- Not claimable: `SUCCEEDED`, `FAILED_TERMINAL`, `UNKNOWN_OUTCOME`.
- Each claim increments `lease_epoch` (fencing) and `attempt_count`.

## 3. Lease & heartbeat

- `lease_expires_at = now + lease_seconds` (default 30, bounded).
- Heartbeat: `UPDATE ... WHERE id AND owner_id AND lease_epoch` — only the
  current owner extends.
- Two workers racing to reclaim an expired step: SQLite serializes the UPDATE;
  exactly one wins (rowcount 1).
- Clock: injectable (`Clock` / `RealClock` / `FixedClock`) — all lease math
  uses it; tests advance it deterministically.

## 4. Fencing evidence (tests)

- `test_stale_worker_cannot_complete`: w1 claims (epoch 1), lease expires,
  w2 reclaims (epoch 2); w1's `complete(epoch=1)` returns False; w2's
  `complete(epoch=2)` returns True.
- `test_stale_worker_epoch_rejected_after_recovery` (subprocess): the crashed
  worker's epoch cannot complete after a new worker recovered the step; the
  SUCCEEDED result_ref stays the recovery worker's.
- `test_completed_step_not_reclaimable`: SUCCEEDED is never claimed again.

## 5. UNKNOWN_OUTCOME

- Set via `mark_unknown` (fenced). Distinct from RETRY/FAILED.
- Not in the claim WHERE → not auto-retried. Requires reconciliation (verify
  external state) or human review. This is the V3 rule "不确定外部结果不会
  被当成确定失败而盲目重试".

## 6. Idempotent creation

`create_logical_step` uses `INSERT ... ON CONFLICT(task_id, plan_hash,
step_key) DO NOTHING` then SELECTs the existing id. Concurrent duplicate
creation cannot produce a second logical step.
