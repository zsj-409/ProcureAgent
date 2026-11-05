"""Phase 11A — OTel span lifecycle correctness tests.

Proves every execution path ends its span (success, early return, raised
exception) — a leaked span produces no export and no failure trace. Also
verifies the MCP gateway closes its tool span on every denial/error path
(SCHEMA_INVALID, RATE_LIMITED, RESULT_SCHEMA_INVALID, TOOL_ERROR), and
that span counts are test-order independent (autouse pre-clear).
"""

import json
from decimal import Decimal

import pytest

from app.infrastructure.settings import Settings
from app.main import build_components
from app.mcp_gateway.policy import ToolPolicyError, build_gateway
from app.mcp_gateway.registry import TOOL_REGISTRY
from app.observability.otel import end_span, flush, setup_otel, start_task_span


@pytest.fixture(autouse=True)
def _clean_otel():
    """Every test starts with a cleared in-memory exporter so span counts
    never depend on the order tests ran (no cross-test leakage)."""
    handle = getattr(build_components, "_otel_handle", None)
    if handle is not None:
        handle["span_exporter"].clear()
    yield handle


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
        database_url=f"sqlite:///{tmp_path / 't.db'}",
        suppliers_config_path=str(cfg),
        approval_threshold=Decimal(100),
        auth_enabled=False,
        demo_identity_header="X-Demo-Actor",
        chrome_cdp_url="",
        web_allow_launch=False,
        otel_enabled=True,
        otel_export_enabled=False,
    )


def _finished(handle):
    flush(handle)
    return handle["span_exporter"].get_finished_spans()


def _gateway_with_tracer(handle):
    """A gateway wired to the in-memory OTel handle."""
    from app.security.principal import Principal, Role

    gateway = build_gateway(
        identity_resolver=lambda ctx: Principal(
            id="demo-approver", role=Role.PROCUREMENT_OPERATOR, is_demo=True
        ),
    )
    # Register a valid policy handler so allowed-path tests pass the
    # output-schema check (the server layer wires real handlers; here we
    # need one for the pure-gateway tests).
    def _policy_handler(arguments, principal, context):
        return {
            "approval_threshold": 100,
            "hard_budget_enforced": True,
            "quote_expiry_required": True,
            "max_split_suppliers": 2,
            "policy_version": "v1",
        }

    gateway.register_handler("get_procurement_policy", _policy_handler)
    gateway._tracer = handle["tracer"]  # instrument the gateway
    return gateway


# ---------------------------------------------------------------------------
# 1. Orchestrator run(): the root span must end on EVERY path
# ---------------------------------------------------------------------------

def test_orchestrator_run_plan_reject_ends_span(tmp_path, _clean_otel):
    """A plan that fails validation early (before execution) must still end
    the root span with a FAILED outcome — not leak it."""
    import asyncio

    from app.agent.state import TaskStatus
    from app.procurement.schemas import BudgetMode, ProcurementRequest

    components = build_components(_settings(tmp_path))
    orchestrator = components["orchestrator"]
    otel = components["otel_handle"]
    assert otel is not None

    # No usable suppliers (base_url 127.0.0.1:1) -> the task fails with an
    # early path (plan rejected or no usable quotes). The root span MUST end.
    req = ProcurementRequest(
        product_name="laptop",
        quantity=1,
        budget_currency="USD",
        user_budget_limit=Decimal("1000"),
        budget_mode=BudgetMode.HARD,
    )
    # The orchestrator expects the task row to exist (repository.create).
    components["repository"].create("task-reject", req)

    state = asyncio.run(orchestrator.run("task-reject", req))
    assert state.status == TaskStatus.FAILED

    spans = _finished(otel)
    roots = [s for s in spans if s.name == "ProcurementTask"]
    assert len(roots) == 1, "exactly one root span"
    assert roots[0].attributes.get("outcome") in ("FAILED", "ERROR"), (
        "root span must be ended with a failure outcome on the reject path"
    )


# ---------------------------------------------------------------------------
# 2. Orchestrator resume(): span ends on missing-task / invalid-plan paths
# ---------------------------------------------------------------------------

def test_orchestrator_resume_missing_task_ends_span(tmp_path, _clean_otel):
    """resume() on a nonexistent task raises — the span must still end."""
    import asyncio

    components = build_components(_settings(tmp_path))
    orchestrator = components["orchestrator"]
    otel = components["otel_handle"]
    assert otel is not None

    with pytest.raises(ValueError):
        asyncio.run(orchestrator.resume("no-such-task"))

    spans = _finished(otel)
    resumes = [s for s in spans if s.name == "ResumeAttempt"]
    assert len(resumes) == 1, "resume span must exist"
    assert resumes[0].attributes.get("outcome") == "ERROR", (
        "resume span must be ended even when resume raises"
    )


