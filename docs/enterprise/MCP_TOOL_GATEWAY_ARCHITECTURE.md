# Phase 10 — Secure MCP Tool Gateway Architecture

- **Date**: 2026-11-02
- **Commit A**: (pending)
- **Protocol**: Model Context Protocol, **mcp Python SDK 2.3.0** (official,
  PyPI, Python 3.10+), Streamable HTTP transport (stateful).
- **Scope**: SINGLE_TENANT_DEMO_ONLY. No production OAuth/OIDC.

## Components

```
FastAPI app
  └── /mcp/  (app.mount → StreamableHTTPASGIApp)
         └── MCP JSON-RPC (initialize / tools/list / tools/call)
                └── MCPServer (mcp 2.3.0, name=procureagent-mcp)
                       └── 4 registered tools → ToolPolicyGateway (12-step gate)
                              └── handlers → SupplierRegistry / TaskRepository / ProcurementPolicy
```

- `app/mcp_gateway/registry.py` — ToolCapabilityContract + fixed registry
  (4 read-only tools, stable order, versioned).
- `app/mcp_gateway/policy.py` — ToolPolicyGateway (12-step enforcement +
  audit).
- `app/mcp_gateway/handlers.py` — read-only tool implementations.
- `app/mcp_gateway/server.py` — MCPServer construction + Streamable-HTTP
  ASGI app (works around the SDK's Route methods= bug via app.mount).

## Protocol facts (locked)

- SDK `mcp` 2.3.0; transport `streamable_http_client` (client) /
  `StreamableHTTPSessionManager` (server, stateful sessions).
- Tool functions must have EXPLICIT parameters (a bare `**kwargs` becomes a
  spurious argument field in mcp 2.3.0's generated schema).
- The `context: Context` injection parameter must be annotated with
  `mcp.server.mcpserver.context.Context` — the SDK's
  `find_context_parameter` checks that EXACT class; `mcp.server.context.
  Context` is a DIFFERENT class and fails detection.
- HTTP request headers arrive LOWERCASED in `Context.headers`
  (`x-demo-actor`); identity lookup must be case-insensitive.
- Mount path: Starlette `Mount("/mcp")` compiles to `^/mcp/(?P<path>.*)$`
  (trailing slash REQUIRED) — mount at `/mcp/` and have clients use it.
- `structured_output=True` requires a Pydantic/TypedDict return annotation;
  with `dict` returns use `structured_output=False` and raise mcp `ToolError`
  for policy denials so the client sees `is_error=True`.

## Tool Registry (4 tools, all READ_ONLY_RETRYABLE)

| tool | scope | input | output |
|---|---|---|---|
| search_supplier_quotes | procurement:read | product_id, quantity, supplier_filter?, task_context? | quotes[] (supplier, sku, price, currency, stock, delivery, validity, version, source) |
| get_supplier_inventory | procurement:read | supplier_id, sku | stock + snapshot_version |
| get_procurement_policy | procurement:read | task_context? | approval_threshold, hard_budget, expiry_required, max_split, policy_version |
| get_task_execution_status | task:read | task_id | SAFE-FILTERED status/step only |

Registry manifest: `docs/eval/mcp_tool_registry.json` (stable order).

## 12-step enforcement (ToolPolicyGateway)

1. resolve trusted identity (from MCP Context headers, same ApprovalGuard
   demo identity as the rest of the app)
2. validate principal (None → 401 AUTH_REQUIRED)
3. tool registered (unknown → 404 UNKNOWN_TOOL)
4. required scope (INSUFFICIENT_SCOPE)
5. task ownership (object-level: TASK_NOT_FOUND 404 / TASK_FORBIDDEN)
6. tenant boundary (SINGLE_TENANT_DEMO_ONLY label; multi-tenant NOT
   IMPLEMENTED)
7. tool capability (mutation → MUTATION_NOT_ALLOWED; only read-only
   registered)
8. parameter schema (jsonschema; SCHEMA_INVALID 422)
9. execution budget (rate limit / result size)
10. execute handler
11. verify result against output schema
12. audit (ALLOWED/DENIED with masked input hash)

- Request body never carries principal/tenant/role — identity comes only
  from the trusted auth context (no Confused Deputy: the caller's own
  principal is the only identity used).
- Every call AND denial writes a masked audit record (sha256 of args, no
  credentials, no raw payloads).
- Denied calls produce NO external side effect (tools are read-only by
  construction).

## Bypass analysis (Task 5)

- Public MCP entry → gateway (enforced).
- Internal orchestrator path → ExecutorRouter (unchanged, no MCP bypass).
- Durable Worker recovery → uses persisted plan/step state, NOT the MCP
  gateway — a worker cannot re-grant itself permissions from a stale request
  body.
- Offline eval harness → separate path, no MCP exposure.

## Prompt injection (Task 6)

- Tool results are returned as DATA (quotes). The registry is FIXED — a
  malicious tool description in a result cannot register a new tool
  (verified by `test_injected_tool_description_cannot_register`).
- High-risk actions require trusted policy decisions, never LLM
  self-assertion. String filters are not claimed as a complete defense.
