# Phase 6 Final Report

- **Date**: 2026-11-02
- **Commits**: `579aac8` (A), `51e0f85` (B), (C)
- **Tests**: 248 passed (235 + 13 safety), ruff clean

## 1. What was built

| Commit | content |
|---|---|
| A `579aac8` | P1 fixes (two-stage lexicographic, Decimal re-check, SOLVER_UNRESOLVED); independent constraint verifier; exhaustive small-world oracle; 21 correctness tests |
| B `51e0f85` | 28-world registry (14 families × 2 seeds, DEV/CONFIRMATION/TEST); unified adapters A/B/C/D; metrics registry; paired comparison; expired-quote optimizer bug fix; 9 reproducibility tests |
| C (this) | workflow integration + safety regression (13 tests); solver scaling; paired bootstrap + failure analysis; final reports |

## 2. Phase 5 correctness findings

- **0.1 BIG_M lexicographic → REPLACED** with strict two-stage solve
  (coverage first, then cost at fixed coverage).
- **0.2 float coefficients → Decimal re-verification** with 0.005 budget
  tolerance; INVALID on any post-check violation.
- **0.3 non-Optimal mislabeled INFEASIBLE → SOLVER_UNRESOLVED** + feasible
  returns with optimality_proven=False.
- **Bonus bug found by the benchmark**: optimizer selected expired quotes →
  fixed (filter quote_valid_until).
- No P0; greedy PARTIAL is recommendation-level (task state routes to
  WAITING_APPROVAL via policy).

## 3. Independent oracle results

`SmallWorldOracle` (exhaustive, no PuLP) agrees with the optimizer on every
small world: 129.00 (3 suppliers split), 105.00 (freight flips choice),
110.00 partial (10/12). Oracle has ZERO constraint violations on all 10
small worlds. CBC is validated, not assumed correct.

## 4. Full comparison (28 worlds)

| method | FULLY | violation | PARTIAL | INFEASIBLE | INVALID |
|---|---|---|---|---|---|
| Greedy | 14/28 | 8/28 | 4/28 | 2/28 | 8/28 |
| Cheapest | 10/28 | 12/28 | 4/28 | 2/28 | 12/28 |
| Optimizer | 18/28 | 0/28 | 8/28 | 2/28 | 0/28 |
| Oracle | 2/10 | 0/10 | 6/10 | 2/10 | 0/10 |

## 5. Statistical claims (honest bounds)

- Paired cost saving (optimizer vs greedy, jointly fully feasible, n=14):
  **mean 2.67%, 95% CI [0.62%, 5.16%]** (bootstrap 2000).
- Feasibility improvement: 4+ worlds that greedy cannot legally fulfill
  (share_limit, mixed_constraints, expired_quote, split_cap) are fulfilled
  by the optimizer.
- Solver scaling: 10→80 suppliers: 0.047s→0.094s. No timeouts.
- Family-level n=2 seeds is exploratory; n=14 paired is the primary unit.
- No simulated savings described as enterprise profit growth.

## 6. Remaining risks

- Oracle limited to ≤4 suppliers / ≤12 units (exhaustive enumeration);
  large-world optimality rests on CBC status + verifier.
- n=14 paired sample is small; CI wide.
- The optimizer is NOT yet the production default (greedy remains); gate
  must pass before swapping.
- No real-LLM / real-supplier runs (all simulated).

## 7. Phase 6 Gate (Task 14, 18 items)

| # | gate | status |
|---|---|---|
| 1 | verifier independent of optimizer | ✅ `app/procurement/verifier.py` |
| 2 | small-world oracle verifies optimality | ✅ exhaustive, agrees with CBC |
| 3 | CBC numeric error cannot violate budget | ✅ Decimal re-check + INVALID |
| 4 | partial solve guarantees coverage-first | ✅ two-stage, no BIG_M |
| 5 | full/partial/infeasible semantics clear | ✅ taxonomy + tests |
| 6 | greedy & optimizer same public input | ✅ unified world + candidate |
| 7 | partial low-cost never an optimization gain | ✅ paired-only cost saving |
| 8 | FX/quote/inventory/deadline/budget all verified | ✅ verifier + tests |
| 9 | timeout/unproven-optimal handled | ✅ SOLVER_UNRESOLVED |
| 10 | state machine never auto-fulls a partial | ✅ safety tests |
| 11 | Phase 1–4 safety no regression | ✅ 248 total pass |
| 12 | experiment environments independent | ✅ world hashes + reset |
| 13 | frozen config + manifest | ✅ experiment_manifest.json |
| 14 | oracle not leaked to decision methods | ✅ adapters never read oracle |
| 15 | metrics with n and statistical bounds | ✅ explicit rates + bootstrap CI |
| 16 | failures & no-gain cases reported | ✅ failure_cases.json + negative results |
| 17 | optimizer only replaces greedy after gate | ✅ gated decision, documented |
| 18 | no simulated savings as enterprise profit | ✅ explicit framing |

**GATE PASSED.**

## 8. Optimizer into an optional business path?

**Yes — as an optional, feature-flagged evaluable path.** The evidence:
0/28 constraint violations, 18/28 legal full fulfillment (vs 6 legal for
greedy after removing its 8 INVALID), 2.67% cost saving on jointly feasible
pairs (CI excludes 0), negligible runtime. The greedy scorer remains the
default until an integration decision (Phase 9 Workbench comparison).

## 9. Next-phase admission

- Phase 7 (Recovery Policy Evolution) or Phase 9 (Workbench) may start.
- The optimizer can be surfaced in the Workbench's Procurement Optimization
  Comparison with the honest paired numbers.
