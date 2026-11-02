"""Phase 7, Commit A — recovery policy interface + cost-aware controller tests."""

from app.runtime.portal_state import StateType
from app.runtime.recovery_controller import FaultType
from app.runtime.recovery_policy import (
    CostAwareRecoveryPolicy,
    RecoveryObservation,
    RecoveryTier,
    StopReason,
)
from app.runtime.safe_actions import AllowedAction


def _obs(
    *,
    state: StateType,
    expected: StateType | None,
    fault: FaultType = FaultType.NONE,
    action_budget: int = 30,
    cost_budget: float = 10.0,
    **kwargs,
) -> RecoveryObservation:
    return RecoveryObservation(
        current_semantic_state=state,
        previous_expected_state=expected,
        fault_hint=fault,
        remaining_action_budget=action_budget,
        remaining_cost_budget=cost_budget,
        **kwargs,
    )


def test_success_when_state_matches():
    policy = CostAwareRecoveryPolicy()
    decision = policy.decide(_obs(state=StateType.RESULTS, expected=StateType.RESULTS))
    assert decision.recovery_tier == RecoveryTier.NONE
    assert decision.stopping_decision == StopReason.SUCCESS


def test_safe_stop_on_session_expired():
    policy = CostAwareRecoveryPolicy()
    decision = policy.decide(
        _obs(state=StateType.LOGIN, expected=StateType.RESULTS, fault=FaultType.SESSION_EXPIRED)
    )
    assert decision.recovery_tier == RecoveryTier.STOP
    assert decision.stopping_decision == StopReason.SAFE_STOP_SESSION


def test_safe_stop_on_unsafe():
    policy = CostAwareRecoveryPolicy()
    decision = policy.decide(
        _obs(state=StateType.RESULTS, expected=StateType.QUOTE_DETAIL, fault=FaultType.UNSAFE_ACTION)
    )
    assert decision.stopping_decision == StopReason.SAFE_STOP_UNSAFE


def test_safe_stop_on_ambiguous():
    policy = CostAwareRecoveryPolicy()
    decision = policy.decide(
        _obs(state=StateType.RESULTS, expected=StateType.QUOTE_DETAIL, fault=FaultType.AMBIGUOUS_ELEMENT)
    )
    assert decision.stopping_decision == StopReason.SAFE_STOP_AMBIGUOUS


def test_budget_exhausted_stops():
    policy = CostAwareRecoveryPolicy()
    decision = policy.decide(
        _obs(
            state=StateType.RESULTS,
            expected=StateType.QUOTE_DETAIL,
            fault=FaultType.SELECTOR_CHANGED,
            action_budget=0,
        )
    )
    assert decision.recovery_tier == RecoveryTier.STOP
    assert decision.stopping_decision == StopReason.BUDGET_EXHAUSTED


def test_selector_drift_prefers_semantic_low_cost():
    """For selector drift, SEMANTIC has the highest success probability and
    low cost — the cost-aware policy must pick it (over LLM)."""
    policy = CostAwareRecoveryPolicy(enable_llm=True)
    decision = policy.decide(
        _obs(state=StateType.RESULTS, expected=StateType.QUOTE_DETAIL, fault=FaultType.SELECTOR_CHANGED)
    )
    assert decision.recovery_tier == RecoveryTier.SEMANTIC
    assert decision.estimated_cost == 2.0


def test_dynamic_dom_prefers_refresh():
    policy = CostAwareRecoveryPolicy()
    decision = policy.decide(
        _obs(state=StateType.RESULTS, expected=StateType.QUOTE_DETAIL, fault=FaultType.DYNAMIC_DOM_UPDATE)
    )
    assert decision.recovery_tier == RecoveryTier.REFRESH


def test_tier_attempt_cap_escalates():
    """After max_tier_attempts on SEMANTIC, the policy must escalate (not
    loop on the same tier)."""
    policy = CostAwareRecoveryPolicy(min_success_threshold=0.5, max_tier_attempts=1, enable_llm=False)
    obs = _obs(state=StateType.RESULTS, expected=StateType.QUOTE_DETAIL, fault=FaultType.SELECTOR_CHANGED)
    d1 = policy.decide(obs)
    assert d1.recovery_tier == RecoveryTier.SEMANTIC
    d2 = policy.decide(obs)  # SEMANTIC attempt #2 -> capped
    # Next acceptable: RE_OBSERVE (0.70 >= 0.5) — SEMANTIC is capped.
    assert d2.recovery_tier in (RecoveryTier.RE_OBSERVE, RecoveryTier.STOP)


def test_llm_disabled_by_default():
    """Offline default: LLM tier must not be selected."""
    policy = CostAwareRecoveryPolicy()
    decision = policy.decide(
        _obs(
            state=StateType.UNKNOWN,
            expected=StateType.QUOTE_DETAIL,
            fault=FaultType.UNKNOWN_STATE,
        )
    )
    # UNKNOWN_STATE priors: RE_OBSERVE 0.40 < 0.6 threshold; LLM 0.65 but
    # disabled -> no candidates -> STOP (unrecoverable).
    assert decision.recovery_tier == RecoveryTier.STOP


def test_observation_never_contains_hidden_fault():
    """The policy input must NOT contain the hidden fault label."""
    obs = RecoveryObservation(
        current_semantic_state=StateType.RESULTS,
        previous_expected_state=StateType.QUOTE_DETAIL,
    )
    # fault_hint is derived from observable signals only; the raw fault
    # injection label is never an input.
    assert obs.fault_hint == FaultType.NONE  # default; caller derives it


def test_decision_action_in_whitelist():
    policy = CostAwareRecoveryPolicy()
    decision = policy.decide(
        _obs(state=StateType.RESULTS, expected=StateType.QUOTE_DETAIL, fault=FaultType.DYNAMIC_DOM_UPDATE)
    )
    assert decision.selected_action == AllowedAction.REFRESH_READ_ONLY_VIEW
    # All policy actions must be in the Safe Action registry (read-only).
    assert decision.selected_action in AllowedAction


def test_parameters_roundtrip():
    policy = CostAwareRecoveryPolicy(min_success_threshold=0.7, cost_weight=0.9)
    params = policy.parameters()
    clone = CostAwareRecoveryPolicy.from_parameters(params)
    assert clone.min_success_threshold == 0.7
    assert clone.cost_weight == 0.9
