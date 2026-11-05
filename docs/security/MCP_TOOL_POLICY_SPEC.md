# Phase 10 — MCP Tool Policy Spec

- **Date**: 2026-11-02
- **Scope**: SINGLE_TENANT_DEMO_ONLY. This is a controlled enterprise tool
  gateway prototype, NOT a production identity infrastructure.

## Policy model

Every tool invocation is gated by `ToolPolicyGateway.execute()` in a fixed
12-step order. Any denial returns a machine-readable `reason_code` and is
audited.

| step | check | denial code | HTTP status |
|---|---|---|---|
| 1-2 | trusted identity resolved | AUTH_REQUIRED | 401 |
| 3 | tool registered | UNKNOWN_TOOL | 404 |
| 4 | required scope | INSUFFICIENT_SCOPE | 403 |
| 5 | task ownership (object-level) | TASK_NOT_FOUND / TASK_FORBIDDEN | 404 / 403 |
| 6 | tenant boundary (demo label) | — (single tenant) | — |
| 7 | capability read-only | MUTATION_NOT_ALLOWED | 403 |
| 8 | parameter schema | SCHEMA_INVALID | 422 |
| 9 | rate limit / result size | RATE_LIMITED | 429 |
| 10 | execute handler | TOOL_ERROR | 500 |
| 11 | output schema | (audited) | — |
| 12 | audit event | — | — |

## Identity

- Identity comes ONLY from the trusted auth context (MCP request headers
  resolved through the SAME `ApprovalGuard` as the HTTP API).
- `auth_enabled=True` → no identity can be resolved → all tool calls denied
  (default deny).
- `auth_enabled=False` + explicit `demo_identity_header` → the demo
  principal `demo-approver` is resolved from that header (allowlist, exact
  value match, case-insensitive header key).
- A request body can never set principal/tenant/role (no Confused Deputy:
  an agent cannot borrow a higher-privilege service identity).

## Scopes

| scope | granted to | tools |
|---|---|---|
| procurement:read | demo principal | search_supplier_quotes, get_supplier_inventory, get_procurement_policy |
| task:read | demo principal | get_task_execution_status |

A non-demo principal must carry scopes in its metadata; unknown → deny.

## Object-level authorization

- `get_task_execution_status` checks the task owner (gateway's
  `task_owner` callback). Non-existent task → 404 (no ID enumeration);
  owner mismatch → 403 TASK_FORBIDDEN.
- Single-tenant demo: all tasks belong to the demo tenant; the owner check
  is enforced against the repository (task must exist).

## Capability contract

- Only `READ_ONLY_RETRYABLE` tools are registered. Mutations are denied by
  construction (no mutation contract exists in the registry).
- `retry_policy.retry_safe=True` for all (read-only → bounded retry safe).
- Every contract declares timeout, rate_limit_per_min, result_size_limit.

## Audit

- EVERY call and EVERY denial writes an `AuditEvent`:
  timestamp, tool, principal, decision (ALLOWED/DENIED), reason_code,
  task_id, input_hash (sha256 of arguments — masked, not raw), outcome,
  duration_ms.
- No credentials, no full payloads, no prompts in audit records.

## Demo vs production

| aspect | demo (this prototype) | production (NOT implemented) |
|---|---|---|
| identity | demo header allowlist | OAuth/OIDC + token verification |
| tenant | SINGLE_TENANT_DEMO_ONLY | real tenant isolation |
| scopes | demo principal has all | per-tenant RBAC |
| bearer | X-Demo-Actor (isolated config only) | real bearer tokens with audience/issuer |
