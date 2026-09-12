"""Scorer unit tests."""

from decimal import Decimal

from app.procurement.schemas import SupplierQuote
from app.procurement.scorer import ProcurementScorer


def _quote(supplier_id: str, price: str, delivery: int) -> SupplierQuote:
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="MX Master 3S",
        unit_price=Decimal(price),
        currency="USD",
        available_stock=20,
        delivery_days=delivery,
        source_type="api",
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
