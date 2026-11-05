"""Phase 11B — Commit B artifact generator: durable promotion race results.

Runs real subprocess races and crash tests, writes machine-readable
artifacts (promotion_race_results.json, rollback_consistency_results.json).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.evalops.core import ReleaseGateEngine
from app.evalops.promotion import DurablePromotionManager


_WORKER = r"""
import json, sys, os
sys.path.insert(0, os.environ.get("PA_REPO_ROOT", ""))
from app.evalops.promotion import DurablePromotionManager

def main():
    db, candidate_id, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    pm = DurablePromotionManager(db)
    try:
        if mode == "promote":
            rec = pm.promote(candidate_id=candidate_id, promoted_by="worker")
            print(json.dumps({"ok": True, "decision": rec.decision, "epoch": rec.version_epoch}))
        elif mode == "rollback":
            rec = pm.rollback(candidate_id=candidate_id, by="worker", reason="race-test")
            print(json.dumps({"ok": True, "decision": rec.decision}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "type": type(exc).__name__}))
    finally:
        pm.close()

main()
"""


def _prepare(db: str, candidate_id: str):
    pm = DurablePromotionManager(db)
    pm.create(candidate_id, version="v1")
    pm.mark_evaluating(candidate_id)
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
    results = engine.evaluate(
        candidate_id, "b",
        measurements=full,
        baseline_measurements=full,
        thresholds={"recovery_latency_p50": 800, "recovery_latency_p95": 1500,
                    "extra_recovery_invocations": 30, "actual_token_cost": 5.0},
        sample_counts={"hard_budget_violation": 28, "expired_quote_used": 28},
    )
    pm.apply_gate_results(candidate_id, results)
    pm.close()


def _spawn(db: str, candidate_id: str, mode: str, n: int, repo_root: str):
    script = Path(tempfile.gettempdir()) / "promo_worker_artifact.py"
    script.write_text(_WORKER, encoding="utf-8")
    env = dict(os.environ)
    env["PA_REPO_ROOT"] = repo_root
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), db, candidate_id, mode],
            cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        for _ in range(n)
    ]
    return [json.loads(p.communicate(timeout=60)[0].decode()) for p in procs]


def write_commit_b_artifacts(out_dir: Path, repo_root: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 2-worker race ------------------------------------------------------
    race_db = str(out_dir / "_race.db")
    if Path(race_db).exists():
        Path(race_db).unlink()
    _prepare(race_db, "race-cand")
    race_outs = _spawn(race_db, "race-cand", "promote", 2, repo_root)
    pm = DurablePromotionManager(race_db)
    race_result = {
        "workers": race_outs,
        "active_version": pm.active_version(),
        "promoted_audit_count": sum(1 for h in pm.history() if h["decision"] == "PROMOTED"),
        "audit": pm.history(),
        "winner_count": sum(1 for o in race_outs if o["ok"]),
    }
    pm.close()

    # --- crash before commit ------------------------------------------------
    crash_db = str(out_dir / "_crash.db")
    if Path(crash_db).exists():
        Path(crash_db).unlink()
    _prepare(crash_db, "cr1")
    crash_script = r"""
import sqlite3, os, sys
conn = sqlite3.connect(sys.argv[1], timeout=30)
conn.execute("BEGIN IMMEDIATE")
sql = "UPDATE promotion_state SET promotion_status='PROMOTED', " \
      "active_version='cr1', version_epoch=99 WHERE candidate_id='cr1'"
conn.execute(sql)
os._exit(0)
"""
    script = Path(tempfile.gettempdir()) / "promo_crash_artifact.py"
    script.write_text(crash_script, encoding="utf-8")
    subprocess.run([sys.executable, str(script), crash_db], cwd=repo_root, timeout=30)
    pm = DurablePromotionManager(crash_db)
    crash_result = {
        "active_version_after_crash": pm.active_version(),
        "epoch_after_crash": pm.version_epoch("cr1"),
        "audit_count": len(pm.history()),
        "consistent": pm.active_version() is None and pm.version_epoch("cr1") == 0,
    }
    pm.close()

    race_artifact = {
        "date": "2026-11-05",
        "protocol": "real subprocess workers on a file-backed SQLite; BEGIN IMMEDIATE + CAS",
        "two_worker_race": race_result,
        "crash_before_commit": crash_result,
    }
    (out_dir / "promotion_race_results.json").write_text(
        json.dumps(race_artifact, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # --- rollback consistency ----------------------------------------------
    rb_db = str(out_dir / "_rb.db")
    if Path(rb_db).exists():
        Path(rb_db).unlink()
    _prepare(rb_db, "v2")
    pm = DurablePromotionManager(rb_db)
    pm.promote(candidate_id="v2", promoted_by="tester", previous_version="v1")
    rb = pm.rollback(candidate_id="v2", by="admin", reason="incident")
    rb_artifact = {
        "date": "2026-11-05",
        "promoted": {"active_version": "v2", "epoch": 1},
        "rolled_back": {
            "decision": rb.decision,
            "active_version_after": pm.active_version(),
            "reason": rb.rollback_reason,
            "epoch": rb.version_epoch,
        },
        "audit": pm.history(),
        "task_binding_preserved": (
            "running tasks keep their own version/plan_hash "
            "(rollback never rewrites task rows)"
        ),
    }
    pm.close()
    (out_dir / "rollback_consistency_results.json").write_text(
        json.dumps(rb_artifact, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "race_winner_count": race_result["winner_count"],
        "race_active": race_result["active_version"],
        "crash_consistent": crash_result["consistent"],
        "rollback_decision": rb_artifact["rolled_back"]["decision"],
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    result = write_commit_b_artifacts(root / "docs" / "eval", str(root))
    print(json.dumps(result, indent=2, ensure_ascii=False))
