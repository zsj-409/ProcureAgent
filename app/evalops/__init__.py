"""Phase 10 — EvalOps package."""

from .core import (
    CandidateStatus,
    GateStatus,
    PromotionManager,
    ReleaseCandidate,
    ReleaseGateEngine,
    ReleaseGateResult,
    ShadowEvaluator,
    build_metric_registry,
    candidate_from_git,
    gate_summary,
)

__all__ = [
    "CandidateStatus", "GateStatus", "PromotionManager", "ReleaseCandidate",
    "ReleaseGateEngine", "ReleaseGateResult", "ShadowEvaluator",
    "build_metric_registry", "candidate_from_git", "gate_summary",
]
