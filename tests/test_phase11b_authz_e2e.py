"""Phase 11B — Commit C: object-level authorization + end-to-end security
regression.

Proves:
- Two principals with different task ownership: A reads A's task, A CANNOT
  read B's task (TASK_FORBIDDEN) — via the REAL repository-backed
  task_owner (no hard-coded demo owner).
- Worker resume does not escalate identity (no request-body role, no demo
  header as a long-lived background identity).
- The full E2E chain: real mutant candidate execution → independent
  verifier → release gate REJECT → promotion blocked → active version
  unchanged — all linked by one manifest with traceable ids.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.infrastructure.settings import Settings
from app.main import build_components
from app.mcp_gateway.policy import ToolPolicyError, build_gateway
from app.security.principal import Principal, Role


def _settings(tmp_path) -> Settings:
    cfg = tmp_path / "suppliers.json"
    cfg.write_text(
        json.dumps(
            {
                "suppliers": [
                    {
                        "supplier_id": "mock_api_supplier",
                        "display_name": "Mock API",
                        "source_type": "api",
                        "base_url": "http://127.0.0.1:1",
                        "search_endpoint": "/products/search",
                        "enabled": True,
                        "priority": 10,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        suppliers_config_path=str(cfg),
        approval_threshold=Decimal(100),
        auth_enabled=False,
        demo_identity_header="X-Demo-Actor",
        chrome_cdp_url="",
        web_allow_launch=False,
        otel_enabled=True,
        otel_export_enabled=False,
    )


def _gateway_with_repo(repo, principal, registry=None):
    """Gateway with the REAL handlers wired (server-layer equivalent)."""
    from app.mcp_gateway.handlers import make_handlers

    gateway = build_gateway(
        identity_resolver=lambda ctx: principal,
        task_owner=lambda tid: repo.get_owner(tid),
    )
    handlers = make_handlers(registry=registry, repository=repo)
    for name, handler in handlers.items():
        gateway.register_handler(name, handler)
    return gateway


# ---------------------------------------------------------------------------
# 1. Object-level authorization (real repository-backed owner)
# ---------------------------------------------------------------------------

def test_principal_reads_own_task_only(tmp_path):
    """Principal A can read A's task; A CANNOT read B's task. The owner is
    the REAL repository owner_id, not a hard-coded demo owner."""
    from app.procurement.schemas import ProcurementRequest

    components = build_components(_settings(tmp_path))
    repo = components["repository"]
    # A owns task-a; B owns task-b.
    repo.create("task-a", ProcurementRequest(product_name="X", quantity=1), owner_id="alice")
    repo.create("task-b", ProcurementRequest(product_name="Y", quantity=1), owner_id="bob")

    alice = Principal(
        id="alice", role=Role.PROCUREMENT_OPERATOR, tenant="t1",
        metadata={"scopes": ["task:read", "procurement:read"]},
    )

    gw = _gateway_with_repo(repo, alice, registry=components["registry"])
    # Alice reads her own task → allowed.
    result = gw.execute("get_task_execution_status", {"task_id": "task-a"})
    assert result is not None
    assert result["task_id"] == "task-a"
    # Alice reads Bob's task → TASK_FORBIDDEN.
    with pytest.raises(ToolPolicyError) as ei:
        gw.execute("get_task_execution_status", {"task_id": "task-b"})
    assert ei.value.reason_code == "TASK_FORBIDDEN"


def test_unknown_owner_denied_fail_closed(tmp_path):
    """A task with NO owner (legacy row / owner_id NULL) must be denied —
    fail-closed, not silently readable."""
    from app.procurement.schemas import ProcurementRequest

    components = build_components(_settings(tmp_path))
    repo = components["repository"]
    repo.create("task-noowner", ProcurementRequest(product_name="Z", quantity=1))  # no owner

    mallory = Principal(
        id="mallory", role=Role.PROCUREMENT_OPERATOR, tenant="t1",
        metadata={"scopes": ["task:read", "procurement:read"]},
    )
    gw = _gateway_with_repo(repo, mallory, registry=components["registry"])
    with pytest.raises(ToolPolicyError) as ei:
        gw.execute("get_task_execution_status", {"task_id": "task-noowner"})
    assert ei.value.reason_code in ("TASK_NOT_FOUND", "TASK_FORBIDDEN")


def test_missing_task_not_leaked(tmp_path):
    """A nonexistent task id yields TASK_NOT_FOUND (404) — no existence
    leakage to unauthorized principals."""
    from app.procurement.schemas import ProcurementRequest

    components = build_components(_settings(tmp_path))
    repo = components["repository"]
    repo.create("task-a", ProcurementRequest(product_name="X", quantity=1), owner_id="alice")

    eve = Principal(
        id="eve", role=Role.AUDITOR, tenant="t1",
        metadata={"scopes": ["task:read"]},
    )
    gw = _gateway_with_repo(repo, eve, registry=components["registry"])
    with pytest.raises(ToolPolicyError) as ei:
        gw.execute("get_task_execution_status", {"task_id": "nonexistent-42"})
    assert ei.value.reason_code == "TASK_NOT_FOUND"
    assert ei.value.http_status == 404


# ---------------------------------------------------------------------------
# 2. Worker identity boundary
# ---------------------------------------------------------------------------

def test_worker_resume_does_not_escalate(tmp_path):
    """A worker resume must NOT elevate a request-body role or a demo
    header into a background identity. The orchestrator's resume path does
    not accept a principal from the caller — the durable worker identity is
    fixed (orchestrator) and the MCP gateway still enforces ownership."""
    components = build_components(_settings(tmp_path))
    orch = components["orchestrator"]
    # The orchestrator has no principal-escalation surface:
    # resume(task_id) takes only the task id.
    import inspect

    sig = inspect.signature(orch.resume)
    assert list(sig.parameters) == ["task_id"], (
        "resume must not accept a caller-supplied identity"
    )


def test_demo_header_not_a_background_identity(tmp_path):
    """The X-Demo-Actor header resolves a DEMO principal only; it is not a
    durable credential. A demo principal still cannot read another
    principal's task (object-level check applies to demo too)."""
    from app.procurement.schemas import ProcurementRequest

    components = build_components(_settings(tmp_path))
    repo = components["repository"]
    repo.create("task-alice", ProcurementRequest(product_name="X", quantity=1), owner_id="alice")

    demo = Principal(id="demo-approver", role=Role.PROCUREMENT_OPERATOR, is_demo=True)
    gw = _gateway_with_repo(repo, demo, registry=components["registry"])
    # Demo principal cannot read alice's task.
    with pytest.raises(ToolPolicyError) as ei:
        gw.execute("get_task_execution_status", {"task_id": "task-alice"})
    assert ei.value.reason_code == "TASK_FORBIDDEN"


