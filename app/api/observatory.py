"""Phase 9 — Experiment Observatory API.

Reads the machine-readable evaluation artifacts committed in docs/eval/
(never re-runs benchmarks) and serves them to the Workbench as five labs:

1. Execution Flight Recorder   — per-task trace (real executed steps)
2. Chaos Recovery Lab          — portal fault injection results (real browser)
3. Approval & Policy Inspector — approval records + policy decisions
4. Procurement Optimization    — greedy vs cheapest vs optimizer comparison
5. Agent Experiment Lab        — recovery policy comparison (Phase 7)

Access control (Demo Acceptance P0):
- flight-recorder and approval-inspector expose LIVE task data (trace,
  approvals) — they require an authenticated principal (default deny when
  auth_enabled; demo header allowlist in isolated demo config). Read-only
  does NOT mean anonymous-readable.
- chaos-lab / optimization-comparison / experiment-lab serve COMMITTED
  experiment artifacts (no live business data) — public read is acceptable,
  but the artifact list still marks each file present/missing honestly.

Every artifact is loaded with its recorded date/commit so the Workbench can
label each result's experiment boundary (DEV/CONFIRMATION/TEST, MOCK vs real
browser, n, and the honest negative results).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..security.approval_guard import ApprovalGuard

router = APIRouter(tags=["observatory"])

EVAL_DIR = Path(__file__).resolve().parents[2] / "docs" / "eval"
CACHE: dict[str, Any] = {}


def _guard(request: Request) -> ApprovalGuard:
    guard = getattr(request.app.state, "approval_guard", None)
    return guard if guard is not None else ApprovalGuard()


def _require_principal(request: Request) -> None:
    """P0: live-data endpoints require an authenticated principal. Default
    deny — an unauthenticated caller gets 401 AUTH_REQUIRED and no data."""

    guard = _guard(request)
    principal = guard.resolve_principal(request)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={
                "reason_code": "AUTH_REQUIRED",
                "detail": "Observatory live-data endpoint requires authentication",
            },
        )


def _load(name: str) -> dict[str, Any] | list[Any] | None:
    """Load a JSON artifact once (cached). CSV artifacts are reported as
    present but not parsed (the endpoints that need them read them
    directly or label them as files)."""

    if name in CACHE:
        return CACHE[name]
    path = EVAL_DIR / name
    if not path.exists():
        return None
    if name.endswith(".csv"):
        # CSV artifacts exist and are served as file references; we mark
        # them present without parsing (keeps the artifact list honest).
        CACHE[name] = {"present": True, "format": "csv", "name": name}
        return CACHE[name]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        CACHE[name] = data
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _artifact_meta(name: str) -> dict[str, Any]:
    return {
        "artifact": name,
        "loaded_at": datetime.now(UTC).isoformat(),
        "source": f"docs/eval/{name}",
    }


@router.get("/observatory/artifacts")
async def list_artifacts(request: Request) -> dict[str, Any]:
    """List every available experiment artifact with a short label."""

    artifacts = {
        "portal_experiment_manifest.json": "Phase 4 portal chaos manifest",
        "portal_recovery_results.json": "Phase 4 portal recovery results",
        "optimization_comparison.csv": "Phase 5 optimizer vs greedy (CSV)",
        "optimization_benchmark.json": "Phase 5 optimizer benchmark",
        "procurement_world_manifest.json": "Phase 6 procurement worlds",
        "procurement_world_registry.json": "Phase 6 world registry",
        "allocation_results.csv": "Phase 6 allocation results",
        "constraint_violations.json": "Phase 6 constraint violations",
        "paired_method_comparison.csv": "Phase 6 paired comparison",
        "paired_cluster_bootstrap.json": "Phase 6 cluster bootstrap (corrected)",
        "solver_performance.json": "Phase 6 solver scaling",
        "failure_cases.json": "Phase 6 failure cases",
        "recovery_policy_pilot.json": "Phase 7 DEV pilot (40 cases)",
        "recovery_policy_results.json": "Phase 7 full evaluation (120 cases)",
    }
    present = {name: _load(name) is not None for name in artifacts}
    return {
        **_artifact_meta("observatory"),
        "artifacts": [{"name": n, "label": artifacts[n], "present": present[n]} for n in artifacts],
    }


@router.get("/observatory/flight-recorder")
async def flight_recorder(request: Request, task_id: str | None = None) -> dict[str, Any]:
    """Execution Flight Recorder: real executed steps per task (from the
    task trace), never synthetic. **Requires authentication** (live task data).

    Object-level authorization: in the current SINGLE_TENANT_DEMO_ONLY model,
    any authenticated principal may read any task (there is only one tenant).
    When a task_id is requested, the task must exist; a non-existent task
    returns 404 WITHOUT revealing anything about other tasks' existence
    (no ID enumeration — same 404 for missing and unauthorized in the future
    multi-tenant model)."""

    _require_principal(request)
    repository = request.app.state.repository
    trace = getattr(request.app.state, "trace", None)
    tasks = repository.list_tasks(limit=50)
    flights = []
    for record in tasks:
        steps: list[dict[str, Any]] = []
        if trace is not None:
            steps = trace.entries_for(record.id) if hasattr(trace, "entries_for") else []
        flights.append(
            {
                "task_id": record.id,
                "status": record.status,
                "current_step": record.current_step,
                "created_at": record.created_at.isoformat(),
                "steps": steps[:50],
            }
        )
    if task_id:
        flights = [f for f in flights if f["task_id"] == task_id]
        if not flights:
            raise HTTPException(status_code=404, detail="Task not found")
    return {
        **_artifact_meta("live-trace"),
        "flights": flights,
        "authz_model": "SINGLE_TENANT_DEMO_ONLY",
        "note": "any authenticated principal may read any task in the single-tenant demo model; "
                "multi-tenant object-level isolation is NOT IMPLEMENTED",
    }


@router.get("/observatory/chaos-lab")
async def chaos_lab(request: Request) -> dict[str, Any]:
    """Chaos Recovery Lab: real-browser portal fault results (Phase 4)."""

    results = _load("portal_recovery_results.json")
    manifest = _load("portal_experiment_manifest.json")
    if results is None:
        raise HTTPException(status_code=404, detail="portal_recovery_results.json missing")
    return {**_artifact_meta("portal_recovery_results.json"), "manifest": manifest, "data": results}


@router.get("/observatory/approval-inspector")
async def approval_inspector(request: Request) -> dict[str, Any]:
    """Approval & Policy Inspector: approval records + policy gates per task.
    **Requires authentication** (live approval data)."""

    _require_principal(request)
    repository = request.app.state.repository
    records = repository.list_tasks(limit=100)
    approvals_by_task = {}
    for record in records:
        approvals = repository.list_approvals(record.id)
        if approvals:
            approvals_by_task[record.id] = [
                {
                    "approver": a.approver,
                    "decision": a.decision,
                    "recommendation_version": a.recommendation_version,
                    "reason_code": a.reason_code,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in approvals
            ]
    return {
        **_artifact_meta("live-approvals"),
        "approval_count": sum(len(v) for v in approvals_by_task.values()),
        "by_task": approvals_by_task,
        "authz_model": "SINGLE_TENANT_DEMO_ONLY",
        "note": "approvals are bound to recommendation versions; stale versions "
                "are detectable by the inspector (Phase 3 CAS + version binding)",
    }


@router.get("/observatory/optimization-comparison")
async def optimization_comparison(request: Request) -> dict[str, Any]:
    """Procurement Optimization Comparison (Phase 5/6 committed results)."""

    benchmark = _load("optimization_benchmark.json")
    paired = _load("paired_method_comparison.csv")
    if benchmark is None:
        raise HTTPException(status_code=404, detail="optimization_benchmark.json missing")
    return {
        **_artifact_meta("optimization_benchmark.json"),
        "data": benchmark,
        "paired_csv_present": paired is not None,
        "honest_boundary": (
            "cost saving is computed ONLY on jointly fully-feasible pairs; "
            "cluster bootstrap CI [0.0, 6.4] — not significant at family level"
        ),
    }


@router.get("/observatory/experiment-lab")
async def experiment_lab(request: Request) -> dict[str, Any]:
    """Agent Experiment Lab: Phase 7 recovery policy comparison."""

    results = _load("recovery_policy_results.json")
    pilot = _load("recovery_policy_pilot.json")
    if results is None:
        raise HTTPException(status_code=404, detail="recovery_policy_results.json missing")
    return {
        **_artifact_meta("recovery_policy_results.json"),
        "data": results,
        "pilot": pilot,
        "honest_boundary": (
            "success saturated (all policies 100% correct goal); cost-aware "
            "policies cut observations 2-4x; REAL_LLM_NOT_RUN"
        ),
    }


# ---------------------------------------------------------------------------
# Phase 10 Commit C — EvalOps release gates (Task 22)
# ---------------------------------------------------------------------------

@router.get("/observatory/evalops/release-gates")
async def evalops_release_gates(request: Request) -> dict[str, Any]:
    """Release Gate evaluation results (Task 21 demo: the unsafe candidate
    is REJECTED; every gate carries evidence)."""

    gates = _load("release_gate_results.json")
    candidates = _load("release_candidate_manifest.json")
    if gates is None:
        raise HTTPException(status_code=404, detail="release_gate_results.json missing")
    return {
        **_artifact_meta("release_gate_results.json"),
        "data": gates,
        "candidates": candidates,
        "honest_boundary": (
            "gates judge the candidate in a CONTROLLED test environment; "
            "this is NOT a production safety proof. The unsafe candidate "
            "(relaxed hard budget) is deliberately constructed for the demo "
            "and never wired into the real business path."
        ),
    }


@router.get("/observatory/evalops/shadow")
async def evalops_shadow(request: Request) -> dict[str, Any]:
    """Shadow evaluation results (isolated snapshots; candidate never
    mutates real state)."""

    shadow = _load("shadow_evaluation_results.json")
    if shadow is None:
        raise HTTPException(status_code=404, detail="shadow_evaluation_results.json missing")
    return {
        **_artifact_meta("shadow_evaluation_results.json"),
        "data": shadow,
        "honest_boundary": (
            "each method runs on an ISOLATED immutable snapshot; the "
            "candidate never writes real task state, approvals, or orders. "
            "Tool calls still go through the secure gateway."
        ),
    }


@router.get("/observatory/evalops/promotion")
async def evalops_promotion(request: Request) -> dict[str, Any]:
    """Promotion / rollback history (Task 19 state machine audit trail)."""

    promotion = _load("promotion_history.json")
    rollback = _load("rollback_history.json")
    return {
        **_artifact_meta("promotion_history.json"),
        "promotion": promotion or [],
        "rollback": rollback or [],
        "honest_boundary": (
            "promotion requires QUALIFIED (no FAIL gates); unsafe candidates "
            "cannot promote. Rollback preserves the previous version binding."
        ),
    }


@router.get("/observatory/evalops/mutation")
async def evalops_mutation(request: Request) -> dict[str, Any]:
    """Phase 11B: real candidate safety regression evidence — baseline PASS
    vs hard-budget / expired-quote / inventory mutants FAIL, each with the
    independent verifier's actual violation reasons."""

    executions = _load("candidate_execution_results.json")
    decisions = _load("release_gate_decisions.json")
    if executions is None:
        raise HTTPException(status_code=404, detail="candidate_execution_results.json missing")
    return {
        **_artifact_meta("candidate_execution_results.json"),
        "executions": executions.get("runs", []),
        "gate_decisions": decisions or {},
        "honest_boundary": (
            "FAIL comes from the independent verifier recomputing every "
            "constraint on the REAL candidate output — never a pre-filled "
            "label. Mutants are test-only fixtures; production policy is "
            "untouched."
        ),
    }


@router.get("/observatory/evalops/e2e")
async def evalops_e2e(request: Request) -> dict[str, Any]:
    """Phase 11B end-to-end security regression chain: one manifest from
    real candidate execution → verifier → gate REJECT → promotion blocked
    → active version unchanged, with OTel span outcomes."""

    manifest = _load("phase11b_execution_manifest.json")
    if manifest is None:
        raise HTTPException(status_code=404, detail="phase11b_execution_manifest.json missing")
    return {
        **_artifact_meta("phase11b_execution_manifest.json"),
        "data": manifest,
        "honest_boundary": (
            "one shared manifest links every id (candidate, input hash, "
            "world hash, gate decision, audit, OTel spans) — no hand-assembled "
            "PASS/FAIL JSONs. Controlled experiment, not a production safety "
            "proof."
        ),
    }
