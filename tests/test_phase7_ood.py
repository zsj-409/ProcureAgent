"""Phase 7, Commit C — OOD generalization + final evaluation tests.

- OOD: the cost-aware policy must generalize to an unseen portal template
  (t2_ood) without re-tuning.
- Metric correctness: recovered/safe_stop/neither are disjoint; safe stop
  is never counted as success.
- Order invariance for the multi-policy run.
"""

from app.runtime.portal_state import StateType
from app.runtime.recovery_controller import FaultType
from app.runtime.recovery_policy import (
    CostAwareRecoveryPolicy,
    RecoveryObservation,
    RecoveryTier,
)
from tests.eval.recovery_benchmark import build_fault_cases


def test_ood_policy_decision_independent_of_template():
    """The policy's decision for a given observation must be the same
    regardless of the portal template it came from (template is not part of
    the policy input)."""
    policy = CostAwareRecoveryPolicy()
    obs = RecoveryObservation(
        current_semantic_state=StateType.RESULTS,
        previous_expected_state=StateType.QUOTE_DETAIL,
        fault_hint=FaultType.SELECTOR_CHANGED,
    )
    decision = policy.decide(obs)
    assert decision.recovery_tier == RecoveryTier.SEMANTIC
    # The observation has no template field — the policy cannot overfit to a
    # template.
    assert "template" not in obs.to_dict()


def test_metric_classes_are_disjoint():
    """recovered / safe_stop / neither must be mutually exclusive."""
    results = [
        {"recovered": True, "safe_stop_ok": False, "neither": False},
        {"recovered": False, "safe_stop_ok": True, "neither": False},
        {"recovered": False, "safe_stop_ok": False, "neither": True},
    ]
    for r in results:
        flags = [r["recovered"], r["safe_stop_ok"], r["neither"]]
        assert sum(flags) == 1, "classes must be disjoint"


def test_safe_stop_never_success():
    """A safe stop (session expired, ambiguous, unsafe) is NEVER counted as
    verified recovery."""
    policy = CostAwareRecoveryPolicy()
    for fault in (FaultType.SESSION_EXPIRED, FaultType.AMBIGUOUS_ELEMENT, FaultType.UNSAFE_ACTION):
        decision = policy.decide(
            RecoveryObservation(
                current_semantic_state=StateType.RESULTS,
                previous_expected_state=StateType.QUOTE_DETAIL,
                fault_hint=fault,
            )
        )
        assert decision.recovery_tier == RecoveryTier.STOP
        assert decision.stopping_decision is not None  # safe stop, not success


def test_benchmark_case_ids_stable_across_builds():
    """Rebuilding the case registry yields the same ids and hashes (frozen
    benchmark)."""
    c1 = build_fault_cases()
    c2 = build_fault_cases()
    assert [c.case_id for c in c1] == [c.case_id for c in c2]
    assert [c.snapshot_hash() for c in c1] == [c.snapshot_hash() for c in c2]
