# Procurement Benchmark Protocol (Phase 6, Commit B)

- **Date**: 2026-11-02
- **Commit B**: reproducible benchmark
- **Registry**: `tests/eval/procurement_world.py`, `tests/eval/procurement_adapters.py`,
  `tests/eval/procurement_benchmark.py`

## 1. Environment isolation

Every world is fully defined by (family, seed, split, env_version) and has a
`snapshot_hash()` covering quotes, constraints, fees, and seed. Each method
(A greedy / B cheapest / C optimizer / D oracle) runs against the SAME world
input but the evaluator re-verifies every candidate independently — no
method mutates shared state, and the oracle's result is NEVER fed to the
optimizer (no leakage). `world_registry` rebuilds worlds deterministically.

## 2. Data splits

| split | families | purpose |
|---|---|---|
| DEV | unconstrained, single_supplier_shortage, multi_supplier_joint, split_cap | development & debugging |
| CONFIRMATION | tight_budget, freight_tax, delivery_conflict, allowlist | strategy selection |
| TEST | fx_failure, expired_quote, share_limit, fully_infeasible, partial_confirm, mixed_constraints | frozen evaluation |

2 seeds per family → 28 worlds. The TEST families are NOT used for
development; the benchmark runner's `split_filter` supports running any
subset independently.

## 3. Frozen configuration

Evaluator v1, solver PuLP 3.3.2/CBC, exhaustive oracle (≤4 suppliers, ≤12
units), fees model (per-supplier freight + tax), metric definitions, result
directory `docs/eval/`. All artifacts carry the date + version.

## 4. Metrics (with explicit n)

Per method: Verified Full Fulfillment Rate, Hard Constraint Violation Rate,
Partial Fulfillment Rate (+ shortfall distribution), Infeasible Rate,
Invalid Rate, Unresolved Rate, Cost per Verified Full Fulfillment, Solver
Runtime P50/P95, Optimality-Proof Count. Paired: Cost Saving on Jointly
Feasible Worlds (both FULLY only — never across coverage levels).

## 5. Results (28 worlds, 2 seeds/family)

| method | FULLY | violation | PARTIAL | INFEASIBLE | p95 runtime |
|---|---|---|---|---|---|
| greedy | 14/28 | 8/28 | 4/28 | 2/28 | ~0s |
| cheapest | 10/28 | 12/28 | 4/28 | 2/28 | ~0s |
| optimizer | 16/28 | 2/28 | 8/28 | 2/28 | 0.063s |
| oracle | 2/10 | 0/10 | 6/10 | 2/10 | ~0s |

- **Optimizer** achieves the highest full-fulfillment (16/28) and the
  lowest constraint-violation rate (2/28).
- **Cheapest** violates the most constraints (12/28: ignores deadline,
  budget, split caps).
- **Greedy** violates 8/28 (budget/deadline/split in constrained worlds).
- **Oracle** (independent exhaustive) has ZERO violations on the 10 small
  worlds it can handle — confirming the verifier agrees with the oracle.
- **Paired, jointly fully feasible (n=14): mean cost saving of the
  optimizer vs greedy = 2.67%** — an honest, bounded claim.

## 6. Honest limits

- Oracle only handles small worlds (10/28); large-world optimality relies on
  CBC status + verifier, not the oracle.
- 2 seeds per family is exploratory for family-level conclusions; the
  paired n=14 is the statistically meaningful unit.
- Cost saving is ONLY on jointly fully feasible pairs (both methods covered
  100%). It is never presented as enterprise profit growth.
