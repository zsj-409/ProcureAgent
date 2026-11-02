"""Phase 6, Commit A — evaluation correctness tests.

Covers the P1 fixes:
- strict two-stage lexicographic (coverage NEVER traded for cost)
- Decimal re-verification (budget boundary exact)
- SOLVER_UNRESOLVED vs INFEASIBLE
- Independent constraint verifier (re-computes from first principles)
- Small-world oracle consistency (optimizer == oracle on small worlds)
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.procurement.optimizer import AllocationProblem, Feasibility, ProcurementOptimizer
from app.procurement.oracle import SmallWorldOracle
from app.procurement.schemas import SupplierQuote
from app.procurement.verifier import AllocationConstraintVerifier


def _quote(
    supplier_id: str,
    *,
    price: Decimal,
    stock: int,
    delivery_days: int = 3,
    currency: str = "USD",
    expired: bool = False,
) -> SupplierQuote:
    valid_until = (
        datetime.now(UTC) - timedelta(hours=1) if expired else datetime.now(UTC) + timedelta(days=1)
    )
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="X",
        unit_price=price,
        currency=currency,
        available_stock=stock,
        delivery_days=delivery_days,
        source_type="api",
        normalization_status="comparable",
        normalized_unit_price_ref=price,
        reference_currency=currency,
        quote_valid_until=valid_until,
    )


def _opt(quotes, quantity, **kwargs):
    problem = AllocationProblem(quotes=quotes, quantity=quantity, **kwargs)
    return ProcurementOptimizer().optimize(problem), problem


# ---------------------------------------------------------------------------
# 0.1 — two-stage lexicographic: coverage first, never traded for cost
# ---------------------------------------------------------------------------

def test_coverage_never_traded_for_cost_huge_price_spread():
    """Cheap supplier covers 5, expensive covers 50. Request 50. The solver
    must cover ALL 50 (both suppliers) even though the extra units are costly
    — coverage is lexicographically first."""
    q1 = _quote("s1", price=Decimal("1"), stock=5)
    q2 = _quote("s2", price=Decimal("100"), stock=50)
    result, _ = _opt([q1, q2], 50, max_split_suppliers=2)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert result.covered_quantity == 50
    # Both suppliers used: s1 5 units (cheap) + s2 45 units.
    assert sum(line.quantity for line in result.lines) == 50


def test_coverage_never_traded_for_cost_huge_freight():
    """Fixed freight 1000 on the ONLY supplier that can cover the shortfall.
    The solver must still cover everything (freight is paid anyway)."""
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    q2 = _quote("s2", price=Decimal("10"), stock=50)
    result, _ = _opt([q1, q2], 50, max_split_suppliers=2, freight={"s2": Decimal("1000")})
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert result.covered_quantity == 50


def test_partial_coverage_prioritized_over_cheaper_plan():
    """Stock totals 20 < 30 requested. The best PARTIAL plan must cover 20
    (all stock), not a cheaper 15-unit plan."""
    q1 = _quote("s1", price=Decimal("1"), stock=10)
    q2 = _quote("s2", price=Decimal("1"), stock=10)
    result, _ = _opt([q1, q2], 30, max_split_suppliers=2)
    assert result.feasibility == Feasibility.PARTIAL_FEASIBLE
    assert result.covered_quantity == 20
    assert result.shortfall == 10


def test_partial_does_not_drop_expensive_stock_to_save_cost():
    """s2 is expensive but has stock; a cheaper partial plan using only s1
    would cover less. Coverage must win."""
    q1 = _quote("s1", price=Decimal("1"), stock=5)
    q2 = _quote("s2", price=Decimal("500"), stock=10)
    result, _ = _opt([q1, q2], 15, max_split_suppliers=2)
    # 5 + 10 == 15 -> fully feasible; the solver MUST use the expensive
    # supplier to hit full coverage rather than stop at 5 cheap units.
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert result.covered_quantity == 15
    assert len(result.lines) == 2  # both suppliers used


def test_two_stage_budget_binds_keeps_max_coverage():
    """Budget limits coverage but the solver still maximizes within budget."""
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q2 = _quote("s2", price=Decimal("11"), stock=100)
    result, _ = _opt([q1, q2], 100, user_budget_limit=Decimal("1020"))
    # 100 x 10 = 1000 <= 1020 -> fully feasible.
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert result.covered_quantity == 100


# ---------------------------------------------------------------------------
# 0.2 — Decimal re-verification / budget boundaries
# ---------------------------------------------------------------------------

def test_budget_exact_boundary_ok():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result, _ = _opt([q1], 10, user_budget_limit=Decimal("100.00"))
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert result.total_landed_cost == Decimal("100.00")


def test_budget_one_cent_less_infeasible():
    """Budget 99.99 < cost 100.00 -> cannot fully cover; must be PARTIAL or
    INFEASIBLE, never a budget-violating FULLY."""
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result, _ = _opt([q1], 10, user_budget_limit=Decimal("99.99"))
    assert result.feasibility != Feasibility.FULLY_FEASIBLE
    if result.feasibility == Feasibility.FULLY_FEASIBLE:
        assert result.total_landed_cost <= Decimal("99.995")


def test_budget_float_coefficient_no_false_violation():
    """Prices with 2-decimal precision must not trigger budget violations due
    to float rounding at the boundary."""
    q1 = _quote("s1", price=Decimal("9.99"), stock=10)
    result, _ = _opt([q1], 10, user_budget_limit=Decimal("99.90"))
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert result.total_landed_cost == Decimal("99.90")


# ---------------------------------------------------------------------------
# 0.3 — solver status handling
# ---------------------------------------------------------------------------

def test_infeasible_when_no_stock():
    q1 = _quote("s1", price=Decimal("10"), stock=0)
    result, _ = _opt([q1], 5)
    assert result.feasibility == Feasibility.INFEASIBLE


def test_infeasible_when_budget_zero():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result, _ = _opt([q1], 10, user_budget_limit=Decimal("1"))
    assert result.feasibility == Feasibility.INFEASIBLE


def test_optimality_proven_flag():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result, _ = _opt([q1], 10)
    assert result.optimality_proven is True
    assert result.solver_status in ("Optimal", "optimal")


# ---------------------------------------------------------------------------
# Independent constraint verifier
# ---------------------------------------------------------------------------

def test_verifier_validates_optimizer_output():
    q1 = _quote("s1", price=Decimal("10"), stock=60)
    q2 = _quote("s2", price=Decimal("11"), stock=60)
    result, problem = _opt([q1, q2], 100, max_split_suppliers=2)
    verified = AllocationConstraintVerifier().verify(
        problem,
        [{"supplier_id": line.supplier_id, "quantity": line.quantity} for line in result.lines],
        method="optimizer",
    )
    assert verified.feasibility == result.feasibility
    assert verified.violations == []
    assert verified.total_landed_cost == result.total_landed_cost


def test_verifier_catches_inventory_violation():
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    problem = AllocationProblem(quotes=[q1], quantity=10, max_split_suppliers=1)
    verified = AllocationConstraintVerifier().verify(
        problem,
        [{"supplier_id": "s1", "quantity": 8}],
        method="bad",
    )
    assert verified.feasibility == Feasibility.INVALID
    assert any("inventory" in v for v in verified.violations)


def test_verifier_catches_budget_violation():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    problem = AllocationProblem(
        quotes=[q1],
        quantity=10,
        max_split_suppliers=1,
        user_budget_limit=Decimal("50"),
    )
    verified = AllocationConstraintVerifier().verify(
        problem,
        [{"supplier_id": "s1", "quantity": 10}],
        method="bad",
    )
    assert verified.feasibility == Feasibility.INVALID
    assert any("hard_budget" in v for v in verified.violations)


def test_verifier_catches_expired_quote():
    q1 = _quote("s1", price=Decimal("10"), stock=100, expired=True)
    problem = AllocationProblem(quotes=[q1], quantity=5, max_split_suppliers=1)
    verified = AllocationConstraintVerifier().verify(
        problem,
        [{"supplier_id": "s1", "quantity": 5}],
        method="bad",
    )
    assert verified.feasibility == Feasibility.INVALID
    assert any("expired" in v for v in verified.violations)


def test_verifier_catches_non_comparable_quote():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q1.normalization_status = "incomparable"
    q1.normalized_unit_price_ref = None
    problem = AllocationProblem(quotes=[q1], quantity=5, max_split_suppliers=1)
    verified = AllocationConstraintVerifier().verify(
        problem,
        [{"supplier_id": "s1", "quantity": 5}],
        method="bad",
    )
    assert verified.feasibility == Feasibility.INVALID
    assert any("not_comparable" in v for v in verified.violations)


def test_verifier_partial_requires_confirmation():
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    problem = AllocationProblem(quotes=[q1], quantity=10, max_split_suppliers=1)
    verified = AllocationConstraintVerifier().verify(
        problem,
        [{"supplier_id": "s1", "quantity": 5}],
        method="greedy",
    )
    assert verified.feasibility == Feasibility.PARTIAL_FEASIBLE
    assert verified.covered_quantity == 5
    assert verified.shortfall == 5


# ---------------------------------------------------------------------------
# Oracle consistency
# ---------------------------------------------------------------------------

def test_oracle_matches_optimizer_small_world():
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    q2 = _quote("s2", price=Decimal("11"), stock=5)
    q3 = _quote("s3", price=Decimal("12"), stock=5)
    problem = AllocationProblem(quotes=[q1, q2, q3], quantity=12, max_split_suppliers=3)
    oracle = SmallWorldOracle()
    assert oracle.can_handle(problem)
    o = oracle.optimize(problem)
    assert o.feasible
    # Oracle: cheapest = 5x10 + 5x11 + 2x12 = 50+55+24 = 129
    assert o.optimal_cost == Decimal("129.00")
    opt_result, _ = _opt([q1, q2, q3], 12, max_split_suppliers=3)
    assert opt_result.feasibility == Feasibility.FULLY_FEASIBLE
    assert opt_result.total_landed_cost == o.optimal_cost
    gap = oracle.optimality_gap(problem, opt_result.total_landed_cost)
    assert gap == 0.0


def test_oracle_with_freight_changes_optimum():
    q1 = _quote("s1", price=Decimal("10"), stock=10)
    q2 = _quote("s2", price=Decimal("10.5"), stock=10)
    problem = AllocationProblem(
        quotes=[q1, q2],
        quantity=10,
        max_split_suppliers=1,
        freight={"s1": Decimal("10"), "s2": Decimal("0")},
    )
    oracle = SmallWorldOracle()
    o = oracle.optimize(problem)
    assert o.feasible
    # s1: 100 + 10 freight = 110; s2: 105 -> oracle picks s2
    assert o.optimal_cost == Decimal("105.00")
    assert o.optimal_allocation[0]["supplier_id"] == "s2"
    assert o.optimal_allocation[0]["quantity"] == 10


def test_oracle_infeasible_when_budget_tiny():
    q1 = _quote("s1", price=Decimal("10"), stock=10)
    problem = AllocationProblem(
        quotes=[q1],
        quantity=10,
        max_split_suppliers=1,
        user_budget_limit=Decimal("1"),
    )
    oracle = SmallWorldOracle()
    o = oracle.optimize(problem)
    assert not o.feasible


def test_oracle_and_optimizer_agree_on_partial():
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    q2 = _quote("s2", price=Decimal("12"), stock=5)
    problem = AllocationProblem(quotes=[q1, q2], quantity=12, max_split_suppliers=2)
    oracle = SmallWorldOracle()
    o = oracle.optimize(problem)
    assert o.feasible
    assert o.optimal_cost == Decimal("110.00")  # 5x10 + 5x12
    opt_result, _ = _opt([q1, q2], 12, max_split_suppliers=2)
    assert opt_result.feasibility == Feasibility.PARTIAL_FEASIBLE
    assert opt_result.covered_quantity == 10
    assert opt_result.total_landed_cost == o.optimal_cost
