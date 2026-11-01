"""Deterministic approval policy.

Phase 1: separates the **user hard budget** (an absolute constraint that
internal approval cannot override) from the **internal approval threshold**
(a policy rule that only gates whether human sign-off is needed).

Business rule version: ``BUDGET_APPROVAL_RULES_V1`` (2026-11-01).

Semantics:
- ``user_budget_limit`` (+ ``budget_currency``): if set, the total cost must
  not exceed it. In ``HARD`` mode an over-budget solution is **infeasible**
  (FAIL / cannot be approved); in ``SOFT``/``APPROVAL_GATE`` mode it routes to
  human approval with an explicit reason.
- ``max_budget`` (legacy): interpreted per ``budget_mode``. When
  ``budget_mode=HARD`` it acts as the hard limit; otherwise it is an approval
  trigger (legacy behavior). ``user_budget_limit`` takes precedence when both
  are set.
- ``approval_threshold`` (internal policy): purchases at/above this amount
  require human approval regardless of user budget satisfaction. A solution
  that satisfies the user budget may still require approval.
- An unknown total (no comparable quote) must NOT be auto-declared
  budget-satisfied; it routes to approval / cannot be auto-approved.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from ..agent.state import TaskStatus
from ..errors import PolicyError
from .fx import FxStatus, build_fx_service

# Business rule version for budget/approval semantics.
BUDGET_APPROVAL_RULES_V1 = "budget-approval-rules-v1"


class ConstraintViolation(str):
    """Machine-readable reason codes for budget constraint outcomes."""

    HARD_BUDGET_EXCEEDED = "HARD_BUDGET_EXCEEDED"
    BUDGET_ABOVE_APPROVAL_THRESHOLD = "BUDGET_ABOVE_APPROVAL_THRESHOLD"
    TOTAL_UNKNOWN = "TOTAL_UNKNOWN"
    CURRENCY_UNRESOLVED = "CURRENCY_UNRESOLVED"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"


class PolicyDecision(BaseModel):
    """The result of applying the approval policy."""

    requires_approval: bool
    status: TaskStatus
    reason: str
    reason_code: str | None = None
    budget_mode: str | None = None
    hard_budget_ok: bool | None = None
    approval_threshold_ok: bool | None = None
    total_unknown: bool = False
    constraint_violation_reasons: list[str] = Field(default_factory=list)


class ProcurementPolicy:
    """Approve purchases below the threshold and route larger ones to review.

    Phase 1: enforces the user hard budget BEFORE the internal threshold.
    """

    def __init__(self, threshold: Decimal = Decimal(1000), fx_service=None):
        if threshold < 0:
            raise PolicyError("Approval threshold cannot be negative")
        self.threshold = threshold
        self._fx = fx_service or build_fx_service()

    def evaluate(
        self,
        estimated_total: Decimal | None,
        max_budget: Decimal | None = None,
        shortfall: int = 0,
        *,
        user_budget_limit: Decimal | None = None,
        budget_currency: str = "USD",
        budget_mode: str = "APPROVAL_GATE",
        estimated_total_currency: str = "USD",
        budget_policy_version: str = BUDGET_APPROVAL_RULES_V1,
    ) -> PolicyDecision:
        """Evaluate whether a purchase requires human approval.

        Order of checks (deterministic):
        1. Quantity shortfall -> always human review (partial order is a
           business call).
        2. User hard budget (``user_budget_limit`` or ``max_budget`` in HARD
           mode) -> if exceeded: HARD -> infeasible (FAIL); SOFT/APPROVAL_GATE
           -> WAITING_APPROVAL with reason.
        3. Internal approval threshold -> WAITING_APPROVAL when at/above.
        4. Otherwise auto-approved (COMPLETED).
        5. Unknown total -> cannot be auto-declared budget-satisfied;
           route to approval (TOTAL_UNKNOWN).
        """

        total = estimated_total

        # -- currency resolution --------------------------------------------
        # If the estimated total and the budget are in different currencies,
        # we must convert before comparing. If conversion fails (no/stale FX)
        # we cannot decide: route to approval with CURRENCY_UNRESOLVED.
        if total is not None and budget_currency.upper() != estimated_total_currency.upper():
            converted, status = self._fx.convert(total, estimated_total_currency, budget_currency)
            if status != FxStatus.OK or converted is None:
                return self._approval_decision(
                    reason=(
                        f"Cannot compare {estimated_total_currency} total against "
                        f"{budget_currency} budget without a valid FX rate "
                        f"(fx_status={status.value}); manual review required."
                    ),
                    reason_code=ConstraintViolation.CURRENCY_UNRESOLVED,
                    budget_mode=budget_mode,
                    total_unknown=True,
                    violations=[ConstraintViolation.CURRENCY_UNRESOLVED],
                )
            total = converted

        # -- shortfall --------------------------------------------------------
        if shortfall > 0:
            return PolicyDecision(
                requires_approval=True,
                status=TaskStatus.WAITING_APPROVAL,
                reason=(
                    f"Requested quantity cannot be fully covered (shortfall {shortfall}); "
                    f"human approval required for a partial order."
                ),
                reason_code="SHORTFALL",
                budget_mode=budget_mode,
                hard_budget_ok=None,
                constraint_violation_reasons=[ConstraintViolation.HARD_BUDGET_EXCEEDED],
            )

        # -- effective hard budget -------------------------------------------
        # user_budget_limit is the explicit hard budget; legacy max_budget is
        # interpreted per budget_mode (HARD -> hard limit, else approval gate).
        effective_limit: Decimal | None = user_budget_limit
        hard_mode = budget_mode == "HARD"
        if effective_limit is None and max_budget is not None:
            effective_limit = max_budget

        if total is None:
            return self._approval_decision(
                reason=(
                    "Total cost is unknown (no comparable quote); cannot declare "
                    "budget-satisfied without a verified total."
                ),
                reason_code=ConstraintViolation.TOTAL_UNKNOWN,
                budget_mode=budget_mode,
                total_unknown=True,
                violations=[ConstraintViolation.TOTAL_UNKNOWN],
            )

        # If a hard limit exists and the total exceeds it -> HARD mode:
        # infeasible; SOFT/APPROVAL_GATE mode: approval.
        if effective_limit is not None and total > effective_limit:
            if hard_mode:
                return PolicyDecision(
                    requires_approval=True,
                    status=TaskStatus.FAILED,
                    reason=(
                        f"Estimated total {total} {budget_currency} exceeds the user "
                        f"hard budget {effective_limit} {budget_currency}; infeasible "
                        f"under HARD budget mode. Re-quote or revise constraints required."
                    ),
                    reason_code=ConstraintViolation.HARD_BUDGET_EXCEEDED,
                    budget_mode=budget_mode,
                    hard_budget_ok=False,
                    constraint_violation_reasons=[ConstraintViolation.HARD_BUDGET_EXCEEDED],
                )
            return PolicyDecision(
                requires_approval=True,
                status=TaskStatus.WAITING_APPROVAL,
                reason=(
                    f"Estimated total {total} {budget_currency} exceeds the user "
                    f"budget {effective_limit} {budget_currency}; human approval "
                    f"required (approval-gate mode)."
                ),
                reason_code=ConstraintViolation.HARD_BUDGET_EXCEEDED,
                budget_mode=budget_mode,
                hard_budget_ok=False,
                constraint_violation_reasons=[ConstraintViolation.HARD_BUDGET_EXCEEDED],
            )

        hard_budget_ok = effective_limit is None or total <= effective_limit

        # -- internal approval threshold --------------------------------------
        # Note: this is a POLICY threshold, not the user budget. A solution
        # that satisfies the user budget may still require approval.
        if total >= self.threshold:
            return PolicyDecision(
                requires_approval=True,
                status=TaskStatus.WAITING_APPROVAL,
                reason=(
                    f"Estimated total {total} {budget_currency} meets or exceeds the "
                    f"internal approval threshold {self.threshold} {budget_currency}; "
                    f"human approval required."
                ),
                reason_code=ConstraintViolation.BUDGET_ABOVE_APPROVAL_THRESHOLD,
                budget_mode=budget_mode,
                hard_budget_ok=hard_budget_ok,
                constraint_violation_reasons=[ConstraintViolation.BUDGET_ABOVE_APPROVAL_THRESHOLD],
            )

        return PolicyDecision(
            requires_approval=False,
            status=TaskStatus.APPROVED,
            reason=(
                f"Estimated total {total} {budget_currency} is below the internal "
                f"threshold {self.threshold} {budget_currency} and within the user "
                f"budget; auto-approved."
            ),
            reason_code=ConstraintViolation.BELOW_THRESHOLD,
            budget_mode=budget_mode,
            hard_budget_ok=hard_budget_ok,
        )

    def _approval_decision(
        self, *, reason: str, reason_code: str, budget_mode: str,
        total_unknown: bool = False, violations: list[str] | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(
            requires_approval=True,
            status=TaskStatus.WAITING_APPROVAL,
            reason=reason,
            reason_code=reason_code,
            budget_mode=budget_mode,
            total_unknown=total_unknown,
            constraint_violation_reasons=violations or [],
        )
