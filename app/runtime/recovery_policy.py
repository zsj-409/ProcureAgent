"""Unified recovery policy interface + cost-aware controller (Phase 7).

RecoveryObservation — everything a policy may see (runtime-observable only;
the hidden fault label is NEVER exposed):
- current_semantic_state, previous_expected_state
- last_action, last_error
- recent_action_history
- remaining action/time/cost budgets
- session_status, permitted_actions, operation_capability

RecoveryDecision — what the policy outputs:
- selected_action (from the finite Safe Action registry)
- recovery_tier (NONE / RE_OBSERVE / REFRESH / SEMANTIC / LLM / STOP)
- reason_code, expected_postcondition, estimated_cost, stopping_decision

Cost-aware controller (Task 4):
- per-tier cost model
- success-probability estimates per (fault, tier) — explainable priors
- budget-aware tier selection (choose the lowest-cost tier whose estimated
  success probability is acceptable given remaining budget)
- hard bounds: finite retries, finite observations, explicit stop
- a parameterized scoring rule tuned ONLY on DEV worlds (Confirmation
  decides promotion; TEST is frozen)
- safety: no tier can bypass SafeActionExecutor or raise success estimates
  to justify a side-effecting action (all actions remain read-only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .portal_state import StateType
from .recovery_controller import FaultType
from .safe_actions import AllowedAction

logger = logging.getLogger(__name__)


class RecoveryTier(str, Enum):
    NONE = "NONE"  # no recovery needed (success)
    RE_OBSERVE = "RE_OBSERVE"  # re-snapshot the page
    REFRESH = "REFRESH"  # reload read-only view
    SEMANTIC = "SEMANTIC"  # semantic re-locate / re-extract
    RETRY = "RETRY"  # retry the same action once
    LLM = "LLM"  # LLM decision (only when deterministic fails)
    STOP = "STOP"  # safe stop / escalate


class StopReason(str, Enum):
    SUCCESS = "SUCCESS"
    SAFE_STOP_UNSAFE = "SAFE_STOP_UNSAFE"
    SAFE_STOP_SESSION = "SAFE_STOP_SESSION"
    SAFE_STOP_AMBIGUOUS = "SAFE_STOP_AMBIGUOUS"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    UNRECOVERABLE = "UNRECOVERABLE"


@dataclass
class RecoveryObservation:
    """Everything the policy is allowed to see."""

    current_semantic_state: StateType
    previous_expected_state: StateType | None
    last_action: str = ""
    last_error: str = ""
    fault_hint: FaultType = FaultType.NONE  # derived from observable signals ONLY
    recent_action_history: list[str] = field(default_factory=list)
    remaining_action_budget: int = 30
    remaining_time_budget_s: float = 120.0
    remaining_cost_budget: float = 10.0
    session_status: str = "unknown"
    permitted_actions: list[str] = field(default_factory=list)
    operation_capability: str = "READ_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_semantic_state": self.current_semantic_state.value,
            "previous_expected_state": self.previous_expected_state.value
            if self.previous_expected_state
            else None,
            "last_action": self.last_action,
            "last_error": self.last_error,
            "fault_hint": self.fault_hint.value,
            "history": self.recent_action_history[-8:],
            "remaining_action_budget": self.remaining_action_budget,
            "remaining_time_budget_s": self.remaining_time_budget_s,
            "remaining_cost_budget": self.remaining_cost_budget,
            "session_status": self.session_status,
            "permitted_actions": self.permitted_actions,
            "operation_capability": self.operation_capability,
        }


@dataclass
class RecoveryDecision:
    """What the policy decided."""

    selected_action: AllowedAction
    recovery_tier: RecoveryTier
    reason_code: str
    expected_postcondition: StateType | None = None
    estimated_cost: float = 0.0
    stopping_decision: StopReason | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_action": self.selected_action.value,
            "recovery_tier": self.recovery_tier.value,
            "reason_code": self.reason_code,
            "expected_postcondition": self.expected_postcondition.value
            if self.expected_postcondition
            else None,
            "estimated_cost": self.estimated_cost,
            "stopping_decision": self.stopping_decision.value if self.stopping_decision else None,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Cost model (explainable priors, per fault × tier)
# ---------------------------------------------------------------------------

# Estimated success probability of each recovery tier per fault type.
# These are explainable priors from the Phase 4 experiments, NOT learned.
TIER_SUCCESS_PRIORS: dict[FaultType, dict[RecoveryTier, float]] = {
    FaultType.SELECTOR_CHANGED: {
        RecoveryTier.SEMANTIC: 0.95,
        RecoveryTier.RE_OBSERVE: 0.70,
        RecoveryTier.REFRESH: 0.30,
        RecoveryTier.RETRY: 0.30,
        RecoveryTier.LLM: 0.85,
    },
    FaultType.LAYOUT_CHANGED: {
        RecoveryTier.SEMANTIC: 0.90,
        RecoveryTier.RE_OBSERVE: 0.60,
        RecoveryTier.REFRESH: 0.25,
        RecoveryTier.LLM: 0.80,
    },
    FaultType.PAGINATION_CHANGED: {
        RecoveryTier.SEMANTIC: 0.85,
        RecoveryTier.RE_OBSERVE: 0.55,
        RecoveryTier.REFRESH: 0.20,
        RecoveryTier.LLM: 0.75,
    },
    FaultType.DYNAMIC_DOM_UPDATE: {
        RecoveryTier.REFRESH: 0.80,
        RecoveryTier.RE_OBSERVE: 0.60,
        RecoveryTier.RETRY: 0.40,
        RecoveryTier.LLM: 0.70,
    },
    FaultType.TIMEOUT: {
        RecoveryTier.REFRESH: 0.75,
        RecoveryTier.RETRY: 0.50,
        RecoveryTier.RE_OBSERVE: 0.50,
        RecoveryTier.LLM: 0.60,
    },
    FaultType.MODAL_BLOCKED: {
        RecoveryTier.SEMANTIC: 0.90,
        RecoveryTier.RE_OBSERVE: 0.50,
        RecoveryTier.LLM: 0.85,
    },
    FaultType.UNEXPECTED_NAVIGATION: {
        RecoveryTier.RE_OBSERVE: 0.60,
        RecoveryTier.REFRESH: 0.30,
        RecoveryTier.LLM: 0.70,
    },
    FaultType.UNKNOWN_STATE: {
        RecoveryTier.RE_OBSERVE: 0.40,
        RecoveryTier.LLM: 0.65,
    },
    FaultType.SESSION_EXPIRED: {},  # never recoverable by policy -> STOP
    FaultType.AMBIGUOUS_ELEMENT: {},  # -> STOP
    FaultType.UNSAFE_ACTION: {},  # -> STOP
    FaultType.NONE: {},
}

# Per-tier execution cost (relative units: snapshot ~1, refresh ~3, LLM ~50).
TIER_COSTS: dict[RecoveryTier, float] = {
    RecoveryTier.NONE: 0.0,
    RecoveryTier.RE_OBSERVE: 1.0,
    RecoveryTier.RETRY: 1.5,
    RecoveryTier.REFRESH: 3.0,
    RecoveryTier.SEMANTIC: 2.0,
    RecoveryTier.LLM: 50.0,
    RecoveryTier.STOP: 0.0,
}

# Tier -> Safe Action mapping.
TIER_ACTIONS: dict[RecoveryTier, AllowedAction] = {
    RecoveryTier.RE_OBSERVE: AllowedAction.WAIT_FOR_EXPECTED_STATE,
    RecoveryTier.REFRESH: AllowedAction.REFRESH_READ_ONLY_VIEW,
    RecoveryTier.SEMANTIC: AllowedAction.EXTRACT_QUOTE,
    RecoveryTier.RETRY: AllowedAction.WAIT_FOR_EXPECTED_STATE,
    RecoveryTier.LLM: AllowedAction.EXTRACT_QUOTE,
    RecoveryTier.STOP: AllowedAction.STOP_AND_ESCALATE,
}


class CostAwareRecoveryPolicy:
    """Parameterized, cost-aware recovery policy (Task 4).

    Selection rule (explainable, deterministic):
    1. Success (state == expected) -> NONE.
    2. Unrecoverable faults (session/ambiguous/unsafe) -> STOP.
    3. Budget exhausted -> STOP.
    4. Among tiers with acceptable estimated success probability
       (est_p >= min_success_threshold), pick the LOWEST cost tier
       (cost-aware) — unless a cheap tier has been tried N times already
       (max_tier_attempts), in which case escalate to the next acceptable
       tier.
    5. If only high-cost tiers (LLM) remain and the LLM budget is spent or
       LLM is disabled -> STOP.
    """

    def __init__(
        self,
        *,
        min_success_threshold: float = 0.6,
        max_tier_attempts: int = 2,
        cost_weight: float = 0.5,
        enable_llm: bool = False,
        max_observations: int = 10,
        max_refreshes: int = 3,
        max_llm_calls: int = 2,
        max_actions: int = 30,
    ):
        self.min_success_threshold = min_success_threshold
        self.max_tier_attempts = max_tier_attempts
        self.cost_weight = cost_weight
        self.enable_llm = enable_llm
        self.max_observations = max_observations
        self.max_refreshes = max_refreshes
        self.max_llm_calls = max_llm_calls
        self.max_actions = max_actions
        # Per-tier attempt counters (runtime state).
        self._tier_attempts: dict[RecoveryTier, int] = {}
        self._tier_used: dict[RecoveryTier, int] = {}

    def reset(self) -> None:
        self._tier_attempts = {}
        self._tier_used = {}

    # -- decision ------------------------------------------------------------

    def decide(self, obs: RecoveryObservation) -> RecoveryDecision:
        """Choose the next recovery action from a runtime observation."""

        # 1. Success.
        if obs.fault_hint == FaultType.NONE and obs.current_semantic_state == obs.previous_expected_state:
            return RecoveryDecision(
                selected_action=AllowedAction.EXTRACT_QUOTE,
                recovery_tier=RecoveryTier.NONE,
                reason_code="SUCCESS_STATE_MATCH",
                expected_postcondition=obs.previous_expected_state,
                estimated_cost=0.0,
                stopping_decision=StopReason.SUCCESS,
            )

        # 2. Unrecoverable faults.
        if obs.fault_hint == FaultType.UNSAFE_ACTION:
            return self._stop(StopReason.SAFE_STOP_UNSAFE, "hostile/unsafe page", obs)
        if obs.fault_hint == FaultType.SESSION_EXPIRED:
            return self._stop(
                StopReason.SAFE_STOP_SESSION, "session expired — no cross-identity recovery", obs
            )
        if obs.fault_hint == FaultType.AMBIGUOUS_ELEMENT:
            return self._stop(StopReason.SAFE_STOP_AMBIGUOUS, "ambiguous element — safe stop", obs)

        # 3. Budget exhaustion.
        exhausted = self._budget_exhausted(obs)
        if exhausted:
            return self._stop(StopReason.BUDGET_EXHAUSTED, exhausted, obs)

        # 4. Candidate tiers with acceptable success probability.
        priors = TIER_SUCCESS_PRIORS.get(obs.fault_hint, {})
        candidates: list[tuple[RecoveryTier, float, float, float]] = []
        for tier, prob in priors.items():
            if tier == RecoveryTier.LLM and not self.enable_llm:
                continue
            if prob < self.min_success_threshold:
                continue
            # Hard per-tier budget caps.
            if (
                tier == RecoveryTier.RE_OBSERVE
                and self._tier_used.get(RecoveryTier.RE_OBSERVE, 0) >= self.max_observations
            ):
                continue
            if (
                tier == RecoveryTier.REFRESH
                and self._tier_used.get(RecoveryTier.REFRESH, 0) >= self.max_refreshes
            ):
                continue
            if tier == RecoveryTier.LLM and self._tier_used.get(RecoveryTier.LLM, 0) >= self.max_llm_calls:
                continue
            # Attempt cap per tier (escalate after max_tier_attempts).
            if self._tier_attempts.get(tier, 0) >= self.max_tier_attempts:
                continue
            cost = TIER_COSTS[tier]
            # Score: higher success probability and lower cost win.
            score = prob - self.cost_weight * (cost / 50.0)
            candidates.append((tier, prob, cost, score))

        if not candidates:
            return self._stop(StopReason.UNRECOVERABLE, "no acceptable tier remains", obs)

        # Pick the best-scoring tier (explainable, deterministic).
        best = max(candidates, key=lambda item: item[3])
        tier = best[0]
        self._tier_attempts[tier] = self._tier_attempts.get(tier, 0) + 1
        self._tier_used[tier] = self._tier_used.get(tier, 0) + 1

        action = TIER_ACTIONS[tier]
        post = obs.previous_expected_state
        if tier == RecoveryTier.RE_OBSERVE:
            reason = "re-observe to disambiguate state"
        elif tier == RecoveryTier.REFRESH:
            reason = "refresh read-only view (dynamic DOM/timeout)"
        elif tier == RecoveryTier.SEMANTIC:
            reason = "semantic re-locate (drift)"
        elif tier == RecoveryTier.RETRY:
            reason = "retry same action once (transient)"
        else:
            reason = "escalate to LLM (deterministic tiers exhausted)"
        return RecoveryDecision(
            selected_action=action,
            recovery_tier=tier,
            reason_code=reason,
            expected_postcondition=post,
            estimated_cost=TIER_COSTS[tier],
        )

    def _stop(self, reason: StopReason, detail: str, obs: RecoveryObservation) -> RecoveryDecision:
        return RecoveryDecision(
            selected_action=AllowedAction.STOP_AND_ESCALATE,
            recovery_tier=RecoveryTier.STOP,
            reason_code=detail,
            estimated_cost=0.0,
            stopping_decision=reason,
            detail=detail,
        )

    def _budget_exhausted(self, obs: RecoveryObservation) -> str:
        if obs.remaining_action_budget <= 0:
            return "remaining_action_budget exhausted"
        if obs.remaining_time_budget_s <= 0:
            return "remaining_time_budget exhausted"
        if obs.remaining_cost_budget <= 0:
            return "remaining_cost_budget exhausted"
        return ""

    # -- tuning --------------------------------------------------------------

    def parameters(self) -> dict[str, Any]:
        return {
            "min_success_threshold": self.min_success_threshold,
            "max_tier_attempts": self.max_tier_attempts,
            "cost_weight": self.cost_weight,
            "enable_llm": self.enable_llm,
            "max_observations": self.max_observations,
            "max_refreshes": self.max_refreshes,
            "max_llm_calls": self.max_llm_calls,
            "max_actions": self.max_actions,
        }

    @classmethod
    def from_parameters(cls, params: dict[str, Any]) -> CostAwareRecoveryPolicy:
        allowed = {
            "min_success_threshold",
            "max_tier_attempts",
            "cost_weight",
            "enable_llm",
            "max_observations",
            "max_refreshes",
            "max_llm_calls",
            "max_actions",
        }
        filtered = {k: v for k, v in params.items() if k in allowed}
        return cls(**filtered)
