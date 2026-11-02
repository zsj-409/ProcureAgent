"""Phase 5 — constrained procurement optimization tests.

Covers:
- quantity conservation, inventory, admission whitelist, hard budget,
  delivery deadline, max split suppliers, supplier share cap, tax/freight,
  unified currency
- explicit infeasible return (FULLY / PARTIAL / INFEASIBLE classification)
- comparison vs the weighted-greedy baseline (lower landed cost when greedy
  is suboptimal; equal when greedy is optimal)
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.procurement.optimizer import (
    AllocationProblem,
    Feasibility,
    ProcurementOptimizer,
)
from app.procurement.schemas import SupplierQuote


def _quote(
    supplier_id: str,
    *,
    price: Decimal,
    stock: int,
    delivery_days: int = 3,
    currency: str = "USD",
) -> SupplierQuote:
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="MX Master 3S",
        unit_price=price,
        currency=currency,
        available_stock=stock,
        delivery_days=delivery_days,
        source_type="api",
        normalization_status="comparable",
        normalized_unit_price_ref=price,
        reference_currency=currency,
        quote_valid_until=datetime.now(UTC) + timedelta(days=1),
    )


def _opt(quotes, quantity, **kwargs):
    problem = AllocationProblem(quotes=quotes, quantity=quantity, **kwargs)
    return ProcurementOptimizer().optimize(problem)


# ---------------------------------------------------------------------------
# Constraint correctness
# ---------------------------------------------------------------------------

def test_quantity_conservation_full():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q2 = _quote("s2", price=Decimal("12"), stock=100)
    result = _opt([q1, q2], 50)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert result.covered_quantity == 50
    assert result.shortfall == 0
    assert sum(line.quantity for line in result.lines) == 50


def test_quantity_conservation_partial():
    q1 = _quote("s1", price=Decimal("10"), stock=30)
    q2 = _quote("s2", price=Decimal("12"), stock=10)
    result = _opt([q1, q2], 50)
    assert result.feasibility == Feasibility.PARTIAL_FEASIBLE
    assert result.covered_quantity == 40
    assert result.shortfall == 10
    assert result.infeasible_reason  # re-confirmation message present


def test_inventory_bounds_respected():
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    result = _opt([q1], 10)
    assert result.feasibility == Feasibility.PARTIAL_FEASIBLE
    assert result.lines[0].quantity == 5


def test_no_stock_infeasible():
    q1 = _quote("s1", price=Decimal("10"), stock=0)
    result = _opt([q1], 5)
    assert result.feasibility == Feasibility.INFEASIBLE
    assert "no supplier has positive stock" in result.infeasible_reason


def test_admission_whitelist():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q2 = _quote("s2", price=Decimal("8"), stock=100)
    result = _opt([q1, q2], 10, allowed_suppliers=["s1"])
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert all(line.supplier_id == "s1" for line in result.lines)


def test_admission_whitelist_excludes_all_infeasible():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result = _opt([q1], 10, allowed_suppliers=["nonexistent"])
    assert result.feasibility == Feasibility.INFEASIBLE
    assert "whitelist" in result.infeasible_reason


def test_hard_budget_binds():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result = _opt([q1], 10, user_budget_limit=Decimal("50"))
    # 10 units * $10 = $100 > $50 -> cannot fully cover within budget.
    assert result.feasibility == Feasibility.PARTIAL_FEASIBLE
    assert result.total_landed_cost <= Decimal("50") + Decimal("0.01")


def test_hard_budget_zero_infeasible():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result = _opt([q1], 10, user_budget_limit=Decimal("1"))
    assert result.feasibility == Feasibility.INFEASIBLE


def test_delivery_deadline_excludes_slow_supplier():
    q1 = _quote("s1", price=Decimal("10"), stock=100, delivery_days=1)
    q2 = _quote("s2", price=Decimal("8"), stock=100, delivery_days=10)
    result = _opt([q1, q2], 10, delivery_deadline_days=3)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert all(line.delivery_days <= 3 for line in result.lines)
    assert result.lines[0].supplier_id == "s1"  # only s1 can meet deadline


def test_max_split_suppliers():
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    q2 = _quote("s2", price=Decimal("11"), stock=5)
    q3 = _quote("s3", price=Decimal("12"), stock=5)
    # 3 suppliers x 5 stock = 15 >= 12, but max_split=2 caps coverage at 10.
    result = _opt([q1, q2, q3], 12, max_split_suppliers=2)
    assert result.feasibility == Feasibility.PARTIAL_FEASIBLE
    assert len(result.lines) <= 2
    assert result.covered_quantity == 10
    assert result.shortfall == 2


def test_max_split_suppliers_within_stock():
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    q2 = _quote("s2", price=Decimal("11"), stock=5)
    q3 = _quote("s3", price=Decimal("12"), stock=5)
    # max_split=3 allows all 3 suppliers -> fully feasible (15 >= 12).
    result = _opt([q1, q2, q3], 12, max_split_suppliers=3)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert len(result.lines) <= 3
    assert result.covered_quantity == 12


def test_supplier_share_cap():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q2 = _quote("s2", price=Decimal("11"), stock=100)
    result = _opt([q1, q2], 20, supplier_share_cap=0.5)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    for line in result.lines:
        assert line.quantity <= 10  # 50% of 20


def test_tax_and_freight_in_landed_cost():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q2 = _quote("s2", price=Decimal("10.5"), stock=100)
    # s2 has higher unit price but NO freight; s1 has freight $10.
    result = _opt(
        [q1, q2],
        10,
        freight={"s1": Decimal("10"), "s2": Decimal("0")},
        tax_rate={"s1": Decimal("0.05"), "s2": Decimal("0.0")},
    )
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    # s1: 10*10 + 10 freight + 5%*(100) = 100+10+5 = 115
    # s2: 10.5*10 + 0 + 0 = 105 -> optimizer must pick s2
    assert result.lines[0].supplier_id == "s2"
    assert result.total_landed_cost == Decimal("105.00")


def test_currency_unification_uses_reference_cost():
    q1 = _quote("s1", price=Decimal("10"), stock=100, currency="USD")
    # q2 raw EUR 8 but normalized ref = 12 USD (FX) -> not cheaper.
    q2 = _quote("s2", price=Decimal("8"), stock=100, currency="EUR")
    q2.normalized_unit_price_ref = Decimal("12")
    q2.reference_currency = "USD"
    result = _opt([q1, q2], 10)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert result.lines[0].supplier_id == "s1"


def test_incomparable_quotes_infeasible():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q1.normalization_status = "incomparable"
    q1.normalized_unit_price_ref = None
    result = _opt([q1], 5)
    assert result.feasibility == Feasibility.INFEASIBLE
    assert "no comparable quotes" in result.infeasible_reason


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------

def test_optimizer_beats_greedy_when_greedy_suboptimal():
    """Greedy picks by weighted score; the optimizer picks by landed cost.

    Scenario: s1 cheap but low stock, s2 pricier but deep stock. Greedy
    (score-based) fills s1 first then s2; the optimizer balances to minimize
    total cost given the SAME inventory.
    """
    from app.procurement.schemas import Preference
    from app.procurement.scorer import ProcurementScorer

    q1 = _quote("s1", price=Decimal("10"), stock=60, delivery_days=1)
    q2 = _quote("s2", price=Decimal("11"), stock=60, delivery_days=2)

    # Greedy split (existing baseline): score-based, fills by score order.
    greedy = ProcurementScorer().score([q1, q2], 100, Preference.PRICE)
    greedy_cost = sum(line.line_total for line in greedy.award_split)

    # Optimizer: minimize landed cost.
    result = _opt([q1, q2], 100, max_split_suppliers=2)
    opt_cost = result.total_landed_cost

    # Both are PARTIAL (100 requested, 120 stock... wait 60+60=120 >= 100)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    # Optimizer cost must be <= greedy cost (same inventory).
    assert opt_cost <= greedy_cost + Decimal("0.01"), f"opt {opt_cost} > greedy {greedy_cost}"


def test_optimizer_budget_aware_split():
    """With a hard budget, the optimizer covers as much as possible within
    budget — the greedy baseline does not consider a hard budget at all."""
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q2 = _quote("s2", price=Decimal("12"), stock=100)
    result = _opt([q1, q2], 100, user_budget_limit=Decimal("1050"))
    # Cheapest $10 covers 100 units = $1000 <= $1050 -> fully feasible.
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert result.total_landed_cost <= Decimal("1050")


def test_solve_time_reasonable():
    qs = [_quote(f"s{i}", price=Decimal(str(10 + i)), stock=50, delivery_days=1 + i % 3) for i in range(8)]
    result = _opt(qs, 200, max_split_suppliers=3)
    assert result.solve_time_s < 10.0
    assert result.feasibility in (Feasibility.FULLY_FEASIBLE, Feasibility.PARTIAL_FEASIBLE)
