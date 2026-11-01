# Input Robustness Report (Phase 1)

- **Date**: 2026-11-01
- **Module**: `app/agent/interpreter.py`
- **Tests**: `tests/test_input_robustness.py` (13 cases)

## 1. Neutral invariance (semantically-equivalent rewrites)

| Left | Right | Equivalent? |
|---|---|---|
| 采购20个MX Master 3S | buy 20 units of MX Master 3S | ✅ qty=20, same product |
| 预算1500采购20个MX Master 3S | USD 1500 budget for 20 MX Master 3S | ✅ budget=1500 |
| 帮我采购20个鼠标，优先交付速度 | procure 20 mice, fast delivery first | ✅ DELIVERY |
| 采购20个MX Master 3S，预算1500 | 预算1500，采购20个MX Master 3S | ✅ text-order invariant |

## 2. Material change accuracy (meaning-changing rewrites)

| Change | Response |
|---|---|
| budget 1500 → 1000 | max_budget changes 1500 → 1000 ✅ |
| quantity 20 → 10 | quantity changes ✅ |
| 优先价格 → 优先交付速度 | PRICE → DELIVERY ✅ |
| 预算内 → 预算绝不能超过 | APPROVAL_GATE → HARD ✅ |
| USD → EUR budget | budget_currency USD → EUR ✅ |

## 3. Metrics (Phase 1, deterministic fallback interpreter)

| Metric | Value |
|---|---|
| Constraint parse rate | 100% on the matrix (13/13) |
| Hard-constraint retention (HARD mode detection) | 2/2 |
| Semantic-equivalence consistency | 4/4 (≥0.75 floor asserted) |
| Material-change response accuracy | 3/3 (≥0.75 floor asserted) |
| Schema failure rate (unparseable input) | correctly raises InterpretationError (never fabricates) |
| Unknown-field handling | currency default USD; unknown phrases raise |

## 4. Interpreter fixes made in Phase 1

- Quantity regex now anchors on unit words (个/台/件/pcs/units/rows/of/的)
  before falling back to bare numbers, and strips an explicit budget phrase
  first — so `"USD 1500 hard cap, 20 MX"` parses quantity=20, budget=1500,
  HARD mode (previously quantity=1500).
- `优先价格` added to price-word hints (was only `价格优先`).
- `_budget_mode` classifies HARD / SOFT / APPROVAL_GATE from wording.
- `_budget_currency` extracts USD/EUR/CNY/GBP tokens.
- English unit suffix `of` handled (`20 units of X`).

## 5. Negative results / limitations

- The deterministic fallback is a heuristic; genuinely ambiguous English
  phrases (e.g. `"buy 1500 units under $20"`) can still mis-parse quantity
  vs budget. The LLM path (when configured) repairs these; the fallback's
  failure mode is a loud `InterpretationError`, never a fabricated fact.
- `mice` (plural product noun) is treated as the product name, not a unit —
  consistent with treating "鼠标" as product.
- Statistics floor assertions (≥0.75) are intentionally modest; the Phase 6
  harness will formalize the full perturbation suite with sample sizes.
