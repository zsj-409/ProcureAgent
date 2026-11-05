"""Phase 10 — OTel integration with the orchestrator + MCP gateway.

Verifies that a REAL procurement task run emits spans, and the MCP tool
gateway emits tool spans — without breaking the task when OTel is absent.
"""

import json
from decimal import Decimal

import pytest

from app.infrastructure.settings import Settings
from app.main import build_components


@pytest.fixture(autouse=True)
def _clear_otel_spans():
    """build_components caches ONE OTel handle per process; clear the
    exporter BEFORE each test so spans from earlier tests (including other
    test files that run real tasks) never leak in."""
    handle = getattr(build_components, "_otel_handle", None)
    if handle is not None:
        handle["span_exporter"].clear()
    yield


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


def test_orchestrator_run_emits_root_span(tmp_path):
    """A real task run must emit a ProcurementTask span with task_id."""
    import asyncio

    from app.agent.state import TaskStatus

    components = build_components(_settings(tmp_path))
    orchestrator = components["orchestrator"]
    otel = components["otel_handle"]
    assert otel is not None

    from app.procurement.schemas import ProcurementRequest

    components["repository"].create(
        "otel-task-1", ProcurementRequest(product_name="Test Product", quantity=1)
    )

    async def run():
        state = await orchestrator.run(
            "otel-task-1",
            ProcurementRequest(product_name="Test Product", quantity=1),
        )
        return state

    state = asyncio.run(run())
    # Task still completes normally with OTel enabled.
    assert state.status in (TaskStatus.COMPLETED, TaskStatus.WAITING_APPROVAL, TaskStatus.FAILED)

    otel["tracer_provider"].force_flush()
    spans = otel["span_exporter"].get_finished_spans()
    root = next((s for s in spans if s.name == "ProcurementTask"), None)
    assert root is not None, "root span missing"
    assert root.attributes["task_id"] == "otel-task-1"
    assert "outcome" in root.attributes


def test_orchestrator_without_otel_still_works(tmp_path):
    """otel_enabled=False must not break task execution."""
    import asyncio

    settings = _settings(tmp_path)
    settings.otel_enabled = False
    components = build_components(settings)
    assert components["otel_handle"] is None
    orchestrator = components["orchestrator"]

    from app.procurement.schemas import ProcurementRequest

    async def run():
        return await orchestrator.run(
            "no-otel-task",
            ProcurementRequest(product_name="X", quantity=1),
        )

    # The orchestrator expects the task row to exist (repository.create).
    components["repository"].create(
        "no-otel-task", ProcurementRequest(product_name="X", quantity=1)
    )
    state = asyncio.run(run())
    assert state.task_id == "no-otel-task"


def test_mcp_gateway_tool_span(tmp_path):
    """A gateway tool call emits a tool:<name> span."""
    from app.mcp_gateway.policy import build_gateway
    from app.security.principal import DEMO_APPROVER

    components = build_components(_settings(tmp_path))
    otel = components["otel_handle"]
    gw = build_gateway(identity_resolver=lambda ctx: DEMO_APPROVER)
    gw._tracer = otel["tracer"]  # type: ignore[attr-defined]

    # Register a trivial handler.
    gw.register_handler(
        "search_supplier_quotes",
        lambda args, p, ctx: {"quotes": []},
    )

    result = gw.execute("search_supplier_quotes", {"product_id": "x", "quantity": 1})
    assert result == {"quotes": []}

    otel["tracer_provider"].force_flush()
    spans = otel["span_exporter"].get_finished_spans()
    tool_span = next((s for s in spans if s.name == "tool:search_supplier_quotes"), None)
    assert tool_span is not None
    assert tool_span.attributes["decision"] == "ALLOWED"


def test_mcp_gateway_denial_span(tmp_path):
    """A denied tool call emits a tool span with the denial reason."""
    from app.mcp_gateway.policy import ToolPolicyError, build_gateway

    components = build_components(_settings(tmp_path))
    otel = components["otel_handle"]
    gw = build_gateway(identity_resolver=lambda ctx: None)  # unauthenticated
    gw._tracer = otel["tracer"]  # type: ignore[attr-defined]

    with pytest.raises(ToolPolicyError) as exc:
        gw.execute("search_supplier_quotes", {"product_id": "x", "quantity": 1})
    assert exc.value.reason_code == "AUTH_REQUIRED"

    otel["tracer_provider"].force_flush()
    spans = otel["span_exporter"].get_finished_spans()
    tool_span = next((s for s in spans if s.name == "tool:search_supplier_quotes"), None)
    assert tool_span is not None
    assert tool_span.attributes["decision"] == "DENIED"
    assert tool_span.attributes["reason_code"] == "AUTH_REQUIRED"
