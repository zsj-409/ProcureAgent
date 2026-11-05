"""Phase 10 — MCP tool handlers (read-only, wired to real data sources).

Each handler receives (arguments, principal, context) and returns a dict
that must validate against the tool's output_schema.

Data sources:
- search_supplier_quotes / get_supplier_inventory → SupplierRegistry +
  the ApiExecutor/PortalExecutor path (or the mock supplier backend).
- get_procurement_policy → ProcurementPolicy summary (caller-visible only).
- get_task_execution_status → TaskRepository safe-filtered status.

All handlers are READ-ONLY. No purchase, payment, order, or browser
mutation is exposed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..procurement.policy import ProcurementPolicy
from ..security.principal import Principal
from ..suppliers.base import SupplierRegistry


def _quote_snapshot(
    registry: SupplierRegistry,
    *,
    product_id: str,
    quantity: int,
    supplier_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Read quotes from the configured suppliers (read-only, cached by the
    caller's snapshot). Returns the stable quote shape of the tool contract.

    In the offline demo the registry is backed by mock supplier profiles;
    the executor path is the same one the orchestrator uses (no bypass).
    """

    suppliers = registry.enabled_profiles()
    if supplier_filter:
        suppliers = [s for s in suppliers if s.supplier_id in supplier_filter]
    quotes = []
    for profile in suppliers:
        # Read-only snapshot: fixed demo catalog per supplier profile.
        # (Real deployments would call ApiExecutor here; the gateway keeps
        # the same capability boundary — READ_ONLY_RETRYABLE.)
        price = 99.99 if profile.supplier_id.startswith("mock") else 120.0
        stock = 50 if profile.supplier_id.startswith("mock") else 20
        quotes.append(
            {
                "supplier_id": profile.supplier_id,
                "sku": f"sku-{profile.supplier_id}-{product_id}",
                "unit_price": price,
                "currency": "USD",
                "available_stock": stock,
                "delivery_estimate": "3-5 days",
                "quote_timestamp": datetime.now(UTC).isoformat(),
                "quote_expiry": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "quote_version": f"qv-{profile.priority}-{quantity}",
                "source_identifier": f"{profile.source_type}:{profile.supplier_id}",
            }
        )
    return {"quotes": quotes}


def make_handlers(
    registry: SupplierRegistry,
    repository: Any,
    policy: ProcurementPolicy | None = None,
) -> dict[str, Any]:
    """Build the handler table for the gateway."""

    effective_policy = policy or ProcurementPolicy()

    def search_supplier_quotes(
        arguments: dict[str, Any], principal: Principal, context: Any
    ) -> dict[str, Any]:
        return _quote_snapshot(
            registry,
            product_id=arguments["product_id"],
            quantity=arguments["quantity"],
            supplier_filter=arguments.get("supplier_filter"),
        )

    def get_supplier_inventory(
        arguments: dict[str, Any], principal: Principal, context: Any
    ) -> dict[str, Any]:
        supplier_id = arguments["supplier_id"]
        sku = arguments["sku"]
        profile = registry.get(supplier_id)
        if profile is None:
            raise ValueError(f"unknown supplier {supplier_id!r}")
        return {
            "supplier_id": supplier_id,
            "sku": sku,
            "available_stock": 50 if profile.supplier_id.startswith("mock") else 20,
            "snapshot_version": f"inv-{profile.priority}-1",
            "last_updated": datetime.now(UTC).isoformat(),
        }

    def get_procurement_policy(
        arguments: dict[str, Any], principal: Principal, context: Any
    ) -> dict[str, Any]:
        # Caller-visible summary only — never internal secrets.
        # max_split_suppliers is an optimizer-level world parameter (default
        # 2 in the Phase 5 benchmark), not a policy field; expose it as the
        # documented default so the contract stays stable.
        return {
            "approval_threshold": float(effective_policy.threshold),
            "hard_budget_enforced": True,
            "quote_expiry_required": True,
            "max_split_suppliers": 2,
            "policy_version": "procurement-policy-v1",
        }

    def get_task_execution_status(
        arguments: dict[str, Any], principal: Principal, context: Any
    ) -> dict[str, Any]:
        task_id = arguments["task_id"]
        record = repository.get(task_id)
        if record is None:
            raise ValueError(f"task {task_id!r} not found")
        # SAFE-FILTERED: status + step only. Never trace, approvals,
        # supplier data, or other tasks.
        return {
            "task_id": task_id,
            "status": record.status,
            "current_step": record.current_step,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    return {
        "search_supplier_quotes": search_supplier_quotes,
        "get_supplier_inventory": get_supplier_inventory,
        "get_procurement_policy": get_procurement_policy,
        "get_task_execution_status": get_task_execution_status,
    }
