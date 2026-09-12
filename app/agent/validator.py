"""Plan validation and execution feedback."""

from enum import Enum

from pydantic import BaseModel

from ..procurement.schemas import PlanAction, ProcurementPlan, ProcurementRequest, SupplierQuote
from ..suppliers.base import SupplierProfile
from .model import ModelClient
from .prompts import REVIEW_SYSTEM


class PlanValidator:
    """Validate LLM plans before they can influence execution."""

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
        if PlanAction.SCORE not in actions:
            return "Plan must include SCORE"
        if PlanAction.CHECK_POLICY not in actions:
            return "Plan must include CHECK_POLICY"

        seen_suppliers: set[str] = set()
        for step in plan.steps:
            if step.action.value not in allowed:
                return f"Illegal action: {step.action.value}"
            if step.action == PlanAction.COLLECT_SUPPLIER:
                if not step.supplier_id:
                    return "COLLECT_SUPPLIER requires supplier_id"
                if step.supplier_id not in supplier_ids:
                    return f"Unknown supplier: {step.supplier_id}"
                if step.supplier_id in seen_suppliers:
                    return f"Duplicate supplier: {step.supplier_id}"
                seen_suppliers.add(step.supplier_id)
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
        """Check whether collected quotes are sufficient for a decision."""

        if not quotes:
            return ValidationResult(
                decision=ValidationDecision.FAIL,
                reason="No usable supplier quotes",
            )

        sufficient = [quote for quote in quotes if quote.available_stock >= request.quantity]
        if not sufficient:
            return ValidationResult(
                decision=ValidationDecision.FAIL,
                reason="All supplier quotes have insufficient stock",
            )

        if len(sufficient) != len(quotes):
            return ValidationResult(
                decision=ValidationDecision.CONTINUE,
                reason="Some quotes have insufficient stock and will be scored lower",
                needs_review=True,
            )

        return ValidationResult(
            decision=ValidationDecision.CONTINUE,
            reason="Collected quotes are sufficient",
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
