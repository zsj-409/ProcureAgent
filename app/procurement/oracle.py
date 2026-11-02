"""Independent small-world oracle (Phase 6, Task 4).

For small integer procurement worlds (≤4 suppliers, ≤12 units), enumerate
ALL integer allocations that satisfy the hard constraints and compute the
true minimum Total Landed Cost by exhaustive search. This is an independent
reference: it does NOT call PuLP/CBC, so the optimizer cannot "verify
itself".

The oracle returns:
- optimal cost (min over all feasible allocations)
- an optimal allocation (ties are equally optimal — different supplier
  combos with the same cost are NOT a mismatch)
- optimality gap of a candidate = (candidate_cost - oracle_cost) / oracle_cost

If no feasible positive allocation exists → INFEASIBLE.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .optimizer import AllocationProblem


@dataclass
class OracleResult:
    optimal_cost: Decimal | None = None
    optimal_allocation: list[dict[str, Any]] = field(default_factory=list)
    feasible: bool = False
    n_evaluated: int = 0
    n_feasible: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "optimal_cost": str(self.optimal_cost) if self.optimal_cost is not None else None,
            "optimal_allocation": self.optimal_allocation,
            "feasible": self.feasible,
            "n_evaluated": self.n_evaluated,
            "n_feasible": self.n_feasible,
        }


class SmallWorldOracle:
    """Exhaustive enumeration oracle for small procurement worlds."""

    MAX_SUPPLIERS = 4
    MAX_QUANTITY = 12

    def can_handle(self, problem: AllocationProblem) -> bool:
        return len(problem.quotes) <= self.MAX_SUPPLIERS and problem.quantity <= self.MAX_QUANTITY

    def optimize(self, problem: AllocationProblem) -> OracleResult:
        """Enumerate all allocations x_i in [0, stock_i] summing to ≤ quantity,
        respecting split/share/deadline/budget/whitelist; return min landed cost."""

        quotes = [q for q in problem.quotes if q.is_comparable()]
        if problem.allowed_suppliers is not None:
            allowed = set(problem.allowed_suppliers)
            quotes = [q for q in quotes if q.supplier_id in allowed]
        if not quotes:
            return OracleResult(feasible=False, n_evaluated=0, n_feasible=0)

        ranges = [range(q.available_stock + 1) for q in quotes]
        best_cost: Decimal | None = None
        best_alloc: list[dict[str, Any]] = []
        n_evaluated = 0
        n_feasible = 0
        now = datetime.now(UTC)

        # Enumerate feasible allocations, then pick lexicographically:
        # first maximize covered quantity, then minimize landed cost.
        feasible_allocations: list[tuple[int, Decimal, tuple]] = []

        for combo in itertools.product(*ranges):
            n_evaluated += 1
            total_qty = sum(combo)
            if total_qty == 0:
                continue
            if total_qty > problem.quantity:
                continue  # over-procurement is not a valid business allocation
            # Split cap.
            used = sum(1 for v in combo if v > 0)
            if used > problem.max_split_suppliers:
                continue
            # Share cap.
            if problem.supplier_share_cap is not None:
                cap = Decimal(str(problem.supplier_share_cap)) * problem.quantity
                if any(Decimal(v) > cap for v in combo):
                    continue
            # Deadline + quote validity + landed cost.
            total = Decimal(0)
            ok = True
            for q, v in zip(quotes, combo, strict=True):
                if v == 0:
                    continue
                if (
                    problem.delivery_deadline_days is not None
                    and q.delivery_days > problem.delivery_deadline_days
                ):
                    ok = False
                    break
                if q.quote_valid_until is not None and q.quote_valid_until < now:
                    ok = False
                    break
                unit = q.normalized_unit_price_ref or q.unit_price
                freight = problem.freight.get(q.supplier_id, Decimal(0))
                tax = problem.tax_rate.get(q.supplier_id, Decimal(0))
                base = unit * v
                total += base + freight + tax * base
            if not ok:
                continue
            # Budget.
            if problem.user_budget_limit is not None and total > problem.user_budget_limit:
                continue
            n_feasible += 1
            # Coverage cannot exceed the requested quantity.
            effective_covered = min(total_qty, problem.quantity)
            feasible_allocations.append((effective_covered, total, combo))

        if not feasible_allocations:
            return OracleResult(feasible=False, n_evaluated=n_evaluated, n_feasible=0)

        # Lexicographic: max covered quantity, then min cost.
        max_covered = max(item[0] for item in feasible_allocations)
        best = min(
            (item for item in feasible_allocations if item[0] == max_covered),
            key=lambda item: item[1],
        )
        best_cost = best[1]
        best_alloc = [
            {"supplier_id": q.supplier_id, "quantity": v}
            for q, v in zip(quotes, best[2], strict=True)
            if v > 0
        ]

        return OracleResult(
            optimal_cost=best_cost.quantize(Decimal("0.01")) if best_cost is not None else None,
            optimal_allocation=best_alloc,
            feasible=best_cost is not None,
            n_evaluated=n_evaluated,
            n_feasible=n_feasible,
        )

    def optimality_gap(self, problem: AllocationProblem, candidate_cost: Decimal) -> float | None:
        """Gap of a candidate vs the oracle optimum (None if oracle infeasible)."""

        oracle = self.optimize(problem)
        if oracle.optimal_cost is None or oracle.optimal_cost == 0:
            return None
        return float((candidate_cost - oracle.optimal_cost) / oracle.optimal_cost)
