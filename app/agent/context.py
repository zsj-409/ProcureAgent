"""Structured working memory and compact context building."""

from pydantic import BaseModel

from ..procurement.schemas import ProcurementPlan, ProcurementRequest, SupplierQuote
from .state import TaskState


class WorkingContext(BaseModel):
    """Structured working memory. No vector store is used."""

    original_goal: str
    request: ProcurementRequest
    plan: ProcurementPlan
    completed_steps: list[str]
    failed_steps: list[str]
    quotes: list[SupplierQuote]
    preference: str
    approval_state: str


class ContextBuilder:
    """Build a compact, model-safe context from structured state."""

    def build(
        self,
        *,
        original_goal: str,
        request: ProcurementRequest,
        plan: ProcurementPlan,
        state: TaskState,
        quotes: list[SupplierQuote],
        failures: dict[str, str] | None = None,
    ) -> str:
        """Return a short text block for LLM calls.

        Full traces, logs, and HTML are intentionally excluded.
        """

        failures = failures or {}
        quote_lines = [
            (
                f"- {quote.supplier_id}: {quote.unit_price} {quote.currency}, "
                f"stock {quote.available_stock}, delivery {quote.delivery_days}d"
            )
            for quote in quotes
        ]
        steps = [f"{step.action.value}:{step.supplier_id or ''}" for step in plan.steps]
        return "\n".join(
            [
                f"Goal: {original_goal}",
                f"Request: {request.model_dump_json()}",
                f"Plan: {steps}",
                f"Completed: {state.completed_steps}",
                f"Failed: {failures}",
                "Quotes:",
                *quote_lines,
                f"Preference: {request.preference.value}",
                f"Approval state: {state.status.value}",
            ]
        )
