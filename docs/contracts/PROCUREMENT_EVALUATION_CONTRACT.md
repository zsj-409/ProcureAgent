# Procurement Evaluation Contract (Phase 6)

- **Version**: `procurement-eval-v1` (2026-11-02)
- **Evaluator**: `tests/eval/procurement_benchmark.py` + `procurement_adapters.py`
  + `procurement_world.py`; `app/procurement/verifier.py` (independent);
  `app/procurement/oracle.py` (exhaustive reference).

## 1. Unified problem input

Every method accepts the SAME `ProcurementWorld`:

product_id (implied by catalog), requested_quantity, reference_currency,
user_budget_limit, budget_mode (HARD), delivery_deadline, supplier_allowlist,
inventory_snapshot (per quote stock), supplier_quote_snapshot (price,
currency, validity), quote_expiry, max_split_suppliers, supplier_share_limit,
freight, tax, required_policy_version. Reuses the existing schemas
(`SupplierQuote`, `AllocationProblem`) — no duplicate business models.

## 2. Unified output

`AllocationCandidate`: allocation lines (supplier_id, quantity), feasibility,
covered_quantity, shortfall, total_landed_cost, solver_status,
optimality_proven, solve_time_s, violations, detail. The SAME shape for
greedy / cheapest / optimizer / oracle — no structural advantage for any
method.

## 3. Feasibility taxonomy

| class | meaning |
|---|---|
| FULLY_FEASIBLE | all quantity + all hard constraints satisfied |
| PARTIAL_FEASIBLE | hard constraints satisfied, partial coverage; explicit shortfall; NOT a complete success |
| INFEASIBLE | no legal positive allocation (or full-fulfillment impossible by spec) with reason |
| INVALID | candidate violates a hard constraint or cannot be confirmed legal |
| SOLVER_UNRESOLVED | timeout / exception / no optimality proof |

FULLY_FEASIBLE and PARTIAL_FEASIBLE are NEVER mixed in one success-rate
denominator.

## 4. Independent verification

The evaluator uses `AllocationConstraintVerifier` (recomputes every
constraint from first principles) and `SmallWorldOracle` (exhaustive
enumeration ≤4 suppliers / ≤12 units). The optimizer's own solver output is
never treated as ground truth. CBC statuses: OPTIMAL / FEASIBLE_NOT_PROVEN
/ INFEASIBLE / TIME_LIMIT / ERROR are mapped to the taxonomy above.

## 5. Metrics (with explicit n)

Verified Full Fulfillment Rate, Hard Constraint Violation Rate, Partial
Fulfillment Rate (+ shortfall distribution), Independent Oracle Optimality
Gap, Cost per Verified Full Fulfillment, Solver Runtime P50/P95, Timeouts,
Optimality Status, Supplier Count, Allocation Stability, Budget Utilization,
Cost Saving on Jointly Feasible Worlds (paired, both FULLY), Recommendation
Correctness, Approval Routing Correctness. Purchasing spend and compute cost
are separate metrics.

## 6. Fairness rules

- Greedy 105/150 is reported as (covered 105, shortfall 45, cost 1107.5) —
  NEVER compared as "cheaper" than optimizer 150/150.
- Cost Saving only on jointly-fully-feasible pairs.
- Each method runs on a fresh, hashed world; no cache/shared mutated state;
  oracle results never leak to the optimizer.
- Same business-meaning inputs → same feasibility; changed quantity/budget/
  deadline → correct change.
