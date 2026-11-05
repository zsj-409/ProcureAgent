"""Phase 10 — Shadow Evaluation protocol demo (Task 18).

Runs the baseline (greedy) and a candidate policy on ISOLATED immutable
snapshots of the same procurement world. The candidate NEVER writes real
task state — it only reads the world and produces a decision. Every tool
call goes through the policy gateway (shadow mode is NOT a bypass).

Produces docs/eval/shadow_evaluation_results.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# tests/eval is not a package; add to sys.path for procurement_world.
TESTS_EVAL = Path(__file__).resolve().parents[2] / "tests" / "eval"
if str(TESTS_EVAL) not in sys.path:
    sys.path.insert(0, str(TESTS_EVAL))

from app.evalops.core import ShadowEvaluator  # noqa: E402


def _make_world(name: str = "shadow-world"):
    from procurement_world import make_world  # type: ignore

    world = make_world("multi_supplier_joint", seed=1, split="DEV", name=name)
    return world


def run_shadow_demo(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    world = _make_world()

    def snapshot_fn(w):
        return w.snapshot_hash()  # immutable world identity
    # run_fn: each method sees its OWN isolated world state (fresh setup per
    # call) — candidate never mutates the baseline's world.
    def run_fn(method: str, w):
        w.reset()  # fresh isolated snapshot per method
        if method == "greedy":
            from procurement_adapters import GreedyAdapter  # type: ignore

            adapter = GreedyAdapter()
            candidate = adapter.run(w)
            return {
                "verified": candidate.feasibility == "FULLY_FEASIBLE",
                "cost": float(candidate.total_landed_cost or 0),
                "feasibility": candidate.feasibility,
            }
        # candidate = optimizer (read-only solve via the adapter)
        from procurement_adapters import OptimizerAdapter  # type: ignore

        adapter = OptimizerAdapter()
        candidate = adapter.run(w)
        return {
            "verified": candidate.feasibility == "FULLY_FEASIBLE",
            "cost": float(candidate.total_landed_cost or 0),
            "feasibility": candidate.feasibility,
        }

    sh = ShadowEvaluator(snapshot_fn=snapshot_fn, run_fn=run_fn)
    result = sh.evaluate(
        candidate_id="optimizer-candidate-v1",
        baseline_id="greedy-baseline",
        world=world,
        candidate_method="optimizer",
        baseline_method="greedy",
    )

    artifact = {
        "date": "2026-11-02",
        "protocol": "isolated immutable snapshot per method; candidate never writes real state",
        "result": result.as_dict(),
    }
    (out_dir / "shadow_evaluation_results.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return artifact


if __name__ == "__main__":
    out = run_shadow_demo(Path(__file__).resolve().parents[2] / "docs" / "eval")
    print(json.dumps(out, indent=2, ensure_ascii=False))
