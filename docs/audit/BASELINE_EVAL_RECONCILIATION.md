# Baseline Eval Reconciliation (Phase 1)

- **Date**: 2026-11-01

## 1. The discrepancy

- README historical claim: `Recommendation Accuracy = 1.000`, `Recovery Success Rate = 1.000`.
- Measured on this machine (2026-11-01, before Phase-1 fixes):
  - `Recommendation Accuracy` = **0.667** (run 1) / **0.750** (run 2)
  - `Recovery Success Rate` = 1.000, but **n=1** (single `flaky_retry_recovers` case)

## 2. Why the README 1.000 was not reproducible

1. **The metric is label-matching, not correctness.** `accuracy_cases` compare
   `recommended_supplier` against the **pre-filled** `CASES[].supplier` chosen by
   the test author. The mock catalog's ground-truth optimum is not derived —
   portal prices differ from API prices, and mock state mutates across runs
   (a `_flaky_failed_once` module flag). So the "right answer" is
   author-determined, not cost-determined, and the score varies with the
   environment.
2. **Expected supplier ids are not objective.** `low_total_no_approval`
   expected `mock_api_supplier`, but the mock portal can price `MX Master 3S`
   at $89.99 vs API $99.99 — under BALANCED weights the portal supplier wins,
   so `supplier_match=False` on some runs. That is a *test-expectation* flaw,
   not a system regression.
3. **Recovery 1.000 from n=1** — one successful flaky retry. Not a rate.

## 3. Resolution (Phase 1 changes)

- `tests/eval/eval_runner.py`:
  - `recovery_success_rate` → **`single_fault_recovery: {pass, n}`** with the
    explicit label "NOT a population rate".
  - `recommendation_accuracy` now reports **n** alongside the rate.
  - `low_stock_fails` → `low_stock_shortfall_routes_to_approval`
    (expected `WAITING_APPROVAL`): the v2 split-award change routes stock
    shortfalls to human review; the old `FAILED` expectation contradicted the
    shipped behavior. Business rule version: `budget-approval-rules-v1`.
- README: metrics table now classifies each number as
  `HISTORICAL_RESULT` / `REPRODUCED_BASELINE` / `CURRENT_PHASE1_RESULT` /
  `NOT_EVALUATED`; the historical 1.000s are preserved but explicitly
  **not claimed** for the current code.

## 4. Current status (after Phase-1 fixes)

| Metric | Value | Classification |
|---|---|---|
| Task Success Rate | 1.000 | CURRENT_PHASE1_RESULT (16-case suite) |
| Recommendation Accuracy | 0.833 (n=12) | CURRENT_PHASE1_RESULT — label-matching; true cost-optimal derivation is Phase 6 |
| Single Fault Recovery | pass=1/1 (n=1) | CURRENT_PHASE1_RESULT — NOT a population rate |
| Plan Validation | 1.000 (inline) + adversarial suite | CURRENT_PHASE1_RESULT |

## 5. Open items

- True recovery-rate benchmark: Phase 2 fault-injection matrix.
- True recommendation ground truth (min landed cost in one currency over the
  actual catalog): Phase 6 evaluator.
- `low_stock` semantics now match the shipped behavior; the case name was
  changed rather than the program, per the "do not bend tests to pass" rule.
