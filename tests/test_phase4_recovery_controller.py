"""Phase 4, Commit B — State-Aware Recovery Controller unit tests
(no browser needed; decision logic only)."""


from app.runtime.portal_state import StateType
from app.runtime.recovery_controller import (
    FaultType,
    RecoveryContext,
    RecoveryDecision,
    StateAwareRecoveryController,
    classify_failure,
)


def _ctx(*, state, expected=None, fault=FaultType.NONE, **kwargs):
    return RecoveryContext(
        task_id="t1",
        logical_step_id=1,
        lease_epoch=1,
        plan_hash="h",
        action_input_hash="a",
        current_state=state,
        expected_state=expected,
        fault=fault,
        **kwargs,
    )


def test_success_when_state_matches_expected():
    controller = StateAwareRecoveryController()
    ctx = _ctx(state=StateType.QUOTE_DETAIL, expected=StateType.QUOTE_DETAIL, fault=FaultType.NONE)
    assert controller.decide(ctx) == RecoveryDecision.SUCCESS


def test_safe_stop_when_budget_exhausted():
    controller = StateAwareRecoveryController()
    ctx = _ctx(
        state=StateType.RESULTS,
        expected=StateType.QUOTE_DETAIL,
        fault=FaultType.SELECTOR_CHANGED,
        actions_used=30,
        max_actions=30,
    )
    assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP


def test_safe_stop_on_session_expired():
    controller = StateAwareRecoveryController()
    ctx = _ctx(
        state=StateType.SESSION_EXPIRED,
        expected=StateType.RESULTS,
        fault=FaultType.SESSION_EXPIRED,
    )
    assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP


def test_safe_stop_on_ambiguous():
    controller = StateAwareRecoveryController()
    ctx = _ctx(
        state=StateType.RESULTS,
        expected=StateType.QUOTE_DETAIL,
        fault=FaultType.AMBIGUOUS_ELEMENT,
    )
    assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP


def test_refresh_for_dynamic_dom_then_stop():
    controller = StateAwareRecoveryController()
    ctx = _ctx(
        state=StateType.RESULTS,
        expected=StateType.QUOTE_DETAIL,
        fault=FaultType.DYNAMIC_DOM_UPDATE,
        refreshes_used=0,
    )
    assert controller.decide(ctx) == RecoveryDecision.REFRESH_READ_ONLY
    ctx.refreshes_used = 3  # max
    assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP


def test_semantic_locate_for_layout_drift():
    controller = StateAwareRecoveryController()
    ctx = _ctx(
        state=StateType.RESULTS,
        expected=StateType.QUOTE_DETAIL,
        fault=FaultType.LAYOUT_CHANGED,
    )
    decision = controller.decide(ctx, semantic_locator_available=True)
    assert decision == RecoveryDecision.SEMANTIC_LOCATE


def test_reobserve_then_stop_for_unknown_state():
    controller = StateAwareRecoveryController()
    ctx = _ctx(
        state=StateType.UNKNOWN,
        expected=StateType.QUOTE_DETAIL,
        fault=FaultType.UNKNOWN_STATE,
        reobservations_used=0,
    )
    assert controller.decide(ctx) == RecoveryDecision.RE_OBSERVE
    ctx.reobservations_used = 10
    assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP


def test_retry_once_for_transient_then_stop():
    controller = StateAwareRecoveryController()
    # TIMEOUT: refresh budget first; when exhausted -> SAFE_STOP (no blind retry).
    ctx = _ctx(
        state=StateType.RESULTS,
        expected=StateType.QUOTE_DETAIL,
        fault=FaultType.TIMEOUT,
        refreshes_used=3,  # no refresh budget left
        actions_used=0,
    )
    assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP
    # Unknown transient (NONE fault but state mismatch) -> retry once, then stop.
    ctx2 = _ctx(
        state=StateType.RESULTS,
        expected=StateType.QUOTE_DETAIL,
        fault=FaultType.NONE,
        actions_used=0,
    )
    assert controller.decide(ctx2) == RecoveryDecision.RETRY_SAME
    ctx2.actions_used = 1
    assert controller.decide(ctx2) == RecoveryDecision.SAFE_STOP


def test_classify_failure_mapping():
    assert (
        classify_failure(current_state=StateType.SESSION_EXPIRED, expected_state=StateType.RESULTS)
        == FaultType.SESSION_EXPIRED
    )
    assert (
        classify_failure(
            current_state=StateType.ERROR,
            expected_state=StateType.RESULTS,
            page_changed=True,
        )
        == FaultType.UNEXPECTED_NAVIGATION
    )
    assert (
        classify_failure(
            current_state=StateType.RESULTS,
            expected_state=StateType.QUOTE_DETAIL,
            page_changed=True,
            last_error="selector not found",
        )
        == FaultType.SELECTOR_CHANGED
    )
    assert (
        classify_failure(
            current_state=StateType.RESULTS,
            expected_state=StateType.QUOTE_DETAIL,
            page_changed=True,
            last_error="card layout",
        )
        == FaultType.LAYOUT_CHANGED
    )
    assert (
        classify_failure(current_state=StateType.UNKNOWN, expected_state=StateType.QUOTE_DETAIL)
        == FaultType.UNKNOWN_STATE
    )
    assert (
        classify_failure(current_state=StateType.RESULTS, expected_state=StateType.RESULTS)
        == FaultType.NONE
    )
    assert (
        classify_failure(
            current_state=StateType.RESULTS,
            expected_state=StateType.QUOTE_DETAIL,
            hostile=True,
        )
        == FaultType.UNSAFE_ACTION
    )


def test_recovery_context_budget_remaining():
    ctx = _ctx(state=StateType.RESULTS, fault=FaultType.NONE, cost_used=9.0, max_cost=10.0)
    exhausted, reason = ctx.budget_remaining()
    assert not exhausted
    ctx.cost_used = 10.0
    exhausted, reason = ctx.budget_remaining()
    assert exhausted
    assert reason == "max_cost"
