"""Procurement planning.

The rule-based planner is the reliable default. The LLM planner is an optional
enhancement and always passes through ``PlanValidator`` with a rule-based
fallback.
"""

from abc import ABC, abstractmethod

from ..procurement.schemas import (
    PlanAction,
    PlanStep,
    ProcurementPlan,
    ProcurementRequest,
)
from ..suppliers.base import SupplierProfile
from .context import ContextBuilder
from .model import ModelClient
from .prompts import PLANNER_SYSTEM
from .validator import PlanValidator


class Planner(ABC):
    """Interface for producing a procurement execution plan."""

    @abstractmethod
    async def plan(
        self,
        request: ProcurementRequest,
        suppliers: list[SupplierProfile],
        context: str,
    ) -> ProcurementPlan:
        """Return a validated plan."""


class RuleBasedPlanner(Planner):
    """Deterministic planner that emits one collect step per supplier."""

    async def plan(
        self,
        request: ProcurementRequest,
        suppliers: list[SupplierProfile],
        context: str,
    ) -> ProcurementPlan:
        del request, context
        steps = [
            PlanStep(action=PlanAction.COLLECT_SUPPLIER, supplier_id=profile.supplier_id)
            for profile in suppliers
        ]
        steps.extend(
            [
                PlanStep(action=PlanAction.NORMALIZE),
                PlanStep(action=PlanAction.SCORE),
                PlanStep(action=PlanAction.CHECK_POLICY),
                PlanStep(action=PlanAction.FINALIZE),
            ]
        )
        return ProcurementPlan(steps=steps)


class LLMPlanner(Planner):
    """LLM-assisted planner with strict validation and rule-based fallback."""

    def __init__(
        self,
        *,
        model: ModelClient,
        context_builder: ContextBuilder,
        validator: PlanValidator,
        fallback: Planner,
    ):
        self.model = model
        self.context_builder = context_builder
        self.validator = validator
        self.fallback = fallback

    async def plan(
        self,
        request: ProcurementRequest,
        suppliers: list[SupplierProfile],
        context: str,
    ) -> ProcurementPlan:
        supplier_ids = [profile.supplier_id for profile in suppliers]
        user = (
            f"Request: {request.model_dump_json()}\n"
            f"Registered suppliers: {supplier_ids}\n"
            f"Current context: {context}"
        )
        try:
            plan = await self.model.generate_structured(
                system=PLANNER_SYSTEM,
                user=user,
                output_model=ProcurementPlan,
            )
        except Exception:
            return await self.fallback.plan(request, suppliers, context)

        error = self.validator.validate(plan, suppliers)
        if error:
            return await self.fallback.plan(request, suppliers, context)
        return plan