# ---------------------------------------------------------------------------
# 3. End-to-end security regression chain
# ---------------------------------------------------------------------------

def test_e2e_mutant_rejected_promotion_blocked_active_unchanged(tmp_path):
    """The full chain on ONE manifest:
    real mutant candidate → independent verifier FAIL → release gate
    REJECT → durable promotion refused → active version unchanged."""
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "eval"))

    from app.evalops.core import ReleaseGateEngine
    from app.evalops.mutation import run_mutation
    from app.evalops.promotion import DurablePromotionManager, PromotionRaceError

    db = str(tmp_path / "e2e.db")

    # 1. Baseline candidate (real greedy) — qualifies.
    baseline = run_mutation(mutant="baseline", world_seed=7)
    assert baseline.gate_status == "PASS"

    # 2. Mutant A (hard budget regression) — real execution, verifier FAIL.
    mutant = run_mutation(mutant="mutant_a_hard_budget", world_seed=7)
    assert mutant.gate_status == "FAIL"
    assert any("hard_budget_exceeded" in v for v in mutant.verifier["violations"])

    # 3. Release gate: derive the gate decision from the VERIFIER output
    # (never the mutant's own claim).
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
    mutant_measurements = dict(full)
    mutant_measurements["hard_budget_violation"] = (
        1 if mutant.verifier["feasibility"] == "INVALID"
        and any("hard_budget_exceeded" in v for v in mutant.verifier["violations"]) else 0
    )
    gate_results = engine.evaluate(
        mutant.candidate_id, baseline.candidate_id,
        measurements=mutant_measurements,
        baseline_measurements=full,
        thresholds={"recovery_latency_p50": 800, "recovery_latency_p95": 1500,
                    "extra_recovery_invocations": 30, "actual_token_cost": 5.0},
        sample_counts={"hard_budget_violation": 28, "expired_quote_used": 28},
    )
    from app.evalops.core import gate_summary

    summary = gate_summary(gate_results)
    assert summary["verdict"] == "REJECTED"

    # 4. Promotion: the mutant can never become active.
    pm = DurablePromotionManager(db)
    pm.create(baseline.candidate_id, version="baseline-v1")
    pm.mark_evaluating(baseline.candidate_id)
    pm.apply_gate_results(
        baseline.candidate_id,
        engine.evaluate(
            baseline.candidate_id, baseline.candidate_id,
            measurements=full,
            baseline_measurements=full,
            thresholds={"recovery_latency_p50": 800, "recovery_latency_p95": 1500,
                        "extra_recovery_invocations": 30, "actual_token_cost": 5.0},
            sample_counts={"hard_budget_violation": 28, "expired_quote_used": 28},
        ),
    )
    pm.promote(candidate_id=baseline.candidate_id, promoted_by="tester")
    assert pm.active_version() == baseline.candidate_id

    # The mutant (REJECTED) cannot promote.
    pm.create(mutant.candidate_id, version="mutant-v1")
    pm.mark_evaluating(mutant.candidate_id)
    pm.apply_gate_results(mutant.candidate_id, gate_results)
    assert pm.status(mutant.candidate_id).value == "REJECTED"
    with pytest.raises(PromotionRaceError):
        pm.promote(candidate_id=mutant.candidate_id, promoted_by="tester")
    # Active version unchanged.
    assert pm.active_version() == baseline.candidate_id
    pm.close()


def test_e2e_manifest_traceable_ids(tmp_path):
    """The E2E rejection carries traceable ids: input hash, candidate id,
    verifier evidence, gate decision — one shared manifest."""
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "eval"))

    from app.evalops.mutation import run_mutation

    mutant = run_mutation(mutant="mutant_b_expired_quote", world_seed=7)
    assert mutant.gate_status == "FAIL"
    assert mutant.input_hash
    assert mutant.world_hash
    assert mutant.verifier["violations"]  # line_quote_expired
    assert mutant.verifier["feasibility"] == "INVALID"
