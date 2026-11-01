"""Scorer unit tests."""

from decimal import Decimal

from app.procurement.schemas import SupplierQuote
from app.procurement.scorer import ProcurementScorer


def _quote(supplier_id: str, price: str, delivery: int) -> SupplierQuote:
    price_dec = Decimal(price)
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="MX Master 3S",
        unit_price=price_dec,
        currency="USD",
        available_stock=20,
        delivery_days=delivery,
        source_type="api",
        # Phase 1: same-currency quotes are directly comparable — the
        # normalizer would set these fields; tests construct them explicitly.
        raw_quote={"unit_price": price},
        reference_currency="USD",
        normalized_unit_price_ref=price_dec,
        normalized_total_cost_ref=(price_dec * Decimal(20)),
        fx_status="OK",
        normalization_status="comparable",
    )


def test_scorer_selects_cheapest_balanced_supplier():
    quotes = [
        _quote("supplier_a", "10", 5),
        _quote("supplier_b", "12", 2),
        _quote("supplier_c", "8", 4),
    ]

    recommendation = ProcurementScorer().score(quotes, quantity=20)

    assert recommendation.recommended_supplier == "supplier_c"
    assert recommendation.score == Decimal("85.0000")
    assert recommendation.estimated_total == Decimal("160.00")
    assert recommendation.partial_result is False
