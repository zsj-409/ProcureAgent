"""Phase 11B — Commit C artifact generator: authorization test results.

Runs the object-level authorization scenarios against the REAL repository
and writes authorization_test_results.json.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.infrastructure.settings import Settings
from app.main import build_components
from app.mcp_gateway.handlers import make_handlers
from app.mcp_gateway.policy import ToolPolicyError, build_gateway
from app.security.principal import Principal, Role


def _settings(tmp_path: Path) -> Settings:
    cfg = tmp_path / "suppliers.json"
    cfg.write_text(
        json.dumps(
            {
                "suppliers": [
                    {
                        "supplier_id": "mock_api_supplier",
                        "display_name": "Mock",
                        "source_type": "api",
                        "base_url": "http://127.0.0.1:1",
                        "search_endpoint": "/s",
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
    )


def write_commit_c_artifacts(out_dir: Path) -> dict:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="phase11b-authz-"))
    components = build_components(_settings(tmp))
    repo = components["repository"]
    registry = components["registry"]

    from app.procurement.schemas import ProcurementRequest

    repo.create("task-alice", ProcurementRequest(product_name="X", quantity=1), owner_id="alice")
    repo.create("task-bob", ProcurementRequest(product_name="Y", quantity=1), owner_id="bob")
    repo.create("task-noowner", ProcurementRequest(product_name="Z", quantity=1))

    def _gw(principal):
        gw = build_gateway(
            identity_resolver=lambda ctx: principal,
            task_owner=lambda tid: repo.get_owner(tid),
        )
        handlers = make_handlers(registry=registry, repository=repo)
        for name, handler in handlers.items():
            gw.register_handler(name, handler)
        return gw

    alice = Principal(
        id="alice", role=Role.PROCUREMENT_OPERATOR, tenant="t1",
        metadata={"scopes": ["task:read", "procurement:read"]},
    )
    bob = Principal(
        id="bob", role=Role.PROCUREMENT_OPERATOR, tenant="t1",
        metadata={"scopes": ["task:read", "procurement:read"]},
    )
    demo = Principal(id="demo-approver", role=Role.PROCUREMENT_OPERATOR, is_demo=True)

    scenarios = []

    def _run(name, gw, tool, args):
        try:
            result = gw.execute(tool, args)
            scenarios.append({
                "scenario": name,
                "decision": "ALLOWED",
                "reason": None,
                "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
            })
        except ToolPolicyError as exc:
            scenarios.append({
                "scenario": name,
                "decision": "DENIED",
                "reason_code": exc.reason_code,
                "http_status": exc.http_status,
            })

    _run("alice_reads_own_task", _gw(alice), "get_task_execution_status", {"task_id": "task-alice"})
    _run("alice_reads_bobs_task", _gw(alice), "get_task_execution_status", {"task_id": "task-bob"})
    _run("bob_reads_bobs_task", _gw(bob), "get_task_execution_status", {"task_id": "task-bob"})
    _run("alice_reads_ownerless_task", _gw(alice), "get_task_execution_status", {"task_id": "task-noowner"})
    _run("demo_reads_others_task", _gw(demo), "get_task_execution_status", {"task_id": "task-alice"})
    _run("anyone_reads_missing_task", _gw(alice), "get_task_execution_status", {"task_id": "ghost-99"})

    artifact = {
        "date": "2026-11-05",
        "identity_model": "SINGLE_TENANT_DEMO_ONLY (real per-task owner_id; "
                          "no hard-coded demo owner; fail-closed on unknown)",
        "scenarios": scenarios,
    }
    (out_dir / "authorization_test_results.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {s["scenario"]: s["decision"] for s in scenarios}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    result = write_commit_c_artifacts(root / "docs" / "eval")
    print(json.dumps(result, indent=2, ensure_ascii=False))
