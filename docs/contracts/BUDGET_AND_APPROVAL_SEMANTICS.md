# Budget & Approval Semantics (Phase 1)

- **Business rule version**: `budget-approval-rules-v1` (2026-11-01)
- **Module**: `app/procurement/policy.py`

## 1. Two distinct constraints

1. **User hard budget** (`user_budget_limit` + `budget_currency`): an absolute
   constraint declared by the requester. It can never be overridden by an
   internal approval. Over-budget → `HARD_BUDGET_EXCEEDED`.
   - `budget_mode=HARD` → solution is **infeasible** (`FAILED`), re-quote or
     revise constraints required.
   - `budget_mode=SOFT`/`APPROVAL_GATE` → explicit human approval (gated).
2. **Internal approval threshold** (`approval_threshold`, configured):
   an enterprise policy that only gates *whether human sign-off is required*.
   A solution that satisfies the user budget may still require approval when
   `total >= threshold` (`BUDGET_ABOVE_APPROVAL_THRESHOLD`).

Legacy `max_budget` is interpreted per `budget_mode` (see
`PROCUREMENT_INPUT_CONTRACT.md` §4). `user_budget_limit` wins over `max_budget`.

## 2. Evaluation order (deterministic)

1. Currency resolution (convert total to budget currency; unresolved →
   `CURRENCY_UNRESOLVED` review).
2. Shortfall check (`shortfall > 0` → `WAITING_APPROVAL`, partial order is a
   business call).
3. User hard budget (over → HARD `FAILED` / SOFT `WAITING_APPROVAL`).
4. Unknown total → `TOTAL_UNKNOWN` (cannot auto-approve).
5. Internal threshold (at/above → `WAITING_APPROVAL`).
6. Else auto-approve (`APPROVED`).

## 3. Decision fields

`PolicyDecision`: `requires_approval`, `status`, `reason`, `reason_code`,
`budget_mode`, `hard_budget_ok`, `total_unknown`, `constraint_violation_reasons`.

## 4. Matrix (verified by tests)

| Hard budget | Threshold | Cost | Mode | Outcome |
|---|---|---|---|---|
| 1500 | 1000 | 900 | any | auto-approved |
| 1500 | 1000 | 1200 | any | approval required (threshold) |
| 1500 | 1000 | 1600 | HARD | FAILED (infeasible) |
| 1500 | 1000 | 1600 | APPROVAL_GATE | WAITING_APPROVAL |
| 1500 | 1000 | unknown | any | WAITING_APPROVAL (TOTAL_UNKNOWN) |
| 1500 USD | 1000 | 1400 EUR | HARD | convert → FAILED (1521.80 USD) |
| JPY total | 1000 | 5000 JPY | any | WAITING_APPROVAL (CURRENCY_UNRESOLVED) |
| 1500 | 1000 | 1000 | any | approval required (equal threshold) |
| 1500 | 1000 | 1500 | HARD | threshold applies; budget OK |
| 1500 | 1000 | 900, shortfall 3 | any | WAITING_APPROVAL (SHORTFALL) |

## 5. Orchestrator integration

`ProcurementOrchestrator._execute_plan` passes `user_budget_limit`,
`budget_currency`, `budget_mode`, `budget_policy_version` from the request into
`policy.evaluate`. The scorer emits `estimated_total` in the reference
currency (USD), which the policy compares against the budget currency with FX.
