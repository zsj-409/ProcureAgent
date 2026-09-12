"""Deterministic approval policy."""

from decimal import Decimal

from pydantic import BaseModel

from ..agent.state import TaskStatus
from ..errors import PolicyError


class PolicyDecision(BaseModel):
    """The result of applying the approval policy."""

    requires_approval: bool
    status: TaskStatus
    reason: str


class ProcurementPolicy:
    """Approve purchases below the threshold and route larger ones to review."""

    def __init__(self, threshold: Decimal = Decimal("1000")):
        if threshold < 0:
            raise PolicyError("Approval threshold cannot be negative")
        self.threshold = threshold

    def evaluate(
        self,
        estimated_total: Decimal,
        max_budget: Decimal | None = None,
    ) -> PolicyDecision:
        """Evaluate whether a purchase requires human approval."""

        if max_budget is not None and estimated_total > max_budget:
            return PolicyDecision(
                requires_approval=True,
                status=TaskStatus.WAITING_APPROVAL,
                reason=(
                    f"Estimated total {estimated_total} exceeds requested budget "
                    f"{max_budget}; human approval required."
                ),
            )

        if estimated_total < self.threshold:
            return PolicyDecision(
                requires_approval=False,
                status=TaskStatus.APPROVED,
                reason=(
                    f"Estimated total {estimated_total} is below threshold "
                    f"{self.threshold}; auto-approved."
                ),
            )
        return PolicyDecision(
            requires_approval=True,
            status=TaskStatus.WAITING_APPROVAL,
            reason=(
                f"Estimated total {estimated_total} meets or exceeds threshold "
                f"{self.threshold}; human approval required."
            ),
        )
