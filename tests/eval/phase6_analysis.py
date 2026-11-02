"""Phase 6, Commit C — solver scaling + statistical analysis.

- Solver scaling: supplier count / stock size impact on runtime.
- Paired bootstrap (mean cost saving on jointly-fully-feasible worlds,
  with CI).
- Failure-case analysis: which worlds/methods failed and why.
"""

from __future__ import annotations

import json
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.procurement.optimizer import AllocationProblem, ProcurementOptimizer
from app.procurement.schemas import SupplierQuote
from tests.eval.procurement_benchmark import run_benchmark

OUT = Path(__file__).resolve().parents[2] / "docs" / "eval"


def _q(supplier_id: str, *, price: str, stock: int, delivery_days: int = 3) -> SupplierQuote:
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="X",
        unit_price=Decimal(price),
        currency="USD",
        available_stock=stock,
        delivery_days=delivery_days,
        source_type="api",
        normalization_status="comparable",
        normalized_unit_price_ref=Decimal(price),
        reference_currency="USD",
        quote_valid_until=datetime.now(UTC) + timedelta(days=1),
    )


def solver_scaling() -> dict:
    """Runtime vs supplier count (10..80 suppliers, quantity 5x suppliers)."""

    results = []
    for n_suppliers in (10, 20, 40, 80):
        rng = random.Random(n_suppliers)
        quotes = [
            _q(f"s{i}", price=str(round(10 + rng.random() * 90, 2)), stock=50 + i)
            for i in range(n_suppliers)
        ]
        quantity = n_suppliers * 5
        problem = AllocationProblem(
            quotes=quotes, quantity=quantity, max_split_suppliers=4, time_limit_s=15
        )
        start = time.monotonic()
        result = ProcurementOptimizer().optimize(problem)
        elapsed = time.monotonic() - start
        results.append(
            {
                "n_suppliers": n_suppliers,
                "quantity": quantity,
                "solve_time_s": round(elapsed, 4),
                "feasibility": result.feasibility.value,
                "covered": result.covered_quantity,
            }
        )
    return results


def paired_bootstrap(result: dict, n_boot: int = 2000, seed: int = 42) -> dict:
    """Ordinary i.i.d. bootstrap (ignores family correlation)."""

    rng = random.Random(seed)
    savings = [
        p["cost_saving_pct_on_fully"]
        for p in result["paired_comparison"]
        if p["both_fully_feasible"] and p["cost_saving_pct_on_fully"] is not None
    ]
    if not savings:
        return {"n": 0, "mean": None, "ci95": None}
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(savings) for _ in range(len(savings))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot)]
    return {"n": len(savings), "mean": round(sum(savings) / len(savings), 3), "ci95": [round(lo, 3), round(hi, 3)]}


def cluster_bootstrap(result: dict, n_boot: int = 2000, seed: int = 42) -> dict:
    """Cluster bootstrap: resample WHOLE families (not individual pairs).

    The 2 seeds of one family share the same template and are correlated;
    ordinary bootstrap overstates precision. Cluster bootstrap resamples
    entire families with replacement, so the correlation structure is
    preserved. This is the primary CI for family-level conclusions.
    """

    rng = random.Random(seed)
    # Group jointly-fully-feasible pairs by family.
    clusters: dict[str, list[float]] = {}
    for p in result["paired_comparison"]:
        if p["both_fully_feasible"] and p["cost_saving_pct_on_fully"] is not None:
            clusters.setdefault(p["family"], []).append(p["cost_saving_pct_on_fully"])
    families = list(clusters.keys())
    if not families:
        return {"n_clusters": 0, "n_pairs": 0, "mean": None, "ci95": None}
    means = []
    for _ in range(n_boot):
        # Resample families with replacement, keep ALL their pairs.
        sample = []
        for _ in range(len(families)):
            fam = rng.choice(families)
            sample.extend(clusters[fam])
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot)]
    n_pairs = sum(len(v) for v in clusters.values())
    return {
        "n_clusters": len(families),
        "n_pairs": n_pairs,
        "mean": round(sum(sum(v) for v in clusters.values()) / n_pairs, 3),
        "ci95": [round(lo, 3), round(hi, 3)],
        "note": "cluster bootstrap by family (preserves within-family correlation); "
                "n_clusters is the effective independent sample size",
    }


def failure_analysis(result: dict) -> dict:
    """Classify non-successful cases per method with reasons."""

    failures = {}
    for r in result["rows"]:
        if r["feasibility"] in ("FULLY_FEASIBLE",):
            continue
        key = f"{r['method']}:{r['feasibility']}"
        failures.setdefault(key, {"n": 0, "families": set(), "examples": []})
        failures[key]["n"] += 1
        failures[key]["families"].add(r["family"])
        if len(failures[key]["examples"]) < 3:
            failures[key]["examples"].append(
                {"world": r["world"], "detail": r["detail"] or r["violations"]}
            )
    out = {}
    for key, val in failures.items():
        out[key] = {
            "n": val["n"],
            "families": sorted(val["families"]),
            "examples": val["examples"],
        }
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # Scaling.
    scaling = solver_scaling()
    (OUT / "solver_performance.json").write_text(
        json.dumps({"scaling": scaling, "solver": "PuLP 3.3.2/CBC"}, indent=2), encoding="utf-8"
    )
    print("=== Solver scaling ===")
    for row in scaling:
        print(f"  {row['n_suppliers']:>3} suppliers x {row['quantity']:>4} qty -> {row['solve_time_s']}s ({row['feasibility']}, covered {row['covered']})")

    # Full benchmark + statistics.
    result = run_benchmark()
    boot = paired_bootstrap(result)
    cluster = cluster_bootstrap(result)
    (OUT / "paired_bootstrap.json").write_text(json.dumps(boot, indent=2), encoding="utf-8")
    (OUT / "paired_cluster_bootstrap.json").write_text(json.dumps(cluster, indent=2), encoding="utf-8")
    print("\n=== Paired bootstrap (optimizer vs greedy, jointly fully) ===")
    print(f"  ordinary: n={boot['n']}, mean saving={boot['mean']}%, 95% CI={boot['ci95']}")
    print(f"  cluster (by family): n_clusters={cluster['n_clusters']}, n_pairs={cluster['n_pairs']}, "
          f"mean={cluster['mean']}%, 95% CI={cluster['ci95']}")

    failures = failure_analysis(result)
    (OUT / "failure_cases.json").write_text(
        json.dumps(failures, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("\n=== Failure cases ===")
    for key, val in failures.items():
        print(f"  {key}: n={val['n']} families={val['families']}")


if __name__ == "__main__":
    main()
