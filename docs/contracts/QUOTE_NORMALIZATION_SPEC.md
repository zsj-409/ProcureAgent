# Quote Normalization Spec (Phase 1)

- **Version**: `quote-normalization-v1` (2026-11-01)
- **Reference currency**: USD
- **Module**: `app/procurement/normalizer.py`, `app/procurement/fx.py`

## 1. Pipeline

```
raw supplier payload
   │  parse (unit_price/stock/delivery/currency as text-tolerant decimals)
   ▼
QuoteNormalizer.normalize(raw, supplier_id, product_name, source_type, quantity)
   │  FX conversion to reference currency via FxService
   ▼
SupplierQuote
   ├─ raw_quote (immutable original)
   ├─ unit_price / currency (original)
   ├─ normalized_unit_price_ref / normalized_total_cost_ref
   ├─ fx_status / normalization_status
   └─ tax / shipping / validity (recorded, not blended)
```

## 2. FX rules (directed, versioned, sourced)

`FxRate(source_currency, target_currency, rate, as_of, source, valid_for_hours)`
means **1 unit of source = `rate` units of target**.

- Same currency → identity (no rate needed).
- Missing rate → `FX_RATE_MISSING` (never default to 1.0).
- Rate older than `valid_for_hours` → `FX_RATE_STALE`.
- Reverse direction → documented inversion (never a guess).
- Non-positive rate → `INVALID_DIRECTION`.
- Unresolvable quotes are `incomparable` and are **excluded from price
  ranking** by `ProcurementScorer`.

Offline fixture `FX_RATES_V1` (illustrative; replaced by a real provider feed
before production):
`USD→EUR 0.92`, `USD→CNY 7.25`, `EUR→USD 1.087`, `EUR→CNY 7.88`,
`CNY→USD 0.138`, `CNY→EUR 0.127`, `GBP→USD 1.27`.

## 3. Landed cost

```
normalized_total_cost_ref = unit_price_ref × quantity   (Decimal, 2 dp)
tax_amount / shipping_fee: recorded from raw payload only when explicitly
provided and in the SAME currency as the unit price; never blended into the
comparable unit price, never double-counted.
```

If the necessary fees are unknown, the system does **not** emit a
pseudo-precise final landed cost.

## 4. Decimal & rounding

- All money math uses `Decimal` (never float).
- Reference prices: 4 decimal places (unit), 2 decimal places (total).
- No silent rounding at boundary; explicit `quantize`.

## 5. Validity

`quote_valid_until` is parsed from the raw payload when present. An expired
quote must not be auto-trusted; the policy path treats unknown/invalid
validity conservatively (human review).
