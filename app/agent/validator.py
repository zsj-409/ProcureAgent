"""Plan validation and execution feedback.

Phase 1 rewrite of ``PlanValidator``:

- Validates a plan as a **dependency-ordered sequence**, not just a set of
  actions:
  - every action must be in the allowed set;
  - COLLECT_SUPPLIER steps must come before any scoring/decision step;
  - NORMALIZE must appear before SCORE when quotes are collected;
  - SCORE must appear before CHECK_POLICY;
  - CHECK_POLICY must appear before FINALIZE;
  - FINALIZE, when present, must be the last step;
  - no duplicate COLLECT_SUPPLIER for the same supplier;
  - no dependency cycle (strict total order makes cycles structurally
    impossible).
- Supports **legitimate conditional terminal paths**:
  - a plan may end at CHECK_POLICY when the business outcome is a human
    review (shortfall / budget exception -> WAITING_APPROVAL) — FINALIZE is
    NOT forced on a review path;
  - a plan that ends at FINALIZE is a normal auto-completed path.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from ..procurement.schemas import (
    PlanAction,
    ProcurementPlan,
    ProcurementRequest,
    SupplierQuote,
)
from ..suppliers.base import SupplierProfile
from .model import ModelClient
from .prompts import REVIEW_SYSTEM


class PlanValidator:
    """Validate LLM plans before they can influence execution.

    The valid skeleton is:

        (COLLECT_SUPPLIER* → NORMALIZE* → SCORE → CHECK_POLICY [→ FINALIZE])?

    with these hard rules:
    - at least one COLLECT_SUPPLIER (an empty plan is invalid);
    - NORMALIZE before SCORE (only when any quote source exists);
    - SCORE before CHECK_POLICY;
    - FINALIZE, when present, is the last step and only after CHECK_POLICY;
    - no duplicate supplier collection;
    - no action outside the allowed set.
    """

    def __init__(self, max_steps: int = 20):
        self.max_steps = max_steps

    def validate(
        self,
        plan: ProcurementPlan,
        suppliers: list[SupplierProfile],
    ) -> str | None:
        """Return an error string, or ``None`` when the plan is valid."""

        allowed = {action.value for action in PlanAction}
        supplier_ids = {profile.supplier_id for profile in suppliers}
        if not plan.steps:
            return "Plan has no steps"
        if len(plan.steps) > self.max_steps:
            return f"Plan exceeds max_steps={self.max_steps}"

        actions = [step.action for step in plan.steps]

        # 1) All actions must be legal.
        for step in plan.steps:
            if step.action.value not in allowed:
                return f"Illegal action: {step.action.value}"

        # 2) No duplicate supplier collection, well-formed supplier ids.
        seen_suppliers: set[str] = set()
        for step in plan.steps:
            if step.action == PlanAction.COLLECT_SUPPLIER:
                if not step.supplier_id:
                    return "COLLECT_SUPPLIER requires supplier_id"
                if step.supplier_id not in supplier_ids:
                    return f"Unknown supplier: {step.supplier_id}"
                if step.supplier_id in seen_suppliers:
                    return f"Duplicate supplier: {step.supplier_id}"
                seen_suppliers.add(step.supplier_id)

        # 3) Dependency ordering (the core Phase-1 fix).
        has_collect = PlanAction.COLLECT_SUPPLIER in actions
        has_normalize = PlanAction.NORMALIZE in actions
        has_score = PlanAction.SCORE in actions
        has_policy = PlanAction.CHECK_POLICY in actions
        has_finalize = PlanAction.FINALIZE in actions

        if not has_collect:
            return "Plan must include at least one COLLECT_SUPPLIER"
        if not has_score:
            return "Plan must include SCORE"
        if not has_policy:
            return "Plan must include CHECK_POLICY"

        # Quotes must be sourced before scoring.
        score_pos = actions.index(PlanAction.SCORE)
        last_collect_pos = max(
            i for i, step in enumerate(plan.steps) if step.action == PlanAction.COLLECT_SUPPLIER
        )
        if score_pos < last_collect_pos:
            return "SCORE must come after all COLLECT_SUPPLIER steps"

        if has_normalize:
            normalize_pos = actions.index(PlanAction.NORMALIZE)
            if normalize_pos > score_pos:
                return "NORMALIZE must come before SCORE"
        else:
            return "Plan must include NORMALIZE before SCORE"

        # Policy check after scoring.
        policy_pos = actions.index(PlanAction.CHECK_POLICY)
        if policy_pos < score_pos:
            return "CHECK_POLICY must come after SCORE"

        # FINALIZE, when present, must be the last step and after CHECK_POLICY.
        if has_finalize:
            finalize_pos = actions.index(PlanAction.FINALIZE)
            if finalize_pos != len(plan.steps) - 1:
                return "FINALIZE must be the last step of the plan"
            if finalize_pos < policy_pos:
                return "FINALIZE must come after CHECK_POLICY"
        # A plan may legitimately end at CHECK_POLICY (human review path:
        # shortfall / budget exception -> WAITING_APPROVAL). FINALIZE is
        # therefore NOT required for validity; the orchestrator decides the
        # actual terminal state from the policy result.

        return None


class ValidationDecision(str, Enum):
    """Outcome of execution validation/review."""

    CONTINUE = "CONTINUE"
    REPLAN = "REPLAN"
    FAIL = "FAIL"


class ValidationResult(BaseModel):
    """The result of execution feedback."""

    decision: ValidationDecision
    reason: str
    needs_review: bool = False


class ReviewDecision(BaseModel):
    """Strict LLM review output."""

    decision: ValidationDecision
    reason: str


class ExecutionValidator:
    """Deterministic quote checks plus an optional single LLM review."""

    def validate(self, quotes: list[SupplierQuote], request: ProcurementRequest) -> ValidationResult:
        """Check whether collected quotes are sufficient for a decision.

        Stock shortfalls are not fatal: the scorer's split-award step can
        cover the quantity across suppliers, and any remainder is reported as
        an explicit shortfall. Only a completely empty quote set fails here.
        """

        if not quotes:
            return ValidationResult(
                decision=ValidationDecision.FAIL,
                reason="No usable supplier quotes",
            )

        sufficient = [quote for quote in quotes if quote.available_stock >= request.quantity]
        if sufficient:
            return ValidationResult(
                decision=ValidationDecision.CONTINUE,
                reason="Collected quotes are sufficient",
            )

        total_stock = sum(quote.available_stock for quote in quotes)
        return ValidationResult(
            decision=ValidationDecision.CONTINUE,
            reason=(
                f"No single supplier stocks {request.quantity} units (total available "
                f"{total_stock}); scoring will split the award across suppliers and "
                f"report any shortfall."
            ),
            needs_review=True,
        )

    async def decide(
        self,
        *,
        quotes: list[SupplierQuote],
        request: ProcurementRequest,
        context: str,
        model: ModelClient | None = None,
    ) -> ValidationResult:
        """Return deterministic feedback, optionally refined by one LLM review."""

        result = self.validate(quotes, request)
        if not result.needs_review or model is None:
            return result
        try:
            review = await model.generate_structured(
                system=REVIEW_SYSTEM,
                user=(
                    f"Request: {request.model_dump_json()}\n"
                    f"Quotes: {[q.model_dump_json() for q in quotes]}\n"
                    f"Context: {context}"
                ),
                output_model=ReviewDecision,
            )
            return ValidationResult(
                decision=review.decision,
                reason=review.reason,
                needs_review=False,
            )
        except Exception:
            return result
