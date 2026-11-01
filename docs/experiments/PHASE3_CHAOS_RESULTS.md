# Phase 3 Chaos Results

- **Date**: 2026-11-01
- **Commits**: `581be71` (A), `21b27fa` (B), (C)
- **Run**: `.venv/Scripts/python.exe -m pytest tests/test_external_effect_safety.py tests/test_full_task_recovery.py tests/test_approval_integrity.py`

## 1. Fault-family results (numerator/denominator per family)

| fault family | n | success | fail | notes |
|---|---|---|---|---|
| claim scope (wrong-step / cross-task / cross-plan) | 5 | 5 | 0 | expired lease of B not claimed via A |
| concurrent claim same step | 3 | 3 | 0 | 2/4/8 workers → 1 winner each |
| concurrent different steps | 2 | 2 | 0 | all workers win their own step |
| stale fencing token | 2 | 2 | 0 | old epoch rejected, result preserved |
| read-only crash → re-query | 1 | 1 | 0 | invocation=2, committed=0 |
| idempotent crash → dedup | 1 | 1 | 0 | invocation=2, committed=1 |
| non-idempotent crash → UNKNOWN | 1 | 1 | 0 | no repeat; committed stays 1 |
| full-task process kill + recovery | 1 | 1 | 0 | WAITING_APPROVAL, 2 quotes, 2 invocations |
| concurrent approve/reject | 1 | 1 | 0 | 1×200 + 1×409, 1 approval row |
| expired-quote approval | 1 | 1 | 0 | 409 QUOTE_EXPIRED |
| ledger multi-process write | 4 | 4 | 0 | 80 invocations, no lost update |

**No duplicate committed business side effect observed in any scenario.**

## 2. What the two crash-window effects actually are

| scenario | invocation 1 | invocation 2 | committed effects |
|---|---|---|---|
| read-only crash | quote (worker A) | quote (worker B) | **0** |
| idempotent crash | order (key K) | order (key K) | **1** (dedup) |
| non-idempotent crash | order (no key) | — (NOT re-executed) | **1** (then UNKNOWN) |

The Phase 2 report's "2 effects = correct" is now **precisely classified by
action type**: correct only for READ_ONLY (2 invocations, 0 committed) and
REMOTE_IDEMPOTENT (2 invocations, 1 committed). For NON_IDEMPOTENT it would
have been a bug — Phase 3 prevents it.

## 3. State consistency

`StateConsistencyChecker` passed on every Phase-3 scenario (task status ↔
steps ↔ attempts ↔ ledger consistent).

## 4. Sample-size honesty

Most families have n=1-5 (controlled harness). These are NOT population
rates; they prove the mechanism. A scaled fault matrix with per-family n≥20
is Phase 6 work.
