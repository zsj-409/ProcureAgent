"""Policy unit tests."""

from decimal import Decimal

from app.agent.state import TaskStatus
from app.procurement.policy import ProcurementPolicy


def test_policy_below_threshold_does_not_require_approval():
    policy = ProcurementPolicy(threshold=Decimal(1000))

    decision = policy.evaluate(Decimal(999))

    assert decision.requires_approval is False
    assert decision.status == TaskStatus.APPROVED


def test_policy_at_threshold_requires_approval():
    policy = ProcurementPolicy(threshold=Decimal(1000))

    decision = policy.evaluate(Decimal(1000))

    assert decision.requires_approval is True
    assert decision.status == TaskStatus.WAITING_APPROVAL
