"""Phase 11A — correctness fixes for EvalOps (regression tests first).

Proves three defects in app/evalops/core.py before fixing:

1. gate_summary treats INSUFFICIENT_EVIDENCE as PASS: a candidate whose
   gates all returned INSUFFICIENT_EVIDENCE (no data, no baseline) gets
   verdict QUALIFIED and can promote. The rule is: INSUFFICIENT_EVIDENCE
   is NOT PASS — it must block promotion.

2. PromotionManager.apply_gate_verdict trusts a caller-supplied verdict
   string. A forged {"verdict": "QUALIFIED"} bypasses the gate engine and
   lets an unevaluated candidate promote. The manager must recompute the
   verdict from the actual gate results.

3. The unsafe-candidate demo writes release_candidate_manifest.json as a
   nested {"unsafe": ..., "safe": ...} dict while ReleaseCandidate.
   manifest() is flat — a machine-readable contract mismatch that breaks
   CI readers expecting the flat shape.
"""

import pytest

from app.evalops.core import (
    CandidateStatus,
    GateStatus,
    PromotionManager,
    ReleaseGateEngine,
    gate_summary,
)

# ---------------------------------------------------------------------------
# 1. INSUFFICIENT_EVIDENCE must not be treated as PASS
# ---------------------------------------------------------------------------

def test_insufficient_evidence_blocks_promotion():
    """A candidate with only INSUFFICIENT_EVIDENCE gates must NOT get
    verdict QUALIFIED (it has no evidence it is safe)."""
    engine = ReleaseGateEngine()
    results = engine.evaluate(
        "c", "b",
        measurements={"_world_classes": []},  # no worlds, no data at all
    )
    assert all(r.gate_status in (GateStatus.INSUFFICIENT_EVIDENCE, GateStatus.NOT_APPLICABLE)
               for r in results)
    summary = gate_summary(results)
    assert summary["verdict"] != CandidateStatus.QUALIFIED.value, (
        "INSUFFICIENT_EVIDENCE must never yield QUALIFIED"
    )


def test_all_insufficient_cannot_promote():
    """PromotionManager must refuse to promote when the gate evidence is
    insufficient, even though no gate FAILed."""
    pm = PromotionManager()
    pm.create("c")
    pm.mark_evaluating("c")
    # Gate engine produced only INSUFFICIENT_EVIDENCE.
    engine = ReleaseGateEngine()
    results = engine.evaluate("c", "b", measurements={"_world_classes": []})
    pm.apply_gate_results("c", results)
    assert pm.status("c") == CandidateStatus.REJECTED


# ---------------------------------------------------------------------------
# 2. apply_gate_verdict must not trust a caller-supplied verdict string
# ---------------------------------------------------------------------------

def test_forged_qualified_verdict_rejected():
    """A caller must NOT be able to force QUALIFIED by passing a forged
    verdict dict — the manager recomputes from gate results."""
    pm = PromotionManager()
    pm.create("c")
    pm.mark_evaluating("c")
    # Forged claim of QUALIFIED with no gate results at all.
    pm.apply_gate_results("c", [])
    assert pm.status("c") != CandidateStatus.QUALIFIED, (
        "empty gate results must not qualify a candidate"
    )


def test_apply_gate_results_recomputes_verdict():
    """apply_gate_results derives the verdict from the actual gate results:
    one FAIL → REJECTED; all PASS → QUALIFIED."""
    pm = PromotionManager()
    pm.create("c")
    pm.mark_evaluating("c")
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
    ok = engine.evaluate(
        "c", "b",
        measurements=full,
        baseline_measurements=full,
        thresholds={"recovery_latency_p50": 800, "recovery_latency_p95": 1500,
                    "extra_recovery_invocations": 30, "actual_token_cost": 5.0},
        sample_counts={"hard_budget_violation": 28, "expired_quote_used": 28},
    )
    pm.apply_gate_results("c", ok)
    assert pm.status("c") == CandidateStatus.QUALIFIED

    pm2 = PromotionManager()
    pm2.create("bad")
    pm2.mark_evaluating("bad")
    bad = engine.evaluate(
        "bad", "b",
        measurements={"hard_budget_violation": 1, "_world_classes": ["procurement_worlds"]},
    )
    pm2.apply_gate_results("bad", bad)
    assert pm2.status("bad") == CandidateStatus.REJECTED


def test_legacy_apply_gate_verdict_removed():
    """The trust-string method must be gone (or non-functional)."""
    pm = PromotionManager()
    assert not hasattr(pm, "apply_gate_verdict"), (
        "apply_gate_verdict (trust-string) must be removed"
    )


# ---------------------------------------------------------------------------
# 3. manifest shape consistency
# ---------------------------------------------------------------------------

def test_unsafe_demo_manifest_flat():
    """The unsafe-candidate demo writes release_candidate_manifest.json
    where each candidate entry is a flat manifest() dict (the same shape
    ReleaseCandidate.manifest() returns), NOT a nested {"unsafe": ...,
    "safe": ...} wrapper."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "docs" / "eval" / "release_candidate_manifest.json"
    if not path.exists():
        pytest.skip("artifact not present in this checkout")
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "candidates" in data, "manifest must carry a candidates array"
    assert "roles" in data, "manifest must label each candidate's role"
    for c in data["candidates"]:
        assert "candidate_id" in c, "each entry is the flat ReleaseCandidate.manifest() shape"
        assert "commit_hash" in c
    assert "unsafe" not in data, "nested wrapper shape is a contract mismatch"
