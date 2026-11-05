"""Phase 10 — Tool Policy Gateway.

Deterministic 12-step enforcement in front of every MCP tool invocation:

1. resolve trusted identity
2. validate token/principal
3. check tool is registered
4. check required scope
5. check task ownership (object-level)
6. check tenant/organization boundary (single-tenant demo: all principals
   share the demo tenant; NOT production multi-tenancy)
7. check tool capability (read-only allowed; mutations denied by policy)
8. validate parameter schema
9. check execution budget (rate limit / result size)
10. execute the tool
11. verify the result (schema)
12. write an audit event (every call AND every denial)

Security rules:
- The request body never carries principal/tenant/role — identity comes only
  from the trusted auth context (the MCP session's authenticated principal,
  resolved by the gateway's identity provider).
- Confused deputy: an agent cannot use a high-privilege service identity to
  access data the low-privilege caller may not see — the principal resolved
  for the CALLER is the only identity used.
- Denied invocations produce NO external side effect (read-only tools have
  none by construction; mutations are not registered).
- Every failure/denial writes a masked audit record (no credentials,
  no full payloads).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..executors.base import ExternalCapability
from ..security.principal import Principal
from .registry import ToolCapabilityContract, get_contract


class ToolPolicyError(Exception):
    """A policy-level rejection. Carries a machine-readable reason code."""

    def __init__(self, reason_code: str, message: str, http_status: int = 403):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message
        self.http_status = http_status


@dataclass
class AuditEvent:
    timestamp: str
    tool_name: str
    principal_id: str | None
    decision: str  # ALLOWED | DENIED
    reason_code: str | None
    task_id: str | None
    input_hash: str  # sha256 of the arguments (masked, not raw)
    outcome: str | None
    duration_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "tool": self.tool_name,
            "principal": self.principal_id,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "task_id": self.task_id,
            "input_hash": self.input_hash,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
        }


@dataclass
class ToolPolicyGateway:
    """Policy enforcement + audit in front of tool execution.

    ``identity_resolver`` returns the Principal for a call context, or None
    (unauthenticated). ``task_owner`` optionally maps task_id → owner id for
    object-level checks (single-tenant demo: returns the caller).
    """

    identity_resolver: Callable[[Any], Principal | None]
    task_owner: Callable[[str], str | None] | None = None
    audit_sink: Callable[[AuditEvent], None] | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    _audit_log: list[AuditEvent] = field(default_factory=list, repr=False)
    _call_window: list[float] = field(default_factory=list, repr=False)

    # -- public entry --------------------------------------------------------

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: Any = None,
    ) -> dict[str, Any]:
        """Run the 12-step gate and, if allowed, the tool. Returns the tool
        result; raises ToolPolicyError on any denial BEFORE execution."""

        started = time.perf_counter()
        event_input_hash = self._mask_hash(arguments)
        principal = self.identity_resolver(context)
        task_id = arguments.get("task_id") if isinstance(arguments, dict) else None

        # Phase 10: emit an OTel span for this tool invocation (best-effort;
        # a missing tracer must never break the gate).
        otel_span = None
        tracer = getattr(self, "_tracer", None)
        if tracer is not None:
            try:
                otel_span = tracer.start_span(
                    f"tool:{tool_name}",
                    attributes={
                        "tool_name": tool_name,
                        "tool_version": (
                            get_contract(tool_name).tool_version
                            if get_contract(tool_name)
                            else "unknown"
                        ),
                    },
                )
            except Exception:
                otel_span = None

        def _close_span(decision: str, reason: str | None = None) -> None:
            if otel_span is not None:
                try:
                    otel_span.set_attribute("decision", decision)
                    if reason:
                        otel_span.set_attribute("reason_code", reason)
                    otel_span.end()
                except Exception:
                    pass

        # 1-2. identity
        if principal is None:
            self._audit("DENIED", tool_name, None, "AUTH_REQUIRED", task_id, event_input_hash, started)
            _close_span("DENIED", 'AUTH_REQUIRED')
            raise ToolPolicyError("AUTH_REQUIRED", "unauthenticated tool call", http_status=401)

        # 3. registered tool
        contract = get_contract(tool_name)
        if contract is None:
            self._audit("DENIED", tool_name, principal.id, "UNKNOWN_TOOL", task_id, event_input_hash, started)
            _close_span("DENIED", 'UNKNOWN_TOOL')
            raise ToolPolicyError("UNKNOWN_TOOL", f"tool '{tool_name}' is not registered", http_status=404)

        # 4. scope
        if not self._has_scope(principal, contract.required_scope):
            self._audit(
                "DENIED",
                tool_name,
                principal.id,
                "INSUFFICIENT_SCOPE",
                task_id,
                event_input_hash,
                started,
            )
            _close_span("DENIED", 'INSUFFICIENT_SCOPE')
            raise ToolPolicyError("INSUFFICIENT_SCOPE", f"principal lacks scope '{contract.required_scope}'")

        # 5. task ownership
        if contract.tool_name == "get_task_execution_status":
            owner = self.task_owner(task_id) if self.task_owner else None
            if owner is None:
                self._audit(
                    "DENIED",
                    tool_name,
                    principal.id,
                    "TASK_NOT_FOUND",
                    task_id,
                    event_input_hash,
                    started,
                )
                _close_span("DENIED", 'TASK_NOT_FOUND')
                raise ToolPolicyError("TASK_NOT_FOUND", "task does not exist", http_status=404)
            if owner != principal.id and not (principal.is_demo and owner == "demo"):
                self._audit(
                    "DENIED",
                    tool_name,
                    principal.id,
                    "TASK_FORBIDDEN",
                    task_id,
                    event_input_hash,
                    started,
                )
                _close_span("DENIED", 'TASK_FORBIDDEN')
                raise ToolPolicyError("TASK_FORBIDDEN", "principal has no access to this task")

        # 6. tenant boundary — single-tenant demo: enforced label only.
        # A production multi-tenant model is NOT implemented; the response
        # carries SINGLE_TENANT_DEMO_ONLY so we never claim otherwise.

        # 7. capability — only READ_ONLY_RETRYABLE is registered; any
        # mutation capability is denied by construction.
        if contract.operation_capability != ExternalCapability.READ_ONLY_RETRYABLE:
            self._audit(
                "DENIED",
                tool_name,
                principal.id,
                "MUTATION_NOT_ALLOWED",
                task_id,
                event_input_hash,
                started,
            )
            _close_span("DENIED", 'MUTATION_NOT_ALLOWED')
            raise ToolPolicyError("MUTATION_NOT_ALLOWED", "only read-only tools are exposed by this gateway")

        # 8. schema
        try:
            self._validate_schema(contract, arguments)
        except Exception as exc:  # jsonschema.ValidationError (schema mismatch)
            self._audit(
                "DENIED",
                tool_name,
                principal.id,
                "SCHEMA_INVALID",
                task_id,
                event_input_hash,
                started,
            )
            _close_span("DENIED", 'SCHEMA_INVALID')
            raise ToolPolicyError(
                "SCHEMA_INVALID",
                f"arguments fail tool schema: {exc}",
                http_status=422,
            ) from exc

        # 9. budget (RATE_LIMITED raises ToolPolicyError; span closed below)
        try:
            self._check_budget(contract)
        except ToolPolicyError:
            self._audit("DENIED", tool_name, principal.id, "RATE_LIMITED", task_id, event_input_hash, started)
            _close_span("DENIED", 'RATE_LIMITED')
            raise

        # 10. execute
        handler = self._handlers.get(tool_name)
        if handler is None:
            self._audit("DENIED", tool_name, principal.id, "NO_HANDLER", task_id, event_input_hash, started)
            _close_span("DENIED", 'NO_HANDLER')
            raise ToolPolicyError("NO_HANDLER", f"no handler for tool '{tool_name}'")
        try:
            result = handler(arguments, principal, context)
        except ToolPolicyError:
            _close_span("DENIED", 'TOOL_POLICY_ERROR')
            raise
        except Exception as exc:
            self._audit("DENIED", tool_name, principal.id, "TOOL_ERROR", task_id, event_input_hash, started)
            _close_span("DENIED", 'TOOL_ERROR')
            raise ToolPolicyError("TOOL_ERROR", f"tool execution failed: {exc}", http_status=500) from exc

        # 11. verify result shape (best-effort structural check)
        try:
            self._verify_result(contract, result)
        except Exception as exc:
            self._audit(
                "DENIED",
                tool_name,
                principal.id,
                "RESULT_SCHEMA_INVALID",
                task_id,
                event_input_hash,
                started,
            )
            _close_span("DENIED", 'RESULT_SCHEMA_INVALID')
            raise ToolPolicyError(
                "RESULT_SCHEMA_INVALID",
                f"tool result fails output schema: {exc}",
                http_status=500,
            ) from exc

        # 12. audit
        _close_span("ALLOWED")
        self._audit(
            "ALLOWED",
            tool_name,
            principal.id,
            None,
            task_id,
            event_input_hash,
            started,
            outcome="OK",
        )
        return result

    # -- handlers (wired by the server) --------------------------------------

    _handlers: dict[str, Callable[[dict, Principal, Any], dict]] = field(default_factory=dict)

    def register_handler(self, tool_name: str, handler: Callable[[dict, Principal, Any], dict]) -> None:
        self._handlers[tool_name] = handler

    # -- internals -----------------------------------------------------------

    def _has_scope(self, principal: Principal, required_scope: str) -> bool:
        # Demo principal has both scopes; a future real principal would carry
        # scopes in metadata. Unknown principals → deny.
        if principal.is_demo:
            return True
        scopes = set(principal.metadata.get("scopes", []))
        return required_scope in scopes

    def _validate_schema(self, contract: ToolCapabilityContract, arguments: dict[str, Any]) -> None:
        import jsonschema  # installed with mcp sdk

        jsonschema.validate(arguments, contract.input_schema)

    def _check_budget(self, contract: ToolCapabilityContract) -> None:
        now = time.monotonic()
        self._call_window = [t for t in self._call_window if now - t < 60.0]
        if len(self._call_window) >= contract.rate_limit_per_min:
            raise ToolPolicyError("RATE_LIMITED", f"tool '{contract.tool_name}' rate limit exceeded")
        self._call_window.append(now)

    def _verify_result(self, contract: ToolCapabilityContract, result: dict[str, Any]) -> None:
        import jsonschema

        jsonschema.validate(result, contract.output_schema)

    def _mask_hash(self, arguments: dict[str, Any]) -> str:
        raw = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _audit(self, decision, tool, principal_id, reason_code, task_id, input_hash, started, outcome=None):
        event = AuditEvent(
            timestamp=self.clock().isoformat(),
            tool_name=tool,
            principal_id=principal_id,
            decision=decision,
            reason_code=reason_code,
            task_id=task_id,
            input_hash=input_hash,
            outcome=outcome,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        self._audit_log.append(event)
        if self.audit_sink:
            self.audit_sink(event)

    def audit_log(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self._audit_log]


def build_gateway(
    *,
    identity_resolver: Callable[[Any], Principal | None],
    task_owner: Callable[[str], str | None] | None = None,
    audit_sink: Callable[[AuditEvent], None] | None = None,
) -> ToolPolicyGateway:
    return ToolPolicyGateway(
        identity_resolver=identity_resolver,
        task_owner=task_owner,
        audit_sink=audit_sink,
    )
