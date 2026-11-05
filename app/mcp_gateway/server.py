"""Phase 10 — Secure MCP Tool Gateway (server).

Wraps the read-only ToolRegistry + ToolPolicyGateway in a REAL MCP server
using the official `mcp` Python SDK (v2.3.0), served over Streamable HTTP
transport, mounted on the FastAPI app.

Protocol facts (locked, Task 1):
- SDK: `mcp` 2.3.0 (PyPI, Python 3.10+), official Model Context Protocol
  Python implementation.
- Transport: Streamable HTTP (stateful). Lifecycle: initialize → tools/
  list → tools/call. JSON-RPC message framing per MCP spec.
- Authorization: the gateway resolves the principal from the request via
  the identity resolver; Bearer-token verification is delegated to the
  caller-provided resolver (demo: X-Demo-Actor allowlist; production:
  not implemented — labeled SINGLE_TENANT_DEMO_ONLY).
- The server exposes ONLY the 4 registered read-only tools. Unknown tool
  → gateway DENY. No dynamic tool registration.

Every tool call goes through ToolPolicyGateway.execute() (12-step gate)
which audits ALLOWED and DENIED events.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from ..infrastructure.settings import Settings
from ..security.approval_guard import ApprovalGuard
from ..security.principal import Principal
from ..suppliers.base import SupplierRegistry
from .handlers import make_handlers
from .policy import ToolPolicyError, build_gateway
from .registry import TOOL_REGISTRY


def _demo_identity_resolver(guard: ApprovalGuard) -> Callable[[Any], Principal | None]:
    """Resolve the principal from an MCP tool-call context.

    mcp 2.3.0 hands the tool fn a ``Context`` whose ``headers`` carry the
    HTTP request headers (including the demo identity header). We reuse the
    SAME ApprovalGuard identity resolution as the rest of the app so the
    gateway cannot be bypassed with a different identity model.
    """

    def resolve(context: Any) -> Principal | None:
        if isinstance(context, Context):
            headers = dict(context.headers or {})
            # HTTP/2 headers arrive lowercased ('x-demo-actor'); match case-
            # insensitively.
            demo_value = None
            if guard._settings and guard._settings.demo_identity_header:
                for key, value in headers.items():
                    if key.lower() == guard._settings.demo_identity_header.lower():
                        demo_value = value
                        break
            if not demo_value:
                return None
            # Reuse the DemoIdentityProvider allowlist check.
            provider = guard._demo_provider
            if provider is not None:
                return provider.resolve(_HeaderRequest(headers))
            return None
        if isinstance(context, Request):
            return guard.resolve_principal(context)
        return None

    return resolve


class _HeaderRequest:
    """Minimal stand-in exposing .headers for DemoIdentityProvider.resolve.

    HTTP headers arrive lowercased; provide case-insensitive lookup.
    """

    class _CaseInsensitive:
        def __init__(self, headers: dict):
            self._lower = {k.lower(): v for k, v in headers.items()}

        def get(self, name: str, default=None):
            return self._lower.get(name.lower(), default)

    def __init__(self, headers: dict):
        self.headers = self._CaseInsensitive(headers)


def build_mcp_server(
    *,
    settings: Settings,
    registry: SupplierRegistry,
    repository: Any,
    guard: ApprovalGuard,
    task_owner: Callable[[str], str | None] | None = None,
    audit_sink: Callable[[Any], None] | None = None,
) -> MCPServer:
    """Build the MCP server with the policy gateway in front of the tools."""

    gateway = build_gateway(
        identity_resolver=_demo_identity_resolver(guard),
        task_owner=task_owner,
        audit_sink=audit_sink,
    )
    handlers = make_handlers(registry, repository)
    for tool_name, handler in handlers.items():
        gateway.register_handler(tool_name, handler)

    server = MCPServer(
        name="procureagent-mcp",
        title="ProcureAgent Secure Tool Gateway",
        description=(
            "Read-only procurement tools (quote search, inventory, policy "
            "summary, task status) behind a policy gateway. "
            "SINGLE_TENANT_DEMO_ONLY — no production multi-tenant auth."
        ),
        version="1.0.0",
    )

    for contract in TOOL_REGISTRY:
        props = contract.input_schema.get("properties", {})
        required = contract.input_schema.get("required", [])

        # Dynamically build a tool function whose signature mirrors the
        # contract's input schema. mcp 2.3.0 needs explicit parameters
        # (a bare **kwargs becomes a spurious argument field) and the
        # `context` parameter must be annotated with Context for injection.
        param_defs = ["context: Context"]
        for name, meta in props.items():
            t = meta.get("type", "string")
            py_type = {"string": str, "integer": int, "number": float, "boolean": bool}.get(t, Any)
            if name in required:
                param_defs.append(f"{name}: {py_type.__name__ if py_type is not Any else 'Any'}")
            else:
                param_defs.append(f"{name}: {py_type.__name__ if py_type is not Any else 'Any'} = None")

        ns: dict[str, Any] = {
            "Context": Context, "gateway": gateway, "tool_name": contract.tool_name,
            "Any": Any,
        }

        def _make_tool(tool_name: str):
            async def _impl(context: Context, **kw: Any) -> dict[str, Any]:
                try:
                    return gateway.execute(tool_name, kw, context)
                except ToolPolicyError as exc:
                    # Policy denials must surface as is_error=True in the MCP
                    # result; raising ToolError (mcp's tool error type) does
                    # that while keeping the reason code in the message.
                    from mcp.server.mcpserver.exceptions import ToolError

                    raise ToolError(f"{exc.reason_code}: {exc.message}") from exc
            return _impl

        ns["_make_tool"] = _make_tool
        exec(
            "async def tool_fn(" + ", ".join(param_defs) + ") -> dict:\n"
            "    kw = {k: v for k, v in locals().items() if k != 'context' and v is not None}\n"
            "    return await _make_tool(" + repr(contract.tool_name) + ")(context, **kw)\n",
            ns,
        )
        tool_fn = ns["tool_fn"]

        try:
            server.add_tool(
                tool_fn,
                name=contract.tool_name,
                description=contract.description,
                structured_output=False,
            )
        except Exception as exc:  # pragma: no cover — debugging aid
            import sys

            print(f"add_tool failed for {contract.tool_name}: {exc}", file=sys.stderr)
            raise

    # Store the gateway for tests/observability.
    server._policy_gateway = gateway  # type: ignore[attr-defined]
    return server


def build_mcp_asgi_app(server: MCPServer, *, path: str = "/mcp") -> tuple[Any, Any]:
    """Build the Streamable-HTTP ASGI app + its session manager.

    mcp 2.3.0's ``MCPServer.streamable_http_app`` registers the transport via
    ``Route(path, endpoint=...)`` WITHOUT explicit ``methods=``, so under
    Starlette >=0.30 only GET is routed (POST → 405). Workaround: build the
    session manager + ASGI app ourselves and let FastAPI ``app.mount``
    forward ALL methods (mount does not restrict by HTTP verb).

    The session manager must be entered via ``async with manager.run()``
    (it owns the anyio task group); the FastAPI lifespan does that. Without
    it, requests fail with "Task group is not initialized".
    """

    from mcp.server.streamable_http_manager import (
        StreamableHTTPASGIApp,
        StreamableHTTPSessionManager,
    )

    session_manager = StreamableHTTPSessionManager(
        app=server._lowlevel_server,
        json_response=False,
        stateless=False,
    )
    return StreamableHTTPASGIApp(session_manager), session_manager
