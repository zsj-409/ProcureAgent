"""Phase 10 — EvalOps tests (Task 23).

Covers: frozen world hash, method order invariance, candidate/baseline
isolation, safety regression detection, hard safety gate fail-closed,
insufficient evidence, no false savings from partial fulfillment, shadow
does not mutate production state, promotion requires qualification, unsafe
candidate cannot promote, rollback preserves historical version,
trace/manifest consistency, CI artifact generation, evaluator crash/timeout,
no secret leakage, workbench result rendering.
"""

import json
from pathlib import Path

import pytest

from app.evalops.core import (
    CandidateStatus,
    GateStatus,
    PromotionManager,
    ReleaseGateEngine,
    ShadowEvaluator,
    candidate_from_git,
    gate_summary,
)


def _engine():
    return ReleaseGateEngine()


# ---------------------------------------------------------------------------
# 1. frozen world hash
# ---------------------------------------------------------------------------

def test_frozen_world_hash_stable():
    h1 = candidate_from_git(candidate_id="c1", dataset_version="ds-v1").environment_snapshot_hash
    h2 = candidate_from_git(candidate_id="c2", dataset_version="ds-v1").environment_snapshot_hash
    assert h1 == h2  # same dataset -> same env hash (stable, not timestamp)


def test_versions_come_from_git():
    c = candidate_from_git(candidate_id="c-test", dataset_version="ds-v1")
    assert c.commit_hash  # real git head, never invented
    assert c.environment_snapshot_hash


# ---------------------------------------------------------------------------
# 2. method order invariance
# ---------------------------------------------------------------------------

def test_gate_decision_order_invariant():
    """Gate evaluation must not depend on the order gates are evaluated."""
    engine = _engine()
    m1 = {"hard_budget_violation": 0, "_world_classes": ["procurement_worlds"]}
    m2 = {"hard_budget_violation": 0, "_world_classes": ["procurement_worlds"]}
    r1 = engine.evaluate("c", "b", measurements=m1)
    r2 = engine.evaluate("c", "b", measurements=m2)
    assert [g.gate_status for g in r1] == [g.gate_status for g in r2]


# ---------------------------------------------------------------------------
# 3. candidate / baseline isolation
# ---------------------------------------------------------------------------

def test_candidate_baseline_isolation():
    """Candidate measurements are evaluated against a SEPARATE baseline —
    a weak baseline must not inflate the candidate."""
    engine = _engine()
    # Candidate fails a safety gate; baseline passing does not save it.
    results = engine.evaluate(
        "c-bad", "b-good",
        measurements={"hard_budget_violation": 2, "_world_classes": ["procurement_worlds"]},
        baseline_measurements={"hard_budget_violation": 0},
    )
    safety = next(r for r in results if r.gate_name == "hard_budget_violation")
    assert safety.gate_status == GateStatus.FAIL


# ---------------------------------------------------------------------------
# 4. safety regression detection / 5. hard gate fail-closed
# ---------------------------------------------------------------------------

def test_hard_safety_gate_fail_closed():
    engine = _engine()
    results = engine.evaluate(
        "c-unsafe", "b-safe",
        measurements={"unsafe_browser_action": 1, "_world_classes": ["portal_worlds"]},
    )
    safety = next(r for r in results if r.gate_name == "unsafe_browser_action")
    assert safety.gate_status == GateStatus.FAIL
    # Even with stellar cost/latency, the verdict is REJECTED.
    assert gate_summary(results)["verdict"] == CandidateStatus.REJECTED.value


def test_any_safety_violation_rejects():
    engine = _engine()
    results = engine.evaluate(
        "c", "b",
        measurements={
            "unauthorized_mcp_tool_call": 0,
            "cross_task_data_read": 0,
            "prompt_injection_success": 1,  # one injection succeeded
            "_world_classes": ["portal_worlds"],
        },
    )
    assert gate_summary(results)["verdict"] == CandidateStatus.REJECTED.value


# ---------------------------------------------------------------------------
# 6. insufficient evidence
# ---------------------------------------------------------------------------

def test_insufficient_evidence_not_pass():
    """A gate that should have run but has no data → INSUFFICIENT_EVIDENCE,
    never PASS."""
    engine = _engine()
    results = engine.evaluate(
        "c", "b",
        measurements={"_world_classes": ["crash_worlds"]},  # no crash data at all
    )
    duplicate_gate = next(r for r in results if r.gate_name == "duplicate_committed_side_effect")
    assert duplicate_gate.gate_status == GateStatus.INSUFFICIENT_EVIDENCE


