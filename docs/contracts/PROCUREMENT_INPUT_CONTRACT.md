# ProcureAgent Input Contract (Phase 1)

- **Version**: `input-contract-v1` (2026-11-01)
- **Business rules**: `budget-approval-rules-v1`
- **Applies to**: `ProcurementRequest`, `SupplierQuote`, `ProcurementPlan`, `PolicyDecision`

## 1. Request contract (`ProcurementRequest`)

| Field | Type | Semantics |
|---|---|---|
| `product_name` | str (1..200) | Product to procure; must be non-empty |
| `quantity` | int > 0 | Units requested (procurement quantity) |
| `max_budget` | Decimal? (legacy) | Kept for compatibility. Interpretation depends on `budget_mode` (see §4) |
| `user_budget_limit` | Decimal? | **User hard budget** — an absolute constraint that internal approval cannot override. Takes precedence over `max_budget` when both set |
| `budget_currency` | str | Currency of the budget (default USD) |
| `budget_mode` | HARD / SOFT / APPROVAL_GATE | Hard = infeasible if exceeded; SOFT/APPROVAL_GATE = approval trigger |
| `budget_policy_version` | str | Version of the budget/approval rule applied (default `v1`) |
| `preference` | BALANCED/PRICE/DELIVERY/STOCK | Deterministic scoring weights |

**Interpretation rules for legacy `max_budget`**:
- `budget_mode=HARD` → `max_budget` acts as an absolute hard limit.
- `budget_mode=APPROVAL_GATE` (default) → `max_budget` is an approval trigger
  (legacy behavior preserved).
- `user_budget_limit` always takes precedence over `max_budget`.

## 2. Quote contract (`SupplierQuote`)

The quote keeps BOTH the original raw payload and the normalized reference
cost. Original data is never overwritten.

| Field | Semantics |
|---|---|
| `unit_price` / `currency` | Original price in the supplier's currency |
| `raw_quote` | Immutable raw payload as returned by the supplier |
| `reference_currency` | Normalization target (USD by default) |
| `normalized_unit_price_ref` | Unit price converted to reference currency (only when comparable) |
| `normalized_total_cost_ref` | `unit_price_ref × quantity` (only when comparable) |
| `fx_rate_id` / `fx_rate_as_of` | FX provenance (rate id + timestamp) |
| `fx_status` | `OK` / `FX_RATE_MISSING` / `FX_RATE_STALE` / `INVALID_DIRECTION` |
| `normalization_status` | `raw` / `comparable` / `incomparable` / `incomplete` |
| `tax_amount`, `tax_mode`, `shipping_fee` | Landed-cost extras; recorded, never double-counted into unit price |
| `quote_valid_until` | Quote validity; expired quotes must not be auto-trusted |
| `source_ref`, `source_snapshot_hash` | Audit provenance |

**Comparability rule**: a quote is comparable (`is_comparable()`) only when
`normalization_status == "comparable"` and `normalized_unit_price_ref` is set.
Non-comparable quotes are never price-ranked by the scorer.

## 3. Plan contract (`ProcurementPlan`)

Valid skeleton (dependency-ordered):

```
(COLLECT_SUPPLIER* → NORMALIZE* → SCORE → CHECK_POLICY [→ FINALIZE])?
```

- Empty plan → invalid.
- Every action must be in `PlanAction`.
- COLLECT_SUPPLIER requires a registered supplier_id; no duplicates.
- SCORE after all COLLECT_SUPPLIER; NORMALIZE before SCORE.
- CHECK_POLICY after SCORE.
- FINALIZE, when present, must be the last step (after CHECK_POLICY).
- **A plan may end at CHECK_POLICY** — that is the legitimate human-review
  path (shortfall / budget exception → WAITING_APPROVAL). FINALIZE is NOT
  forced on review tasks.

## 4. Budget vs approval semantics

| User hard budget | Internal approval threshold | Solution cost | Outcome |
|---|---|---|---|
| 1500 USD | 1000 USD | 900 USD | Budget OK, below threshold → auto-approved |
| 1500 USD | 1000 USD | 1200 USD | Budget OK, at/above threshold → **approval required** |
| 1500 USD | 1000 USD | 1600 USD | **Violates hard budget** → infeasible (HARD) / approval-gated (SOFT) |
| 1500 USD | 1000 USD | unknown | **Cannot auto-approve** (TOTAL_UNKNOWN) |
| 1500 USD | 1000 USD | 1400 EUR | Must convert EUR→USD first; unresolved FX → CURRENCY_UNRESOLVED review |

`approval_threshold` is an internal **policy** rule, never the user's budget.

## 5. Validation failures (machine-readable)

`PolicyDecision` carries `reason_code`:
`HARD_BUDGET_EXCEEDED`, `BUDGET_ABOVE_APPROVAL_THRESHOLD`, `TOTAL_UNKNOWN`,
`CURRENCY_UNRESOLVED`, `SHORTFALL`, `BELOW_THRESHOLD`.
`ApprovalGuardDecision` carries `AUTH_REQUIRED` / `FORBIDDEN`.

## 6. Versioning & migration

- Schema changes are additive (new optional fields); persisted JSON from older
  versions deserializes under pydantic defaults.
- A persisted plan from an older schema is **re-validated on resume** by the
  runtime gate before execution (`ProcurementOrchestrator.resume`).
- Business-rule versions are recorded per decision (`budget_policy_version`).
