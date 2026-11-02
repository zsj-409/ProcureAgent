# Recovery Policy Contract (Phase 7, Commit A)

- **Module**: `app/runtime/recovery_policy.py`
- **Benchmark**: `tests/eval/recovery_benchmark.py`

## 1. RecoveryObservation (policy input)

Everything a policy may see — **runtime-observable only**:

| field | source |
|---|---|
| current_semantic_state | semantic classifier (snapshot) |
| previous_expected_state | the goal state from the plan/contract |
| last_action / last_error | executor result |
| recent_action_history | executed actions (bounded) |
| remaining action/time/cost budgets | SafeActionExecutor budgets |
| session_status | observable session signal |
| permitted_actions | Safe Action registry subset |
| operation_capability | READ_ONLY (this system) |
| fault_hint | **derived from observable signals only** — the hidden injection label is NEVER an input |

## 2. RecoveryDecision (policy output)

- selected_action ∈ finite Safe Action registry (read-only whitelist)
- recovery_tier ∈ {NONE, RE_OBSERVE, REFRESH, SEMANTIC, RETRY, LLM, STOP}
- reason_code, expected_postcondition, estimated_cost, stopping_decision

Every action still passes SafeActionExecutor validation. If the state is
untrustworthy or an outcome is uncertain, UNKNOWN_OUTCOME / Safe Containment
rules apply (Phase 3/4 semantics unchanged).

## 3. Cost-aware selection rule (explainable, deterministic)

1. state == expected → SUCCESS (tier NONE)
2. session-expired / ambiguous / unsafe → STOP (never recoverable by policy)
3. budget exhausted → STOP
4. candidate tiers = those with prior success probability ≥ threshold,
   not attempt-capped, not budget-capped, LLM only if enabled
5. score(tier) = success_prob − cost_weight × (cost/50); pick the max
6. per-tier attempt cap forces escalation (no infinite same-tier loops)

Success priors per (fault, tier) are explainable constants derived from
Phase 4 experiments; costs are relative units (re-observe 1, refresh 3,
LLM 50). Tuning (thresholds, weights, caps) happens ONLY on DEV; promotion
is decided on CONFIRMATION; TEST is frozen.

## 4. Ground-truth isolation

- The fault injector knows the true fault; the policy derives `fault_hint`
  from observable signals (semantic state, error text, history).
- The oracle / goal is used ONLY by the independent evaluator.
- Replay may only reuse actions and observations that actually occurred;
  counterfactual actions are never pretended as executed.

## 5. Discriminative fault benchmark (12 families × 3 difficulties)

F1 selector drift, F2 layout drift, F3 dynamic DOM, F4 pagination change,
F5 modal interruption, F6 session expiration, F7 ambiguous product,
F8 prompt injection, F9 partial page load, F10 stale quote,
F11 delayed rendering, F12 multi-sequential faults.

Each case has an independent recovery goal (`recover_quote` or `safe_stop`)
and goal state — safe_stop is NEVER counted as success. DEV /
CONFIRMATION / TEST splits are disjoint; multiple templates + seeds per
family (not just renamed product IDs).
