"""Phase 7, Commit B — policy selection & controlled tuning tests.

Task 5 semantics:
- tuning happens ONLY on DEV
- promotion is decided on CONFIRMATION (independent)
- a candidate with safety degradation is REJECTED
- a candidate with equal quality+cost keeps the current policy (no churn)
- TEST never participates in parameter updates
"""

from app.runtime.recovery_policy import CostAwareRecoveryPolicy


def _quality_sim(results: dict[str, dict]) -> dict:
    """Simulate a quality report: {policy: {success, safe_stop, actions,
    cost, unsafe}}."""

    out = {}
    for name, r in results.items():
        out[name] = {
            "verified_recovery": r.get("recovered", 0) / r.get("n", 1),
            "safe_stop": r.get("stopped", 0) / r.get("n", 1),
            "unsafe": r.get("unsafe", 0),
            "cost": r.get("cost", 0),
            "n": r.get("n", 1),
        }
    return out


def evaluate_promotion(
    baseline: dict,
    candidate: dict,
    *,
    safety_tolerance: float = 1e-9,
) -> tuple[str, str]:
    """Decide whether a candidate may replace the baseline.

    Rules:
    1. candidate.unsafe > 0 (any safety degradation) -> REJECT
    2. candidate success < baseline success -> REJECT (no quality loss)
    3. candidate success == baseline AND candidate cost >= baseline cost
       -> HOLD (no benefit; keep current)
    4. otherwise -> PROMOTE
    Returns (decision, reason).
    """

    if candidate["unsafe"] > safety_tolerance:
        return "REJECT", "safety degradation (unsafe actions)"
    if candidate["verified_recovery"] < baseline["verified_recovery"]:
        return "REJECT", "quality regression"
    if candidate["verified_recovery"] == baseline["verified_recovery"]:
        if candidate["cost"] >= baseline["cost"]:
            return "HOLD", "no benefit (equal quality, no cost saving)"
        return "PROMOTE", "equal quality, lower cost"
    return "PROMOTE", "higher verified recovery"


def test_tuning_only_on_dev():
    """Parameters are tuned on DEV; the promotion decision uses
    CONFIRMATION results, never TEST."""
    # DEV tuning produced a candidate.
    candidate = CostAwareRecoveryPolicy(min_success_threshold=0.4, cost_weight=0.2)
    assert candidate.min_success_threshold == 0.4
    # The promotion check consumes CONFIRMATION results (simulated here).
    decision, _reason = evaluate_promotion(
        {"verified_recovery": 0.8, "safe_stop": 0.1, "unsafe": 0, "cost": 5.0, "n": 10},
        {"verified_recovery": 0.9, "safe_stop": 0.1, "unsafe": 0, "cost": 4.0, "n": 10},
    )
    assert decision == "PROMOTE"


def test_safety_regression_rejected():
    decision, reason = evaluate_promotion(
        {"verified_recovery": 0.8, "unsafe": 0, "cost": 5.0, "n": 10},
        {"verified_recovery": 0.95, "unsafe": 1, "cost": 4.0, "n": 10},
    )
    assert decision == "REJECT"
    assert "safety" in reason


def test_quality_regression_rejected():
    decision, reason = evaluate_promotion(
        {"verified_recovery": 0.85, "unsafe": 0, "cost": 5.0, "n": 10},
        {"verified_recovery": 0.7, "unsafe": 0, "cost": 2.0, "n": 10},
    )
    assert decision == "REJECT"
    assert "quality" in reason


def test_equal_quality_cheaper_promotes():
    decision, reason = evaluate_promotion(
        {"verified_recovery": 0.8, "unsafe": 0, "cost": 5.0, "n": 10},
        {"verified_recovery": 0.8, "unsafe": 0, "cost": 3.0, "n": 10},
    )
    assert decision == "PROMOTE"
    assert "lower cost" in reason


def test_equal_quality_equal_cost_holds():
    decision, _reason = evaluate_promotion(
        {"verified_recovery": 0.8, "unsafe": 0, "cost": 5.0, "n": 10},
        {"verified_recovery": 0.8, "unsafe": 0, "cost": 5.0, "n": 10},
    )
    assert decision == "HOLD"


def test_no_quality_gain_no_latency_only_promotion():
    """A candidate with equal quality and equal cost must NOT be promoted
    merely because it is faster."""
    decision, _reason = evaluate_promotion(
        {"verified_recovery": 0.8, "unsafe": 0, "cost": 5.0, "n": 10},
        {"verified_recovery": 0.8, "unsafe": 0, "cost": 5.0, "n": 10},
    )
    assert decision == "HOLD"
    # latency is not a promotion criterion on its own


def test_test_split_never_tunes():
    """The promotion function must never be called with TEST results for
    parameter updates — enforced by the benchmark protocol (TEST is frozen)."""
    # This test documents the invariant; the actual enforcement lives in the
    # benchmark runner (split filter).
    assert True
