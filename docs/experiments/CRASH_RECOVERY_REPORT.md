# Crash Recovery Report (Phase 2)

- **Date**: 2026-11-01
- **Artifacts**: `docs/eval/crash_recovery_results.json`, `docs/eval/external_effect_ledger.json`, `docs/eval/state_consistency_report.json`
- **Run**: `.venv/Scripts/python.exe -m pytest tests/test_crash_recovery.py tests/test_chaos_concurrency.py`

## 1. Method

A REAL child process claims a logical step, records an external effect in the
**separate** ledger, then calls `os._exit(0)` — no Python exception, no
cleanup, no completion commit. A NEW worker process then discovers the orphaned
task (expired lease) and reclaims it.

## 2. Results

| scenario | external effects after crash | recovered | total effects | state consistent |
|---|---|---|---|---|
| crash after external, before commit | 1 | ✅ | 2 | ✅ |

- The ledger proves the exact crash window: **external executed, local commit
  never happened**.
- The recovery worker re-executes the step (correct: the first completion
  never committed) → 2 effects total, which is the CORRECT count for
  crash+recovery, NOT a duplicate business effect.
- Stale-worker fencing: the crashed worker's epoch (epoch 1) cannot complete
  after recovery (epoch 2) — verified by
  `test_stale_worker_epoch_rejected_after_recovery`.

## 3. Verified recovery sample

- Recovery scenarios run: 2 (crash-recovery subprocess tests + chaos crash).
- Verified recovery success: 2/2.
- Failure distribution: 0 failures.
- **Confidence limit**: this is a small controlled sample (2 scenarios), not a
  statistically significant population rate. Phase 6 will scale the fault
  matrix (BEFORE_EXTERNAL_CALL, AFTER_COMMIT_BEFORE_RESPONSE, LEASE_EXPIRED,
  HEARTBEAT_LOST, SQLITE_BUSY, ...) with per-class n.

## 4. What survives a kill

- Task row: survives (SQLite file).
- Logical step CLAIMED by the dead worker: survives; lease expires → reclaimable.
- External effect ledger: survives (separate file) — the audit trail proves
  the effect existed before the crash.
- Step result: does NOT exist (the completion never committed) — by design.

## 5. Known limitations

- `os._exit` is a hard kill but does not simulate power loss / disk flush
  loss; SQLite WAL with synchronous=NORMAL gives durability within the
  process's committed transactions.
- Recovery latency measured at the harness level is dominated by subprocess
  startup (~1-2 s); the lease-expiry wait is simulated (we expire the lease
  deterministically instead of sleeping 30 s). A real-time lease-expiry
  latency test is Phase 6 work.
