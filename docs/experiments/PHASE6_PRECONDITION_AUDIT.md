# Phase 6 Precondition Audit — Phase 5 Correctness Review

- **Date**: 2026-11-02
- **Baseline HEAD**: `df8d8f6` (Phase 5 complete)
- **Environment**: Python 3.11.9, PuLP 3.3.2, CBC (via PULP_CBC_CMD)
- **Baseline tests**: 205 passed, ruff clean
- **Scope**: Phase 5 optimizer correctness review (Task 0)

## 0.1 Partial-fulfillment objective (BIG_M lexicographic)

**Current** (`app/procurement/optimizer.py` `_solve` require_full=False):
single-stage objective `TotalCost + 1_000_000 × Shortfall`.

**Finding (P1)**: A fixed BIG_M does NOT strictly guarantee coverage-first.
When landed-cost differences exceed BIG_M (huge unit-price spread, large
fixed freight, tax interactions), the solver may trade coverage for cost.
BIG_M must also be provably > max possible total landed cost, which is not
guaranteed by a magic constant.

**Fix (Commit A)**: strict two-stage solve.
- Stage 1: maximize covered quantity under all hard constraints
  (objective = maximize sum(x), or minimize shortfall).
- Stage 2: FIX the optimal covered quantity from stage 1 (constraint
  `sum(x) == stage1_covered`), then minimize Total Landed Cost.
- Check CBC status + integer feasibility at BOTH stages.
- Regression tests: price spread, large fixed freight, budget boundary,
  stock shortage — coverage must never be sacrificed for cost.

## 0.2 Decimal → float coefficient conversion

**Current**: `float(q.normalized_unit_price_ref)`, `float(freight)`,
`float(tax)`, `float(user_budget_limit)` fed to CBC; results written back
via `Decimal` quantize to 0.01.

**Finding (P1)**: float coefficients can violate exact Decimal budget
boundaries (budget == cost, cost - 0.01, cost + 0.01). No post-solve
Decimal re-verification exists; even a CBC "Optimal" that violates the hard
budget after re-computation in Decimal would pass.

**Fix (Commit A)**:
- Keep float for CBC (necessary), but AFTER solving, recompute
  `total_landed_cost` and `covered` in pure Decimal from the integer
  allocation, and RE-VERIFY every hard constraint (budget ≤ limit with a
  defined tolerance; inventory; deadline; split; share; integer ≥0).
- Budget check: `total_landed_cost <= budget + tolerance`, where tolerance
  is explicitly defined (e.g. 0.005 = half of one cent) to absorb float
  rounding; if the Decimal re-check fails → mark INVALID (never accept a
  budget-violating solution even if CBC says Optimal).

## 0.3 Solver status handling

**Current**: `status != Optimal → INFEASIBLE` with reason "solver returned
{status}".

**Finding (P1)**: CBC may return a FEASIBLE (not proven optimal) solution on
time-limit. Treating that as INFEASIBLE throws away a valid allocation and
mislabels it. Also `LpStatus` "Not Solved" / undefined statuses are not
handled distinctly.

**Fix (Commit A)**:
- Add `Feasibility.SOLVER_UNRESOLVED` (per the Phase 6 contract) and a
  `solver_status` field carrying OPTIMAL / FEASIBLE_NOT_PROVEN_OPTIMAL /
  INFEASIBLE / TIME_LIMIT / ERROR.
- On FEASIBLE_NOT_PROVEN_OPTIMAL with a feasible allocation: return the
  allocation with feasibility class (FULLY/PARTIAL per coverage) AND
  `optimality_proven=False` — never silently claim optimality.
- On true infeasibility (CBC proves no solution): INFEASIBLE.
- On solve error/timeout with no solution: SOLVER_UNRESOLVED.

## 0.4 Greedy PARTIAL vs task-level status (Task 0.3 cross-check)

Phase 5 benchmark shows Greedy covers 105/150 in `many_suppliers` and
reports a recommendation. This is a *recommendation-level* shortfall: the
greedy scorer sets `recommendation.shortfall` and (in the orchestrator)
shortfall routes to WAITING_APPROVAL via the policy gate (Phase 1).
Recommendation generation ≠ task final status — the report must state this
explicitly. No P0 here, but the Phase 6 evaluator will verify at the TASK
level, not the recommendation level.

## 0.5 Other Phase 5 artifacts preserved

- `docs/eval/optimization_comparison.csv`, `optimization_benchmark.json`,
  `ALLOCATION_OPTIMIZER_SPEC.md`, `PHASE5_OPTIMIZATION_REPORT.md` — kept
  as historical results, NOT overwritten by Phase 6.

## Findings summary

| # | finding | severity | disposition |
|---|---|---|---|
| 0.1 | BIG_M single-stage not coverage-first | P1 | **FIXED** in Commit A: strict two-stage lexicographic |
| 0.2 | float coefficients, no Decimal post-check | P1 | **FIXED** in Commit A: Decimal re-verify + 0.005 tolerance |
| 0.3 | non-Optimal mislabeled INFEASIBLE | P1 | **FIXED** in Commit A: SOLVER_UNRESOLVED + feasible-return with optimality_proven=False |
| 0.4 | greedy PARTIAL at recommendation level | P2 | documented; task-level verified in Phase 6 |

No P0 (no state corruption, no approval bypass, no fabricated results).
All three P1s fixed and regression-tested in Commit A (`226 passed`).
