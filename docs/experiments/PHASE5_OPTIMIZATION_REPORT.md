# Phase 5 Experiment Report — Constrained Allocation Optimization

- **Date**: 2026-11-01
- **Module**: `app/procurement/optimizer.py` (new), `tests/test_phase5_optimizer.py`,
  `tests/eval/optimization_benchmark.py`
- **Solver**: PuLP 3.3.2 / CBC

## 1. What was built

An independently testable integer-programming allocation optimizer that
minimizes Total Landed Cost under: quantity conservation, inventory,
supplier admission, hard budget, delivery deadline, max split suppliers,
supplier share cap, tax/freight, unified currency. Returns an explicit
feasibility class (FULLY / PARTIAL / INFEASIBLE) — a low-cost partial plan
is never auto-presented as success.

## 2. Tests

18 new unit tests (`tests/test_phase5_optimizer.py`), all pass (0.38s).
Coverage: every constraint + feasibility classification + baseline
comparison.

## 3. Benchmark results (real CBC solves, 7 cases)

See `docs/contracts/ALLOCATION_OPTIMIZER_SPEC.md` §6 table.

- **Optimizer beats/equals greedy in constraint satisfaction**: greedy
  violates split cap (3 suppliers when max=2) and reports a recommendation
  while covering only 105/150 (many_suppliers); the optimizer honors all
  constraints and returns PARTIAL (20/25) explicitly when the cap binds.
- **Cheapest-feasible violates the delivery deadline** (picks the 10-day
  supplier at a lower price) — the optimizer excludes it.
- **On unconstrained simple cases all three agree** — the optimizer adds no
  value there (honest boundary: constrained scenarios are where it earns
  its keep).

## 4. Honest negative / limits

- On simple unconstrained cases the optimizer is not better than greedy
  (both pick the cheapest supplier).
- CBC is exact but exponential in supplier count in the worst case; the
  benchmark set (≤8 suppliers) solves in <0.1s. Very large catalogs would
  need a heuristic fallback (out of scope).
- The optimizer is NOT yet wired into the orchestrator (greedy remains the
  default Baseline); integration is a separate gated decision.
- Tax/freight data come from the problem definition (per-supplier), not
  from a live rate source — no fabricated rates.

## 5. Machine-readable artifacts

- `docs/eval/optimization_comparison.csv`
- `docs/eval/optimization_benchmark.json`
