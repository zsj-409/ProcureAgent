"""Phase 6, Task 5 — fair baseline adapters.

Every method accepts the SAME public ProcurementWorld input and returns the
SAME `AllocationCandidate` shape, so the evaluator never accidentally favors
one method by data structure.

Methods:
- A: Existing Greedy Split Award (ProcurementScorer, unchanged)
- B: Naive Cheapest-Price Allocation
- C: Constrained Optimizer (two-stage, Decimal re-checked)
- D: Independent Small-World Oracle (only for worlds it can handle)

Fairness rules:
- A greedy 105/150 plan is reported as (covered=105, shortfall=45) with its
  true cost, NEVER compared as "cheaper" than an optimizer 150/150 plan.
- Only when both methods FULLY fulfill may a Full Allocation Cost Saving be
  computed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.procurement.optimizer import AllocationProblem, Feasibility, ProcurementOptimizer
from app.procurement.oracle import SmallWorldOracle
from app.procurement.schemas import Preference
from app.procurement.scorer import ProcurementScorer
from app.procurement.verifier import AllocationConstraintVerifier
from tests.eval.procurement_world import ProcurementWorld


@dataclass
class AllocationCandidate:
    """Unified output shape for every method."""

    method: str
    lines: list[dict[str, Any]] = field(default_factory=list)  # supplier_id, quantity
    feasibility: str = ""
    covered_quantity: int = 0
    shortfall: int = 0
    total_landed_cost: Decimal = Decimal(0)
    solver_status: str = ""
    optimality_proven: bool = False
    solve_time_s: float = 0.0
    violations: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "lines": self.lines,
            "feasibility": self.feasibility,
            "covered_quantity": self.covered_quantity,
            "shortfall": self.shortfall,
            "total_landed_cost": str(self.total_landed_cost),
            "solver_status": self.solver_status,
            "optimality_proven": self.optimality_proven,
            "solve_time_s": round(self.solve_time_s, 4),
            "violations": self.violations,
            "detail": self.detail,
        }


def _world_to_problem(world: ProcurementWorld) -> AllocationProblem:
    return AllocationProblem(
        quotes=world.quotes,
        quantity=world.quantity,
        reference_currency=world.reference_currency,
        user_budget_limit=world.user_budget_limit,
        delivery_deadline_days=world.delivery_deadline_days,
        max_split_suppliers=world.max_split_suppliers,
        supplier_share_cap=world.supplier_share_cap,
        allowed_suppliers=world.allowed_suppliers,
        freight=world.freight,
        tax_rate=world.tax_rate,
    )


def _candidate_from_problem(
    problem: AllocationProblem,
    method: str,
    lines: list[dict[str, Any]],
    solver_status: str = "",
    optimality_proven: bool = False,
    solve_time_s: float = 0.0,
    detail: str = "",
) -> AllocationCandidate:
    """Verify the candidate with the independent verifier and normalize."""

    verified = AllocationConstraintVerifier().verify(
        problem,
        lines,
        method=method,
        solver_status=solver_status,
        optimality_proven=optimality_proven,
    )
    return AllocationCandidate(
        method=method,
        lines=lines,
        feasibility=verified.feasibility.value,
        covered_quantity=verified.covered_quantity,
        shortfall=verified.shortfall,
        total_landed_cost=verified.total_landed_cost,
        solver_status=solver_status,
        optimality_proven=optimality_proven,
        solve_time_s=solve_time_s,
        violations=verified.violations,
        detail=detail,
    )


class GreedyAdapter:
    """A: existing weighted-greedy split award (scorer untouched)."""

    def run(self, world: ProcurementWorld) -> AllocationCandidate:
        import time

        start = time.monotonic()
        problem = _world_to_problem(world)
        try:
            rec = ProcurementScorer().score(
                world.quotes,
                world.quantity,
                Preference.BALANCED,
                max_split=world.max_split_suppliers,
            )
        except Exception as exc:
            return AllocationCandidate(
                method="greedy",
                feasibility=Feasibility.INFEASIBLE.value,
                detail=f"scorer error: {exc}",
                solve_time_s=time.monotonic() - start,
            )
        lines: list[dict[str, Any]] = []
        if rec.award_split:
            lines = [
                {"supplier_id": line.supplier_id, "quantity": line.quantity}
                for line in rec.award_split
            ]
        elif rec.quotes:
            best = rec.quotes[0]
            lines = [{"supplier_id": best.supplier_id, "quantity": min(best.available_stock, world.quantity)}]
        return _candidate_from_problem(
            problem, "greedy", lines, solver_status="greedy", solve_time_s=time.monotonic() - start
        )


class CheapestAdapter:
    """B: naive cheapest-price allocation (fill from cheapest first)."""

    def run(self, world: ProcurementWorld) -> AllocationCandidate:
        import time

        start = time.monotonic()
        problem = _world_to_problem(world)
        ordered = sorted(
            [q for q in world.quotes if q.is_comparable()],
            key=lambda q: (q.normalized_unit_price_ref or q.unit_price),
        )
        remaining = world.quantity
        lines: list[dict[str, Any]] = []
        for q in ordered:
            if remaining <= 0:
                break
            take = min(q.available_stock, remaining)
            if take <= 0:
                continue
            lines.append({"supplier_id": q.supplier_id, "quantity": take})
            remaining -= take
        return _candidate_from_problem(
            problem, "cheapest", lines, solver_status="greedy-heuristic", solve_time_s=time.monotonic() - start
        )


class OptimizerAdapter:
    """C: constrained optimizer (two-stage, Decimal re-checked)."""

    def __init__(self) -> None:
        self._optimizer = ProcurementOptimizer()

    def run(self, world: ProcurementWorld) -> AllocationCandidate:
        problem = _world_to_problem(world)
        result = self._optimizer.optimize(problem)
        lines = [
            {"supplier_id": line.supplier_id, "quantity": line.quantity}
            for line in result.lines
        ]
        verified = AllocationConstraintVerifier().verify(
            problem, lines, method="optimizer", solver_status=result.solver_status,
            optimality_proven=result.optimality_proven,
        )
        return AllocationCandidate(
            method="optimizer",
            lines=lines,
            feasibility=verified.feasibility.value,
            covered_quantity=verified.covered_quantity,
            shortfall=verified.shortfall,
            total_landed_cost=verified.total_landed_cost,
            solver_status=result.solver_status,
            optimality_proven=result.optimality_proven,
            solve_time_s=result.solve_time_s,
            violations=verified.violations,
            detail=result.infeasible_reason,
        )


class OracleAdapter:
    """D: independent small-world oracle (only for worlds it can handle)."""

    def __init__(self) -> None:
        self._oracle = SmallWorldOracle()

    def run(self, world: ProcurementWorld) -> AllocationCandidate:
        problem = _world_to_problem(world)
        if not self._oracle.can_handle(problem):
            return AllocationCandidate(
                method="oracle",
                feasibility=Feasibility.SOLVER_UNRESOLVED.value,
                detail="world too large for exhaustive oracle",
            )
        result = self._oracle.optimize(problem)
        if not result.feasible:
            return AllocationCandidate(
                method="oracle",
                feasibility=Feasibility.INFEASIBLE.value,
                detail="oracle found no feasible allocation",
                solve_time_s=0.0,
            )
        lines = result.optimal_allocation
        verified = AllocationConstraintVerifier().verify(problem, lines, method="oracle")
        return AllocationCandidate(
            method="oracle",
            lines=lines,
            feasibility=verified.feasibility.value,
            covered_quantity=verified.covered_quantity,
            shortfall=verified.shortfall,
            total_landed_cost=verified.total_landed_cost,
            solver_status="exhaustive",
            optimality_proven=True,
            violations=verified.violations,
        )


def run_all_adapters(world: ProcurementWorld) -> list[AllocationCandidate]:
    """Run A/B/C (+D when applicable) on the SAME world."""

    candidates = [
        GreedyAdapter().run(world),
        CheapestAdapter().run(world),
        OptimizerAdapter().run(world),
    ]
    oracle = OracleAdapter().run(world)
    if oracle.feasibility != Feasibility.SOLVER_UNRESOLVED.value:
        candidates.append(oracle)
    return candidates
