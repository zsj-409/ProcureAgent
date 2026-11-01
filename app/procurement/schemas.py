"""Pydantic models for the procurement domain."""

from datetime import UTC, datetime
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


class BudgetMode(str, Enum):
    """Semantics of the user's budget constraint.

    - HARD: the user budget is an absolute constraint; a solution exceeding it
      is infeasible and must NOT be auto-approved or approved-as-is.
    - SOFT / APPROVAL_GATE: the budget is an approval trigger; a solution may
      exceed it only with explicit human approval (legacy behavior).
    """

    HARD = "HARD"
    SOFT = "SOFT"
    APPROVAL_GATE = "APPROVAL_GATE"


class ProcurementRequest(BaseModel):
    """A user request for a named product and quantity."""

    product_name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0)
    # Legacy field: kept for compatibility. Interpretation depends on
    # ``budget_mode`` (see BudgetAndApprovalSemantics contract).
    max_budget: Decimal | None = None
    # Phase 1: explicit hard budget (user constraint that cannot be exceeded
    # even with internal approval) vs internal approval threshold.
    user_budget_limit: Decimal | None = None
    budget_currency: str = "USD"
    budget_mode: BudgetMode = BudgetMode.APPROVAL_GATE
    budget_policy_version: str = "v1"
    preference: Preference = Preference.BALANCED


class SupplierQuote(BaseModel):
    """A normalized quote collected from one supplier.

    Phase 1: the quote keeps BOTH the original raw payload (``raw_quote``) and
    the normalized reference cost (``normalized_unit_price_ref`` /
    ``normalized_total_cost_ref``). Original data is never overwritten; the
    normalization status (``normalization_status``) tells callers whether the
    quote is comparable in ``reference_currency``.
    """

    supplier_id: str
    product_name: str
    unit_price: Decimal = Field(gt=0)
    currency: str = "USD"
    available_stock: int = Field(ge=0)
    delivery_days: int = Field(ge=0)
    source_type: Literal["api", "portal"]
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # --- Phase 1 normalization fields -------------------------------------
    # Original raw payload (immutable record of what the supplier returned).
    raw_quote: dict = Field(default_factory=dict)
    # Normalized cost in a reference currency (only set when comparable).
    reference_currency: str = "USD"
    normalized_unit_price_ref: Decimal | None = None
    normalized_total_cost_ref: Decimal | None = None
    fx_rate_id: str | None = None
    fx_rate_as_of: datetime | None = None
    fx_status: str | None = None  # OK / FX_RATE_MISSING / FX_RATE_STALE / INVALID_DIRECTION
    normalization_status: str = "raw"  # raw | comparable | incomparable | incomplete

    # Tax / shipping / landed-cost fields (Phase 1 contract).
    tax_amount: Decimal | None = None
    tax_mode: str | None = None  # inclusive | exclusive | unknown
    shipping_fee: Decimal | None = None
    quote_valid_until: datetime | None = None
    source_ref: str | None = None
    source_snapshot_hash: str | None = None

    def is_comparable(self) -> bool:
        """A quote is comparable when it has a normalized reference cost."""
        return self.normalization_status == "comparable" and self.normalized_unit_price_ref is not None


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


class AwardLine(BaseModel):
    """One supplier line in a split-award recommendation."""

    supplier_id: str
    quantity: int = Field(gt=0)
    unit_price: Decimal
    line_total: Decimal
    delivery_days: int = Field(ge=0)


class ProcurementRecommendation(BaseModel):
    """The deterministic purchase recommendation.

    ``award_split`` is empty for single-source awards; when no single supplier
    can cover the requested quantity from stock, the order is split across
    suppliers (at most ``max_split_lines``) by effective score.
    """

    recommended_supplier: str
    quotes: list[SupplierQuote]
    score: Decimal
    reason: str
    estimated_total: Decimal
    partial_result: bool = False
    approval_required: bool = False
    summary: str = ""
    award_split: list[AwardLine] = Field(default_factory=list)
    shortfall: int = 0


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
    decision: Literal["approved", "rejected"]
    recommendation_version: str | None = None
    reason_code: str | None = None
    created_at: datetime
