"""Independent allocation constraint verifier (Phase 6, Task 3).

The Evaluator does NOT trust the optimizer's own checks. This verifier
recomputes every hard constraint from first principles for ANY candidate
allocation (from greedy, cheapest, optimizer, or oracle) and returns an
explicit list of violation reason codes.

Re-verified constraints:
- total allocated quantity
- per-supplier allocation vs inventory
- supplier admission whitelist
- delivery deadline
- max split suppliers
- supplier share cap
- total landed cost (Decimal, unified reference currency)
- hard budget
- quote validity (not expired)
- FX comparability (only comparable quotes can be allocated)
- integer & non-negative quantities
- shortfall computation

Feasibility classification per the Phase 6 contract:
FULLY_FEASIBLE / PARTIAL_FEASIBLE / INFEASIBLE / INVALID / SOLVER_UNRESOLVED.
UNKNOWN or non-comparable data is never coerced into FULLY_FEASIBLE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .optimizer import AllocationProblem, Feasibility


@dataclass
class VerifiedAllocation:
    """One candidate allocation + independent verification result."""

    method: str
    lines: list[dict[str, Any]] = field(default_factory=list)  # supplier_id / quantity
    feasibility: Feasibility = Feasibility.INVALID
    total_landed_cost: Decimal = Decimal(0)
    covered_quantity: int = 0
    shortfall: int = 0
    violations: list[str] = field(default_factory=list)
    solver_status: str = ""
    optimality_proven: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "lines": self.lines,
            "feasibility": self.feasibility.value,
            "total_landed_cost": str(self.total_landed_cost),
            "covered_quantity": self.covered_quantity,
            "shortfall": self.shortfall,
            "violations": self.violations,
            "solver_status": self.solver_status,
            "optimality_proven": self.optimality_proven,
        }


class AllocationConstraintVerifier:
    """Re-verifies any candidate against the problem's hard constraints."""

    BUDGET_TOLERANCE = Decimal("0.005")

    def verify(
        self,
        problem: AllocationProblem,
        lines: list[dict[str, Any]],
        *,
        method: str = "unknown",
        solver_status: str = "",
        optimality_proven: bool = False,
    ) -> VerifiedAllocation:
        """Independently verify a candidate allocation (lines: supplier_id,
        quantity). Returns the feasibility class and every violation."""

        now = datetime.now(UTC)
        violations: list[str] = []
        total = Decimal(0)
        covered = 0

        quotes_by_id = {q.supplier_id: q for q in problem.quotes}

        # --- per-line checks -------------------------------------------------
        for line in lines:
            supplier_id = line.get("supplier_id")
            quantity = line.get("quantity")
            if supplier_id is None:
                violations.append("line_missing_supplier_id")
                continue
            if quantity is None or not isinstance(quantity, int) or quantity < 0:
                violations.append(f"line_non_integer_or_negative:{supplier_id}")
                continue
            quote = quotes_by_id.get(supplier_id)
            if quote is None:
                violations.append(f"line_unknown_supplier:{supplier_id}")
                continue
            # Admission whitelist.
            if problem.allowed_suppliers is not None and supplier_id not in problem.allowed_suppliers:
                violations.append(f"line_not_admitted:{supplier_id}")
            # Inventory.
            if quantity > quote.available_stock:
                violations.append(f"line_inventory_exceeded:{supplier_id}")
            # Delivery deadline.
            if (
                problem.delivery_deadline_days is not None
                and quote.delivery_days > problem.delivery_deadline_days
            ):
                violations.append(f"line_deadline_exceeded:{supplier_id}")
            # Quote validity.
            if quote.quote_valid_until is not None and quote.quote_valid_until < now:
                violations.append(f"line_quote_expired:{supplier_id}")
            # FX comparability — non-comparable quotes cannot be allocated.
            if not quote.is_comparable():
                violations.append(f"line_not_comparable:{supplier_id}")
            # Share cap.
            if problem.supplier_share_cap is not None:
                cap = Decimal(str(problem.supplier_share_cap)) * problem.quantity
                if Decimal(quantity) > cap:
                    violations.append(f"line_share_cap_exceeded:{supplier_id}")
            # Landed cost (Decimal, reference currency).
            unit = quote.normalized_unit_price_ref or quote.unit_price
            freight = problem.freight.get(supplier_id, Decimal(0))
            tax = problem.tax_rate.get(supplier_id, Decimal(0))
            base = unit * quantity
            total += base + freight + tax * base
            covered += quantity

        # --- aggregate checks -------------------------------------------------
        if len(lines) > problem.max_split_suppliers:
            violations.append(f"split_count_exceeded:{len(lines)}>{problem.max_split_suppliers}")

        shortfall = max(problem.quantity - covered, 0)

        if problem.user_budget_limit is not None:
            if total > problem.user_budget_limit + self.BUDGET_TOLERANCE:
                violations.append(f"hard_budget_exceeded:{total}>{problem.user_budget_limit}")

        total = total.quantize(Decimal("0.01"))

        # --- feasibility classification --------------------------------------
        if violations:
            feasibility = Feasibility.INVALID
        elif covered == problem.quantity:
            feasibility = Feasibility.FULLY_FEASIBLE
        elif covered > 0:
            feasibility = Feasibility.PARTIAL_FEASIBLE
        else:
            feasibility = Feasibility.INFEASIBLE

        return VerifiedAllocation(
            method=method,
            lines=[
                {"supplier_id": line.get("supplier_id"), "quantity": line.get("quantity")}
                for line in lines
            ],
            feasibility=feasibility,
            total_landed_cost=total,
            covered_quantity=covered,
            shortfall=shortfall,
            violations=violations,
            solver_status=solver_status,
            optimality_proven=optimality_proven,
        )
