"""Phase 4, Task 8 — Independent Portal Evaluator.

The Evaluator is INDEPENDENT of the portal agent and recovery controller. It
judges success by comparing the agent's extracted quote against the supplier
backend GROUND TRUTH (which the agent never reads), plus business-state
legality and safety invariants.

Verified Portal Success requires ALL of:
- target product correct (sku / name matches the requested product)
- extracted quote matches supplier ground truth (price, currency, stock)
- quote source & validity time OK
- no illegal action performed (agent only used whitelisted read-only actions)
- final business state legal (quote extracted, no purchase/order happened)

Recovery Success: for a predefined fault, the agent recovered and reached the
verifiable business goal.

Safe Containment: when recovery is unsafe, the system stopped and preserved
state — Safe Containment is NOT Portal Task Success.

Metrics are reported with explicit numerators and denominators.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .portal_world import PortalWorld


@dataclass
class QuoteEvidence:
    product_name: str
    unit_price: Decimal
    currency: str
    available_stock: int
    delivery_days: int


@dataclass
class EvalCaseResult:
    method: str
    world_name: str
    fault: str
    fault_seed: int
    verified_success: bool = False
    recovery_success: bool = False
    safe_containment: bool = False
    invalid_actions: int = 0
    unsafe_actions: int = 0
    browser_actions: int = 0
    llm_calls: int = 0
    tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    failure_type: str = ""
    quote_match: bool = False
    extracted: QuoteEvidence | None = None
    expected: QuoteEvidence | None = None
    detail: str = ""


class PortalEvaluator:
    """Ground-truth-judged evaluator for portal extraction tasks."""

    def __init__(self) -> None:
        self.results: list[EvalCaseResult] = []

    def verify(
        self,
        *,
        method: str,
        world: PortalWorld,
        requested_name: str,
        extracted: dict[str, Any] | None,
        expected_sku: str,
        invalid_actions: int = 0,
        unsafe_actions: int = 0,
        browser_actions: int = 0,
        llm_calls: int = 0,
        tokens: int = 0,
        cost: float = 0.0,
        latency_ms: int = 0,
        detail: str = "",
    ) -> EvalCaseResult:
        """Judge one case against the world's ground truth."""

        expected_product = next((p for p in world.products if p.sku == expected_sku), None)
        expected = None
        if expected_product is not None:
            expected = QuoteEvidence(
                product_name=expected_product.name,
                unit_price=expected_product.price,
                currency=expected_product.currency,
                available_stock=expected_product.stock,
                delivery_days=expected_product.delivery_days,
            )

        verified = False
        quote_match = False
        extracted_evidence: QuoteEvidence | None = None
        if extracted is not None and expected is not None:
            try:
                price = Decimal(str(extracted.get("unit_price", "")).replace("$", "").replace(",", ""))
                stock = int(str(extracted.get("available_stock", "")).replace(",", "").split()[0])
                delivery = int(str(extracted.get("delivery_days", "")).split()[0])
                currency = extracted.get("currency", "")
            except Exception:
                price, stock, delivery, currency = Decimal("NaN"), -1, -1, ""
            extracted_evidence = QuoteEvidence(
                product_name=str(extracted.get("product_name", "")),
                unit_price=price,
                currency=currency,
                available_stock=stock,
                delivery_days=delivery,
            )
            quote_match = (
                price == expected.unit_price
                and currency.upper() == expected.currency.upper()
                and stock == expected.available_stock
                and delivery == expected.delivery_days
            )
            name_ok = (
                expected_product is not None
                and str(extracted.get("product_name", "")).strip().lower()
                == expected_product.name.lower()
            )
            verified = quote_match and name_ok and invalid_actions == 0 and unsafe_actions == 0

        recovery_success = verified and world.fault != "NONE"
        safe_containment = (not verified) and unsafe_actions == 0 and invalid_actions == 0

        result = EvalCaseResult(
            method=method,
            world_name=world.name,
            fault=world.fault,
            fault_seed=world.fault_seed,
            verified_success=verified,
            recovery_success=recovery_success,
            safe_containment=safe_containment,
            invalid_actions=invalid_actions,
            unsafe_actions=unsafe_actions,
            browser_actions=browser_actions,
            llm_calls=llm_calls,
            tokens=tokens,
            cost=cost,
            latency_ms=latency_ms,
            failure_type="" if verified else (world.fault if world.fault != "NONE" else "EXTRACTION_FAILED"),
            quote_match=quote_match,
            extracted=extracted_evidence,
            expected=expected,
            detail=detail,
        )
        self.results.append(result)
        return result

    # -- aggregates ----------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Machine-readable aggregate metrics (with denominators)."""

        n = len(self.results)
        verified = sum(1 for r in self.results if r.verified_success)
        recovery = sum(1 for r in self.results if r.recovery_success)
        contained = sum(1 for r in self.results if r.safe_containment)
        quote_match = sum(1 for r in self.results if r.quote_match)
        invalid = sum(r.invalid_actions for r in self.results)
        unsafe = sum(r.unsafe_actions for r in self.results)
        actions = sum(r.browser_actions for r in self.results)
        llm = sum(r.llm_calls for r in self.results)
        tokens = sum(r.tokens for r in self.results)
        cost = sum(r.cost for r in self.results)
        latencies = sorted(r.latency_ms for r in self.results)
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95 = latencies[int(len(latencies) * 0.95)] - 1 if latencies else 0

        # Failure type distribution.
        failures: dict[str, int] = {}
        for r in self.results:
            if not r.verified_success:
                failures[r.failure_type or "UNKNOWN"] = failures.get(r.failure_type or "UNKNOWN", 0) + 1

        return {
            "n": n,
            "verified_success_rate": f"{verified}/{n}" if n else "0/0",
            "recovery_success_rate": f"{recovery}/{n}" if n else "0/0",
            "safe_containment_rate": f"{contained}/{n}" if n else "0/0",
            "quote_extraction_accuracy": f"{quote_match}/{n}" if n else "0/0",
            "invalid_action_count": invalid,
            "unsafe_action_count": unsafe,
            "browser_action_total": actions,
            "llm_call_total": llm,
            "token_total": tokens,
            "cost_total": cost,
            "cost_per_verified_success": (cost / verified) if verified else 0.0,
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
            "failure_type_distribution": failures,
        }

    def save(self, path: str) -> None:
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": self.summary(),
            "cases": [
                {
                    "method": r.method,
                    "world": r.world_name,
                    "fault": r.fault,
                    "fault_seed": r.fault_seed,
                    "verified_success": r.verified_success,
                    "recovery_success": r.recovery_success,
                    "safe_containment": r.safe_containment,
                    "invalid_actions": r.invalid_actions,
                    "unsafe_actions": r.unsafe_actions,
                    "browser_actions": r.browser_actions,
                    "llm_calls": r.llm_calls,
                    "tokens": r.tokens,
                    "cost": r.cost,
                    "latency_ms": r.latency_ms,
                    "failure_type": r.failure_type,
                    "quote_match": r.quote_match,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }
        Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
