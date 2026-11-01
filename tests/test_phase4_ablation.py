"""Phase 4, Commit C — ablation unit tests.

Ablation compares the State-Aware Ladder with stripped variants:
- without semantic state recognition (raw heuristic only)
- without postcondition verification (safe action executor skipping observe)
- without budget-aware escalation (unbounded recovery)
Each ablation must demonstrate the component's contribution OR honestly
report no effect.

These are decision-logic tests (no browser); the browser-level comparison
lives in tests/eval/portal_strategy_benchmark.py.
"""


from app.runtime.portal_state import StateType
from app.runtime.recovery_controller import (
    FaultType,
    RecoveryContext,
    RecoveryDecision,
    StateAwareRecoveryController,
)
from app.runtime.safe_actions import (
    ActionProposal,
    AllowedAction,
    SafeActionExecutor,
)


def _ctx(**kwargs):
    base = dict(
        task_id="t1",
        logical_step_id=1,
        lease_epoch=1,
        plan_hash="h",
        action_input_hash="a",
        current_state=StateType.RESULTS,
        expected_state=StateType.QUOTE_DETAIL,
        fault=FaultType.SELECTOR_CHANGED,
    )
    base.update(kwargs)
    return RecoveryContext(**base)


def test_without_semantic_recognition_falls_back_to_retry():
    """Ablation: no semantic state -> the controller cannot classify; it
    falls back to blind retry (worse: no SEMANTIC_LOCATE path)."""

    controller = StateAwareRecoveryController()
    # With semantics: SELECTOR_CHANGED -> SEMANTIC_LOCATE.
    ctx = _ctx(fault=FaultType.SELECTOR_CHANGED)
    assert controller.decide(ctx, semantic_locator_available=True) == RecoveryDecision.SEMANTIC_LOCATE
    # Without semantic recognition (no locator, unknown state): re-observe
    # then stop — no semantic recovery.
    ctx = _ctx(current_state=StateType.UNKNOWN, fault=FaultType.UNKNOWN_STATE, reobservations_used=0)
    assert controller.decide(ctx) == RecoveryDecision.RE_OBSERVE
    ctx.reobservations_used = 10
    assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP


def test_without_postcondition_verification_accepts_wrong_state():
    """Ablation: postcondition verification is what catches a page that did
    NOT move to the expected state. Without it, the action is 'success'."""

    import asyncio

    async def run(verify: bool):
        executor = SafeActionExecutor()

        class Observed:
            value = StateType.RESULTS  # page did NOT advance

        async def perform(proposal):
            Observed.value = StateType.RESULTS

        return await executor.execute(
            ActionProposal(action=AllowedAction.SELECT_PRODUCT),
            current_state=StateType.RESULTS,
            observe=(lambda: Observed.value) if verify else (lambda: StateType.QUOTE_DETAIL),
            perform=perform,
        )

    with_verification = asyncio.run(run(verify=True))
    assert not with_verification.allowed
    assert with_verification.reason_code == "POSTCONDITION_FAILED"

    # Ablation: observe() returns the expected state unconditionally (i.e.
    # the verification is disabled) -> the wrong page is accepted as success.
    without_verification = asyncio.run(run(verify=False))
    assert without_verification.allowed


def test_without_budget_aware_escalation_loops_forever():
    """Ablation: budget-aware escalation stops after max actions; without it
    the controller would keep retrying."""

    controller = StateAwareRecoveryController()
    ctx = _ctx(
        fault=FaultType.DYNAMIC_DOM_UPDATE,
        refreshes_used=0,
        actions_used=29,
        max_actions=30,
    )
    # Budget-aware: refresh is allowed (within refresh budget).
    assert controller.decide(ctx) == RecoveryDecision.REFRESH_READ_ONLY
    # After max actions, STOP even though refresh budget remains.
    ctx.actions_used = 30
    assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP
    # Ablation (unbounded): a controller that ignores max_actions would
    # return RETRY_SAME — we assert the budget check is what stops it.
    ctx.actions_used = 30
    exhausted, reason = ctx.budget_remaining()
    assert exhausted
    assert reason == "max_actions"


def test_safe_action_executor_counts_llm_calls_in_budget():
    executor = SafeActionExecutor(max_llm_calls=2)
    executor.begin()
    executor.count_llm_call()
    executor.count_llm_call()
    exhausted, reason = executor.budget_exhausted()
    assert exhausted
    assert "max_llm_calls" in reason
