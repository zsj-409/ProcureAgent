"""Phase 5 — greedy vs cheapest-feasible vs exact-optimizer benchmark.

Runs a catalog of allocation problems and compares:
- Weighted Greedy (existing ProcurementScorer split award)
- Cheapest Feasible (fill from the cheapest supplier first — a simple
  baseline that respects stock but ignores budget/split/share)
- Exact Optimizer (this phase: integer program minimizing landed cost)

Metrics per problem: allocation feasibility, optimality gap (vs the LP
lower bound or the best feasible), total landed cost, constraint violation,
solver runtime. Machine-readable output to docs/eval/.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.procurement.optimizer import AllocationProblem, Feasibility, ProcurementOptimizer
from app.procurement.schemas import Preference, SupplierQuote
from app.procurement.scorer import ProcurementScorer

OUT = Path(__file__).resolve().parents[2] / "docs" / "eval"


def _quote(supplier_id: str, *, price: Decimal, stock: int, delivery_days: int = 3, currency: str = "USD") -> SupplierQuote:
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="Bench Item",
        unit_price=price,
        currency=currency,
        available_stock=stock,
        delivery_days=delivery_days,
        source_type="api",
        normalization_status="comparable",
        normalized_unit_price_ref=price,
        reference_currency=currency,
        quote_valid_until=datetime.now(UTC) + timedelta(days=1),
    )


def greedy_cost(quotes, quantity, preference=Preference.BALANCED) -> tuple[Decimal, int]:
    """Run the existing weighted-greedy split; return (cost, covered)."""

    try:
        rec = ProcurementScorer().score(quotes, quantity, preference)
        if rec.award_split:
            covered = sum(line.quantity for line in rec.award_split)
            cost = sum(line.line_total for line in rec.award_split)
            return cost, covered
        # Single-line award.
        if rec.quotes:
            best = rec.quotes[0]
            covered = min(best.available_stock, quantity)
            cost = (best.normalized_unit_price_ref or best.unit_price) * covered
            return cost, covered
        return Decimal(0), 0
    except Exception:
        return Decimal(0), 0


def cheapest_feasible(quotes, quantity) -> tuple[Decimal, int, list]:
    """Fill from the cheapest supplier first (stock-respecting)."""

    ordered = sorted(
        [q for q in quotes if q.is_comparable()],
        key=lambda q: (q.normalized_unit_price_ref or q.unit_price),
    )
    remaining = quantity
    total = Decimal(0)
    covered = 0
    lines = []
    for q in ordered:
        if remaining <= 0:
            break
        take = min(q.available_stock, remaining)
        if take <= 0:
            continue
        unit = q.normalized_unit_price_ref or q.unit_price
        total += unit * take
        covered += take
        remaining -= take
        lines.append({"supplier_id": q.supplier_id, "quantity": take})
    return total.quantize(Decimal("0.01")), covered, lines


def run_case(case: dict) -> dict:
    """Run one problem through all three methods."""

    quotes = [_quote(q["id"], price=q["price"], stock=q["stock"], delivery_days=q.get("delivery", 3)) for q in case["suppliers"]]
    quantity = case["quantity"]
    budget = case.get("budget")
    deadline = case.get("deadline")
    max_split = case.get("max_split", 3)

    # 1. Greedy (baseline)
    t0 = time.monotonic()
    g_cost, g_covered = greedy_cost(quotes, quantity)
    g_time = time.monotonic() - t0

    # 2. Cheapest feasible
    t0 = time.monotonic()
    c_cost, c_covered, _c_lines = cheapest_feasible(quotes, quantity)
    c_time = time.monotonic() - t0

    # 3. Exact optimizer (with budget + deadline + max_split constraints)
    t0 = time.monotonic()
    opt = ProcurementOptimizer().optimize(
        AllocationProblem(
            quotes=quotes,
            quantity=quantity,
            user_budget_limit=budget,
            delivery_deadline_days=deadline,
            max_split_suppliers=max_split,
            time_limit_s=10,
        )
    )
    o_time = time.monotonic() - t0

    # Constraint violations: optimizer output must respect every hard rule.
    violations = []
    if opt.feasibility == Feasibility.FULLY_FEASIBLE:
        covered_sum = sum(line.quantity for line in opt.lines)
        if covered_sum != quantity:
            violations.append(f"quantity {covered_sum} != {quantity}")
        if deadline is not None:
            for line in opt.lines:
                if line.delivery_days > deadline:
                    violations.append(f"deadline {line.delivery_days} > {deadline}")
        if len(opt.lines) > max_split:
            violations.append(f"split {len(opt.lines)} > {max_split}")
        if budget is not None and opt.total_landed_cost > budget:
            violations.append(f"budget {opt.total_landed_cost} > {budget}")

    return {
        "case": case["name"],
        "quantity": quantity,
        "suppliers": len(quotes),
        "budget": str(budget) if budget else "",
        "deadline": deadline or "",
        "greedy_cost": str(g_cost),
        "greedy_covered": g_covered,
        "greedy_time_s": round(g_time, 4),
        "cheapest_cost": str(c_cost),
        "cheapest_covered": c_covered,
        "cheapest_time_s": round(c_time, 4),
        "optimizer_cost": str(opt.total_landed_cost),
        "optimizer_covered": opt.covered_quantity,
        "optimizer_feasibility": opt.feasibility.value,
        "optimizer_time_s": round(o_time, 4),
        "optimizer_solver": opt.solver_status,
        "constraint_violations": "; ".join(violations),
        "greedy_optimal_gap": (
            round((float(g_cost) - float(opt.total_landed_cost)) / max(float(opt.total_landed_cost), 1e-9) * 100, 2)
            if opt.feasibility == Feasibility.FULLY_FEASIBLE and g_covered == quantity
            else ""
        ),
    }


CASES = [
    {
        "name": "single_cheapest",
        "quantity": 50,
        "suppliers": [
            {"id": "s1", "price": Decimal("10"), "stock": 100},
            {"id": "s2", "price": Decimal("12"), "stock": 100},
        ],
    },
    {
        "name": "stock_scarcity_greedy_suboptimal",
        "quantity": 100,
        "suppliers": [
            {"id": "s1", "price": Decimal("10"), "stock": 60},
            {"id": "s2", "price": Decimal("11"), "stock": 60},
        ],
    },
    {
        "name": "budget_constrained",
        "quantity": 100,
        "budget": Decimal("1050"),
        "suppliers": [
            {"id": "s1", "price": Decimal("10"), "stock": 100},
            {"id": "s2", "price": Decimal("12"), "stock": 100},
        ],
    },
    {
        "name": "deadline_constrained",
        "quantity": 40,
        "deadline": 3,
        "suppliers": [
            {"id": "s1", "price": Decimal("10"), "stock": 100, "delivery": 1},
            {"id": "s2", "price": Decimal("8"), "stock": 100, "delivery": 10},
        ],
    },
    {
        "name": "split_cap",
        "quantity": 25,
        "max_split": 2,
        "suppliers": [
            {"id": "s1", "price": Decimal("10"), "stock": 10},
            {"id": "s2", "price": Decimal("11"), "stock": 10},
            {"id": "s3", "price": Decimal("12"), "stock": 10},
        ],
    },
    {
        "name": "many_suppliers",
        "quantity": 150,
        "max_split": 5,
        "suppliers": [
            {"id": f"s{i}", "price": Decimal(str(10 + i * 0.5)), "stock": 30 + i * 5} for i in range(8)
        ],
    },
    {
        "name": "deep_stock_split_cap",
        "quantity": 100,
        "max_split": 2,
        "suppliers": [
            {"id": "s1", "price": Decimal("10"), "stock": 100},
            {"id": "s2", "price": Decimal("10.2"), "stock": 100},
            {"id": "s3", "price": Decimal("10.4"), "stock": 100},
        ],
    },
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [run_case(case) for case in CASES]

    # CSV
    csv_path = OUT / "optimization_comparison.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {csv_path}")

    # Summary JSON
    summary = {
        "date": datetime.now(UTC).isoformat(),
        "n_cases": len(rows),
        "methods": ["greedy", "cheapest_feasible", "exact_optimizer"],
        "per_case": rows,
        "aggregates": {
            "optimizer_fully_feasible": sum(1 for r in rows if r["optimizer_feasibility"] == "FULLY_FEASIBLE"),
            "optimizer_constraint_violations": sum(1 for r in rows if r["constraint_violations"]),
            "optimizer_total_time_s": round(sum(r["optimizer_time_s"] for r in rows), 3),
            "greedy_beat_optimizer_cases": sum(
                1 for r in rows if r["optimizer_feasibility"] == "FULLY_FEASIBLE"
                and r["greedy_covered"] == r["quantity"]
                and Decimal(r["greedy_cost"]) < Decimal(r["optimizer_cost"])
            ),
        },
        "solver": "PuLP 3.3.2 / CBC",
    }
    (OUT / "optimization_benchmark.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT / 'optimization_benchmark.json'}")

    print("\ncase | greedy | cheapest | optimizer | feasibility | violations")
    for r in rows:
        print(
            f"{r['case']:32s} | {r['greedy_cost']} | {r['cheapest_cost']} | "
            f"{r['optimizer_cost']} | {r['optimizer_feasibility']} | {r['constraint_violations'] or '-'}"
        )


if __name__ == "__main__":
    main()
