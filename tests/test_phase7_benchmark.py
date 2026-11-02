"""Phase 7, Commit A — discriminative fault benchmark tests."""

from app.runtime.portal_state import StateType
from app.runtime.recovery_controller import FaultType
from app.runtime.recovery_policy import (
    CostAwareRecoveryPolicy,
    RecoveryObservation,
    RecoveryTier,
)
from tests.eval.recovery_benchmark import FAMILY_GOALS, build_fault_cases


def test_fault_benchmark_covers_12_families():
    cases = build_fault_cases()
    families = {c.family for c in cases}
    assert len(families) == 12
    for family in FAMILY_GOALS:
        assert family in families


def test_three_difficulty_levels_per_family():
    cases = build_fault_cases()
    by_family = {}
    for c in cases:
        by_family.setdefault(c.family, set()).add(c.difficulty)
    for family, diffs in by_family.items():
        assert {"EASY", "MEDIUM", "HARD"} == diffs, family


def test_case_ids_unique_and_hashed():
    cases = build_fault_cases()
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))
    hashes = {c.snapshot_hash() for c in cases}
    assert len(hashes) == len(cases)


def test_splits_are_disjoint():
    cases = build_fault_cases()
    dev = {c.family for c in cases if c.split == "DEV"}
    conf = {c.family for c in cases if c.split == "CONFIRMATION"}
    test = {c.family for c in cases if c.split == "TEST"}
    assert dev.isdisjoint(conf) and conf.isdisjoint(test) and dev.isdisjoint(test)


def test_goal_and_fault_consistent():
    for _family, spec in FAMILY_GOALS.items():
        assert spec["fault"] in FaultType
        assert spec["goal"] in ("recover_quote", "safe_stop")
        assert spec["goal_state"] in StateType.__members__


def test_policy_receives_no_hidden_fault():
    """The policy must derive its fault hint from observable signals, not
    the injection label. We verify that a policy decision does not depend on
    the hidden fault field by checking the observation carries only
    runtime-observable fields."""
    obs = RecoveryObservation(
        current_semantic_state=StateType.RESULTS,
        previous_expected_state=StateType.QUOTE_DETAIL,
    )
    d = obs.to_dict()
    assert "fault" not in d  # hidden label never serialized
    assert "fault_hint" in d  # derived hint only


def test_cost_aware_policy_discriminates_faults():
    """Different faults must produce different tiers — the benchmark is
    discriminative at the policy level."""
    policy = CostAwareRecoveryPolicy(enable_llm=False)
    sel = policy.decide(
        RecoveryObservation(
            current_semantic_state=StateType.RESULTS,
            previous_expected_state=StateType.QUOTE_DETAIL,
            fault_hint=FaultType.SELECTOR_CHANGED,
        )
    )
    dyn = policy.decide(
        RecoveryObservation(
            current_semantic_state=StateType.RESULTS,
            previous_expected_state=StateType.QUOTE_DETAIL,
            fault_hint=FaultType.DYNAMIC_DOM_UPDATE,
        )
    )
    assert sel.recovery_tier != dyn.recovery_tier
    assert sel.recovery_tier == RecoveryTier.SEMANTIC
    assert dyn.recovery_tier == RecoveryTier.REFRESH
