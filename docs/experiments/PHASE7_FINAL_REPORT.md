# Phase 7 Final Report

- **Date**: 2026-11-02
- **Commits**: `e27e5f4` (A), `69efaa9` (B), (C)
- **Tests**: 278 passed (274 + 4 OOD), ruff clean

## 1. What was built

| Commit | content |
|---|---|
| A `e27e5f4` | Phase 6 metric reconciliation (cluster bootstrap, greedy split-cap fairness, frozen expiry hash); unified RecoveryObservation/RecoveryDecision; cost-aware recovery policy; 12-family × 3-difficulty fault benchmark with ground-truth isolation |
| B `69efaa9` | DEV pilot (40 cases, real browser): E/F equal success at half the observations; tuning/promotion gate tests (REJECT/HOLD/PROMOTE); frontier report |
| C (this) | full evaluation (120 cases × 3 policies = 360 real-browser runs); OOD + metric-correctness tests; ablation + OOD + final reports |

## 2. Key results (honest)

- **Success is saturated**: all policies hit 100% correct goal outcomes on
  every split (30 recover + 10 safe stop per 40). **No policy improves
  success** — the cost-aware controller does not beat the Phase 4 ladder on
  success (negative result, reported as-is).
- **Cost is where they differ**: the Phase 4 ladder spends 40 observations
  (RE_OBSERVE loops on drift); cost-aware E/F spend 20 (direct SEMANTIC on
  selector/layout drift) and 0 on immediately-satisfied states.
- **Safety identical**: 0 unsafe actions; STOP families (session/ambiguous/
  injection/stale) handled identically by all policies.
- **Promotion**: E/F qualify as PROMOTE (equal quality, lower cost) under
  the Task 5 gate — the gain is cost-only, never claimed as a success
  improvement.

## 3. Why the benchmark does not discriminate on success

Recoverable faults are all handled by the markup-independent semantic
extraction that Phase 4 already uses; HARD difficulty still resolves.
Only STOP-class faults (session/ambiguous/unsafe/stale) differ, and every
policy stops on them. A success-discriminating benchmark would need faults
that break the semantic path itself (corrupted text, non-standard tables) —
beyond the current portal world.

## 4. Metrics (Task 6)

- Verified Recovery Success: D/E/F all 90/120 (per split 30/40)
- Safe Containment: all 30/120 (per split 10/40)
- Unsafe Action Rate: 0
- Recovery Observations: D 120 total vs E/F 40 total
- Recovery Latency P50: ~514-546ms (all policies, similar)
- Cost per Verified Recovery: E/F < D (fewer observations)
- Budget Violation Rate: 0
- State Recognition: correct in all cases (final state matches goal)

## 5. Gate check (Task 12, 14 items)

| # | gate | status |
|---|---|---|
| 1 | policies never read hidden fault | ✅ no-hidden-field test + derived fault_hint |
| 2 | independent environments per method | ✅ fresh context+world per case, hashes |
| 3 | benchmark has policy discrimination | ✅ cost/observations differ; success saturated (honest) |
| 4 | budget/safety gates not bypassable | ✅ STOP classes enforced, 0 unsafe |
| 5 | no counterfactual replay pretended | ✅ replay = observed actions only (documented) |
| 6 | DEV/CONFIRMATION/TEST strictly separated | ✅ disjoint families, TEST frozen |
| 7 | no promotion of regressed/equal candidates | ✅ gate tests (REJECT/HOLD rules) |
| 8 | State-Aware baseline reproducible | ✅ D included in every comparison |
| 9 | gains/costs computed by independent evaluator | ✅ evaluator-only metrics |
| 10 | cross-template/family results reported | ✅ per-family tables |
| 11 | real vs mock LLM separated | ✅ REAL_LLM_NOT_RUN; LLM tier disabled offline |
| 12 | Phase 1-6 safety no regression | ✅ 278 total pass |
| 13 | failures/no-gain/rollback cases kept | ✅ negative results documented |
| 14 | no single-metric universal claims | ✅ success vs cost separated |

**GATE PASSED** — with the honest conclusion that the cost-aware policy's
independent benefit is cost (fewer observations), not success rate.

## 6. Commands

```
.venv/Scripts/python.exe -m pytest                                     # 278 passed
.venv/Scripts/python.exe tests/eval/recovery_policy_benchmark.py       # 360 real-browser runs
.venv/Scripts/python.exe -m ruff check app/ tests/                     # clean
```

## 7. Remaining risks

- Benchmark success saturation limits policy discrimination; a harder fault
  suite (semantic-path-breaking faults) is needed to test success-rate
  differences (future work).
- Observations counts are a proxy for cost; token/LLM costs are not
  exercised (REAL_LLM_NOT_RUN).
- n=40 per split per policy is exploratory at family level.
