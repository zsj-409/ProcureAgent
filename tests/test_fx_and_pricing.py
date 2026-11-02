"""Phase 1 FX normalization and quote pricing tests.

Covers the §3.3 boundary matrix:
- same currency, USD/EUR, USD/CNY
- missing FX, stale FX, wrong-direction FX
- zero/negative amounts, abnormal high price
- quantity changes, shipping/tax changes
- multi-currency split award
- raw-quote preservation vs normalized quote consistency
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.procurement.fx import FxRate, FxService, FxStatus
from app.procurement.normalizer import QuoteNormalizer
from app.procurement.schemas import Preference, SupplierQuote
from app.procurement.scorer import ProcurementScorer

# ---------------------------------------------------------------------------
# FxService unit tests
# ---------------------------------------------------------------------------

def _rate(source: str, target: str, rate: str, *, as_of: datetime | None = None) -> FxRate:
    # as_of defaults to 1 hour ago (relative to the real clock) so the
    # 24h validity window is never crossed by test runtime drift.
    return FxRate(
        source_currency=source,
        target_currency=target,
        rate=Decimal(rate),
        as_of=as_of or (datetime.now(UTC) - timedelta(hours=1)),
        source="test-fixture",
        valid_for_hours=24,
    )


def test_fx_same_currency_identity():
    fx = FxService()
    value, status = fx.convert(Decimal("100"), "USD", "USD")
    assert status == FxStatus.OK
    assert value == Decimal("100")


def test_fx_usd_to_eur():
    fx = FxService([_rate("USD", "EUR", "0.92")])
    value, status = fx.convert(Decimal("100"), "USD", "EUR")
    assert status == FxStatus.OK
    assert value == Decimal("92.0000")


def test_fx_usd_to_cny():
    fx = FxService([_rate("USD", "CNY", "7.25")])
    value, status = fx.convert(Decimal("10"), "USD", "CNY")
    assert status == FxStatus.OK
    assert value == Decimal("72.5000")


def test_fx_missing_returns_fx_rate_missing():
    fx = FxService([])
    value, status = fx.convert(Decimal("100"), "USD", "JPY")
    assert status == FxStatus.FX_RATE_MISSING
    assert value is None


def test_fx_stale_returns_fx_rate_stale():
    old = datetime(2020, 1, 1, tzinfo=UTC)
    fx = FxService([_rate("USD", "EUR", "0.92", as_of=old)])
    value, status = fx.convert(Decimal("100"), "USD", "EUR")
    assert status == FxStatus.FX_RATE_STALE
    assert value is None


def test_fx_wrong_direction_inverts_documented():
    # We have EUR->USD; converting USD->EUR must invert it (documented), not guess.
    fx = FxService([_rate("EUR", "USD", "1.087")])
    value, status = fx.convert(Decimal("100"), "USD", "EUR")
    assert status == FxStatus.OK
    assert value == Decimal("91.9963")  # 100 / 1.087 ≈ 91.9963


def test_fx_negative_rate_invalid_direction():
    fx = FxService([_rate("USD", "EUR", "-0.92")])
    value, status = fx.convert(Decimal("100"), "USD", "EUR")
    assert status == FxStatus.INVALID_DIRECTION
    assert value is None


# ---------------------------------------------------------------------------
# QuoteNormalizer tests
# ---------------------------------------------------------------------------

def _normalize(raw: dict, *, quantity: int = 1) -> SupplierQuote:
    return QuoteNormalizer().normalize(
        raw,
        supplier_id="s1",
        product_name="MX Master 3S",
        source_type="api",
        quantity=quantity,
    )


def test_normalize_usd_quote_is_comparable():
    quote = _normalize({"unit_price": "$99.99", "currency": "USD", "stock": 50, "delivery_days": 3})
    assert quote.normalization_status == "comparable"
    assert quote.normalized_unit_price_ref == Decimal("99.99")
    assert quote.normalized_total_cost_ref == Decimal("99.99")
    assert quote.fx_status == "OK"
    assert quote.raw_quote["unit_price"] == "$99.99"  # raw preserved


def test_normalize_cny_quote_converts():
    quote = _normalize({"unit_price": "99", "currency": "CNY", "stock": 10, "delivery_days": 2}, quantity=2)
    assert quote.normalization_status == "comparable"
    # 99 CNY -> USD via CNY->USD 0.138 = 13.662
    assert quote.normalized_unit_price_ref == Decimal("13.6620")
    assert quote.normalized_total_cost_ref == Decimal("27.32")
    assert quote.currency == "CNY"  # original preserved


def test_normalize_missing_fx_marked_incomparable():
    quote = _normalize({"unit_price": "5000", "currency": "JPY", "stock": 10, "delivery_days": 2})
    assert quote.normalization_status == "incomparable"
    assert quote.fx_status == "FX_RATE_MISSING"
    assert quote.normalized_unit_price_ref is None
    assert quote.is_comparable() is False


def test_normalize_stale_fx_marked_incomparable():
    old = datetime(2020, 1, 1, tzinfo=UTC)
    fx = FxService([_rate("USD", "EUR", "0.92", as_of=old)])
    normalizer = QuoteNormalizer(fx_service=fx)
    quote = normalizer.normalize(
        {"unit_price": "100", "currency": "EUR", "stock": 10, "delivery_days": 2},
        supplier_id="s1",
        product_name="MX",
        source_type="api",
    )
    assert quote.normalization_status == "incomparable"
    assert quote.fx_status == "FX_RATE_STALE"


def test_normalize_zero_price_rejected():
    from app.errors import ValidationError

    with pytest.raises(ValidationError):
        _normalize({"unit_price": "0", "currency": "USD", "stock": 10, "delivery_days": 2})


def test_normalize_negative_price_rejected():
    from app.errors import ValidationError

    with pytest.raises(ValidationError):
        _normalize({"unit_price": "-5", "currency": "USD", "stock": 10, "delivery_days": 2})


def test_normalize_abnormal_high_price_still_comparable():
    quote = _normalize({"unit_price": "999999", "currency": "USD", "stock": 1, "delivery_days": 30})
    assert quote.normalization_status == "comparable"
    assert quote.normalized_unit_price_ref == Decimal("999999")


def test_normalize_quantity_scales_total_cost():
    q1 = _normalize({"unit_price": "10", "currency": "USD", "stock": 100, "delivery_days": 2}, quantity=5)
    assert q1.normalized_total_cost_ref == Decimal("50.00")


def test_normalize_tax_shipping_kept_when_provided():
    quote = _normalize(
        {
            "unit_price": "10",
            "currency": "USD",
            "stock": 100,
            "delivery_days": 2,
            "tax_amount": "0.80",
            "shipping_fee": "5.00",
            "tax_mode": "exclusive",
        },
        quantity=1,
    )
    assert quote.tax_amount == Decimal("0.80")
    assert quote.shipping_fee == Decimal("5.00")
    # Total reference cost is unit-price-based; tax/shipping are recorded
    # separately and never double-counted into the comparable unit price.
    assert quote.normalized_unit_price_ref == Decimal("10.00")


# ---------------------------------------------------------------------------
# Scorer with mixed currencies
# ---------------------------------------------------------------------------

def _quote(supplier_id: str, price: Decimal | str, currency: str = "USD", stock: int = 50) -> SupplierQuote:
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="MX Master 3S",
        unit_price=Decimal(str(price)),
        currency=currency,
        available_stock=stock,
        delivery_days=3,
        source_type="api",
    )


def test_scorer_uses_reference_price_for_mixed_currencies():
    """A CNY quote that is cheaper in reference terms must win, even when its
    raw number is larger than the USD quote's raw number."""
    normalizer = QuoteNormalizer()
    cny = normalizer.normalize(
        {"unit_price": "99", "currency": "CNY", "stock": 50, "delivery_days": 2},
        supplier_id="cnysup",
        product_name="MX Master 3S",
        source_type="api",
        quantity=1,
    )
    usd = normalizer.normalize(
        {"unit_price": "15", "currency": "USD", "stock": 50, "delivery_days": 3},
        supplier_id="usdsup",
        product_name="MX Master 3S",
        source_type="api",
        quantity=1,
    )
    recommendation = ProcurementScorer().score([usd, cny], quantity=1, preference=Preference.PRICE)
    # 99 CNY -> 13.66 USD < 15 USD -> CNY supplier wins on price.
    assert recommendation.recommended_supplier == "cnysup"
    assert recommendation.estimated_total == Decimal("13.66")


