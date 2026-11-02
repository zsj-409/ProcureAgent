"""Constrained procurement allocation optimizer (Phase 5).

An independently testable integer-programming component that minimizes Total
Landed Cost subject to hard business constraints, replacing the weighted
greedy split award as the *optimization* engine (the greedy remains the
baseline to compare against).

Constraints supported:
- quantity conservation (sum of award lines == requested quantity, when
  feasible)
- inventory (each supplier contributes at most its available stock)
- supplier admission (whitelist/blacklist per task)
- hard budget (user_budget_limit, HARD mode: total landed cost must not
  exceed it; otherwise the problem is INFEASIBLE, never silently over-budget)
- delivery deadline (each line's delivery_days <= deadline; a supplier
  violating it is excluded)
- max split suppliers (at most N suppliers in the award)
- supplier share cap (each supplier at most share_cap fraction of the
  quantity)
- tax & freight (per supplier, in the reference currency) — landed cost =
  unit*quantity + freight + tax, all in one currency
- explicit infeasible return (the caller distinguishes full / partial /
  infeasible)

Feasibility classification (Task: 库存不足必须区分):
- FULLY_FEASIBLE:  quantity can be fully covered within all constraints
- PARTIAL_FEASIBLE: quantity cannot be fully covered, but a non-empty award
  exists (needs re-confirmation — NOT auto-completed)
- INFEASIBLE:       no positive award exists (no supplier has stock / budget
  insufficient / deadline impossible)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from ..errors import ValidationError
from .schemas import AwardLine, SupplierQuote


class Feasibility(str, Enum):
    FULLY_FEASIBLE = "FULLY_FEASIBLE"
    PARTIAL_FEASIBLE = "PARTIAL_FEASIBLE"
    INFEASIBLE = "INFEASIBLE"


@dataclass
class AllocationProblem:
    """One constrained allocation instance."""

    quotes: list[SupplierQuote]           # comparable, normalized quotes
    quantity: int = 1
    reference_currency: str = "USD"
    user_budget_limit: Decimal | None = None   # HARD budget (cannot exceed)
    delivery_deadline_days: int | None = None
    max_split_suppliers: int = 3
    supplier_share_cap: float | None = None    # max fraction of quantity per supplier
    allowed_suppliers: list[str] | None = None  # admission whitelist (None = all)
    freight: dict[str, Decimal] = field(default_factory=dict)   # per supplier
    tax_rate: dict[str, Decimal] = field(default_factory=dict)  # per supplier (0..1)
    time_limit_s: float = 10.0


@dataclass
class AllocationResult:
    """Outcome of one optimization run."""

    feasibility: Feasibility
    lines: list[AwardLine] = field(default_factory=list)
    total_landed_cost: Decimal = Decimal(0)
    covered_quantity: int = 0
    shortfall: int = 0
    solver_status: str = ""
    solve_time_s: float = 0.0
    infeasible_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasibility": self.feasibility.value,
            "lines": [
                {
                    "supplier_id": line.supplier_id,
                    "quantity": line.quantity,
                    "unit_price": str(line.unit_price),
                    "line_total": str(line.line_total),
                    "delivery_days": line.delivery_days,
                }
                for line in self.lines
            ],
            "total_landed_cost": str(self.total_landed_cost),
            "covered_quantity": self.covered_quantity,
            "shortfall": self.shortfall,
            "solver_status": self.solver_status,
            "solve_time_s": self.solve_time_s,
            "infeasible_reason": self.infeasible_reason,
        }


class ProcurementOptimizer:
    """Integer-programming allocation via PuLP/CBC (exact for reasonable n)."""

    def optimize(self, problem: AllocationProblem) -> AllocationResult:
        """Solve the allocation problem; returns an explicit feasibility."""


        if problem.quantity <= 0:
            raise ValidationError("quantity must be positive")
        quotes = [q for q in problem.quotes if q.is_comparable()]
        if not quotes:
            return AllocationResult(
                feasibility=Feasibility.INFEASIBLE,
                infeasible_reason="no comparable quotes (FX missing/stale)",
            )
        # Admission whitelist.
        if problem.allowed_suppliers is not None:
            allowed = set(problem.allowed_suppliers)
            quotes = [q for q in quotes if q.supplier_id in allowed]
            if not quotes:
                return AllocationResult(
                    feasibility=Feasibility.INFEASIBLE,
                    infeasible_reason="no supplier admitted by the whitelist",
                )

        # Supplier stock availability (pre-filter zero-stock).
        stocky = [q for q in quotes if q.available_stock > 0]
        if not stocky:
            return AllocationResult(
                feasibility=Feasibility.INFEASIBLE,
                infeasible_reason="no supplier has positive stock",
            )
        if sum(q.available_stock for q in stocky) < problem.quantity:
            # Cannot fully cover: still try to maximize coverage (partial).
            return self._solve_max_cover(problem, stocky)

        # Try the full-feasibility LP.
        result = self._solve(problem, stocky, require_full=True)
        if result.feasibility == Feasibility.FULLY_FEASIBLE:
            return result
        # LP said infeasible under full constraints (budget/deadline/split/
        # share). Fall back to max-cover so the caller can decide partial.
        return self._solve_max_cover(problem, stocky)

    # ------------------------------------------------------------------

    def _solve(
        self,
        problem: AllocationProblem,
        quotes: list[SupplierQuote],
        *,
        require_full: bool,
    ) -> AllocationResult:
        """Build and solve the MILP. When require_full, quantity conservation
        is hard; otherwise we maximize covered quantity first."""

        import time

        import pulp

        n = len(quotes)
        start = time.monotonic()
        prob = pulp.LpProblem("procure_alloc", pulp.LpMinimize)
        x = [pulp.LpVariable(f"x_{i}", 0, quotes[i].available_stock, pulp.LpInteger) for i in range(n)]
        y = [pulp.LpVariable(f"y_{i}", 0, 1, pulp.LpBinary) for i in range(n)]  # supplier used

        # Objective: minimize total landed cost = sum(unit_ref*qty + freight + tax).
        # Tax on (unit*qty + freight) per line; freight paid once per used supplier.
        landed = []
        for i, q in enumerate(quotes):
            unit = float(q.normalized_unit_price_ref or q.unit_price)
            freight = float(problem.freight.get(q.supplier_id, Decimal(0)))
            tax = float(problem.tax_rate.get(q.supplier_id, Decimal(0)))
            # line landed cost (linear in x_i, with a fixed freight if used)
            base = unit * x[i]
            tax_part = tax * base
            landed.append(base + tax_part + freight * y[i])

        # Quantity conservation (full feasibility) or coverage maximization.
        if require_full:
            prob += pulp.lpSum(x) == problem.quantity
            prob += pulp.lpSum(landed)
        else:
            # maximize covered quantity as a lexicographic objective: we
            # minimize (bigM * shortfall + landed cost). Use a shortfall var.
            shortfall = pulp.LpVariable("shortfall", 0, problem.quantity, pulp.LpInteger)
            prob += pulp.lpSum(x) + shortfall == problem.quantity
            BIG_M = 1_000_000
            prob += pulp.lpSum(landed) + BIG_M * shortfall

        # Link x_i to y_i (y_i=1 iff x_i>0).
        for i in range(n):
            prob += x[i] <= quotes[i].available_stock * y[i]
            prob += x[i] >= 1 * y[i]

        # Max split suppliers.
        prob += pulp.lpSum(y) <= problem.max_split_suppliers

        # Delivery deadline: exclude suppliers that cannot meet it.
        if problem.delivery_deadline_days is not None:
            for i, q in enumerate(quotes):
                if q.delivery_days > problem.delivery_deadline_days:
                    prob += y[i] == 0

        # Supplier share cap.
        if problem.supplier_share_cap is not None:
            cap = problem.supplier_share_cap
            for i in range(n):
                prob += x[i] <= pulp.LpAffineExpression(problem.quantity * cap)

        # Hard budget on total landed cost.
        if problem.user_budget_limit is not None:
            prob += pulp.lpSum(landed) <= float(problem.user_budget_limit)

        status = prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=problem.time_limit_s))
        solve_time = time.monotonic() - start
        status_name = pulp.LpStatus[status]

        if status != pulp.LpStatusOptimal:
            return AllocationResult(
                feasibility=Feasibility.INFEASIBLE,
                solver_status=status_name,
                solve_time_s=solve_time,
                infeasible_reason=f"solver returned {status_name}",
            )

        lines: list[AwardLine] = []
        total = Decimal(0)
        covered = 0
        for i, q in enumerate(quotes):
            xval = int(pulp.value(x[i]) or 0)
            if xval <= 0:
                continue
            unit = q.normalized_unit_price_ref or q.unit_price
            line_total = (unit * xval).quantize(Decimal("0.01"))
            total += line_total
            covered += xval
            lines.append(
                AwardLine(
                    supplier_id=q.supplier_id,
                    quantity=xval,
                    unit_price=unit,
                    line_total=line_total,
                    delivery_days=q.delivery_days,
                )
            )

        # Add freight & tax to the reported total landed cost.
        for i, q in enumerate(quotes):
            xval = int(pulp.value(x[i]) or 0)
            if xval > 0:
                freight = problem.freight.get(q.supplier_id, Decimal(0))
                tax = problem.tax_rate.get(q.supplier_id, Decimal(0))
                base = (q.normalized_unit_price_ref or q.unit_price) * xval
                total += freight + tax * base

        total = total.quantize(Decimal("0.01"))
        shortfall = max(problem.quantity - covered, 0)
        feasibility = Feasibility.FULLY_FEASIBLE if shortfall == 0 else Feasibility.PARTIAL_FEASIBLE

        return AllocationResult(
            feasibility=feasibility,
            lines=lines,
            total_landed_cost=total,
            covered_quantity=covered,
            shortfall=shortfall,
            solver_status=status_name,
            solve_time_s=solve_time,
        )

    def _solve_max_cover(self, problem: AllocationProblem, quotes: list[SupplierQuote]) -> AllocationResult:
        """Maximize covered quantity when full feasibility is impossible."""

        result = self._solve(problem, quotes, require_full=False)
        if result.feasibility == Feasibility.INFEASIBLE:
            return result
        if result.covered_quantity == 0:
            # No positive award exists within constraints (budget too low,
            # deadline impossible, etc.) -> INFEASIBLE, not "partial 0".
            return AllocationResult(
                feasibility=Feasibility.INFEASIBLE,
                solver_status=result.solver_status,
                solve_time_s=result.solve_time_s,
                infeasible_reason=(
                    "no positive allocation possible within constraints "
                    f"(covered 0 of {problem.quantity})"
                ),
            )
        if result.shortfall > 0:
            result.feasibility = Feasibility.PARTIAL_FEASIBLE
            result.infeasible_reason = (
                f"requested {problem.quantity} but only {result.covered_quantity} "
                f"coverable within constraints (shortfall {result.shortfall}); "
                "needs re-confirmation — not auto-completed"
            )
        return result