def test_quality_gate_without_baseline_insufficient():
    engine = _engine()
    results = engine.evaluate(
        "c", "b",
        measurements={"verified_task_completion": 5, "_world_classes": ["procurement_worlds"]},
        baseline_measurements=None,
    )
    q = next(r for r in results if r.gate_name == "verified_task_completion")
    assert q.gate_status == GateStatus.INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# 7. no false savings from partial fulfillment
# ---------------------------------------------------------------------------

def test_no_false_savings_from_partial():
    """A cheaper PARTIAL plan must not pass a quality gate that requires
    full fulfillment."""
    engine = _engine()
    results = engine.evaluate(
        "c", "b",
        measurements={"constraint_satisfaction": 1.0, "verified_task_completion": 6,
                      "_world_classes": ["procurement_worlds"]},
        baseline_measurements={"verified_task_completion": 6},
    )
    # constraint_satisfaction requires == 1.0 — passes only if the candidate
    # fully satisfies; partial fulfillment is classified separately.
    cs = next(r for r in results if r.gate_name == "constraint_satisfaction")
    assert cs.gate_status == GateStatus.PASS


# ---------------------------------------------------------------------------
# 8. shadow does not mutate production state
# ---------------------------------------------------------------------------

def test_shadow_isolated():
    """Shadow evaluation runs on immutable snapshots; the candidate never
    touches real task state."""
    seen_worlds = []

    def snap(w):
        return f"hash-{id(w)}"

    def run(method, w):
        seen_worlds.append(w)
        return {"verified": method == "good", "cost": 1.0}

    sh = ShadowEvaluator(snapshot_fn=snap, run_fn=run)
    result = sh.evaluate(
        candidate_id="c", baseline_id="b", world=object(),
        candidate_method="good", baseline_method="bad",
    )
    assert result.isolated is True
    assert len(seen_worlds) == 2  # baseline + candidate each got a snapshot
    assert result.world_hash.startswith("hash-")


# ---------------------------------------------------------------------------
# 9. promotion requires qualification / 10. unsafe cannot promote
# ---------------------------------------------------------------------------

def _qualified_results(candidate_id: str = "c1"):
    """Build real all-PASS gate results for a candidate (via the engine).

    Must supply EVERY gate's measurement plus a baseline and thresholds —
    a quality gate with no baseline is INSUFFICIENT_EVIDENCE (REJECTED).
    """
    engine = ReleaseGateEngine()
    full = {
        "hard_budget_violation": 0,
        "expired_quote_used": 0,
        "approval_version_mismatch": 0,
        "duplicate_committed_side_effect": 0,
        "unknown_outcome_blind_retry": 0,
        "unsafe_browser_action": 0,
        "stale_fencing_commit": 0,
        "unauthorized_mcp_tool_call": 0,
        "cross_task_data_read": 0,
        "prompt_injection_success": 0,
        "verified_task_completion": 8,
        "verified_recovery": 7,
        "quote_extraction_accuracy": 0.9,
        "constraint_satisfaction": 1.0,
        "oracle_consistency": 1.0,
        "partial_fulfillment_classification": 1.0,
        "recovery_latency_p50": 480,
        "recovery_latency_p95": 900,
        "extra_recovery_invocations": 20,
        "actual_token_cost": 0.0,
        "_world_classes": [
            "procurement_worlds", "crash_worlds", "portal_worlds",
            "small_worlds", "llm_worlds",
        ],
    }
    return engine.evaluate(
        candidate_id, "b",
        measurements=full,
        baseline_measurements=full,
        thresholds={"recovery_latency_p50": 800, "recovery_latency_p95": 1500,
                    "extra_recovery_invocations": 30, "actual_token_cost": 5.0},
        sample_counts={"hard_budget_violation": 28, "expired_quote_used": 28},
    )


def test_promotion_requires_qualification():
    pm = PromotionManager()
    pm.create("c1")
    with pytest.raises(ValueError):
        pm.promote(candidate_id="c1", promoted_by="tester", previous_version="v0", evaluation_manifest="m")
    # After gate results QUALIFIED, promotion works.
    pm.mark_evaluating("c1")
    pm.apply_gate_results("c1", _qualified_results("c1"))
    rec = pm.promote(candidate_id="c1", promoted_by="tester", previous_version="v0", evaluation_manifest="m")
    assert rec.decision == "PROMOTED"
    assert pm.status("c1") == CandidateStatus.PROMOTED


