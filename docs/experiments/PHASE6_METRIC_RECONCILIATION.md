# Phase 6 Metric Reconciliation (Phase 7 Task 0 — blocking gate)

- **Date**: 2026-11-02
- **Baseline HEAD**: `8c4cba4` (Phase 6 complete)
- **Scope**: reconcile Phase 6 metrics before Phase 7 experiments.
  Historical results are PRESERVED; this document is the errata version.

## 1. Greedy FULLY=14, INVALID=8 — any double counting?

**No.** The verifier assigns ONE feasibility label per candidate
(FULLY / PARTIAL / INFEASIBLE / INVALID / SOLVER_UNRESOLVED) — mutually
exclusive. Greedy's 14 FULLY_FEASIBLE worlds all have **zero violations**
(checked: 0 FULLY-with-violations rows); the 8 INVALID are a separate
disjoint count (split_cap ×2, expired_quote ×2, share_limit ×2,
mixed_constraints ×2).

**ERRATA (reporting)**: the Phase 6 final report's phrase "真实合法 FULLY=6"
(14 − 8) was WRONG. FULLY_FEASIBLE already excludes violators by
construction; the correct statement is **Greedy legally fulfills 14/28
(FULLY, 0 violations) and produces 8 INVALID (constraint-violating)
candidates** — those are not part of any success rate, but they are also
not subtracted from the 14. Fix: `GREEDY_VS_OPTIMIZER_REPORT.md` §5.1
updated.

## 2. Paired n=14 — judged by the independent verifier?

**Yes.** `both_fully_feasible` is set when BOTH candidates have
`feasibility == FULLY_FEASIBLE`, which comes from
`AllocationConstraintVerifier` (independent of the optimizer). Verified by
re-checking the paired rows.

## 3. Bootstrap ignored within-family correlation

**Confirmed (P1 metric flaw).** The ordinary paired bootstrap resampled
the 14 pairs i.i.d., ignoring that pairs from the same family (2 seeds,
same template) are correlated.

**Fix (this commit)**: `cluster_bootstrap` resamples WHOLE families with
replacement. Result (n_clusters=7, n_pairs=14):

| method | mean saving | 95% CI |
|---|---|---|
| ordinary bootstrap | 2.671% | [0.621, 5.157] |
| **cluster bootstrap** | 2.671% | **[0.0, 6.4]** |

**ERRATA**: with cluster bootstrap the CI lower bound reaches 0 — the cost
saving is NOT statistically significant at the family-cluster level
(effective independent n=7). The Phase 6 claim "CI excludes 0" was based on
the over-precise ordinary bootstrap. The honest claim is: point estimate
2.67%, cluster CI [0, 6.4] — directionally positive, not significant.

## 4. Did TEST participate in debugging?

**Partial (execution-bug fix, not tuning).** The `expired_quote` family
(TEST) exposed an optimizer bug (it selected expired quotes). The fix was a
correctness patch (filter `quote_valid_until`), NOT a parameter update using
TEST outcomes. Per the Phase 6 protocol, the pre-fix TEST results are
preserved as historical; the post-fix run is the current version. No policy
parameter was tuned on TEST.

## 5. Snapshot hash binds quote validity + frozen clock

**Improved (this commit).** Previously the hash's `expired` flag was
computed at hash-time from the live clock — a world could flip expiry across
its lifetime. Now `_expired_flags` are FROZEN at construction; the hash uses
the frozen flags. Same-seed worlds hash identically; expiry state is stable
for the world's lifetime.

## 6. All methods use the SAME hard constraints?

**Confirmed + fixed (P1 fairness flaw).** Greedy's split cap was hard-coded
to 3 (`max_split_lines()`) while the optimizer used the world's
`max_split_suppliers` (2 in split_cap). **Fix**: `ProcurementScorer.score`
accepts an optional `max_split`; `GreedyAdapter` now passes the world's
`max_split_suppliers`, so every method sees the same constraint.

**Impact**: Greedy violation 8→6, partial 4→6 (split_cap now correctly
PARTIAL instead of INVALID); FULLY stays 14; paired n=14 unchanged;
saving 2.67% unchanged.

## Per-world status table (greedy, post-fix)

| world | family | feasibility | violations |
|---|---|---|---|
| unconstrained-1/2 | unconstrained | FULLY | — |
| single_supplier_shortage-1/2 | single_shortage | PARTIAL | — |
| multi_supplier_joint-1/2 | multi_joint | FULLY | — |
| split_cap-1/2 | split_cap | PARTIAL (was INVALID) | — (was split_count_exceeded) |
| tight_budget-1/2 | tight_budget | FULLY | — |
| freight_tax-1/2 | freight_tax | FULLY | — |
| delivery_conflict-1/2 | delivery_conflict | FULLY | — |
| fx_failure-1/2 | fx_failure | FULLY | — |
| expired_quote-1/2 | expired_quote | INVALID | line_quote_expired:s1 |
| allowlist-1/2 | allowlist | FULLY | — |
| share_limit-1/2 | share_limit | INVALID | line_share_cap_exceeded:s1 |
| fully_infeasible-1/2 | fully_infeasible | INFEASIBLE | — |
| partial_confirm-1/2 | partial_confirm | PARTIAL | — |
| mixed_constraints-1/2 | mixed_constraints | INVALID | hard_budget_exceeded |

## Gate status

Metrics reconciled: errata documented, cluster bootstrap added, frozen
expiry hash, unified split cap. **Gate 0 PASSED** — Phase 7 experiments may
start. Historical Phase 6 files remain; this document is the authoritative
correction.
