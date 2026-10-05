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

    def __init__(self, threshold: Decimal = Decimal(1000)):
        if threshold < 0:
            raise PolicyError("Approval threshold cannot be negative")
        self.threshold = threshold

    def evaluate(
        self,
        estimated_total: Decimal,
        max_budget: Decimal | None = None,
        shortfall: int = 0,
    ) -> PolicyDecision:
        """Evaluate whether a purchase requires human approval.

        A quantity shortfall (split award could not cover the request) always
        goes to human review: accepting a partial order is a business call,
        never an automatic one.
        """

        if shortfall > 0:
            return PolicyDecision(
                requires_approval=True,
                status=TaskStatus.WAITING_APPROVAL,
                reason=(
                    f"Requested quantity cannot be fully covered (shortfall {shortfall}); "
                    f"human approval required for a partial order."
                ),
            )

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
