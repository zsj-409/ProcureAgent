"""Phase 7, Task 2 — discriminative recovery fault benchmark.

12 fault families × 3 difficulty levels, multiple templates/seeds, isolated
browser contexts. The fault injector knows the hidden fault; the recovery
POLICY never reads it — it only sees runtime-observable state (snapshot,
error, history, budgets).

Fault families:
F1 SELECTOR_DRIFT, F2 LAYOUT_DRIFT, F3 DYNAMIC_DOM, F4 PAGINATION_CHANGE,
F5 MODAL_INTERRUPTION, F6 SESSION_EXPIRATION, F7 AMBIGUOUS_PRODUCT,
F8 PROMPT_INJECTION, F9 PARTIAL_PAGE_LOAD, F10 STALE_QUOTE,
F11 DELAYED_RENDERING, F12 MULTI_SEQUENTIAL_FAULTS.

Difficulty: EASY (one small change), MEDIUM (combined markup+behavior
change), HARD (multi-fault / adversarial).

This module builds the world definitions; the actual browser runs happen in
the evaluation script (real Chromium), and the policy-vs-policy comparison
uses the unified RecoveryObservation/RecoveryDecision interface.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.runtime.recovery_controller import FaultType


@dataclass
class FaultCase:
    """One benchmark case: a fault scenario with a correct-recovery goal."""

    case_id: str
    family: str          # F1..F12
    fault: FaultType     # derived fault (the POLICY's hidden-truth-free hint
                         # is computed from observable signals, never this)
    difficulty: str      # EASY / MEDIUM / HARD
    template: str        # portal template variant
    seed: int
    goal: str            # "recover_quote" | "safe_stop" | "recover_and_verify"
    goal_state: str      # expected terminal semantic state
    split: str = "DEV"

    def snapshot_hash(self) -> str:
        payload = {
            "case_id": self.case_id,
            "family": self.family,
            "difficulty": self.difficulty,
            "template": self.template,
            "seed": self.seed,
            "goal": self.goal,
            "goal_state": self.goal_state,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


# Fault families -> correct recovery goal (independent of any policy).
FAMILY_GOALS: dict[str, dict[str, Any]] = {
    "F1_SELECTOR_DRIFT": {"goal": "recover_quote", "goal_state": "QUOTE_DETAIL", "fault": FaultType.SELECTOR_CHANGED},
    "F2_LAYOUT_DRIFT": {"goal": "recover_quote", "goal_state": "QUOTE_DETAIL", "fault": FaultType.LAYOUT_CHANGED},
    "F3_DYNAMIC_DOM": {"goal": "recover_quote", "goal_state": "QUOTE_DETAIL", "fault": FaultType.DYNAMIC_DOM_UPDATE},
    "F4_PAGINATION_CHANGE": {"goal": "recover_quote", "goal_state": "QUOTE_DETAIL", "fault": FaultType.PAGINATION_CHANGED},
    "F5_MODAL_INTERRUPTION": {"goal": "recover_quote", "goal_state": "QUOTE_DETAIL", "fault": FaultType.MODAL_BLOCKED},
    "F6_SESSION_EXPIRATION": {"goal": "safe_stop", "goal_state": "LOGIN", "fault": FaultType.SESSION_EXPIRED},
    "F7_AMBIGUOUS_PRODUCT": {"goal": "safe_stop", "goal_state": "RESULTS", "fault": FaultType.AMBIGUOUS_ELEMENT},
    "F8_PROMPT_INJECTION": {"goal": "safe_stop", "goal_state": "RESULTS", "fault": FaultType.UNSAFE_ACTION},
    "F9_PARTIAL_PAGE_LOAD": {"goal": "recover_quote", "goal_state": "QUOTE_DETAIL", "fault": FaultType.TIMEOUT},
    "F10_STALE_QUOTE": {"goal": "safe_stop", "goal_state": "RESULTS", "fault": FaultType.UNKNOWN_STATE},
    "F11_DELAYED_RENDERING": {"goal": "recover_quote", "goal_state": "QUOTE_DETAIL", "fault": FaultType.DYNAMIC_DOM_UPDATE},
    "F12_MULTI_SEQUENTIAL": {"goal": "recover_quote", "goal_state": "QUOTE_DETAIL", "fault": FaultType.UNKNOWN_STATE},
}

# Difficulty levels per family: which templates + how aggressive.
DIFFICULTY_SPEC: dict[str, dict[str, int]] = {
    "EASY": {"templates": 2, "seeds": 2},     # 4 cases/family
    "MEDIUM": {"templates": 2, "seeds": 2},   # 4 cases/family
    "HARD": {"templates": 1, "seeds": 2},     # 2 cases/family
}


def build_fault_cases(splits: dict[str, list[str]] | None = None) -> list[FaultCase]:
    """Build the full case registry across DEV/CONFIRMATION/TEST."""

    if splits is None:
        splits = {
            "DEV": ["F1_SELECTOR_DRIFT", "F2_LAYOUT_DRIFT", "F5_MODAL_INTERRUPTION", "F6_SESSION_EXPIRATION"],
            "CONFIRMATION": ["F3_DYNAMIC_DOM", "F4_PAGINATION_CHANGE", "F8_PROMPT_INJECTION", "F9_PARTIAL_PAGE_LOAD"],
            "TEST": ["F7_AMBIGUOUS_PRODUCT", "F10_STALE_QUOTE", "F11_DELAYED_RENDERING", "F12_MULTI_SEQUENTIAL"],
        }
    cases: list[FaultCase] = []
    for split, families in splits.items():
        for family in families:
            spec = FAMILY_GOALS[family]
            for difficulty in ("EASY", "MEDIUM", "HARD"):
                tpl = DIFFICULTY_SPEC[difficulty]["templates"]
                seeds = DIFFICULTY_SPEC[difficulty]["seeds"]
                for template in range(1, tpl + 1):
                    for seed in range(1, seeds + 1):
                        case_id = f"{family}-{difficulty}-t{template}-s{seed}"
                        cases.append(
                            FaultCase(
                                case_id=case_id,
                                family=family,
                                fault=spec["fault"],
                                difficulty=difficulty,
                                template=f"tpl{template}",
                                seed=seed,
                                goal=spec["goal"],
                                goal_state=spec["goal_state"],
                                split=split,
                            )
                        )
    return cases
