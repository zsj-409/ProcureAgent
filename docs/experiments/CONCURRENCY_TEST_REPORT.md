# Concurrency Test Report (Phase 2)

- **Date**: 2026-11-01
- **Artifacts**: `docs/eval/concurrent_claim_results.json`, `docs/eval/external_effect_ledger.json`
- **Method**: REAL subprocesses (not threads) racing on the SAME file-based
  SQLite DB (WAL). No `:memory:`.
- **Run**: `.venv/Scripts/python.exe -m pytest tests/test_chaos_concurrency.py`

## 1. Results

| n_workers | winners (per step) | expected | external effects | state consistent |
|---|---|---|---|---|
| 1 | 1 | 1 | 1 | ✅ |
| 2 | 1 | 1 | 1 | ✅ |
| 4 | 1 | 1 | 1 | ✅ |
| 8 | 1 | 1 | 1 | ✅ |

**All 2/4/8-worker races produced exactly one winner per logical step and
exactly one external effect.** No duplicate external invocation.

## 2. Different steps, concurrent claim

- 2 and 4 workers each claiming a DIFFERENT step: every worker won its own
  step; external effects = n_workers (no collision).

## 3. Write contention

- SQLite WAL + busy_timeout 30 s: concurrent writers serialize; no deadlock,
  no `database is locked` errors in any run.
- Wall time for the 8-worker round ≈ 2 s (each subprocess ~0.25 s).

## 4. State consistency

`StateConsistencyChecker` passed for every scenario: task status matches
steps, no step has >1 SUCCEEDED attempt, no CLAIMED step without a RUNNING
attempt.

## 5. Negative / limits

- Only ONE logical step per task was raced here (the harness focuses on the
  claim protocol). Full multi-step task-level concurrency across many tasks is
  covered by the same mechanism (per-step claim).
- The ledger is append-safe for serialized subprocesses; concurrent writes to
  the SAME ledger file from truly parallel processes would need a lock (not
  exercised — each scenario uses its own ledger).
