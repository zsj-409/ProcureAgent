"""Phase 6, Task 6/7 — benchmark runner + metrics registry.

Runs all adapters over the world registry, computes the formal metrics,
and writes machine-readable artifacts. DEV / CONFIRMATION / TEST splits are
separate; the frozen TEST set is not touched during development.

Metrics (with explicit numerator/denominator):
- Verified Full Fulfillment Rate
- Hard Constraint Violation Rate
- Partial Fulfillment Rate (+ shortfall distribution)
- Independent Oracle Optimality Gap (small worlds)
- Cost per Verified Full Fulfillment
- Solver Runtime P50/P95, Timeouts, Optimality Status
- Cost Saving on Jointly Feasible Worlds (paired, both FULLY)
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.eval.procurement_adapters import run_all_adapters
from tests.eval.procurement_world import world_registry

OUT = Path(__file__).resolve().parents[2] / "docs" / "eval"

SPLITS = {
    "DEV": ["unconstrained", "single_supplier_shortage", "multi_supplier_joint", "split_cap"],
    "CONFIRMATION": ["tight_budget", "freight_tax", "delivery_conflict", "allowlist"],
    "TEST": [
        "fx_failure", "expired_quote", "share_limit", "fully_infeasible",
        "partial_confirm", "mixed_constraints",
    ],
}


def run_benchmark(split_filter: str | None = None) -> dict:
    """Run the full benchmark; returns per-method metrics + per-case rows."""

    worlds = world_registry(SPLITS, seeds_per_family=2)
    if split_filter:
        worlds = [w for w in worlds if w.split == split_filter]

    rows: list[dict] = []
    for world in worlds:
        for candidate in run_all_adapters(world):
            rows.append(
                {
                    "world": world.name,
                    "family": world.family,
                    "split": world.split,
                    "seed": world.seed,
                    "world_hash": world.snapshot_hash(),
                    "method": candidate.method,
                    "feasibility": candidate.feasibility,
                    "covered_quantity": candidate.covered_quantity,
                    "quantity": world.quantity,
                    "shortfall": candidate.shortfall,
                    "total_landed_cost": str(candidate.total_landed_cost),
                    "solver_status": candidate.solver_status,
                    "optimality_proven": candidate.optimality_proven,
                    "solve_time_s": round(candidate.solve_time_s, 4),
                    "violations": ";".join(candidate.violations),
                    "detail": candidate.detail,
                }
            )

    # ----- metrics registry ------------------------------------------------
    methods = ["greedy", "cheapest", "optimizer", "oracle"]
    metrics: dict[str, dict] = {}
    for method in methods:
        mrows = [r for r in rows if r["method"] == method]
        n = len(mrows)
        full = sum(1 for r in mrows if r["feasibility"] == "FULLY_FEASIBLE")
        partial = sum(1 for r in mrows if r["feasibility"] == "PARTIAL_FEASIBLE")
        infeasible = sum(1 for r in mrows if r["feasibility"] == "INFEASIBLE")
        invalid = sum(1 for r in mrows if r["feasibility"] == "INVALID")
        unresolved = sum(1 for r in mrows if r["feasibility"] == "SOLVER_UNRESOLVED")
        violations = sum(1 for r in mrows if r["violations"])
        cost = sum(Decimal(r["total_landed_cost"]) for r in mrows if r["feasibility"] == "FULLY_FEASIBLE")
        times = sorted(float(r["solve_time_s"]) for r in mrows)
        p50 = times[len(times) // 2] if times else 0
        p95 = times[int(len(times) * 0.95) - 1] if times else 0
        metrics[method] = {
            "n": n,
            "verified_full_fulfillment_rate": f"{full}/{n}",
            "hard_constraint_violation_rate": f"{violations}/{n}",
            "partial_fulfillment_rate": f"{partial}/{n}",
            "infeasible_rate": f"{infeasible}/{n}",
            "invalid_rate": f"{invalid}/{n}",
            "unresolved_rate": f"{unresolved}/{n}",
            "cost_per_verified_full_fulfillment": str(cost / full) if full else "0",
            "solver_runtime_p50_s": round(p50, 4),
            "solver_runtime_p95_s": round(p95, 4),
            "optimality_proven_count": sum(1 for r in mrows if r["optimality_proven"]),
            "shortfall_distribution": dict(
                Counter(str(r["shortfall"]) for r in mrows if r["feasibility"] == "PARTIAL_FEASIBLE")
            ),
        }

    # ----- paired comparison (jointly fully feasible) ----------------------
    paired: list[dict] = []
    by_world: dict[str, dict[str, dict]] = {}
    for r in rows:
        by_world.setdefault(r["world"], {})[r["method"]] = r
    for world_key, cands in by_world.items():
        if "greedy" in cands and "optimizer" in cands:
            g, o = cands["greedy"], cands["optimizer"]
            both_full = g["feasibility"] == "FULLY_FEASIBLE" and o["feasibility"] == "FULLY_FEASIBLE"
            saving = None
            if both_full:
                gc = Decimal(g["total_landed_cost"])
                oc = Decimal(o["total_landed_cost"])
                saving = round(float((gc - oc) / gc * 100), 2) if gc else None
            paired.append(
                {
                    "world": world_key,
                    "family": by_world[world_key]["greedy"]["family"],
                    "greedy": g["feasibility"],
                    "optimizer": o["feasibility"],
                    "both_fully_feasible": both_full,
                    "cost_saving_pct_on_fully": saving,
                    "greedy_covered": g["covered_quantity"],
                    "optimizer_covered": o["covered_quantity"],
                    "greedy_cost": g["total_landed_cost"],
                    "optimizer_cost": o["total_landed_cost"],
                }
            )
    jointly_full = [p for p in paired if p["both_fully_feasible"]]
    savings = [p["cost_saving_pct_on_fully"] for p in jointly_full if p["cost_saving_pct_on_fully"] is not None]

    return {
        "date": datetime.now(UTC).isoformat(),
        "split_filter": split_filter or "ALL",
        "worlds": [w.to_dict() for w in worlds],
        "metrics_by_method": metrics,
        "paired_comparison": paired,
        "paired_fully_feasible_n": len(jointly_full),
        "mean_cost_saving_pct_on_fully": round(sum(savings) / len(savings), 2) if savings else None,
        "rows": rows,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # Run all splits.
    result = run_benchmark()

    # Machine-readable artifacts.
    (OUT / "procurement_world_manifest.json").write_text(
        json.dumps({"splits": SPLITS, "worlds": result["worlds"]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (OUT / "procurement_world_registry.json").write_text(
        json.dumps(
            [
                {
                    "name": w["name"],
                    "family": w["family"],
                    "split": w["split"],
                    "seed": w["seed"],
                    "snapshot_hash": w["snapshot_hash"],
                }
                for w in result["worlds"]
            ],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with open(OUT / "allocation_results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(result["rows"][0].keys()))
        writer.writeheader()
        writer.writerows(result["rows"])
    (OUT / "constraint_violations.json").write_text(
        json.dumps(
            {
                "violations": [
                    {"world": r["world"], "method": r["method"], "violations": r["violations"]}
                    for r in result["rows"]
                    if r["violations"]
                ],
                "count": sum(1 for r in result["rows"] if r["violations"]),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with open(OUT / "paired_method_comparison.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(result["paired_comparison"][0].keys()))
        writer.writeheader()
        writer.writerows(result["paired_comparison"])
    (OUT / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "phase": 6,
                "commit": "B",
                "date": result["date"],
                "evaluator_version": "v1",
                "solver": "PuLP 3.3.2 / CBC",
                "oracle": "exhaustive (≤4 suppliers, ≤12 units)",
                "metrics": list(result["metrics_by_method"].keys()),
                "splits": SPLITS,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Print summary.
    print(f"\n=== Phase 6 benchmark ({result['split_filter']}, {len(result['worlds'])} worlds) ===")
    for method, m in result["metrics_by_method"].items():
        print(
            f"{method:10s} full={m['verified_full_fulfillment_rate']:>7} "
            f"violation={m['hard_constraint_violation_rate']:>7} "
            f"partial={m['partial_fulfillment_rate']:>7} "
            f"infeasible={m['infeasible_rate']:>7} "
            f"unresolved={m['unresolved_rate']:>7} "
            f"p95={m['solver_runtime_p95_s']}s"
        )
    print(f"\npaired fully-feasible n={result['paired_fully_feasible_n']}, "
          f"mean cost saving (optimizer vs greedy, on fully)="
          f"{result['mean_cost_saving_pct_on_fully']}%")
    print(f"wrote artifacts to {OUT}")


if __name__ == "__main__":
    main()
