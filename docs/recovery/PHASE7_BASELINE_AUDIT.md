# Phase 7 Baseline Audit — Current Recovery Policy

- **Date**: 2026-11-02
- **Baseline HEAD**: `8c4cba4` + Phase 7 Task 0 fixes (metric reconciliation)
- **Scope**: audit the Phase 4 recovery paths before policy evolution.

## 1. Current architecture

| path | module | behavior |
|---|---|---|
| Fixed script | `portal_executor.py` `_run_once` | CSS selectors; structural failure → break immediately |
| Heuristic agent | `portal_agent.py` `HeuristicPortalAgent` | deterministic search-extract via snapshot ordinals |
| LLM agent | `portal_agent.py` `LLMPortalAgent` | `generate_structured` over snapshot render (SCRIPTED/MOCK offline) |
| State-Aware ladder | `recovery_controller.py` | fault taxonomy + bounded decision ladder + SAFE_STOP |
| Safe actions | `safe_actions.py` | finite whitelist, PAGE_HOSTILE, postcondition verify |
| Semantic state | `portal_state.py` | classify_state + state_signature + transition log |

## 2. Fault → recovery mapping (current, from `decide`)

| fault | current decision |
|---|---|
| NONE + state matches | SUCCESS |
| DYNAMIC_DOM / TIMEOUT | REFRESH_READ_ONLY (≤3) then SAFE_STOP |
| LAYOUT / PAGINATION / SELECTOR drift | SEMANTIC_LOCATE if available, else RE_OBSERVE (≤10) then SAFE_STOP |
| UNEXPECTED_NAVIGATION | RE_OBSERVE |
| UNKNOWN_STATE | RE_OBSERVE (≤10) then SAFE_STOP |
| SESSION_EXPIRED / AMBIGUOUS / UNSAFE | SAFE_STOP (never cross identity / never act unsafe) |
| transient mismatch | RETRY_SAME (once) then SAFE_STOP |

## 3. Cost profile (where the current policy spends)

- **Re-observations**: up to 10 for drift/unknown states — each is a
  `take_snapshot` (page.evaluate) — cheap but additive.
- **Refreshes**: up to 3 for dynamic DOM — each is a page reload + re-snapshot.
- **Actions**: bounded by SafeActionExecutor budgets (max 30 actions).
- **LLM**: never called by the State-Aware controller (the ladder only uses
  deterministic tiers); LLM is a separate agent tier in PortalExecutor.
- The current controller does NOT model action success probability,
  remaining-budget tradeoffs, or cost-per-tier — it is a fixed rule ladder
  with hard budgets.

## 4. Key observations for Phase 7

1. **No cost-awareness**: the ladder does not weigh "re-observe vs refresh vs
   LLM" against remaining budget and the estimated success probability.
2. **No per-fault cost model**: SELECTOR_CHANGED is cheap to fix (semantic
   locate) but the controller spends the same re-observe budget as
   UNKNOWN_STATE.
3. **Hard budgets are fixed** (10 re-observations, 3 refreshes) regardless of
   task criticality or remaining budget.
4. **LLM never in the State-Aware ladder** — a potentially useful tier for
   genuinely ambiguous states is unused (and offline, SCRIPTED only).
5. **Phase 4 result**: E (State-Aware) tied D (Current Ladder) on success
   (7/7) with −35% P50 latency; the 7-fault benchmark lacked discrimination.

## 5. What Phase 7 must add

- A **cost-aware controller** (Task 4) with per-action cost, success-probability
  estimates, and budget-aware tier selection.
- A **more discriminative fault benchmark** (Task 2): 12 fault families ×
  3 difficulty levels × multiple templates/seeds — where strategies differ
  measurably in success, cost, or safe-stop.
- A **unified policy interface** (Task 3): `RecoveryObservation` →
  `RecoveryDecision` with recovery_tier, reason_code, expected_postcondition,
  estimated_cost, stopping_decision.
- Ground-truth isolation: the fault injector knows the fault; the policy
  only sees runtime-observable state (never the hidden label).