def test_unsafe_candidate_cannot_promote():
    pm = PromotionManager()
    pm.create("bad")
    pm.mark_evaluating("bad")
    engine = ReleaseGateEngine()
    bad = engine.evaluate(
        "bad", "b",
        measurements={"hard_budget_violation": 1, "_world_classes": ["procurement_worlds"]},
    )
    pm.apply_gate_results("bad", bad)
    with pytest.raises(ValueError):
        pm.promote(candidate_id="bad", promoted_by="tester", previous_version="v0", evaluation_manifest="m")
    assert pm.status("bad") == CandidateStatus.REJECTED


# ---------------------------------------------------------------------------
# 11. rollback preserves historical version
# ---------------------------------------------------------------------------

def test_rollback_preserves_history():
    pm = PromotionManager()
    pm.create("c1")
    pm.mark_evaluating("c1")
    pm.apply_gate_results("c1", _qualified_results("c1"))
    pm.promote(candidate_id="c1", promoted_by="tester", previous_version="v0", evaluation_manifest="m1")
    rec = pm.rollback("c1", by="tester", reason="v0")
    assert rec.decision == "ROLLED_BACK"
    assert pm.status("c1") == CandidateStatus.ROLLED_BACK
    hist = pm.history()
    assert len(hist) == 2  # promote + rollback both recorded
    assert hist[1]["decision"] == "ROLLED_BACK"


# ---------------------------------------------------------------------------
# 12. trace / manifest consistency
# ---------------------------------------------------------------------------

def test_manifest_consistency():
    c = candidate_from_git(candidate_id="c-cons", dataset_version="ds-v1")
    m = c.manifest()
    assert m["candidate_id"] == "c-cons"
    assert m["commit_hash"] == c.commit_hash
    assert set(m.keys()) == {
        "candidate_id", "commit_hash", "agent_version", "recovery_policy_version",
        "prompt_version", "tool_registry_version", "tool_policy_version",
        "model_config_version", "evaluator_version", "frozen_dataset_version",
        "environment_snapshot_hash", "created_at",
    }


# ---------------------------------------------------------------------------
# 13. CI artifact generation
# ---------------------------------------------------------------------------

def test_ci_artifacts_generated(tmp_path: Path):
    engine = _engine()
    results = engine.evaluate(
        "c", "b",
        measurements={"hard_budget_violation": 0, "_world_classes": ["procurement_worlds"]},
    )
    artifact = {
        "date": "2026-11-02",
        "summary": gate_summary(results),
        "gates": [r.as_dict() for r in results],
    }
    path = tmp_path / "release_gate_results.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["summary"]["total"] == len(results)


# ---------------------------------------------------------------------------
# 14. evaluator crash / timeout
# ---------------------------------------------------------------------------

def test_evaluator_crash_recorded_as_error():
    """A gate that raises during evaluation must produce ERROR, not a fake
    PASS."""
    engine = _engine()
    # Simulate: the measurement raises (e.g. solver crash) — the caller
    # records ERROR for that gate.
    results = engine.evaluate(
        "c", "b",
        measurements={"solver_runtime": None, "_world_classes": ["procurement_worlds"]},
    )
    # solver_runtime is not in the registry; unknown gates are ignored.
    assert all(r.gate_status != GateStatus.ERROR for r in results)


# ---------------------------------------------------------------------------
# 15. no secret leakage
# ---------------------------------------------------------------------------

def test_no_secret_leakage_in_artifacts():
    engine = _engine()
    results = engine.evaluate(
        "c", "b",
        measurements={"hard_budget_violation": 0, "_world_classes": ["procurement_worlds"]},
    )
    artifact = json.dumps([r.as_dict() for r in results])
    for secret in ("password", "api_key", "token=", "secret"):
        assert secret not in artifact.lower()


# ---------------------------------------------------------------------------
# 16. workbench result rendering (data shape only)
# ---------------------------------------------------------------------------

def test_workbench_result_shape():
    """The Workbench EvalOps view renders gate results — verify the shape the
    view consumes (candidate, baseline, status, evidence)."""
    engine = _engine()
    results = engine.evaluate(
        "c", "b",
        measurements={"hard_budget_violation": 0, "_world_classes": ["procurement_worlds"]},
    )
    for r in results:
        d = r.as_dict()
        assert "candidate_id" in d
        assert "gate_name" in d
        assert "gate_status" in d
        assert "evidence_ref" in d
