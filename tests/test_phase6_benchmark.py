"""Phase 6, Commit B — benchmark reproducibility tests.

- world registry determinism (same seed -> same snapshot hash)
- environment isolation (reset rebuilds identical quotes)
- method order invariance (running adapters in different orders yields the
  same per-method results)
- unified AllocationCandidate shape (no structural advantage)
- metrics contract (numerator/denominator, paired-only cost saving)
"""


from tests.eval.procurement_adapters import (
    AllocationCandidate,
    CheapestAdapter,
    GreedyAdapter,
    OptimizerAdapter,
    run_all_adapters,
)
from tests.eval.procurement_benchmark import run_benchmark
from tests.eval.procurement_world import make_world, world_registry


def test_world_registry_deterministic_hash():
    w1 = make_world("tight_budget", seed=3)
    w2 = make_world("tight_budget", seed=3)
    assert w1.snapshot_hash() == w2.snapshot_hash()
    w3 = make_world("tight_budget", seed=4)
    assert w1.snapshot_hash() != w3.snapshot_hash()


def test_world_reset_rebuilds_identical_state():
    w = make_world("mixed_constraints", seed=7)
    h1 = w.snapshot_hash()
    w.quotes = w.quotes[:1]  # mutate
    assert w.snapshot_hash() != h1
    w.reset()
    assert w.snapshot_hash() == h1


def test_registry_covers_all_splits():
    worlds = world_registry(
        {"DEV": ["unconstrained"], "TEST": ["tight_budget"]}, seeds_per_family=2
    )
    assert len(worlds) == 4
    assert {w.split for w in worlds} == {"DEV", "TEST"}


def test_adapters_share_unified_shape():
    """All adapters return AllocationCandidate with the same fields."""
    w = make_world("unconstrained", seed=1)
    for candidate in run_all_adapters(w):
        assert isinstance(candidate, AllocationCandidate)
        assert set(candidate.to_dict().keys()) == {
            "method", "lines", "feasibility", "covered_quantity", "shortfall",
            "total_landed_cost", "solver_status", "optimality_proven",
            "solve_time_s", "violations", "detail",
        }


def test_method_order_invariance():
    """Running methods in different orders must not change per-method
    results (no shared mutable state)."""
    w1 = make_world("delivery_conflict", seed=1)
    w2 = make_world("delivery_conflict", seed=1)
    c1 = {
        c.method: c
        for c in [GreedyAdapter().run(w1), CheapestAdapter().run(w1), OptimizerAdapter().run(w1)]
    }
    c2 = {
        c.method: c
        for c in [OptimizerAdapter().run(w2), CheapestAdapter().run(w2), GreedyAdapter().run(w2)]
    }
    assert set(c1.keys()) == set(c2.keys())
    for method in c1:
        a, b = c1[method], c2[method]
        # Same method, same world -> identical feasibility & cost regardless
        # of execution order.
        assert a.feasibility == b.feasibility
        assert a.total_landed_cost == b.total_landed_cost
        assert a.covered_quantity == b.covered_quantity


def test_greedy_shortfall_not_misrepresented_as_full():
    """A greedy partial plan must NOT be labeled FULLY_FEASIBLE."""
    w = make_world("partial_confirm", seed=1)
    cand = GreedyAdapter().run(w)
    if cand.covered_quantity < w.quantity:
        assert cand.feasibility in ("PARTIAL_FEASIBLE", "INFEASIBLE")
    assert cand.shortfall == w.quantity - cand.covered_quantity


def test_optimizer_violations_zero_on_test_worlds():
    """On the frozen TEST families the optimizer must not violate hard
    constraints (verified independently)."""
    result = run_benchmark(split_filter="TEST")
    opt_rows = [r for r in result["rows"] if r["method"] == "optimizer"]
    assert all(not r["violations"] for r in opt_rows)


def test_paired_cost_saving_only_on_jointly_fully():
    """Cost saving must only be computed when BOTH methods FULLY fulfill."""
    result = run_benchmark(split_filter="DEV")
    for p in result["paired_comparison"]:
        if not p["both_fully_feasible"]:
            assert p["cost_saving_pct_on_fully"] is None
        else:
            assert p["cost_saving_pct_on_fully"] is not None


def test_metrics_have_numerators_and_denominators():
    result = run_benchmark(split_filter="DEV")
    for _method, m in result["metrics_by_method"].items():
        assert "/" in m["verified_full_fulfillment_rate"]
        assert "/" in m["hard_constraint_violation_rate"]
        n = int(m["verified_full_fulfillment_rate"].split("/")[1])
        assert n > 0
