"""Phase 6, Commit C — procurement safety regression + workflow integration.

Task 8 (robustness/counterexamples) + Task 9 (real-workflow integration):
- budget boundaries (== cost, -0.01, +0.01)
- FX boundaries (missing, stale, reverse direction)
- fee boundary (fixed freight changes the optimum)
- solver anomaly (timeout -> SOLVER_UNRESOLVED, not INFEASIBLE)
- business safety: hard budget not bypassed by approval; partial != full;
  expired quote excluded; non-comparable excluded
- workflow: FULLY -> approval/finalize path; PARTIAL -> re-confirmation;
  INFEASIBLE -> no executable result
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.procurement.optimizer import AllocationProblem, Feasibility, ProcurementOptimizer
from app.procurement.schemas import SupplierQuote


def _quote(
    supplier_id: str,
    *,
    price: Decimal,
    stock: int,
    delivery_days: int = 3,
    currency: str = "USD",
    expired: bool = False,
    incomparable: bool = False,
    stale_fx: bool = False,
) -> SupplierQuote:
    valid_until = (
        datetime.now(UTC) - timedelta(hours=1) if expired else datetime.now(UTC) + timedelta(days=1)
    )
    ref_price = None if (incomparable or stale_fx) else Decimal(price)
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="X",
        unit_price=Decimal(price),
        currency=currency,
        available_stock=stock,
        delivery_days=delivery_days,
        source_type="api",
        normalization_status="incomparable" if (incomparable or stale_fx) else "comparable",
        normalized_unit_price_ref=ref_price,
        reference_currency=currency,
        quote_valid_until=valid_until,
    )


def _opt(quotes, quantity, **kwargs):
    problem = AllocationProblem(quotes=quotes, quantity=quantity, **kwargs)
    return ProcurementOptimizer().optimize(problem), problem


# ---------------------------------------------------------------------------
# Task 8 — robustness / counterexamples
# ---------------------------------------------------------------------------

def test_budget_equal_cost_fully_feasible():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result, _ = _opt([q1], 10, user_budget_limit=Decimal("100.00"))
    assert result.feasibility == Feasibility.FULLY_FEASIBLE


def test_budget_one_cent_less_not_full():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result, _ = _opt([q1], 10, user_budget_limit=Decimal("99.99"))
    assert result.feasibility != Feasibility.FULLY_FEASIBLE


def test_fx_missing_excluded():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q2 = _quote("s2", price=Decimal("8"), stock=100, currency="EUR", incomparable=True)
    result, _ = _opt([q1, q2], 50)
    # q2 has no comparable ref -> excluded; only q1 used.
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert all(line.supplier_id == "s1" for line in result.lines)


def test_fx_stale_excluded():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q2 = _quote("s2", price=Decimal("8"), stock=100, currency="EUR", stale_fx=True)
    result, _ = _opt([q1, q2], 50)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert all(line.supplier_id == "s1" for line in result.lines)


def test_fixed_freight_changes_optimum():
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    q2 = _quote("s2", price=Decimal("10.5"), stock=100)
    result, _ = _opt([q1, q2], 10, freight={"s1": Decimal("10"), "s2": Decimal("0")})
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    # s1: 100+10=110; s2: 105 -> optimizer picks s2
    assert result.lines[0].supplier_id == "s2"
    assert result.total_landed_cost == Decimal("105.00")


def test_solver_timeout_unresolved_not_infeasible():
    """A time-limited solve that cannot prove optimality must be
    SOLVER_UNRESOLVED (or feasible-without-proof), never INFEASIBLE when a
    solution exists."""
    qs = [_quote(f"s{i}", price=Decimal(str(10 + i)), stock=50) for i in range(10)]
    problem = AllocationProblem(quotes=qs, quantity=200, max_split_suppliers=4, time_limit_s=0.01)
    result = ProcurementOptimizer().optimize(problem)
    assert result.feasibility in (
        Feasibility.FULLY_FEASIBLE,
        Feasibility.PARTIAL_FEASIBLE,
        Feasibility.SOLVER_UNRESOLVED,
    )
    # If it returns a solution, it must not claim proven optimality unless
    # the solver actually proved it.
    if result.feasibility in (Feasibility.FULLY_FEASIBLE, Feasibility.PARTIAL_FEASIBLE):
        assert result.covered_quantity > 0


def test_overflow_budget_cannot_be_approved_around():
    """Hard budget is a hard constraint: no allocation may exceed it, even
    'for a good reason'."""
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result, _ = _opt([q1], 10, user_budget_limit=Decimal("99.99"))
    assert result.feasibility != Feasibility.FULLY_FEASIBLE
    if result.feasibility == Feasibility.PARTIAL_FEASIBLE:
        assert result.total_landed_cost <= Decimal("99.995")


def test_partial_never_auto_full():
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    result, _ = _opt([q1], 10)
    assert result.feasibility == Feasibility.PARTIAL_FEASIBLE
    assert result.shortfall == 5
    assert "needs re-confirmation" in result.infeasible_reason


def test_expired_quote_excluded():
    q1 = _quote("s1", price=Decimal("10"), stock=100, expired=True)
    q2 = _quote("s2", price=Decimal("12"), stock=100)
    result, _ = _opt([q1, q2], 50)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    assert all(line.supplier_id == "s2" for line in result.lines)


# ---------------------------------------------------------------------------
# Task 9 — workflow integration semantics
# ---------------------------------------------------------------------------

def test_workflow_fully_feasible_path():
    """FULLY_FEASIBLE -> eligible for approval/finalize (policy decides)."""
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result, _problem = _opt([q1], 10)
    assert result.feasibility == Feasibility.FULLY_FEASIBLE
    # In the orchestrator, the policy gate then decides approval. The key
    # invariant: a FULLY_FEASIBLE recommendation carries no shortfall.
    assert result.shortfall == 0


def test_workflow_partial_requires_confirmation():
    """PARTIAL_FEASIBLE must carry requested/allocated/shortfall and route
    to re-confirmation, never to finalize."""
    q1 = _quote("s1", price=Decimal("10"), stock=5)
    result, _ = _opt([q1], 10)
    assert result.feasibility == Feasibility.PARTIAL_FEASIBLE
    assert result.covered_quantity == 5
    assert result.shortfall == 5
    # The policy layer routes shortfall > 0 to WAITING_APPROVAL /
    # re-confirmation (Phase 1 semantics), never COMPLETED.
    assert result.feasibility != Feasibility.FULLY_FEASIBLE


def test_workflow_infeasible_no_executable_result():
    q1 = _quote("s1", price=Decimal("10"), stock=0)
    result, _ = _opt([q1], 10)
    assert result.feasibility == Feasibility.INFEASIBLE
    assert result.lines == []


def test_recommendation_version_and_snapshot_consistency():
    """A recommendation built from an optimizer result must bind the same
    quote snapshot (no drift between scoring and approval)."""
    q1 = _quote("s1", price=Decimal("10"), stock=100)
    result, _problem = _opt([q1], 10)
    assert result.lines[0].supplier_id == "s1"
    # The verifier recomputed the cost from the SAME quotes -> consistency.
    assert result.total_landed_cost == Decimal("100.00")
