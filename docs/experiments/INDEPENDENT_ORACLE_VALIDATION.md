# Independent Oracle Validation (Phase 6, Commit A)

- **Date**: 2026-11-02
- **Module**: `app/procurement/oracle.py` (SmallWorldOracle),
  `app/procurement/verifier.py` (AllocationConstraintVerifier),
  `app/procurement/optimizer.py` (two-stage lexicographic + Decimal re-check)
- **Commit A**: Phase 6 evaluation correctness

## 1. Why an independent oracle

The Evaluator must NOT trust the PuLP/CBC output as ground truth. For small
integer worlds (≤4 suppliers, ≤12 units) the `SmallWorldOracle` enumerates
ALL integer allocations satisfying the hard constraints (inventory, split
cap, share cap, deadline, quote validity, budget, whitelist, over-procurement
prohibition) and computes the true minimum Total Landed Cost by exhaustive
search — no PuLP involved. Ties at the same cost are equally optimal (a
different supplier combo with equal cost is NOT a mismatch).

## 2. Optimizer == Oracle consistency (verified)

| world | oracle optimum | optimizer | gap |
|---|---|---|---|
| 3 suppliers × 5 stock, qty 12, split≤3 | 129.00 | 129.00 | 0 |
| 2 suppliers, freight changes optimum (s1 +10 freight) | 105.00 (s2) | s2 | 0 |
| 2 suppliers, qty 12, stock 5+5 (partial) | 110.00 (10 units) | 110.00, PARTIAL | 0 |
| budget 1 < min unit cost | infeasible | INFEASIBLE | — |

The optimizer's allocation matches the oracle's optimal cost in every small
world tested (`tests/test_phase6_eval_correctness.py`, oracle section).

## 3. Coverage-first guarantee (two-stage, no BIG_M)

Phase 5 used `TotalCost + 1_000_000 × Shortfall` — a magic constant that
does NOT strictly guarantee coverage-first when landed-cost differences
exceed BIG_M. Phase 6 replaces it with a strict two-stage solve:

1. **Stage 1**: maximize covered quantity under all hard constraints
   (objective = −Σx). CBC status checked.
2. **Stage 2**: fix stage-1 optimal coverage (`Σx == covered_opt`), minimize
   Total Landed Cost. CBC status checked again.

Regression tests prove coverage is never sacrificed for cost: price spread
500×, fixed freight 1000, stock shortage — the solver always covers the
maximum possible quantity first.

## 4. Decimal re-verification (Task 0.2)

After solving, `_decimal_recheck` recomputes total landed cost and every
constraint in pure Decimal from the integer allocation:
- inventory, deadline, split count, share cap
- budget ≤ limit + 0.005 tolerance (float-rounding absorption)
- integer & non-negative quantities

Any violation → `INVALID` (never pass a budget-violating solution even if
CBC says Optimal). Budget boundary tests: cost == budget (OK), budget − 0.01
(PARTIAL/INFEASIBLE, never FULLY), 2-decimal prices (no false violation).

## 5. Solver status handling (Task 0.3)

`Feasibility` now includes `SOLVER_UNRESOLVED` and `INVALID`. Status mapping:
- CBC Optimal → FULLY/PARTIAL (per coverage) with `optimality_proven=True`
- CBC Feasible (not proven optimal, e.g. time limit) → allocation returned
  with `optimality_proven=False` — never silently claimed optimal
- CBC proved infeasible → INFEASIBLE
- CBC error / timeout with no solution → SOLVER_UNRESOLVED

## 6. Independent constraint verifier (Task 3)

`AllocationConstraintVerifier` re-verifies ANY candidate (greedy, cheapest,
optimizer, oracle) from first principles: total quantity, per-supplier
inventory, admission, deadline, split cap, share cap, landed cost (Decimal,
reference currency), hard budget, quote validity, FX comparability, integer
≥0, shortfall. Emits explicit `reason_code` violations (e.g.
`line_inventory_exceeded:s1`, `hard_budget_exceeded:...`,
`line_quote_expired:s1`, `line_not_comparable:s1`). Feasibility follows the
Phase 6 contract: FULLY / PARTIAL / INFEASIBLE / INVALID /
SOLVER_UNRESOLVED. UNKNOWN or non-comparable data is never coerced into
FULLY_FEASIBLE.

## 7. Tests (Commit A, 21 new)

Two-stage lexicographic (4), Decimal budget boundaries (3), solver status
(3), verifier (7), oracle consistency (4). **226 total passed**, ruff clean.
