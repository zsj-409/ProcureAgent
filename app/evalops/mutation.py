"""Phase 11B — Candidate Mutation Harness (Commit A).

Proves that a REAL candidate policy regression (not a pre-filled verdict)
is detected by the independent verifier and blocked by the release gate.

Design:
- Baseline Candidate: the existing weighted-greedy allocation (real code).
- Mutant A (Hard Budget Regression): a mutation of the allocation that
  over-spends the user's hard budget (choose the most expensive admitted
  supplier beyond budget).
- Mutant B (Expired Quote Regression): allocate from an EXPIRED quote.
- Mutant C (Supplier Constraint Regression): allocate from a supplier
  outside the whitelist / beyond stock.

Every mutant runs in an ISOLATED world (fresh make_world snapshot, fixed
seed) with its OWN database/task id. The independent
AllocationConstraintVerifier recomputes every constraint from the problem
and the actual candidate lines — it never trusts candidate.is_valid.

The mutations are TEST-ONLY fixtures; the production policy/guard code is
never modified.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TESTS_EVAL = Path(__file__).resolve().parents[2] / "tests" / "eval"
if str(TESTS_EVAL) not in sys.path:
    sys.path.insert(0, str(TESTS_EVAL))

from procurement_world import make_world  # type: ignore # noqa: E402

from app.procurement.optimizer import Feasibility  # noqa: E402
from app.procurement.verifier import AllocationConstraintVerifier  # noqa: E402


@dataclass
class MutationSpec:
    name: str
    mutate: Callable[[Any, list[dict]], list[dict]]  # (world, base_lines) -> mutated lines
    description: str


def _world(seed: int = 7, name: str = "mutation-world", family: str = "multi_supplier_joint"):
    """Fresh isolated world — the SAME seed gives the SAME quote snapshot
    for baseline and all mutants (fixed budget/quote snapshot). The hard
    budget and admission whitelist are set explicitly so the mutants have
    real constraints to violate."""
    world = make_world(family, seed=seed, split="DEV", name=name)
    # Set explicit constraints for the mutation experiment (fixed budget /
    # whitelist). Minimum legal landed cost for 15 units is 6×10 + 6×11 +
    # 3×12 = 162 (s1+s2 fill their stock, s3 tops up); a budget of 162 makes
    # the baseline exactly legal, while ANY over-spend (mutant_a allocates
    # the whole quantity to the most expensive s3: 180) is a hard-budget
    # violation the verifier must catch.
    from decimal import Decimal

    world.user_budget_limit = Decimal("162")
    # Whitelist admits ALL suppliers (baseline can fully fulfill at 162);
    # mutant_c instead violates inventory by allocating beyond stock.
    world.allowed_suppliers = ["s1", "s2", "s3"]
    # Give s3 large stock so mutant_a can allocate the FULL quantity to the
    # most expensive supplier (12×15 = 180 > 162 budget). s1/s2 keep stock 6
    # so the baseline's cheapest legal mix is exactly 162.
    for q in world.quotes:
        if q.supplier_id == "s3":
            q.available_stock = 15
    return world


def _problem(world):
    from procurement_adapters import _world_to_problem  # type: ignore

    return _world_to_problem(world)


def _baseline_lines(world) -> list[dict]:
    """Run the REAL weighted-greedy allocation (existing business code)."""
    from procurement_adapters import GreedyAdapter  # type: ignore

    candidate = GreedyAdapter().run(world)
    return list(candidate.lines)


# ---------------------------------------------------------------------------
# Mutants (TEST-ONLY fixtures — never wired into the business path)
# ---------------------------------------------------------------------------

def _mutant_a_hard_budget(world, base_lines):
    """Over-spend the hard budget: take the most expensive ADMITTED
    supplier with stock and allocate the whole quantity to it."""
    problem = _problem(world)
    quotes = sorted(
        (q for q in problem.quotes if q.available_stock >= world.quantity),
        key=lambda q: (q.normalized_unit_price_ref or q.unit_price),
        reverse=True,
    )
    if not quotes:
        return base_lines
    return [{"supplier_id": quotes[0].supplier_id, "quantity": world.quantity}]


def _mutant_b_expired_quote(world, base_lines):
    """Allocate from a quote that has EXPIRED (past quote_valid_until)."""
    now = datetime.now(UTC)
    problem = _problem(world)
    expired = [
        q for q in problem.quotes
        if q.quote_valid_until is not None and q.quote_valid_until < now
    ]
    if not expired:
        # Expire the first quote deterministically (mutate the WORLD's quote
        # so the verifier sees the same snapshot).
        q = problem.quotes[0]
        for wq in world.quotes:
            if wq.supplier_id == q.supplier_id:
                wq.quote_valid_until = now - __import__("datetime").timedelta(hours=1)
        return [{"supplier_id": q.supplier_id, "quantity": min(q.available_stock, world.quantity)}]
    q = expired[0]
    return [{"supplier_id": q.supplier_id, "quantity": min(q.available_stock, world.quantity)}]


def _mutant_c_supplier_constraint(world, base_lines):
    """Violate inventory: allocate beyond a supplier's available stock."""
    problem = _problem(world)
    q = min(problem.quotes, key=lambda x: x.available_stock)
    return [{"supplier_id": q.supplier_id, "quantity": q.available_stock + 1}]


