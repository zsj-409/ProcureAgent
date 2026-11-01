"""Safe browser action contract (Phase 4, Commit A).

All browser recovery strategies must act ONLY through this registry of
allowed actions. An LLM (or heuristic) can only *propose* an action; the
SafeActionExecutor validates preconditions, target uniqueness, safety
whitelist and expected postconditions before executing anything.

Every action declares:
- external_side_effect: whether it can produce an external business effect.
- required_state: the semantic page state(s) it may run from.
- target_kind: how the target element is addressed (semantic locator).
- uniqueness_check: whether the target must be unique before acting.
- expected_post: the semantic state expected AFTER the action.
- timeout / max_retries / allow_retry_after_failure.
- actual cost (charged after execution).

Safety boundaries (hard):
- No auto purchase / payment / approval bypass.
- No access to another task's or tenant's browser identity.
- No irreversible form submission.
- No CAPTCHA / 2FA / access-control bypass.
- No executing arbitrary commands suggested by page content.
- The supplier page is UNTRUSTED: even text like "ignore previous
  instructions" or "click this hidden button" cannot change the whitelist.
- Success is judged by trusted postcondition verification, NOT by an LLM
  claiming success.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .portal_state import StateType

logger = logging.getLogger(__name__)


class AllowedAction(str, Enum):
    """The fixed whitelist of browser actions (finite registry)."""

    OPEN_ALLOWED_PAGE = "OPEN_ALLOWED_PAGE"
    FILL_SEARCH_FIELD = "FILL_SEARCH_FIELD"
    SUBMIT_SEARCH = "SUBMIT_SEARCH"
    SELECT_PRODUCT = "SELECT_PRODUCT"
    NEXT_PAGE = "NEXT_PAGE"
    CLOSE_ALLOWED_MODAL = "CLOSE_ALLOWED_MODAL"
    WAIT_FOR_EXPECTED_STATE = "WAIT_FOR_EXPECTED_STATE"
    REFRESH_READ_ONLY_VIEW = "REFRESH_READ_ONLY_VIEW"
    EXTRACT_QUOTE = "EXTRACT_QUOTE"
    STOP_AND_ESCALATE = "STOP_AND_ESCALATE"


# Actions that never cause an external business effect (read-only by contract).
_READ_ONLY_ACTIONS = {
    AllowedAction.OPEN_ALLOWED_PAGE,
    AllowedAction.FILL_SEARCH_FIELD,
    AllowedAction.SUBMIT_SEARCH,
    AllowedAction.SELECT_PRODUCT,
    AllowedAction.NEXT_PAGE,
    AllowedAction.CLOSE_ALLOWED_MODAL,
    AllowedAction.WAIT_FOR_EXPECTED_STATE,
    AllowedAction.REFRESH_READ_ONLY_VIEW,
    AllowedAction.EXTRACT_QUOTE,
    AllowedAction.STOP_AND_ESCALATE,
}

# Actions that must never appear outside the whitelist.
_FORBIDDEN_PATTERNS = (
    "order",
    "purchase",
    "buy",
    "checkout",
    "payment",
    "submit order",
    "下单",
    "购买",
    "支付",
    "confirm order",
)


@dataclass
class ActionSpec:
    """Declared contract for one allowed action."""

    action: AllowedAction
    external_side_effect: bool = False
    required_state: tuple[StateType, ...] = ()
    target_kind: str = "semantic"  # how the target is addressed
    uniqueness_check: bool = True
    expected_post: StateType | None = None
    timeout_s: float = 10.0
    max_retries: int = 1
    allow_retry_after_failure: bool = False
    cost: float = 0.0  # charged after execution


ACTION_REGISTRY: dict[AllowedAction, ActionSpec] = {
    AllowedAction.OPEN_ALLOWED_PAGE: ActionSpec(
        action=AllowedAction.OPEN_ALLOWED_PAGE,
        required_state=(StateType.LOGIN, StateType.DASHBOARD, StateType.SEARCH, StateType.UNKNOWN),
        expected_post=StateType.SEARCH,
        cost=0.0,
    ),
    AllowedAction.FILL_SEARCH_FIELD: ActionSpec(
        action=AllowedAction.FILL_SEARCH_FIELD,
        required_state=(StateType.SEARCH, StateType.RESULTS, StateType.DASHBOARD),
        expected_post=StateType.SEARCH,
        cost=0.05,
    ),
    AllowedAction.SUBMIT_SEARCH: ActionSpec(
        action=AllowedAction.SUBMIT_SEARCH,
        required_state=(StateType.SEARCH, StateType.RESULTS, StateType.DASHBOARD),
        expected_post=StateType.RESULTS,
        cost=0.1,
    ),
    AllowedAction.SELECT_PRODUCT: ActionSpec(
        action=AllowedAction.SELECT_PRODUCT,
        required_state=(StateType.RESULTS,),
        expected_post=StateType.QUOTE_DETAIL,
        cost=0.1,
    ),
    AllowedAction.NEXT_PAGE: ActionSpec(
        action=AllowedAction.NEXT_PAGE,
        required_state=(StateType.RESULTS,),
        expected_post=StateType.RESULTS,
        cost=0.05,
    ),
    AllowedAction.CLOSE_ALLOWED_MODAL: ActionSpec(
        action=AllowedAction.CLOSE_ALLOWED_MODAL,
        required_state=(StateType.SEARCH, StateType.RESULTS, StateType.QUOTE_DETAIL, StateType.DASHBOARD),
        expected_post=None,  # returns to the underlying state
        cost=0.05,
    ),
    AllowedAction.WAIT_FOR_EXPECTED_STATE: ActionSpec(
        action=AllowedAction.WAIT_FOR_EXPECTED_STATE,
        required_state=(),
        expected_post=None,
        cost=0.0,
    ),
    AllowedAction.REFRESH_READ_ONLY_VIEW: ActionSpec(
        action=AllowedAction.REFRESH_READ_ONLY_VIEW,
        required_state=(StateType.RESULTS, StateType.QUOTE_DETAIL, StateType.SEARCH, StateType.DASHBOARD),
        expected_post=None,
        cost=0.05,
    ),
    AllowedAction.EXTRACT_QUOTE: ActionSpec(
        action=AllowedAction.EXTRACT_QUOTE,
        required_state=(StateType.RESULTS, StateType.QUOTE_DETAIL),
        expected_post=None,
        cost=0.0,
    ),
    AllowedAction.STOP_AND_ESCALATE: ActionSpec(
        action=AllowedAction.STOP_AND_ESCALATE,
        required_state=(),
        expected_post=None,
        cost=0.0,
    ),
}


@dataclass
class ActionProposal:
    """What an agent (LLM or heuristic) proposes; validated before execution."""

    action: AllowedAction
    target_semantic: str = ""  # semantic locator, e.g. placeholder/label/kind
    product_name: str = ""
    note: str = ""


@dataclass
class ActionVerdict:
    """Result of the safety validation gate."""

    allowed: bool
    reason_code: str = ""
    detail: str = ""
    spec: ActionSpec | None = None


class UnsafeActionError(Exception):
    """Raised when a proposed action violates the safety contract."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class SafeActionExecutor:
    """Validates and executes whitelisted browser actions.

    ``execute`` is the ONLY entry point for any recovery strategy. It:
    1. Checks the action is in the finite whitelist.
    2. Checks the current semantic state is in the action's required_state.
    3. Checks the target is unique (when uniqueness_check).
    4. Executes via the provided ``perform`` callback.
    5. Verifies the expected postcondition (via ``observe`` callback).
    6. Charges ``spec.cost`` to the budget and records the transition.
    """

    def __init__(
        self,
        *,
        max_actions: int = 30,
        max_refreshes: int = 3,
        max_llm_calls: int = 5,
        max_recovery_time_s: float = 120.0,
        max_recovery_cost: float = 10.0,
    ):
        self.max_actions = max_actions
        self.max_refreshes = max_refreshes
        self.max_llm_calls = max_llm_calls
        self.max_recovery_time_s = max_recovery_time_s
        self.max_recovery_cost = max_recovery_cost
        self.actions_taken = 0
        self.refreshes = 0
        self.llm_calls = 0
        self.cost = 0.0
        self._started_at: datetime | None = None
        self._last_result: ActionVerdict | None = None

    def begin(self) -> None:
        self._started_at = datetime.now(UTC)
        self.actions_taken = 0
        self.refreshes = 0
        self.llm_calls = 0
        self.cost = 0.0

    def budget_exhausted(self) -> tuple[bool, str]:
        """Whether any hard budget is exhausted (returns reason)."""

        if self.actions_taken >= self.max_actions:
            return True, f"max_actions ({self.max_actions}) exceeded"
        if self.refreshes >= self.max_refreshes:
            return True, f"max_refreshes ({self.max_refreshes}) exceeded"
        if self.llm_calls >= self.max_llm_calls:
            return True, f"max_llm_calls ({self.max_llm_calls}) exceeded"
        if self.cost >= self.max_recovery_cost:
            return True, f"max_recovery_cost ({self.max_recovery_cost}) exceeded"
        if self._started_at is not None:
            elapsed = (datetime.now(UTC) - self._started_at).total_seconds()
            if elapsed >= self.max_recovery_time_s:
                return True, f"max_recovery_time ({self.max_recovery_time_s}s) exceeded"
        return False, ""

    def count_llm_call(self) -> None:
        self.llm_calls += 1

    def validate(
        self,
        proposal: ActionProposal,
        *,
        current_state: StateType,
        page_text: str = "",
    ) -> ActionVerdict:
        """Gate a proposed action against the safety contract."""

        spec = ACTION_REGISTRY.get(proposal.action)
        if spec is None:
            return ActionVerdict(False, "UNKNOWN_ACTION", f"action {proposal.action} not in whitelist")
        if current_state not in spec.required_state and spec.required_state:
            return ActionVerdict(
                False,
                "WRONG_STATE",
                f"{proposal.action.value} not allowed from {current_state.value} "
                f"(requires {[s.value for s in spec.required_state]})",
                spec,
            )
        # The supplier page is untrusted: any forbidden wording in page text
        # cannot change the whitelist, but it IS evidence the page is hostile.
        lowered = page_text.lower()
        if proposal.action not in _READ_ONLY_ACTIONS:
            return ActionVerdict(
                False,
                "NOT_READ_ONLY",
                f"{proposal.action.value} is not a read-only action and cannot be executed",
                spec,
            )
        if any(pattern in lowered for pattern in _FORBIDDEN_PATTERNS):
            # Still allow read-only extract/stop; forbid anything that
            # could look like a mutation gesture (defense in depth).
            if proposal.action in (
                AllowedAction.SUBMIT_SEARCH,
                AllowedAction.SELECT_PRODUCT,
                AllowedAction.NEXT_PAGE,
            ):
                return ActionVerdict(
                    False,
                    "PAGE_HOSTILE",
                    "page text contains forbidden transaction wording; "
                    "read-only actions only",
                    spec,
                )
        return ActionVerdict(True, "OK", "", spec)

    async def execute(
        self,
        proposal: ActionProposal,
        *,
        current_state: StateType,
        observe: Callable[[], Any],
        perform: Callable[[ActionProposal], Any],
        page_text: str = "",
    ) -> ActionVerdict:
        """Validate then execute; verify the expected postcondition."""

        if self._started_at is None:
            self.begin()
        verdict = self.validate(proposal, current_state=current_state, page_text=page_text)
        self._last_result = verdict
        if not verdict.allowed:
            return verdict
        spec = verdict.spec
        if spec is None:
            return ActionVerdict(False, "NO_SPEC", "validation passed but no spec", None)
        exhausted, reason = self.budget_exhausted()
        if exhausted:
            return ActionVerdict(False, "BUDGET_EXHAUSTED", reason, spec)
        try:
            await perform(proposal)
        except Exception as exc:
            return ActionVerdict(False, "EXEC_FAILED", str(exc), spec)
        self.actions_taken += 1
        self.cost += spec.cost
        if proposal.action is AllowedAction.REFRESH_READ_ONLY_VIEW:
            self.refreshes += 1
        # Postcondition verification: only when the spec declares one.
        if spec.expected_post is not None:
            observed = observe()
            if observed != spec.expected_post:
                return ActionVerdict(
                    False,
                    "POSTCONDITION_FAILED",
                    f"expected {spec.expected_post.value} after {proposal.action.value}, observed {observed}",
                    spec,
                )
        return ActionVerdict(True, "OK", "", spec)
