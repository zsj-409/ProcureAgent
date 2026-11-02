"""Phase 6, Task 2 — independent procurement worlds for benchmarking.

Each world is a self-contained, reproducible procurement problem:
- visible business input (quantity, budget, deadline, whitelist, ...)
- visible supplier quotes (price, currency, stock, delivery, validity)
- hidden ground truth (the world's catalog — the evaluator holds it)
- fee rules (freight/tax per supplier)
- seed + environment version + snapshot hash

World families (Task 2.3): unconstrained, single-supplier shortage,
multi-supplier joint fulfillment, split cap, tight budget, freight/tax
changes optimum, delivery-vs-price conflict, currency/FX failure, expired
quotes, allowlist, share limit, fully infeasible, partial needs
re-confirmation, mixed multi-constraint.

Every world supports reset() + snapshot_hash() for environment isolation;
no method may share mutated state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.procurement.schemas import SupplierQuote


@dataclass
class ProcurementWorld:
    name: str
    family: str
    quantity: int
    quotes: list[SupplierQuote]
    reference_currency: str = "USD"
    user_budget_limit: Decimal | None = None
    delivery_deadline_days: int | None = None
    max_split_suppliers: int = 3
    supplier_share_cap: float | None = None
    allowed_suppliers: list[str] | None = None
    freight: dict[str, Decimal] = field(default_factory=dict)
    tax_rate: dict[str, Decimal] = field(default_factory=dict)
    seed: int = 1
    env_version: str = "v1"
    split: str = "DEV"  # DEV / CONFIRMATION / TEST
    _initial_quotes: list[SupplierQuote] = field(default_factory=list, repr=False)
    _expired_flags: dict[str, bool] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        # Snapshot the initial quotes for deterministic reset().
        if not self._initial_quotes:
            self._initial_quotes = list(self.quotes)
        # Freeze the expiry flags at construction time so the snapshot hash
        # is stable across the world's lifetime (no runtime clock drift).
        if not self._expired_flags:
            now = datetime.now(UTC)
            self._expired_flags = {
                q.supplier_id: (q.quote_valid_until is not None and q.quote_valid_until < now)
                for q in self.quotes
            }

    def snapshot_hash(self) -> str:
        payload = {
            "name": self.name,
            "family": self.family,
            "quantity": self.quantity,
            "budget": str(self.user_budget_limit) if self.user_budget_limit else None,
            "deadline": self.delivery_deadline_days,
            "max_split": self.max_split_suppliers,
            "share_cap": self.supplier_share_cap,
            "allowlist": self.allowed_suppliers,
            "freight": {k: str(v) for k, v in self.freight.items()},
            "tax": {k: str(v) for k, v in self.tax_rate.items()},
            "seed": self.seed,
            "env_version": self.env_version,
            "quotes": [
                {
                    "supplier_id": q.supplier_id,
                    "price": str(q.unit_price),
                    "currency": q.currency,
                    "stock": q.available_stock,
                    "delivery": q.delivery_days,
                    # Validity as a frozen boolean (expired or not), NOT the
                    # exact timestamp — two worlds built a second apart must
                    # hash identically if their business validity is the same.
                    "expired": self._expired_flags.get(q.supplier_id, False),
                    "normalization": q.normalization_status,
                    "ref_price": str(q.normalized_unit_price_ref) if q.normalized_unit_price_ref else None,
                }
                for q in self.quotes
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def reset(self) -> None:
        """Environment reset: restore the exact initial quote snapshot."""

        self.quotes = list(self._initial_quotes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "quantity": self.quantity,
            "reference_currency": self.reference_currency,
            "user_budget_limit": str(self.user_budget_limit) if self.user_budget_limit else None,
            "delivery_deadline_days": self.delivery_deadline_days,
            "max_split_suppliers": self.max_split_suppliers,
            "supplier_share_cap": self.supplier_share_cap,
            "allowed_suppliers": self.allowed_suppliers,
            "freight": {k: str(v) for k, v in self.freight.items()},
            "tax_rate": {k: str(v) for k, v in self.tax_rate.items()},
            "seed": self.seed,
            "env_version": self.env_version,
            "split": self.split,
            "snapshot_hash": self.snapshot_hash(),
        }


def _q(
    supplier_id: str,
    *,
    price: str,
    currency: str = "USD",
    stock: int,
    delivery_days: int = 3,
    expired: bool = False,
    incomparable: bool = False,
) -> SupplierQuote:
    valid_until = (
        datetime.now(UTC) - timedelta(hours=1) if expired else datetime.now(UTC) + timedelta(days=1)
    )
    ref_price = None if incomparable else Decimal(price)
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="Item",
        unit_price=Decimal(price),
        currency=currency,
        available_stock=stock,
        delivery_days=delivery_days,
        source_type="api",
        normalization_status="incomparable" if incomparable else "comparable",
        normalized_unit_price_ref=ref_price,
        reference_currency=currency,
        quote_valid_until=valid_until,
    )


def _build_quotes(seed: int, family: str) -> list[SupplierQuote]:
    """Deterministic quote generation per family (seeded)."""

    # seed is used by deterministic families; keep the argument for the
    # registry contract (same seed -> same world).
    _ = seed
    if family == "unconstrained":
        return [_q("s1", price="10.00", stock=100), _q("s2", price="12.00", stock=100)]
    if family == "single_supplier_shortage":
        return [_q("s1", price="10.00", stock=5)]
    if family == "multi_supplier_joint":
        return [_q("s1", price="10.00", stock=6), _q("s2", price="11.00", stock=6), _q("s3", price="12.00", stock=6)]
    if family == "split_cap":
        return [_q("s1", price="10.00", stock=5), _q("s2", price="11.00", stock=5), _q("s3", price="12.00", stock=5)]
    if family == "tight_budget":
        return [_q("s1", price="10.00", stock=100), _q("s2", price="10.50", stock=100)]
    if family == "freight_tax":
        return [_q("s1", price="10.00", stock=100), _q("s2", price="10.50", stock=100)]
    if family == "delivery_conflict":
        return [_q("s1", price="10.00", stock=100, delivery_days=1), _q("s2", price="8.00", stock=100, delivery_days=10)]
    if family == "fx_failure":
        # s2 is EUR with NO comparable ref price (FX missing/stale).
        return [
            _q("s1", price="10.00", stock=100),
            _q("s2", price="8.00", currency="EUR", stock=100, incomparable=True),
        ]
    if family == "expired_quote":
        return [_q("s1", price="10.00", stock=100, expired=True), _q("s2", price="12.00", stock=100)]
    if family == "allowlist":
        return [_q("s1", price="10.00", stock=100), _q("s2", price="8.00", stock=100), _q("s3", price="9.00", stock=100)]
    if family == "share_limit":
        return [_q("s1", price="10.00", stock=100), _q("s2", price="11.00", stock=100)]
    if family == "fully_infeasible":
        return [_q("s1", price="10.00", stock=0)]
    if family == "partial_confirm":
        return [_q("s1", price="10.00", stock=5), _q("s2", price="12.00", stock=5)]
    if family == "mixed_constraints":
        return [
            _q("s1", price="10.00", stock=40, delivery_days=1),
            _q("s2", price="9.50", stock=40, delivery_days=6),
            _q("s3", price="11.00", stock=40, delivery_days=2),
            _q("s4", price="8.50", stock=40, delivery_days=8, currency="EUR", incomparable=True),
        ]
    raise ValueError(f"unknown family {family}")


FAMILY_PARAMS: dict[str, dict[str, Any]] = {
    "unconstrained": {"quantity": 50},
    "single_supplier_shortage": {"quantity": 10},
    "multi_supplier_joint": {"quantity": 15},
    "split_cap": {"quantity": 12, "max_split_suppliers": 2},
    "tight_budget": {"quantity": 100, "user_budget_limit": Decimal("1020")},
    "freight_tax": {
        "quantity": 10,
        "freight": {"s1": Decimal("10"), "s2": Decimal("0")},
        "tax_rate": {"s1": Decimal("0.05"), "s2": Decimal("0.0")},
    },
    "delivery_conflict": {"quantity": 40, "delivery_deadline_days": 3},
    "fx_failure": {"quantity": 50},
    "expired_quote": {"quantity": 50},
    "allowlist": {"quantity": 60, "allowed_suppliers": ["s1", "s3"]},
    "share_limit": {"quantity": 20, "supplier_share_cap": 0.5},
    "fully_infeasible": {"quantity": 10},
    "partial_confirm": {"quantity": 12},
    "mixed_constraints": {
        "quantity": 80,
        "delivery_deadline_days": 3,
        "max_split_suppliers": 2,
        "user_budget_limit": Decimal("800"),
    },
}


def make_world(family: str, *, seed: int, split: str = "DEV", name: str | None = None) -> ProcurementWorld:
    """Build a world from the family template + seed."""

    params = dict(FAMILY_PARAMS[family])
    quotes = _build_quotes(seed, family)
    return ProcurementWorld(
        name=name or f"world-{family}-{seed}",
        family=family,
        quotes=quotes,
        seed=seed,
        split=split,
        **params,
    )


def world_registry(splits: dict[str, list[str]], seeds_per_family: int = 2) -> list[ProcurementWorld]:
    """Build the full registry: families × seeds across DEV/CONFIRMATION/TEST."""

    worlds = []
    for split, families in splits.items():
        for family in families:
            for seed in range(1, seeds_per_family + 1):
                worlds.append(make_world(family, seed=seed, split=split))
    return worlds
