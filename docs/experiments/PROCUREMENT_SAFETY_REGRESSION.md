# Procurement Safety Regression (Phase 6, Commit C)

- **Date**: 2026-11-02
- **Tests**: `tests/test_phase6_safety.py` (13), `tests/test_phase6_eval_correctness.py` (21),
  `tests/test_phase6_benchmark.py` (9) — all pass.

## 1. Business safety invariants (verified)

| invariant | status |
|---|---|
| Hard budget cannot be bypassed (even by approval) | ✅ budget 99.99 < cost 100 → never FULLY; PARTIAL ≤ budget+tolerance |
| Partial fulfillment ≠ complete success | ✅ PARTIAL carries shortfall + "needs re-confirmation"; never auto-full |
| Expired quotes excluded from allocation | ✅ optimizer filters them; verifier flags `line_quote_expired` |
| Non-comparable / FX-missing quotes excluded | ✅ `line_not_comparable`; INFEASIBLE if none remain |
| Over-procurement prohibited | ✅ oracle + optimizer never allocate > requested quantity |
| Integer, non-negative allocations | ✅ verifier + Decimal re-check |
| Recommendation snapshot ↔ approval version consistency | ✅ verifier recomputes cost from the SAME quotes |

## 2. Solver anomaly handling (Task 8)

- Time-limit with feasible-but-unproven solution → returned with
  `optimality_proven=False`, never silently claimed optimal.
- Time-limit with no solution → `SOLVER_UNRESOLVED` (not INFEASIBLE).
- CBC-proven infeasible → INFEASIBLE with reason.
- Decimal re-check catches any budget violation even when CBC says Optimal.

## 3. Workflow integration semantics (Task 9)

| allocation class | workflow behavior |
|---|---|
| FULLY_FEASIBLE | eligible for policy → approval / finalize |
| PARTIAL_FEASIBLE | carries requested/allocated/shortfall → re-confirmation / approval |
| INFEASIBLE | NO executable procurement result (lines == []) |
| SOLVER_UNRESOLVED | treated as unresolved; not executable |

The greedy scorer remains the default path; the optimizer is an evaluable
alternative (feature-flagged) and will only replace the default after the
Phase 6 gate.

## 4. Phase 1–4 safety regressions

All Phase 1–4 tests still pass (235 total). Approval CAS, durable runtime
fencing, portal recovery, and the FX/normalizer contracts are unchanged.
