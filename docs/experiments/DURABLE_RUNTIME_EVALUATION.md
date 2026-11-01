# Durable Runtime Evaluation (Phase 2)

- **Date**: 2026-11-01
- **Metrics registry** (Task 10), each with numerator/denominator.

## 1. Metric registry & values

| Metric | Value | n | Definition |
|---|---|---|---|
| Verified Recovery Success | 2/2 | 2 scenarios | crash-recovery subprocess tests + chaos crash; recovered to SUCCEEDED with state consistency |
| Duplicate External Effect Rate | 0 | 4 races | business-mutation duplicates per ledger; all races produced 1 effect/step |
| Duplicate Invocation Count | 1/step max | 4 races | read-only quote GETs; separate from business duplicates |
| State Consistency | 5/5 | 5 tasks | task/step/attempt/checkpoint cross-check passed |
| Lease Expiration Recovery | 3/3 | 3 | expired-lease reclaim tests (unit + subprocess) |
| Stale Worker Commit Rejection | 2/2 | 2 | stale epoch complete() → False, result preserved |
| Checkpoint Reuse Accuracy | SUCCEEDED never re-claimed | — | unit + concurrency tests |
| Recovery Latency | ~1-2 s (subprocess startup dominated) | 2 | harness-level, not real-time lease wait |
| P50/P95 task latency | not measured this phase | — | Phase 6 |
| Verified Task Completion Rate | 1.000 (single-fault, n=1) | 1 | flaky-retry case from Phase 1 eval |

## 2. Failure distribution

- 0 failures across all Phase-2 scenarios.
- Intentionally exercised fault classes: WORKER_PROCESS_KILLED (os._exit),
  LEASE_EXPIRED, CONCURRENT_CLAIM, DUPLICATE_RESUME (via concurrent claim
  paths), AFTER_EXTERNAL_SUCCESS_BEFORE_COMMIT (crash-after-ledger).

## 3. Honest limits

- n is small (controlled harness, not a population study). All rates are
  labeled with n; none are claimed as statistically significant.
- Single Fault Recovery from Phase 1 (flaky retry, n=1) is NOT folded into
  these numbers — it is a separate anecdote.
- SQLite write throughput: 8-way concurrent claim serialized cleanly; no
  measured throughput ceiling reached (Phase 5/6 would stress with many tasks).

## 4. Independent experiment control (Task 11)

- Every scenario uses a FRESH file DB + FRESH external ledger → no method
  reads another method's DB state (the MarketPilot lesson).
- Fault seed registry: `seed-conc-2/4/8`, `seed-crash-conc`.
- `execution_manifest.json` records environment + methods + seed registry.
- World snapshot hash: the external_effect_ledger.json hash changes iff any
  effect changes (method-order invariance holds by construction: each method
  writes only its own scenario files).
