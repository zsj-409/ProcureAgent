# Constrained Procurement Allocation Optimizer Spec (Phase 5)

- **Module**: `app/procurement/optimizer.py`
- **Solver**: PuLP 3.3.2 / CBC (integer programming, exact for reasonable n)
- **Baseline**: `app/procurement/scorer.py` (weighted greedy split award)

## 1. Problem

Given a set of comparable supplier quotes and a requested quantity, choose
awards x_i (integer units per supplier) that minimize **Total Landed Cost**
subject to hard constraints. The greedy scorer picks by weighted score and
splits only when stock is short; it does NOT model budget, deadline,
admission, share caps, or tax/freight, and it reports a shortfall without
distinguishing feasibility classes.

## 2. Constraints (all hard)

| constraint | semantics |
|---|---|
| quantity conservation | Σ x_i = quantity when fully feasible (else maximize coverage) |
| inventory | 0 ≤ x_i ≤ available_stock_i |
| supplier admission | whitelist; excluded suppliers get x=0 |
| hard budget | Σ landed_cost ≤ user_budget_limit (HARD); exceeding → infeasible |
| delivery deadline | supplier with delivery_days > deadline → excluded |
| max split suppliers | Σ y_i ≤ max_split_suppliers (y_i = used flag) |
| supplier share cap | x_i ≤ share_cap × quantity |
| tax & freight | landed_cost_i = unit_i·x_i + freight_i·y_i + tax_i·(unit_i·x_i) |
| unified currency | all comparisons use normalized_unit_price_ref (reference currency) |

## 3. Feasibility classification (business-critical)

| class | meaning | business handling |
|---|---|---|
| FULLY_FEASIBLE | quantity fully covered within constraints | proceed to approval |
| PARTIAL_FEASIBLE | non-empty award exists but shortfall > 0 | **needs re-confirmation — NEVER auto-completed** |
| INFEASIBLE | no positive award possible (no stock / budget too low / deadline impossible / whitelist empty) | explicit failure with reason |

A low-cost partial allocation is NEVER presented as a complete procurement
success (spec rule).

## 4. Objective

minimize Σ (unit_i·x_i + freight_i·y_i + tax_i·unit_i·x_i), all in the
reference currency. When full feasibility is impossible, a lexicographic
objective first maximizes covered quantity (BIG_M penalty on shortfall),
then minimizes landed cost.

## 5. Verified behavior (tests/test_phase5_optimizer.py, 18 tests)

- quantity conservation (full & partial), inventory bound, zero-stock →
  INFEASIBLE
- admission whitelist (allowed / all-excluded → INFEASIBLE)
- hard budget binds (partial within budget; too-low budget → INFEASIBLE)
- delivery deadline excludes slow suppliers
- max split cap (partial when cap limits coverage; full when cap allows)
- supplier share cap respected
- tax & freight change supplier choice toward landed cost
- currency unification via reference cost (raw EUR price not compared)
- incomparable quotes → INFEASIBLE (no fake FX)
- optimizer ≤ greedy on same inventory; budget-aware split
- solve time < 10s on 8-supplier problems

## 6. Benchmark vs baselines (tests/eval/optimization_benchmark.py, 7 cases)

| case | greedy | cheapest | optimizer | feasibility (opt) |
|---|---|---|---|---|
| single_cheapest | 500 | 500 | 500 | FULLY |
| stock_scarcity | 1040 | 1040 | 1040 | FULLY |
| budget_constrained | 1000 | 1000 | 1000 | FULLY |
| deadline_constrained | 400 | **320 (violates)** | 400 | FULLY |
| split_cap | **270 (3 suppliers, violates cap)** | 270 | 210 (2 suppliers) | **PARTIAL** |
| many_suppliers | **1107 (covers 105/150)** | 1625 | 1625 (covers 150/150) | FULLY |
| deep_stock_split_cap | 1000 | 1000 | 1000 | FULLY |

Key findings:
- **Cheapest-feasible violates the deadline** (picks the 8-unit/10-day
  supplier over the 10-unit/1-day one).
- **Greedy violates the split cap** (uses 3 suppliers when max=2) and, in
  many_suppliers, **covers only 105/150 while reporting a recommendation** —
  the classic "low-cost partial plan presented as success" the spec forbids.
- **Optimizer honors every hard constraint** and returns an explicit
  feasibility class; when it must stay within a 2-supplier cap it reports
  PARTIAL (20/25) instead of pretending full success.
- On simple, unconstrained cases all three methods agree (the optimizer is
  not needed there — honest boundary).

## 7. Runtime

CBC solves all benchmark cases in <0.1s (8 suppliers, integer vars). The
10s time limit is a guardrail; larger catalogs scale polynomially in the
number of suppliers (branch-and-bound), not in quantity.

## 8. Integration (Phase 5 scope)

The optimizer is an independent, testable component. Wiring it into the
orchestrator's scoring step (replacing the greedy split) is a separate
decision gated on these benchmark results — the greedy stays the default
Baseline until then.