# ---------------------------------------------------------------------------
# 3. MCP gateway: every denial/error path closes its tool span
# ---------------------------------------------------------------------------

def test_gateway_schema_invalid_closes_span(_clean_otel):
    handle = setup_otel(in_memory=True)
    handle["span_exporter"].clear()
    gw = _gateway_with_tracer(handle)
    with pytest.raises(ToolPolicyError) as ei:
        gw.execute("search_supplier_quotes", {"task_context": "t1"})  # missing product_id/quantity
    assert ei.value.reason_code == "SCHEMA_INVALID"
    spans = _finished(handle)
    tool_spans = [s for s in spans if s.name == "tool:search_supplier_quotes"]
    assert len(tool_spans) == 1
    assert tool_spans[0].attributes["decision"] == "DENIED"
    assert tool_spans[0].attributes["reason_code"] == "SCHEMA_INVALID"


def test_gateway_rate_limited_closes_span(_clean_otel):
    handle = setup_otel(in_memory=True)
    handle["span_exporter"].clear()
    gw = _gateway_with_tracer(handle)
    contract = next(c for c in TOOL_REGISTRY if c.tool_name == "search_supplier_quotes")
    # Saturate the rate-limit window (timestamps far in the future stay in-window).
    for _ in range(contract.rate_limit_per_min):
        gw._call_window.append(999999.0)
    with pytest.raises(ToolPolicyError) as ei:
        gw.execute(
            "search_supplier_quotes",
            {"product_id": "p1", "quantity": 1, "task_context": "t1"},
        )
    assert ei.value.reason_code == "RATE_LIMITED"
    spans = _finished(handle)
    tool_spans = [s for s in spans if s.name == "tool:search_supplier_quotes"]
    assert len(tool_spans) == 1
    assert tool_spans[0].attributes["reason_code"] == "RATE_LIMITED"


def test_gateway_allowed_closes_span(_clean_otel):
    handle = setup_otel(in_memory=True)
    handle["span_exporter"].clear()
    gw = _gateway_with_tracer(handle)
    result = gw.execute("get_procurement_policy", {"task_context": "t1"})
    assert result is not None
    spans = _finished(handle)
    tool_spans = [s for s in spans if s.name == "tool:get_procurement_policy"]
    assert len(tool_spans) == 1
    assert tool_spans[0].attributes["decision"] == "ALLOWED"


def test_gateway_result_schema_invalid_closes_span(_clean_otel):
    """A handler returning a malformed payload must still close the span
    (RESULT_SCHEMA_INVALID)."""
    handle = setup_otel(in_memory=True)
    handle["span_exporter"].clear()
    gw = _gateway_with_tracer(handle)

    def bad_handler(arguments, principal, context):
        return {"unexpected_key": True}  # violates the output schema

    gw.register_handler("get_procurement_policy", bad_handler)
    with pytest.raises(ToolPolicyError) as ei:
        gw.execute("get_procurement_policy", {"task_context": "t1"})
    assert ei.value.reason_code == "RESULT_SCHEMA_INVALID"
    spans = _finished(handle)
    tool_spans = [s for s in spans if s.name == "tool:get_procurement_policy"]
    assert len(tool_spans) == 1
    assert tool_spans[0].attributes["decision"] == "DENIED"
    assert tool_spans[0].attributes["reason_code"] == "RESULT_SCHEMA_INVALID"


def test_gateway_handler_error_closes_span(_clean_otel):
    """A handler raising an unexpected exception must still close the span
    (TOOL_ERROR)."""
    handle = setup_otel(in_memory=True)
    handle["span_exporter"].clear()
    gw = _gateway_with_tracer(handle)

    def boom_handler(arguments, principal, context):
        raise RuntimeError("boom")

    gw.register_handler("get_procurement_policy", boom_handler)
    with pytest.raises(ToolPolicyError) as ei:
        gw.execute("get_procurement_policy", {"task_context": "t1"})
    assert ei.value.reason_code == "TOOL_ERROR"
    spans = _finished(handle)
    tool_spans = [s for s in spans if s.name == "tool:get_procurement_policy"]
    assert len(tool_spans) == 1
    assert tool_spans[0].attributes["reason_code"] == "TOOL_ERROR"


# ---------------------------------------------------------------------------
# 4. Test-order independence
# ---------------------------------------------------------------------------

def test_span_count_starts_clean(_clean_otel):
    """The autouse fixture clears the exporter BEFORE the test, so the span
    count here is exactly the count this test produced."""
    handle = setup_otel(in_memory=True)
    handle["span_exporter"].clear()
    assert len(handle["span_exporter"].get_finished_spans()) == 0
    s = start_task_span(handle["tracer"], task_id="t-order", stage="ProcurementTask")
    end_span(s, outcome="SUCCESS")
    flush(handle)
    assert len(handle["span_exporter"].get_finished_spans()) == 1
