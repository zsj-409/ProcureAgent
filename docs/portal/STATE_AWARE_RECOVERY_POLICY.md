# State-Aware Recovery Policy

- **Module**: `app/runtime/recovery_controller.py`
- **Commit B**: Phase 4

## 1. RecoveryState (inputs)

```
RecoveryContext(
  task_id, logical_step_id, lease_epoch, plan_hash, action_input_hash,  # durable binding
  current_state, expected_state, fault,
  actions_used, reobservations_used, refreshes_used, llm_calls_used, cost_used,
  max_actions, max_reobservations, max_refreshes, max_llm_calls, max_cost,
  started_at,
)
```

Every recovery attempt is bound to the current fenced worker identity
(lease_epoch) and plan/action hashes. If the fencing token is stale, browser
results must NOT be persisted — the Phase 3 capability contract handles
uncertain external effects (UNKNOWN_OUTCOME), no second state machine.

## 2. Fault taxonomy

| FaultType | detected when |
|---|---|
| SELECTOR_CHANGED | last_error mentions selector/not found |
| LAYOUT_CHANGED | last_error mentions layout/card |
| PAGINATION_CHANGED | last_error mentions pagination |
| MODAL_BLOCKED | last_error mentions modal/dialog |
| SESSION_EXPIRED | semantic state == SESSION_EXPIRED |
| DYNAMIC_DOM_UPDATE | last_error mentions dynamic/async |
| TIMEOUT | last_error mentions timeout |
| AMBIGUOUS_ELEMENT | ambiguous flag |
| UNEXPECTED_NAVIGATION | ERROR state + page_changed |
| UNSAFE_ACTION | hostile flag |
| UNKNOWN_STATE | UNKNOWN semantic state |

Not everything is RETRYABLE: UNSAFE_ACTION, SESSION_EXPIRED, AMBIGUOUS_ELEMENT
→ SAFE_STOP immediately.

## 3. Recovery decision ladder (deterministic, low-cost first)

```
1. current_state == expected_state and no fault   → SUCCESS
2. budget exhausted                               → SAFE_STOP
3. UNSAFE_ACTION / SESSION_EXPIRED / AMBIGUOUS    → SAFE_STOP
4. DYNAMIC_DOM_UPDATE / TIMEOUT                   → REFRESH_READ_ONLY (bounded)
5. LAYOUT/PAGINATION/SELECTOR drift               → SEMANTIC_LOCATE (if available)
                                                   → else RE_OBSERVE (bounded)
6. UNEXPECTED_NAVIGATION / UNKNOWN_STATE          → RE_OBSERVE (bounded)
7. transient mismatch                             → RETRY_SAME (once)
8. otherwise                                      → SAFE_STOP
```

LLM escalation is NOT in the deterministic ladder: it is only considered when
deterministic means fail AND the action boundary remains verifiable (Commit C
compares with/without LLM).

## 4. Bounded execution

| budget | default |
|---|---|
| max actions | 30 |
| max re-observations | 10 |
| max refreshes | 3 |
| max LLM calls | 5 |
| max recovery cost | 10.0 |
| max recovery time | (controller default 120s; portal budget from settings) |

Exhaustion → SAFE_STOP. No infinite click/refresh/re-quote loops.

## 5. Durable Runtime integration

- `RecoveryContext` carries `task_id / logical_step_id / lease_epoch /
  plan_hash / action_input_hash` — the fenced identity.
- A stale fencing token blocks persistence of browser results.
- Uncertain external effects reuse Phase 3 capability contract
  (READ_ONLY re-query / REMOTE_IDEMPOTENT dedup / NON_IDEMPOTENT
  UNKNOWN_OUTCOME). No conflicting recovery state machine.

## 6. Verified

- Unit tests (`test_phase4_recovery_controller.py`, 10 tests): decision
  ladder, budget exhaustion, session/ambiguous safe stop, refresh-then-stop,
  semantic locate for layout drift, re-observe-then-stop for unknown,
  retry-once-then-stop, failure classification.
- Browser e2e (`test_phase4_portal_e2e.py`, 7 tests): real Chromium +
  real local HTTP portal. F5 (session expired) → SAFE_STOP (never crosses
  identity); F8 (ambiguous + unsafe button) → SAFE_STOP; F7 (prompt
  injection) → PAGE_HOSTILE blocks navigation, read-only extraction allowed;
  F1/F2 (selector/layout drift) → semantic extraction recovers; F4 (modal) →
  authorized close + continue.
