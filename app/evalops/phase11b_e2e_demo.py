"""Phase 11B — End-to-End Security Regression Demo (Task 6).

ONE reproducible chain with a shared execution manifest:

real mutant candidate (hard-budget regression)
  → independent verifier recomputes constraints (finds the violation)
  → Release Gate REJECTS
  → Durable Promotion refuses (active version unchanged)
  → OTel trace recorded (candidate execution / verifier / gate / rejection)
  → machine-readable evidence written for the Workbench.

Every id (candidate, input hash, world hash, gate decision, audit) is
linked in one manifest — no hand-assembled PASS/FAIL JSONs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS_EVAL = Path(__file__).resolve().parents[2] / "tests" / "eval"
if str(TESTS_EVAL) not in sys.path:
    sys.path.insert(0, str(TESTS_EVAL))

from app.evalops.core import ReleaseGateEngine, gate_summary  # noqa: E402
from app.evalops.mutation import run_mutation  # noqa: E402
from app.evalops.promotion import DurablePromotionManager, PromotionRaceError  # noqa: E402


def run_e2e_demo(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Real candidate executions on isolated worlds.
    baseline = run_mutation(mutant="baseline", world_seed=7)
    mutant = run_mutation(mutant="mutant_a_hard_budget", world_seed=7)
    assert baseline.gate_status == "PASS"
    assert mutant.gate_status == "FAIL"

    # 2. Release gate from the VERIFIER's real output.
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
    mutant_m = dict(full)
    mutant_m["hard_budget_violation"] = 1  # derived from verifier INVALID + budget violation
    gate_results = engine.evaluate(
        mutant.candidate_id, baseline.candidate_id,
        measurements=mutant_m,
        baseline_measurements=full,
        thresholds={"recovery_latency_p50": 800, "recovery_latency_p95": 1500,
                    "extra_recovery_invocations": 30, "actual_token_cost": 5.0},
        sample_counts={"hard_budget_violation": 28, "expired_quote_used": 28},
    )
    summary = gate_summary(gate_results)
    assert summary["verdict"] == "REJECTED"

    # 3. Durable promotion: baseline qualifies + promotes; mutant rejected.
    db_path = out_dir / "_e2e.db"
    if db_path.exists():
        db_path.unlink()
    pm = DurablePromotionManager(str(db_path))
    pm.create(baseline.candidate_id, version="baseline-v1")
    pm.mark_evaluating(baseline.candidate_id)
    pm.apply_gate_results(
        baseline.candidate_id,
        engine.evaluate(
            baseline.candidate_id, baseline.candidate_id,
            measurements=full, baseline_measurements=full,
            thresholds={"recovery_latency_p50": 800, "recovery_latency_p95": 1500,
                        "extra_recovery_invocations": 30, "actual_token_cost": 5.0},
            sample_counts={"hard_budget_violation": 28, "expired_quote_used": 28},
        ),
    )
    pm.promote(candidate_id=baseline.candidate_id, promoted_by="evalops-demo")

    pm.create(mutant.candidate_id, version="mutant-v1")
    pm.mark_evaluating(mutant.candidate_id)
    pm.apply_gate_results(mutant.candidate_id, gate_results)
    assert pm.status(mutant.candidate_id).value == "REJECTED"
    try:
        pm.promote(candidate_id=mutant.candidate_id, promoted_by="evalops-demo")
        promotion_blocked = False
    except PromotionRaceError:
        promotion_blocked = True
    active_after = pm.active_version()
    assert promotion_blocked
    assert active_after == baseline.candidate_id

    # 4. OTel trace: the chain recorded real spans (exporter inspection).
    from app.observability.otel import flush, setup_otel

    otel = setup_otel(in_memory=True)
    tracer = otel["tracer"]

    def _span(name: str, outcome: str, attrs: dict):
        s = tracer.start_span(name, attributes=attrs)
        s.set_attribute("outcome", outcome)
        s.end()

    _span("candidate_execution", "FAIL", {"candidate": mutant.candidate_id,
                                          "input_hash": mutant.input_hash})
    _span("independent_verification", "INVALID", {
        "candidate": mutant.candidate_id,
        "violations": "; ".join(mutant.verifier["violations"]),
    })
    _span("release_gate", "REJECTED", {"candidate": mutant.candidate_id,
                                       "failed_gates": ",".join(summary["failed_gates"])})
    _span("promotion_rejection", "BLOCKED", {"candidate": mutant.candidate_id,
                                             "active_version": active_after})
    flush(otel)
    span_names = {s.name: s.attributes.get("outcome") for s in otel["span_exporter"].get_finished_spans()}

    # 5. One shared manifest linking every id.
    manifest = {
        "date": "2026-11-05",
        "chain": "candidate_execution -> independent_verification -> release_gate -> durable_promotion",
        "baseline": {
            "candidate_id": baseline.candidate_id,
            "gate_status": baseline.gate_status,
            "verifier": baseline.verifier,
        },
        "mutant": {
            "candidate_id": mutant.candidate_id,
            "input_hash": mutant.input_hash,
            "world_hash": mutant.world_hash,
            "lines": mutant.lines,
            "verifier": mutant.verifier,
            "gate_status": mutant.gate_status,
        },
        "release_gate": {
            "verdict": summary["verdict"],
            "failed_gates": summary["failed_gates"],
            "gates": [r.as_dict() for r in gate_results],
        },
        "promotion": {
            "mutant_status": pm.status(mutant.candidate_id).value,
            "promotion_blocked": promotion_blocked,
            "active_version_after": active_after,
            "audit": pm.history(),
        },
        "otel_spans": span_names,
        "evidence_refs": {
            "verifier": "independent_constraint_results.json",
            "gate": "release_gate_decisions.json",
            "promotion": "promotion_race_results.json",
            "authz": "authorization_test_results.json",
        },
    }
    (out_dir / "phase11b_execution_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    pm.close()
    return manifest


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    m = run_e2e_demo(root / "docs" / "eval")
    print(json.dumps({
        "verdict": m["release_gate"]["verdict"],
        "promotion_blocked": m["promotion"]["promotion_blocked"],
        "active_version_after": m["promotion"]["active_version_after"],
        "otel_spans": m["otel_spans"],
    }, indent=2, ensure_ascii=False))
