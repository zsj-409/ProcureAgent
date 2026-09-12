"""Pydantic models for the procurement domain."""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Preference(str, Enum):
    """The finite user preference that can affect deterministic scoring weights."""

    BALANCED = "BALANCED"
    PRICE = "PRICE"
    DELIVERY = "DELIVERY"
    STOCK = "STOCK"


class ProcurementRequest(BaseModel):
    """A user request for a named product and quantity."""

    product_name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0)
    max_budget: Decimal | None = None
    preference: Preference = Preference.BALANCED


class SupplierQuote(BaseModel):
    """A normalized quote collected from one supplier."""

    supplier_id: str
    product_name: str
    unit_price: Decimal = Field(gt=0)
    currency: str = "USD"
    available_stock: int = Field(ge=0)
    delivery_days: int = Field(ge=0)
    source_type: Literal["api", "portal"]
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlanAction(str, Enum):
    """The finite actions allowed in a procurement plan."""

    COLLECT_SUPPLIER = "COLLECT_SUPPLIER"
    NORMALIZE = "NORMALIZE"
    SCORE = "SCORE"
    CHECK_POLICY = "CHECK_POLICY"
    FINALIZE = "FINALIZE"


class PlanStep(BaseModel):
    """One validated step in a procurement plan."""

    action: PlanAction
    supplier_id: str | None = None


class ProcurementPlan(BaseModel):
    """A finite, validated list of procurement steps."""

    steps: list[PlanStep] = Field(default_factory=list)


class ProcurementRecommendation(BaseModel):
    """The deterministic purchase recommendation."""

    recommended_supplier: str
    quotes: list[SupplierQuote]
    score: Decimal
    reason: str
    estimated_total: Decimal
    partial_result: bool = False
    approval_required: bool = False
    summary: str = ""


class AgentExecutionResult(BaseModel):
    """The complete result returned by the natural-language agent endpoint."""

    task_id: str
    status: str
    request: ProcurementRequest
    plan: ProcurementPlan
    quotes: list[SupplierQuote]
    recommendation: ProcurementRecommendation | None = None
    approval_required: bool
    partial_result: bool
    summary: str


class ApprovalRecordView(BaseModel):
    """Public view of an approval decision."""

    task_id: str
    approver: str
    decision: Literal["approved"]
    created_at: datetime
