# FX & Pricing Phase 1 Report

- **Date**: 2026-11-01
- **Modules**: `app/procurement/fx.py` (FxService), `app/procurement/normalizer.py`, `app/procurement/scorer.py`
- **Tests**: `tests/test_fx_and_pricing.py` (20 cases)

## 1. Rules implemented

- **Directed, sourced, versioned rates**: `FxRate(source, target, rate, as_of, source, valid_for_hours)`.
- Missing rate → `FX_RATE_MISSING`; stale → `FX_RATE_STALE`; non-positive → `INVALID_DIRECTION`.
- Reverse direction → documented inversion; never default to 1.0.
- `QuoteNormalizer` keeps the raw payload (`raw_quote`) and emits
  `normalized_unit_price_ref` / `normalized_total_cost_ref` only when comparable.
- `ProcurementScorer` ranks prices ONLY on comparable reference prices; an
  all-incomparable quote set raises `No comparable quotes`.
- `estimated_total` is computed in the reference currency with the correct
  currency label (previously hard-coded "USD").

## 2. Verified matrix

| Case | Result |
|---|---|
| USD↔USD | identity, comparable |
| USD→EUR (0.92) | 100 → 92.0000 |
| USD→CNY (7.25) | 10 → 72.5000 |
| Missing FX (USD→JPY) | FX_RATE_MISSING, incomparable |
| Stale FX (2020 rate) | FX_RATE_STALE, incomparable |
| Reverse direction (EUR→USD present, USD→EUR asked) | 100 USD → 91.9963 EUR (documented inversion) |
| Negative rate | INVALID_DIRECTION |
| Zero / negative price | rejected (ValidationError) |
| Abnormal high price | still comparable |
| Quantity scaling | total = ref × qty (2 dp) |
| Tax/shipping provided | recorded separately; never blended into unit price |
| Mixed-currency split award | split by score with correct quantities + shortfall |
| Raw vs normalized consistency | `raw_quote` preserved; `currency` original kept |

## 3. Scorer behavior on incomparable quotes

- Non-comparable quotes get a zero price score (cannot be ranked) but keep
  delivery/stock scores; they never win on price.
- If NO quote is comparable → `ValidationError: No comparable quotes ...`
  (cannot rank prices; task fails before scoring rather than silently
  comparing apples to oranges).

## 4. Known limitations / negative results

- The FX fixture (`FX_RATES_V1`) is **offline and illustrative**; real
  provider feed is Phase 3+ work. Using it in production without replacement
  would be unsafe.
- Tax/shipping are recorded but not yet folded into `normalized_total_cost_ref`
  (no verifiable fee rules); the contract forbids pseudo-precise landed cost.
- `quote_valid_until` is parsed but not yet enforced as a hard gate at scoring
  time (expired-quote rejection is Phase 2/3 work).