MUTANTS: dict[str, MutationSpec] = {
    "baseline": MutationSpec(
        "baseline",
        lambda world, lines: lines,
        "real weighted-greedy allocation (unchanged)",
    ),
    "mutant_a_hard_budget": MutationSpec(
        "mutant_a_hard_budget",
        _mutant_a_hard_budget,
        "allocates the full quantity to the most expensive admitted supplier "
        "(total landed cost exceeds the user's hard budget)",
    ),
    "mutant_b_expired_quote": MutationSpec(
        "mutant_b_expired_quote",
        _mutant_b_expired_quote,
        "allocates from a quote whose validity window has expired",
    ),
    "mutant_c_supplier_constraint": MutationSpec(
        "mutant_c_supplier_constraint",
        _mutant_c_supplier_constraint,
        "allocates from a supplier outside the admission whitelist / beyond stock",
    ),
}


@dataclass
class MutationRunResult:
    candidate_id: str
    mutant: str
    world_hash: str
    input_hash: str
    lines: list[dict]
    verifier: dict[str, Any]  # independent constraint result
    gate_status: str
    violation_reason: str | None
    trace_id: str | None = None
    manifest_ref: str | None = None
    executed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "mutant": self.mutant,
            "world_hash": self.world_hash,
            "input_hash": self.input_hash,
            "lines": self.lines,
            "verifier": self.verifier,
            "gate_status": self.gate_status,
            "violation_reason": self.violation_reason,
            "trace_id": self.trace_id,
            "manifest_ref": self.manifest_ref,
            "executed_at": self.executed_at,
        }


def run_mutation(
    *,
    mutant: str,
    world_seed: int = 7,
    candidate_id: str | None = None,
) -> MutationRunResult:
    """Run ONE mutant in an isolated world and verify independently."""
    world = _world(seed=world_seed, name=f"{mutant}-world")
    problem = _problem(world)
    base_lines = _baseline_lines(world)
    spec = MUTANTS[mutant]
    lines = spec.mutate(world, base_lines)

    # Independent verifier: recompute EVERY constraint from problem + lines.
    verifier = AllocationConstraintVerifier()
    verified = verifier.verify(problem, lines, method=f"mutation:{mutant}")
    invalid = verified.feasibility == Feasibility.INVALID
    gate_status = "FAIL" if invalid else "PASS"
    violation_reason = "; ".join(verified.violations) if verified.violations else None

    input_hash = json.dumps(
        {"world_seed": world_seed, "mutant": mutant, "quantity": world.quantity},
        sort_keys=True,
    )
    import hashlib

    input_hash = hashlib.sha256(input_hash.encode()).hexdigest()[:16]

    return MutationRunResult(
        candidate_id=candidate_id or f"{mutant}-{world_seed}",
        mutant=mutant,
        world_hash=world.snapshot_hash(),
        input_hash=input_hash,
        lines=lines,
        verifier={
            "feasibility": verified.feasibility.value,
            "total_landed_cost": str(verified.total_landed_cost),
            "covered_quantity": verified.covered_quantity,
            "shortfall": verified.shortfall,
            "violations": verified.violations,
            "solver_status": verified.solver_status,
            "optimality_proven": verified.optimality_proven,
        },
        gate_status=gate_status,
        violation_reason=violation_reason,
    )


def run_mutation_batch(world_seed: int = 7) -> list[MutationRunResult]:
    """Run baseline + all mutants in isolated worlds."""
    results = []
    for name in MUTANTS:
        results.append(run_mutation(mutant=name, world_seed=world_seed))
    return results
