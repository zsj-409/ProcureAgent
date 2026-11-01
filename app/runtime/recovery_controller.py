"""State-Aware Recovery Controller (Phase 4, Commit B).

The controller sits above the SafeActionExecutor and the Semantic Portal
State Graph. It classifies failures, decides the LOWEST-RISK bounded recovery
step, and re-observes until the expected postcondition holds or the budget
ends (then SAFE STOP / escalate).

Recovery policy answers, in order:
1. Can this failure be retried safely? (capability + retry budget)
2. Do we need to re-observe the page? (dynamic DOM, layout drift)
3. Is the fixed script still usable? (selector path)
4. Is semantic locating more reliable than the raw selector?
5. Do we need to escalate to the LLM? (only when deterministic means fail
   and the action boundary is still verifiable)
6. Does the estimated recovery cost exceed the remaining budget?
7. Should we stop and ask for a human? (unsafe/ambiguous/unrecoverable)

Bounded execution: max actions / re-observations / refreshes / LLM calls /
recovery time / recovery cost.

Durable Runtime integration: every recovery attempt is bound to
(task_id, logical_step_id, lease_epoch, plan_hash, action_input_hash). If
the fencing token is stale, the controller must NOT persist browser results.
Uncertain external effects reuse the Phase 3 capability contract /
UNKNOWN_OUTCOME handling — no second, conflicting state machine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from .portal_state import StateTransitionLog, StateType

logger = logging.getLogger(__name__)


class FaultType(str, Enum):
    """Failure taxonomy (Task 6.2)."""

    SELECTOR_CHANGED = "SELECTOR_CHANGED"
    LAYOUT_CHANGED = "LAYOUT_CHANGED"
    PAGINATION_CHANGED = "PAGINATION_CHANGED"
    MODAL_BLOCKED = "MODAL_BLOCKED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    DYNAMIC_DOM_UPDATE = "DYNAMIC_DOM_UPDATE"
    TIMEOUT = "TIMEOUT"
    AMBIGUOUS_ELEMENT = "AMBIGUOUS_ELEMENT"
    UNEXPECTED_NAVIGATION = "UNEXPECTED_NAVIGATION"
    UNSAFE_ACTION = "UNSAFE_ACTION"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    NONE = "NONE"


class RecoveryDecision(str, Enum):
    """What the controller decides next."""

    RE_OBSERVE = "RE_OBSERVE"
    RETRY_SAME = "RETRY_SAME"
    SEMANTIC_LOCATE = "SEMANTIC_LOCATE"
    ESCALATE_LLM = "ESCALATE_LLM"
    REFRESH_READ_ONLY = "REFRESH_READ_ONLY"
    SAFE_STOP = "SAFE_STOP"
    SUCCESS = "SUCCESS"


@dataclass
class RecoveryContext:
    """Everything the controller needs to make a bounded, fenced decision."""

    task_id: str
    logical_step_id: int
    lease_epoch: int
    plan_hash: str
    action_input_hash: str
    current_state: StateType
    expected_state: StateType | None
    fault: FaultType
    actions_used: int = 0
    reobservations_used: int = 0
    refreshes_used: int = 0
    llm_calls_used: int = 0
    cost_used: float = 0.0
    max_actions: int = 30
    max_reobservations: int = 10
    max_refreshes: int = 3
    max_llm_calls: int = 5
    max_cost: float = 10.0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def budget_remaining(self) -> tuple[bool, str]:
        """(exhausted, reason)."""

        if self.actions_used >= self.max_actions:
            return True, "max_actions"
        if self.reobservations_used >= self.max_reobservations:
            return True, "max_reobservations"
        if self.refreshes_used >= self.max_refreshes:
            return True, "max_refreshes"
        if self.llm_calls_used >= self.max_llm_calls:
            return True, "max_llm_calls"
        if self.cost_used >= self.max_cost:
            return True, "max_cost"
        return False, ""


@dataclass
class RecoveryOutcome:
    """Final outcome of one recovery episode."""

    decision: RecoveryDecision
    result_state: StateType
    fault: FaultType
    steps: int = 0
    llm_calls: int = 0
    cost: float = 0.0
    detail: str = ""
    safe_stop: bool = False


def classify_failure(
    *,
    current_state: StateType,
    expected_state: StateType | None,
    last_error: str = "",
    page_changed: bool = False,
    ambiguous: bool = False,
    hostile: bool = False,
) -> FaultType:
    """Map an observation/error to a fault type (Task 6.2)."""

    if hostile:
        return FaultType.UNSAFE_ACTION
    if current_state == StateType.SESSION_EXPIRED:
        return FaultType.SESSION_EXPIRED
    if current_state == StateType.ERROR:
        if page_changed:
            return FaultType.UNEXPECTED_NAVIGATION
        return FaultType.UNKNOWN_STATE
    if ambiguous:
        return FaultType.AMBIGUOUS_ELEMENT
    if page_changed and expected_state is not None and current_state != expected_state:
        if "layout" in last_error.lower() or "card" in last_error.lower():
            return FaultType.LAYOUT_CHANGED
        if "pag" in last_error.lower():
            return FaultType.PAGINATION_CHANGED
        if "selector" in last_error.lower() or "not found" in last_error.lower():
            return FaultType.SELECTOR_CHANGED
        if "modal" in last_error.lower() or "dialog" in last_error.lower():
            return FaultType.MODAL_BLOCKED
        if "timeout" in last_error.lower():
            return FaultType.TIMEOUT
        if "dynamic" in last_error.lower() or "async" in last_error.lower():
            return FaultType.DYNAMIC_DOM_UPDATE
        return FaultType.UNKNOWN_STATE
    if current_state == StateType.UNKNOWN and expected_state is not None:
        return FaultType.UNKNOWN_STATE
    return FaultType.NONE


class StateAwareRecoveryController:
    """Bounded, safety-gated recovery controller (Task 6)."""

    def __init__(
        self,
        *,
        max_actions: int = 30,
        max_reobservations: int = 10,
        max_refreshes: int = 3,
        max_llm_calls: int = 5,
        max_cost: float = 10.0,
        log: StateTransitionLog | None = None,
    ):
        self.max_actions = max_actions
        self.max_reobservations = max_reobservations
        self.max_refreshes = max_refreshes
        self.max_llm_calls = max_llm_calls
        self.max_cost = max_cost
        self.log = log or StateTransitionLog()

    def decide(
        self,
        ctx: RecoveryContext,
        *,
        heuristic_succeeded: bool = False,
        semantic_locator_available: bool = False,
        llm_available: bool = False,
        last_error: str = "",
    ) -> RecoveryDecision:
        """Choose the next recovery step given the current context.

        Priority (deterministic, low-cost first):
        1. success → SUCCESS
        2. budget exhausted → SAFE_STOP
        3. hostile/unsafe → SAFE_STOP (never continue on an unsafe page)
        4. session expired → SAFE_STOP (cannot auto re-auth in a demo;
           escalate to human)
        5. unknown state → re-observe (bounded) then SAFE_STOP
        6. refresh read-only view (bounded) for dynamic DOM / timeout
        7. re-observe for layout/pagination drift (semantic re-locate)
        8. retry same action if the fault is transient
        9. escalate to LLM only when deterministic means failed AND the
           budget remains AND llm is available
        10. otherwise SAFE_STOP / escalate
        """

        exhausted, _ = ctx.budget_remaining()
        if exhausted:
            return RecoveryDecision.SAFE_STOP
        if ctx.fault == FaultType.NONE and ctx.current_state == ctx.expected_state:
            return RecoveryDecision.SUCCESS
        if ctx.fault == FaultType.UNSAFE_ACTION:
            return RecoveryDecision.SAFE_STOP
        if ctx.fault == FaultType.SESSION_EXPIRED:
            return RecoveryDecision.SAFE_STOP
        if ctx.fault == FaultType.AMBIGUOUS_ELEMENT:
            return RecoveryDecision.SAFE_STOP
        if ctx.fault == FaultType.DYNAMIC_DOM_UPDATE or ctx.fault == FaultType.TIMEOUT:
            if ctx.refreshes_used < ctx.max_refreshes:
                return RecoveryDecision.REFRESH_READ_ONLY
            return RecoveryDecision.SAFE_STOP
        if ctx.fault in (FaultType.LAYOUT_CHANGED, FaultType.PAGINATION_CHANGED, FaultType.SELECTOR_CHANGED):
            if semantic_locator_available:
                return RecoveryDecision.SEMANTIC_LOCATE
            if ctx.reobservations_used < ctx.max_reobservations:
                return RecoveryDecision.RE_OBSERVE
            return RecoveryDecision.SAFE_STOP
        if ctx.fault == FaultType.UNEXPECTED_NAVIGATION:
            return RecoveryDecision.RE_OBSERVE
        if ctx.fault == FaultType.UNKNOWN_STATE:
            if ctx.reobservations_used < ctx.max_reobservations:
                return RecoveryDecision.RE_OBSERVE
            return RecoveryDecision.SAFE_STOP
        # Transient / unknown: retry once, bounded.
        if ctx.actions_used < 1:
            return RecoveryDecision.RETRY_SAME
        return RecoveryDecision.SAFE_STOP
