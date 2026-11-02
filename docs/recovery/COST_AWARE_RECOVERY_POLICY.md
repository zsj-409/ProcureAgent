# Cost-Aware Recovery Policy (Phase 7, Commit B)

- **Module**: `app/runtime/recovery_policy.py`

## 1. Policy inputs & outputs

`RecoveryObservation` (runtime-observable only: semantic state, expected
state, last action/error, history, remaining budgets, session status,
permitted actions, capability) → `RecoveryDecision` (selected action from
the Safe Action whitelist, recovery tier, reason code, expected
postcondition, estimated cost, stopping decision).

## 2. Selection rule (explainable, deterministic)

1. state == expected → NONE (success)
2. session-expired / ambiguous / unsafe → STOP (never recoverable)
3. budget exhausted → STOP
4. candidates = tiers with prior success ≥ threshold, within per-tier
   attempt caps and budget caps (LLM only when enabled)
5. score(tier) = prior_success − cost_weight × (cost/50); pick max
6. per-tier attempt cap (max_tier_attempts) forces escalation — no infinite
   same-tier loops

Success priors (per fault × tier) are explainable constants from the Phase
4 experiments; costs are relative (re-observe 1, retry 1.5, refresh 3,
semantic 2, LLM 50).

## 3. Cost model

| tier | relative cost | typical fault |
|---|---|---|
| RE_OBSERVE | 1.0 | drift / unknown |
| RETRY | 1.5 | transient |
| SEMANTIC | 2.0 | selector/layout drift |
| REFRESH | 3.0 | dynamic DOM / timeout |
| LLM | 50.0 | ambiguous / hard states (disabled offline) |
| STOP | 0.0 | unsafe / session / budget |

## 4. Budgets & safety gates

- max_observations (10), max_refreshes (3), max_llm_calls (2),
  max_actions (30) — hard caps.
- **Safety gates cannot be bypassed by the policy**: no tier may raise a
  success estimate to justify a side-effecting action (all actions are
  read-only by the Safe Action contract); session-expired / ambiguous /
  unsafe always STOP.

## 5. Tuning & promotion (Task 5)

- DEV: tune `min_success_threshold`, `max_tier_attempts`, `cost_weight`,
  budgets.
- CONFIRMATION: independent promotion gate —
  REJECT on safety degradation (any unsafe) or quality regression;
  HOLD on equal quality + no cost saving;
  PROMOTE only on verified improvement.
- TEST: frozen; never used for parameter updates.
- Replay: only real observed actions/observations; counterfactual actions
  are never pretended (independent simulation is clearly labeled).

## 6. Baseline preserved

The Phase 4 State-Aware controller (`recovery_controller.py`) remains the
Baseline D. The cost-aware policy (E) and its tuned variant (F) are
evaluated against it on the 12-family × 3-difficulty benchmark.
