"""Phase 10 — MCP Tool Registry.

Minimal, stable, READ-ONLY tool registry for the enterprise tool gateway.

Every tool declares a ToolCapabilityContract (Task 3):
tool_name / tool_version / input_schema / output_schema / required_scope /
operation_capability / timeout / retry_policy / rate_limit /
result_size_limit / audit_policy.

Rules:
- Only read-only tools are registered (READ_ONLY_RETRYABLE). No purchase,
  payment, order submission, or unguarded browser action.
- The registry is a FIXED ordered list — no dynamic registration of
  model-generated tools (Task 2).
- Unknown tools default to DENY (ToolPolicyGateway rejects them).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from ..executors.base import ExternalCapability, RetryPolicy


class ToolCapabilityContract(BaseModel):
    """The capability/contract for one registered tool (Task 3)."""

    tool_name: str
    tool_version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_scope: str
    operation_capability: ExternalCapability
    timeout_s: float
    retry_policy: RetryPolicy
    rate_limit_per_min: int
    result_size_limit: int  # max JSON bytes
    audit_policy: str = "deny_and_fail_audited"  # every call + denial is audited


# --- input/output JSON Schemas (stable, explicit) ---------------------------

SEARCH_QUOTES_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_id": {"type": "string"},
        "quantity": {"type": "integer", "minimum": 1},
        "supplier_filter": {"type": "array", "items": {"type": "string"}},
        "task_context": {"type": "string", "maxLength": 200},
    },
    "required": ["product_id", "quantity"],
    "additionalProperties": False,
}

SEARCH_QUOTES_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "supplier_id": {"type": "string"},
                    "sku": {"type": "string"},
                    "unit_price": {"type": "number"},
                    "currency": {"type": "string"},
                    "available_stock": {"type": "integer"},
                    "delivery_estimate": {"type": "string"},
                    "quote_timestamp": {"type": "string"},
                    "quote_expiry": {"type": "string"},
                    "quote_version": {"type": "string"},
                    "source_identifier": {"type": "string"},
                },
                "required": [
                    "supplier_id", "sku", "unit_price", "currency",
                    "available_stock", "delivery_estimate", "quote_timestamp",
                    "quote_expiry", "quote_version", "source_identifier",
                ],
            },
        }
    },
    "required": ["quotes"],
}

INVENTORY_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "supplier_id": {"type": "string"},
        "sku": {"type": "string"},
    },
    "required": ["supplier_id", "sku"],
    "additionalProperties": False,
}

INVENTORY_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "supplier_id": {"type": "string"},
        "sku": {"type": "string"},
        "available_stock": {"type": "integer"},
        "snapshot_version": {"type": "string"},
        "last_updated": {"type": "string"},
    },
    "required": ["supplier_id", "sku", "available_stock", "snapshot_version", "last_updated"],
}

POLICY_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_context": {"type": "string", "maxLength": 200},
    },
    "required": [],
    "additionalProperties": False,
}

POLICY_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approval_threshold": {"type": "number"},
        "hard_budget_enforced": {"type": "boolean"},
        "quote_expiry_required": {"type": "boolean"},
        "max_split_suppliers": {"type": "integer"},
        "policy_version": {"type": "string"},
    },
    "required": [
        "approval_threshold", "hard_budget_enforced", "quote_expiry_required",
        "max_split_suppliers", "policy_version",
    ],
}

TASK_STATUS_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}

TASK_STATUS_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "status": {"type": "string"},
        # current_step / timestamps are null for fresh PENDING tasks — they
        # must be allowed to be null (Phase 11B schema correctness fix).
        "current_step": {"type": ["string", "null"]},
        "created_at": {"type": ["string", "null"]},
        "updated_at": {"type": ["string", "null"]},
    },
    "required": ["task_id", "status"],
}


# --- registry ---------------------------------------------------------------

TOOL_REGISTRY_VERSION = "1.0.0"


def _retry_readonly(max_retries: int = 2) -> RetryPolicy:
    return RetryPolicy(
        max_retries=max_retries,
        retryable_error_codes=["timeout", "network", "5xx"],
        retry_safe=True,
    )


def build_registry() -> list[ToolCapabilityContract]:
    """Fixed-order read-only tool registry (stable catalog)."""

    return [
        ToolCapabilityContract(
            tool_name="search_supplier_quotes",
            tool_version="1.0.0",
            description=(
                "Read supplier quotes for a product (read-only). Returns "
                "supplier, sku, price, currency, stock, delivery, quote "
                "validity, version, source."
            ),
            input_schema=SEARCH_QUOTES_INPUT,
            output_schema=SEARCH_QUOTES_OUTPUT,
            required_scope="procurement:read",
            operation_capability=ExternalCapability.READ_ONLY_RETRYABLE,
            timeout_s=15.0,
            retry_policy=_retry_readonly(),
            rate_limit_per_min=60,
            result_size_limit=65536,
        ),
        ToolCapabilityContract(
            tool_name="get_supplier_inventory",
            tool_version="1.0.0",
            description="Read a supplier SKU's available inventory and snapshot version (read-only).",
            input_schema=INVENTORY_INPUT,
            output_schema=INVENTORY_OUTPUT,
            required_scope="procurement:read",
            operation_capability=ExternalCapability.READ_ONLY_RETRYABLE,
            timeout_s=15.0,
            retry_policy=_retry_readonly(),
            rate_limit_per_min=60,
            result_size_limit=16384,
        ),
        ToolCapabilityContract(
            tool_name="get_procurement_policy",
            tool_version="1.0.0",
            description=(
                "Return the caller-visible procurement policy summary "
                "(approval threshold, hard budget, expiry rule, split cap). "
                "No internal secrets, no other-tenant data."
            ),
            input_schema=POLICY_INPUT,
            output_schema=POLICY_OUTPUT,
            required_scope="procurement:read",
            operation_capability=ExternalCapability.READ_ONLY_RETRYABLE,
            timeout_s=5.0,
            retry_policy=_retry_readonly(max_retries=1),
            rate_limit_per_min=120,
            result_size_limit=8192,
        ),
        ToolCapabilityContract(
            tool_name="get_task_execution_status",
            tool_version="1.0.0",
            description=(
                "Return a SAFE-FILTERED execution status for an authorized "
                "task. Never leaks another task's trace, approvals, or "
                "supplier data."
            ),
            input_schema=TASK_STATUS_INPUT,
            output_schema=TASK_STATUS_OUTPUT,
            required_scope="task:read",
            operation_capability=ExternalCapability.READ_ONLY_RETRYABLE,
            timeout_s=5.0,
            retry_policy=_retry_readonly(max_retries=1),
            rate_limit_per_min=120,
            result_size_limit=8192,
        ),
    ]


TOOL_REGISTRY: list[ToolCapabilityContract] = build_registry()


def get_contract(tool_name: str) -> ToolCapabilityContract | None:
    """Look up a registered tool's contract. Unknown tools → None (deny)."""

    for contract in TOOL_REGISTRY:
        if contract.tool_name == tool_name:
            return contract
    return None


def registry_manifest() -> dict[str, Any]:
    """Machine-readable registry catalog (stable order)."""

    return {
        "registry_version": TOOL_REGISTRY_VERSION,
        "tools": [contract.model_dump() for contract in TOOL_REGISTRY],
    }


if __name__ == "__main__":
    print(json.dumps(registry_manifest(), indent=2, ensure_ascii=False))