def test_scorer_raises_when_no_comparable_quote():
    incomparable = _quote("jpy", "5000", currency="JPY")
    incomparable.normalization_status = "incomparable"
    incomparable.normalized_unit_price_ref = None
    with pytest.raises(Exception) as exc:
        ProcurementScorer().score([incomparable], quantity=1)
    assert "No comparable quotes" in str(exc.value)


def test_scorer_split_award_mixed_currency():
    normalizer = QuoteNormalizer()
    usd = normalizer.normalize(
        {"unit_price": "10", "currency": "USD", "stock": 5, "delivery_days": 2},
        supplier_id="usdsup",
        product_name="MX Master 3S",
        source_type="api",
        quantity=15,
    )
    cny = normalizer.normalize(
        {"unit_price": "60", "currency": "CNY", "stock": 8, "delivery_days": 3},
        supplier_id="cnysup",
        product_name="MX Master 3S",
        source_type="api",
        quantity=15,
    )
    # Neither supplier can cover 15 units alone -> split award must trigger.
    recommendation = ProcurementScorer().score([usd, cny], quantity=15)
    assert recommendation.award_split, "expected a split award"
    # Split quantities: usd 5 (its full stock) + cny 8 -> 13 units, shortfall 2.
    lines = {(line.supplier_id, line.quantity) for line in recommendation.award_split}
    assert lines == {("usdsup", 5), ("cnysup", 8)}
    assert recommendation.shortfall == 2


def test_raw_quote_preserved_consistency():
    raw = {"unit_price": "$99.99", "currency": "USD", "stock": 50, "delivery_days": 3}
    quote = _normalize(raw)
    assert quote.raw_quote == raw
    assert quote.unit_price == Decimal("99.99")
    assert quote.currency == "USD"
