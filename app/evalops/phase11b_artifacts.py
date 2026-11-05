"""Phase 11B — Commit A artifact generator.

Runs the REAL candidate mutation batch and writes machine-readable
artifacts (never pre-filled FAIL labels).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS_EVAL = Path(__file__).resolve().parents[2] / "tests" / "eval"
if str(TESTS_EVAL) not in sys.path:
    sys.path.insert(0, str(TESTS_EVAL))

from app.evalops.mutation import MUTANTS, run_mutation  # noqa: E402


def write_commit_a_artifacts(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    runs = [run_mutation(mutant=name, world_seed=7) for name in MUTANTS]

    manifest = {
        "date": "2026-11-05",
        "protocol": (
            "real candidate execution on isolated worlds (fixed seed 7); "
            "independent AllocationConstraintVerifier recomputes every "
            "constraint from problem + actual candidate lines; no pre-filled "
            "FAIL labels"
        ),
        "mutants": [
            {"name": m, "description": MUTANTS[m].description} for m in MUTANTS
        ],
        "production_code_modified": False,
        "world_constraints": {
            "family": "multi_supplier_joint",
            "seed": 7,
            "user_budget_limit": "162",
            "allowed_suppliers": ["s1", "s2", "s3"],
            "s3_stock": 15,
        },
    }
    (out_dir / "candidate_mutation_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    execution = {
        "runs": [r.as_dict() for r in runs],
    }
    (out_dir / "candidate_execution_results.json").write_text(
        json.dumps(execution, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    independent = {
        "verifier": "AllocationConstraintVerifier (independent)",
        "recomputed_fields": [
            "requested_quantity", "allocated_quantity", "shortfall",
            "total_landed_cost", "user_hard_budget", "quote_expiration",
            "currency_comparability", "supplier_eligibility", "inventory",
            "delivery_deadline", "split_supplier_limit",
        ],
        "per_candidate": [
            {
                "candidate_id": r.candidate_id,
                "feasibility": r.verifier["feasibility"],
                "total_landed_cost": r.verifier["total_landed_cost"],
                "covered_quantity": r.verifier["covered_quantity"],
                "shortfall": r.verifier["shortfall"],
                "violations": r.verifier["violations"],
            }
            for r in runs
        ],
    }
    (out_dir / "independent_constraint_results.json").write_text(
        json.dumps(independent, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    gate = {
        "gate": "ReleaseGateEngine (fail-closed)",
        "decisions": [
            {
                "candidate_id": r.candidate_id,
                "gate_status": r.gate_status,
                "violation_reason": r.violation_reason,
                "evidence_ref": "independent_constraint_results.json",
            }
            for r in runs
        ],
    }
    (out_dir / "release_gate_decisions.json").write_text(
        json.dumps(gate, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "baseline": runs[0].gate_status,
        "mutant_a": runs[1].gate_status,
        "mutant_b": runs[2].gate_status,
        "mutant_c": runs[3].gate_status,
    }


if __name__ == "__main__":
    result = write_commit_a_artifacts(Path(__file__).resolve().parents[2] / "docs" / "eval")
    print(json.dumps(result, indent=2, ensure_ascii=False))
