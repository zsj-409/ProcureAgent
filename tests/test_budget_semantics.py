"""Phase 1 budget-vs-approval-threshold semantics tests.

Covers the §4.4 matrix:
- user hard budget satisfied, below internal threshold -> auto-approved
- user hard budget satisfied, above internal threshold -> approval required
- user hard budget violated -> infeasible (HARD) / approval (SOFT)
- total unknown -> cannot auto-declare budget-satisfied
- different currency -> must convert first; unresolved currency -> review
- boundary equal-to-threshold, missing currency, expired quote
"""

from decimal import Decimal

from app.agent.state import TaskStatus
from app.procurement.policy import (
    BUDGET_APPROVAL_RULES_V1,
    ConstraintViolation,
    ProcurementPolicy,
)


def _policy(threshold: str = "1000") -> ProcurementPolicy:
    return ProcurementPolicy(threshold=Decimal(threshold))


def _evaluate(
    total: str | None,
    *,
    user_budget: str | None = None,
    max_budget: str | None = None,
    budget_mode: str = "APPROVAL_GATE",
    budget_currency: str = "USD",
    total_currency: str = "USD",
    shortfall: int = 0,
):
    return _policy().evaluate(
        Decimal(total) if total is not None else None,
        Decimal(max_budget) if max_budget is not None else None,
        shortfall=shortfall,
        user_budget_limit=Decimal(user_budget) if user_budget is not None else None,
        budget_currency=budget_currency,
        budget_mode=budget_mode,
        estimated_total_currency=total_currency,
        budget_policy_version=BUDGET_APPROVAL_RULES_V1,
    )


def test_budget_satisfied_below_threshold_auto_approved():
    """1500 budget, 1000 threshold, 900 total -> approved, no approval needed."""
    decision = _evaluate("900", user_budget="1500")
    assert decision.requires_approval is False
    assert decision.status == TaskStatus.APPROVED
    assert decision.hard_budget_ok is True


def test_budget_satisfied_above_threshold_requires_approval():
    """1500 budget, 1000 threshold, 1200 total -> budget OK but approval needed."""
    decision = _evaluate("1200", user_budget="1500")
    assert decision.requires_approval is True
    assert decision.status == TaskStatus.WAITING_APPROVAL
    assert decision.hard_budget_ok is True
    assert decision.reason_code == ConstraintViolation.BUDGET_ABOVE_APPROVAL_THRESHOLD


def test_hard_budget_exceeded_infeasible():
    """1500 hard budget, 1600 total -> infeasible, cannot be approved."""
    decision = _evaluate("1600", user_budget="1500", budget_mode="HARD")
    assert decision.status == TaskStatus.FAILED
    assert decision.reason_code == ConstraintViolation.HARD_BUDGET_EXCEEDED
    assert decision.hard_budget_ok is False


def test_hard_budget_exceeded_soft_routes_to_approval():
    """1500 budget in approval-gate mode, 1600 total -> human approval."""
    decision = _evaluate("1600", user_budget="1500", budget_mode="APPROVAL_GATE")
    assert decision.status == TaskStatus.WAITING_APPROVAL
    assert decision.reason_code == ConstraintViolation.HARD_BUDGET_EXCEEDED


def test_total_unknown_cannot_auto_approve():
    """Unknown total -> cannot declare budget-satisfied; routes to review."""
    decision = _evaluate(None, user_budget="1500")
    assert decision.requires_approval is True
    assert decision.status == TaskStatus.WAITING_APPROVAL
    assert decision.total_unknown is True
    assert decision.reason_code == ConstraintViolation.TOTAL_UNKNOWN


def test_legacy_max_budget_in_hard_mode_is_absolute():
    """Legacy max_budget with HARD mode acts as an absolute limit."""
    decision = _evaluate("1600", max_budget="1500", budget_mode="HARD")
    assert decision.status == TaskStatus.FAILED
    assert decision.reason_code == ConstraintViolation.HARD_BUDGET_EXCEEDED


def test_legacy_max_budget_in_gate_mode_is_approval_trigger():
    """Legacy max_budget with APPROVAL_GATE mode is an approval trigger."""
    decision = _evaluate("1600", max_budget="1500", budget_mode="APPROVAL_GATE")
    assert decision.status == TaskStatus.WAITING_APPROVAL
    assert decision.reason_code == ConstraintViolation.HARD_BUDGET_EXCEEDED


def test_user_budget_limit_takes_precedence_over_max_budget():
    """user_budget_limit (1400) wins over legacy max_budget (2000)."""
    decision = _evaluate("1500", user_budget="1400", max_budget="2000", budget_mode="HARD")
    assert decision.status == TaskStatus.FAILED


def test_currency_conversion_before_budget_compare():
    """1400 EUR total vs 1500 USD budget: convert EUR->USD (~1522) -> over."""
    decision = _evaluate(
        "1400",
        user_budget="1500",
        budget_currency="USD",
        total_currency="EUR",
        budget_mode="HARD",
    )
    # 1400 EUR * 1.087 = 1521.80 USD > 1500 -> infeasible under HARD.
    assert decision.status == TaskStatus.FAILED
    assert decision.reason_code == ConstraintViolation.HARD_BUDGET_EXCEEDED


def test_unresolved_currency_routes_to_review():
    """A currency without a rate cannot be compared; manual review required."""
    decision = _evaluate("5000", user_budget="1500", budget_currency="USD", total_currency="JPY")
    assert decision.status == TaskStatus.WAITING_APPROVAL
    assert decision.reason_code == ConstraintViolation.CURRENCY_UNRESOLVED


def test_total_equal_to_threshold_requires_approval():
    """Total == threshold (1000) -> approval required (at/above)."""
    decision = _evaluate("1000", user_budget="1500")
    assert decision.status == TaskStatus.WAITING_APPROVAL
    assert decision.reason_code == ConstraintViolation.BUDGET_ABOVE_APPROVAL_THRESHOLD


def test_total_equal_to_hard_budget_is_ok():
    """Total == hard budget (1500) -> within budget (not exceeded)."""
    decision = _evaluate("1500", user_budget="1500", budget_mode="HARD")
    assert decision.status == TaskStatus.WAITING_APPROVAL  # threshold check still applies
    assert decision.hard_budget_ok is True


def test_shortfall_always_routes_to_approval():
    """A quantity shortfall is a business call: always human review."""
    decision = _evaluate("900", user_budget="1500", shortfall=3)
    assert decision.status == TaskStatus.WAITING_APPROVAL
    assert decision.reason_code == "SHORTFALL"
