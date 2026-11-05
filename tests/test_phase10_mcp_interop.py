"""Phase 10 — Real MCP Client ↔ Server interop test (Task 7 #14, Task 27 #2).

Uses the OFFICIAL mcp Python SDK client (ClientSession + streamable HTTP)
against the running FastAPI app's /mcp endpoint. This is a REAL protocol
exchange — initialize → tools/list → tools/call — not a custom REST shim.

Requires a live uvicorn server because the MCP streamable-http client needs
a real HTTP endpoint (TestClient is not enough for the SDK's transport).
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

import pytest

from app.infrastructure.settings import Settings
from app.main import create_app

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" and os.environ.get("PHASE10_SKIP_MCP_E2E") == "1",
    reason="MCP e2e needs a live server; opt-in via PHASE10_SKIP_MCP_E2E=0",
)

PORT = 8771


def _write_suppliers(tmp: Path) -> Path:
    cfg = tmp / "suppliers.json"
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
    return cfg


def _start_server(tmp: Path):
    cfg = _write_suppliers(tmp)
    settings = Settings(
        database_url=f"sqlite:///{tmp / 'mcp.db'}",
        suppliers_config_path=str(cfg),
        approval_threshold=Decimal(100),
        auth_enabled=False,
        demo_identity_header="X-Demo-Actor",
        chrome_cdp_url="",
        web_allow_launch=False,
    )
    app = create_app(settings)
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(80):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1)
            return server, thread
        except Exception:
            time.sleep(0.25)
    server.should_exit = True
    raise RuntimeError("server did not start")


def test_real_mcp_client_discovers_and_calls_tools():
    """Full MCP protocol exchange with the official SDK client."""

    tmp = Path(tempfile.mkdtemp(prefix="phase10-mcp-"))
    server, thread = _start_server(tmp)
    try:
        import anyio
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async def run():
            http_client = httpx2.AsyncClient(
                headers={"X-Demo-Actor": "demo-approver"}, timeout=15
            )
            try:
                async with streamable_http_client(
                    f"http://127.0.0.1:{PORT}/mcp/",
                    http_client=http_client,
                ) as (read, write):
                    async with ClientSession(read, write) as session:
                        # 1. Initialize (protocol handshake).
                        init = await session.initialize()
                        assert init.server_info.name == "procureagent-mcp"
                        assert init.server_info.version == "1.0.0"

                        # 2. tools/list — discovery of the 4 registered tools.
                        tools = await session.list_tools()
                        names = [t.name for t in tools.tools]
                        assert names == [
                            "search_supplier_quotes",
                            "get_supplier_inventory",
                            "get_procurement_policy",
                            "get_task_execution_status",
                        ]

                        # 3. tools/call — legal read-only call.
                        result = await session.call_tool(
                            "search_supplier_quotes",
                            {"product_id": "MX-Master-3S", "quantity": 2},
                        )
                        assert not result.is_error
                        text = result.content[0].text
                        data = json.loads(text)
                        assert data["quotes"][0]["supplier_id"] == "mock_api_supplier"
                        assert data["quotes"][0]["currency"] == "USD"

                        # 4. tools/call — unknown tool must error. mcp 2.3.0
                        # rejects unregistered tools at the protocol layer
                        # BEFORE the gateway (it never reaches policy).
                        bad = await session.call_tool("submit_purchase_order", {})
                        assert bad.is_error
                        assert "Unknown tool" in bad.content[0].text
            finally:
                await http_client.aclose()

        anyio.run(run)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_mcp_unauthenticated_call_denied():
    """Without the demo header, the gateway must deny (401)."""

    tmp = Path(tempfile.mkdtemp(prefix="phase10-mcp-"))
    server, thread = _start_server(tmp)
    try:
        import anyio
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async def run():
            http_client = httpx2.AsyncClient(timeout=15)
            try:
                async with streamable_http_client(
                    f"http://127.0.0.1:{PORT}/mcp/",
                    http_client=http_client,
                ) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            "get_procurement_policy", {}
                        )
                        assert result.is_error
                        assert "AUTH_REQUIRED" in result.content[0].text
            finally:
                await http_client.aclose()

        anyio.run(run)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
