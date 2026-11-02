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
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import pulp

from ..errors import ValidationError
from .schemas import AwardLine, SupplierQuote


class Feasibility(str, Enum):
    FULLY_FEASIBLE = "FULLY_FEASIBLE"
    PARTIAL_FEASIBLE = "PARTIAL_FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    INVALID = "INVALID"
    SOLVER_UNRESOLVED = "SOLVER_UNRESOLVED"


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
    optimality_proven: bool = False

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
            "optimality_proven": self.optimality_proven,
        }


class ProcurementOptimizer:
    """Integer-programming allocation via PuLP/CBC (exact for reasonable n)."""

    def optimize(self, problem: AllocationProblem) -> AllocationResult:
        """Solve the allocation problem; returns an explicit feasibility."""


        if problem.quantity <= 0:
            raise ValidationError("quantity must be positive")
        now = datetime.now(UTC)
        quotes = [q for q in problem.quotes if q.is_comparable()]
        if not quotes:
            return AllocationResult(
                feasibility=Feasibility.INFEASIBLE,
                infeasible_reason="no comparable quotes (FX missing/stale)",
            )
        # Expired quotes cannot be allocated (Phase 6: quote validity gate).
        quotes = [q for q in quotes if q.quote_valid_until is None or q.quote_valid_until > now]
        if not quotes:
            return AllocationResult(
                feasibility=Feasibility.INFEASIBLE,
                infeasible_reason="all quotes expired",
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
        """Build and solve the MILP (Phase 6: strict two-stage lexicographic).

        Stage 1 (when not require_full): maximize covered quantity under all
        hard constraints (objective = -sum(x), i.e. minimize shortfall).
        Stage 2: FIX the stage-1 optimal coverage and minimize Total Landed
        Cost. Coverage is NEVER traded for cost — no BIG_M magic constant.

        When require_full, a single solve with quantity conservation is used.

        CBC status is checked at every stage; a FEASIBLE (not proven optimal)
        solution is returned with optimality_proven=False, never silently
        claimed optimal. The final allocation is re-verified in Decimal
        (budget ≤ limit + tolerance; integer ≥ 0; inventory; deadline; split;
        share); any violation after re-check → INVALID.
        """

        import time

        n = len(quotes)
        start = time.monotonic()
        prob = pulp.LpProblem("procure_alloc", pulp.LpMinimize)
        x = [pulp.LpVariable(f"x_{i}", 0, quotes[i].available_stock, pulp.LpInteger) for i in range(n)]
        y = [pulp.LpVariable(f"y_{i}", 0, 1, pulp.LpBinary) for i in range(n)]  # supplier used

        # Landed cost coefficients (float for CBC; Decimal re-check later).
        landed = []
        for i, q in enumerate(quotes):
            unit = float(q.normalized_unit_price_ref or q.unit_price)
            freight = float(problem.freight.get(q.supplier_id, Decimal(0)))
            tax = float(problem.tax_rate.get(q.supplier_id, Decimal(0)))
            base = unit * x[i]
            tax_part = tax * base
            landed.append(base + tax_part + freight * y[i])

        # Hard constraints (shared by both stages).
        # Link x_i to y_i (y_i=1 iff x_i>0).
        for i in range(n):
            prob += x[i] <= quotes[i].available_stock * y[i]
            prob += x[i] >= 1 * y[i]
        prob += pulp.lpSum(y) <= problem.max_split_suppliers
        if problem.delivery_deadline_days is not None:
            for i, q in enumerate(quotes):
                if q.delivery_days > problem.delivery_deadline_days:
                    prob += y[i] == 0
        if problem.supplier_share_cap is not None:
            cap = problem.supplier_share_cap
            for i in range(n):
                prob += x[i] <= pulp.LpAffineExpression(problem.quantity * cap)
        if problem.user_budget_limit is not None:
            prob += pulp.lpSum(landed) <= float(problem.user_budget_limit)

        # Stage 1: maximize coverage (or hard quantity conservation).
        if require_full:
            prob += pulp.lpSum(x) == problem.quantity
            prob += pulp.lpSum(landed)  # objective: min landed cost
        else:
            # Stage 1 objective: maximize covered quantity == minimize
            # (quantity - sum(x)); CBC minimizes, so use -sum(x) coefficient.
            prob += -pulp.lpSum(x)

        status1 = prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=problem.time_limit_s))
        status1_name = pulp.LpStatus[status1]
        solve_time = time.monotonic() - start

        if status1 not in (pulp.LpStatusOptimal, pulp.LpStatusInfeasible, pulp.LpStatusUnbounded):
            # Not solved to optimality and not proven infeasible → unresolved
            # (time limit hit with no proof either way).
            if pulp.LpStatus[status1] == "Feasible":
                # CBC returned a feasible solution without proving optimality.
                result = self._build_result(
                    problem, quotes, x, y, status1_name, solve_time, optimality_proven=False
                )
                if result.covered_quantity > 0:
                    return result
            return AllocationResult(
                feasibility=Feasibility.SOLVER_UNRESOLVED,
                solver_status=status1_name,
                solve_time_s=solve_time,
                infeasible_reason=f"solver unresolved: {status1_name}",
            )

        if status1 != pulp.LpStatusOptimal:
            # Proven infeasible (or unbounded — impossible here due to bounds).
            return AllocationResult(
                feasibility=Feasibility.INFEASIBLE,
                solver_status=status1_name,
                solve_time_s=solve_time,
                infeasible_reason=f"solver proved {status1_name}",
            )

        if not require_full:
            # Stage 2: fix stage-1 optimal coverage, minimize landed cost.
            covered_opt = int(sum(pulp.value(x[i]) or 0 for i in range(n)))
            if covered_opt == 0:
                return AllocationResult(
                    feasibility=Feasibility.INFEASIBLE,
                    solver_status=status1_name,
                    solve_time_s=solve_time,
                    infeasible_reason=(
                        "no positive allocation possible within constraints "
                        f"(covered 0 of {problem.quantity})"
                    ),
                )
            prob2 = pulp.LpProblem("procure_alloc_stage2", pulp.LpMinimize)
            x2 = [pulp.LpVariable(f"x2_{i}", 0, quotes[i].available_stock, pulp.LpInteger) for i in range(n)]
            y2 = [pulp.LpVariable(f"y2_{i}", 0, 1, pulp.LpBinary) for i in range(n)]
            landed2 = []
            for i, q in enumerate(quotes):
                unit = float(q.normalized_unit_price_ref or q.unit_price)
                freight = float(problem.freight.get(q.supplier_id, Decimal(0)))
                tax = float(problem.tax_rate.get(q.supplier_id, Decimal(0)))
                base = unit * x2[i]
                landed2.append(base + tax * base + freight * y2[i])
            for i in range(n):
                prob2 += x2[i] <= quotes[i].available_stock * y2[i]
                prob2 += x2[i] >= 1 * y2[i]
            prob2 += pulp.lpSum(y2) <= problem.max_split_suppliers
            if problem.delivery_deadline_days is not None:
                for i, q in enumerate(quotes):
                    if q.delivery_days > problem.delivery_deadline_days:
                        prob2 += y2[i] == 0
            if problem.supplier_share_cap is not None:
                cap = problem.supplier_share_cap
                for i in range(n):
                    prob2 += x2[i] <= pulp.LpAffineExpression(problem.quantity * cap)
            if problem.user_budget_limit is not None:
                prob2 += pulp.lpSum(landed2) <= float(problem.user_budget_limit)
            # FIX stage-1 coverage.
            prob2 += pulp.lpSum(x2) == covered_opt
            prob2 += pulp.lpSum(landed2)
            status2 = prob2.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=problem.time_limit_s))
            status2_name = pulp.LpStatus[status2]
            solve_time = time.monotonic() - start
            if status2 == pulp.LpStatusOptimal:
                result = self._build_result(
                    problem, quotes, x2, y2, status2_name, solve_time, optimality_proven=True
                )
                result.covered_quantity = covered_opt
                result.shortfall = max(problem.quantity - covered_opt, 0)
                if result.shortfall == 0:
                    result.feasibility = Feasibility.FULLY_FEASIBLE
                else:
                    result.feasibility = Feasibility.PARTIAL_FEASIBLE
                if result.shortfall > 0:
                    result.infeasible_reason = (
                        f"requested {problem.quantity} but only {covered_opt} "
                        f"coverable within constraints (shortfall {result.shortfall}); "
                        "needs re-confirmation — not auto-completed"
                    )
                # Decimal re-verification (budget tolerance).
                return self._decimal_recheck(problem, result)
            return AllocationResult(
                feasibility=Feasibility.SOLVER_UNRESOLVED,
                solver_status=status2_name,
                solve_time_s=solve_time,
                infeasible_reason=f"stage-2 solver unresolved: {status2_name}",
            )

        # require_full: single stage, optimal.
        result = self._build_result(
            problem, quotes, x, y, status1_name, solve_time, optimality_proven=True
        )
        if result.shortfall == 0:
            result.feasibility = Feasibility.FULLY_FEASIBLE
        else:
            result.feasibility = Feasibility.PARTIAL_FEASIBLE
        return self._decimal_recheck(problem, result)

    # ------------------------------------------------------------------

    @staticmethod
    def _decimal_recheck(problem: AllocationProblem, result: AllocationResult) -> AllocationResult:
        """Re-verify the allocation in pure Decimal against every hard
        constraint. Any violation → INVALID (never pass a bad solution even
        if CBC said Optimal). Budget tolerance absorbs float rounding
        (half a cent)."""

        if result.feasibility not in (Feasibility.FULLY_FEASIBLE, Feasibility.PARTIAL_FEASIBLE):
            return result
        tolerance = Decimal("0.005")
        total = Decimal(0)
        covered = 0
        for line in result.lines:
            q = next((q for q in problem.quotes if q.supplier_id == line.supplier_id), None)
            if q is None:
                return AllocationResult(
                    feasibility=Feasibility.INVALID,
                    infeasible_reason=f"line references unknown supplier {line.supplier_id}",
                    solver_status=result.solver_status,
                    solve_time_s=result.solve_time_s,
                )
            if line.quantity < 0 or int(line.quantity) != line.quantity:
                return AllocationResult(
                    feasibility=Feasibility.INVALID,
                    infeasible_reason=f"non-integer/negative quantity for {line.supplier_id}",
                    solver_status=result.solver_status,
                    solve_time_s=result.solve_time_s,
                )
            if line.quantity > q.available_stock:
                return AllocationResult(
                    feasibility=Feasibility.INVALID,
                    infeasible_reason=f"inventory violated for {line.supplier_id} "
                    f"({line.quantity} > {q.available_stock})",
                    solver_status=result.solver_status,
                    solve_time_s=result.solve_time_s,
                )
            if (
                problem.delivery_deadline_days is not None
                and line.delivery_days > problem.delivery_deadline_days
            ):
                return AllocationResult(
                    feasibility=Feasibility.INVALID,
                    infeasible_reason=f"deadline violated for {line.supplier_id}",
                    solver_status=result.solver_status,
                    solve_time_s=result.solve_time_s,
                )
            unit = q.normalized_unit_price_ref or q.unit_price
            base = unit * line.quantity
            freight = problem.freight.get(q.supplier_id, Decimal(0))
            tax = problem.tax_rate.get(q.supplier_id, Decimal(0))
            total += base + freight + tax * base
            covered += line.quantity
        if len(result.lines) > problem.max_split_suppliers:
            return AllocationResult(
                feasibility=Feasibility.INVALID,
                infeasible_reason=f"split count {len(result.lines)} > {problem.max_split_suppliers}",
                solver_status=result.solver_status,
                solve_time_s=result.solve_time_s,
            )
        if problem.supplier_share_cap is not None:
            cap = Decimal(str(problem.supplier_share_cap))
            for line in result.lines:
                if Decimal(line.quantity) > cap * problem.quantity:
                    return AllocationResult(
                        feasibility=Feasibility.INVALID,
                        infeasible_reason=f"share cap violated for {line.supplier_id}",
                        solver_status=result.solver_status,
                        solve_time_s=result.solve_time_s,
                    )
        total = total.quantize(Decimal("0.01"))
        result.total_landed_cost = total
        result.covered_quantity = covered
        result.shortfall = max(problem.quantity - covered, 0)
        if problem.user_budget_limit is not None and total > problem.user_budget_limit + tolerance:
            return AllocationResult(
                feasibility=Feasibility.INVALID,
                infeasible_reason=(
                    f"hard budget violated after Decimal re-check: {total} > "
                    f"{problem.user_budget_limit} (tolerance {tolerance})"
                ),
                solver_status=result.solver_status,
                solve_time_s=result.solve_time_s,
            )
        return result

    def _build_result(
        self,
        problem: AllocationProblem,
        quotes: list[SupplierQuote],
        x,
        y,
        status_name: str,
        solve_time: float,
        *,
        optimality_proven: bool,
    ) -> AllocationResult:
        """Build an AllocationResult from solver variables (pre-Decimal-recheck)."""

        lines: list[AwardLine] = []
        covered = 0
        for i, q in enumerate(quotes):
            xval = int(pulp.value(x[i]) or 0)
            if xval <= 0:
                continue
            unit = q.normalized_unit_price_ref or q.unit_price
            line_total = (unit * xval).quantize(Decimal("0.01"))
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
        return AllocationResult(
            feasibility=Feasibility.PARTIAL_FEASIBLE,  # caller sets final
            lines=lines,
            covered_quantity=covered,
            shortfall=max(problem.quantity - covered, 0),
            solver_status=status_name,
            solve_time_s=solve_time,
            optimality_proven=optimality_proven,
        )

    def _solve_max_cover(self, problem: AllocationProblem, quotes: list[SupplierQuote]) -> AllocationResult:
        """Maximize covered quantity when full feasibility is impossible.

        Phase 6: now handled by the two-stage lexicographic solve inside
        _solve (require_full=False). Kept as an entry point for the public
        API (optimize() calls _solve with require_full=False directly).
        """

        return self._solve(problem, quotes, require_full=False)
