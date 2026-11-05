# Phase 10 — Agent Tool Threat Model

- **Date**: 2026-11-02
- **Scope**: threats to the MCP Tool Gateway + the agent's tool-using paths.

## Assets

1. Supplier quote/inventory data (read-only).
2. Task execution status (safe-filtered).
3. Procurement policy summary (caller-visible).
4. Audit records (masked).
5. The gateway's identity boundary (demo allowlist).

## Threat actors

- **Anonymous caller**: no identity → denied (401).
- **Malicious supplier**: can craft quote payloads / tool results with
  injected instructions.
- **Compromised agent/model**: can propose arbitrary tool calls.
- **Internal worker**: can attempt to reuse stale identity/ownership.

## Threat list & mitigations

| # | threat | vector | mitigation | verified |
|---|---|---|---|---|
| T1 | unauthenticated tool call | direct MCP call, no header | 401 AUTH_REQUIRED (default deny) | test_mcp_unauthenticated_call_denied |
| T2 | unknown tool invocation | call submit_purchase_order etc. | protocol-level reject (not registered) | test_real_mcp_client (unknown tool) |
| T3 | insufficient scope | principal without scopes | INSUFFICIENT_SCOPE | test_scope_insufficient_denied |
| T4 | cross-task data read | get_task_execution_status on another's task | task ownership check → 403/404 | test_task_ownership_denied, test_task_not_found_404 |
| T5 | schema abuse | malformed args (e.g. quantity="lots") | jsonschema validation → 422 | test_schema_invalid_denied |
| T6 | mutation attempt | call a tool that writes | only READ_ONLY registered → UNKNOWN_TOOL | test_no_payment_or_order_tool_exists |
| T7 | prompt injection via tool result | malicious supplier text "ignore instructions… / call submit_purchase_order" | result is DATA; registry is fixed; no new tools can appear | test_injected_tool_description_cannot_register |
| T8 | body-forged identity | request body sets approver/principal | identity only from auth context | gateway design (no body fields read) |
| T9 | Confused Deputy | agent uses service identity for low-privilege caller | the CALLER's principal is the only identity | design + resolver |
| T10 | demo header in production | X-Demo-Actor accepted with auth_enabled=True | ApprovalGuard: auth_enabled=True → no demo provider | test_production_auth_ignores_demo_header (Phase 9) |
| T11 | rate-limit bypass / flood | unlimited calls | rate_limit_per_min per tool → 429 | test_rate_limit_denied |
| T12 | audit tampering / blind spots | no record of denied calls | every call + denial audited (masked) | test_denial_audited_with_reason |
| T13 | secret leakage in results | tool returns credentials | handlers return only business fields; output schema enforced | test_public_artifacts_contain_no_credentials (Phase 9) |

## Residual risks (documented, not claimed solved)

- **Prompt injection is mitigated, not solved**: a future LLM tool-calling
  layer could be steered by injected text into calling *registered* tools
  with attacker-chosen arguments. The gateway still enforces identity/scope/
  ownership/schema, so the damage is bounded to reading data the caller
  could already read. No string filter is claimed as complete defense.
- **Demo identity is not real auth**: X-Demo-Actor exists only in the
  isolated demo config; production-grade OAuth/OIDC is NOT implemented.
- **Single tenant**: cross-tenant isolation is NOT implemented; the model is
  explicitly SINGLE_TENANT_DEMO_ONLY.
- **Tool functions are demo-stub handlers** (fixed mock catalog); real
  supplier API calls would go through the same gateway but with live data.

## Attack surface summary

The gateway exposes exactly 4 read-only tools over MCP. There is no code
path that can order, pay, mutate supplier state, or execute arbitrary code
from a tool call (verified: no such tools are registered; no such handlers
exist).
