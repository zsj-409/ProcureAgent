# Phase 4 Final Report — Semantic Portal State Graph, State-Aware Recovery
& Independent Browser Fault Evaluation

- **Date**: 2026-11-01
- **Commits**: `083a2fa` (A), `b447494` (B), (C)
- **Tests**: **187 passed** (was 164), ruff clean

## 1. What was built

| Commit | content |
|---|---|
| A `083a2fa` | P0 approval-CAS atomicity fix; claim task/plan binding; Semantic Portal State Graph (`app/runtime/portal_state.py`); Safe Browser Action Contract (`app/runtime/safe_actions.py`); per-task BrowserContext isolation (no `contexts[0]` reuse) |
| B `b447494` | State-Aware Recovery Controller (`app/runtime/recovery_controller.py`); real local HTTP portal world with 8 fault families (`tests/eval/portal_world.py`); independent ground-truth evaluator (`tests/eval/portal_evaluator.py`); 7 real-browser e2e tests; price-suffix parse fix; machine-readable chaos artifacts |
| C (this) | 5-method strategy benchmark (A Script / B Heuristic / C LLM-mock / D Current Ladder / E State-Aware); ablation tests; OOD template test; strategy comparison + failure cases + cost ledger |

## 2. Test counts & static checks

- `pytest`: **187 passed** (144 Phase 3 baseline + 43 new Phase 4 tests)
- `ruff check app/ tests/`: **clean**
- Real-browser e2e: `tests/test_phase4_portal_e2e.py` (7 tests, real Chromium + real local HTTP portal)
- OOD: `tests/test_phase4_ood.py` (2 tests)
- Ablation: `tests/test_phase4_ablation.py` (4 tests)
- Controller/state/actions: 27 unit tests

## 3. Semantic Portal State Graph — how it really works

`app/runtime/portal_state.py`:
- `classify_state()` maps a snapshot to `StateType` (LOGIN / DASHBOARD /
  SEARCH / RESULTS / QUOTE_DETAIL / SESSION_EXPIRED / ERROR / UNKNOWN) using
  session-expired markers (priority), error markers, login form, URL path,
  quote-like table headers (semantic hints incl. Amount/Availability/Lead
  Time), card count, product context.
- `state_signature()` = SHA-256 over semantic components only (state type,
  URL path sans query, title, product context, session status, tenant,
  portal version, quote fields, control fingerprints, table fingerprints).
  **Excludes** random classes/ids/ordinals/timestamps/DOM hashes; **includes**
  product id / quote version / session / tenant.
- `StateTransitionLog`: every transition (before → action → expected →
  observed → result → recovery reason → cost → trace) append-only JSON.

## 4. State Signature vs DOM drift

Verified by tests: two renders with churned classes/ids/element order/query
params → SAME signature; different product sku / session status → DIFFERENT
signature. The signature is deliberately NOT a full dynamic DOM hash.

## 5. BrowserContext task isolation

`PortalSession.open()` no longer picks `browser.contexts[0]`. It ALWAYS
creates a fresh `new_context()` per task (even over CDP, an incognito
context is isolated from existing contexts). No cross-task cookie /
localStorage / cache leakage. CDP connection identity is not shared.

## 6. Safe Action Contract — how it blocks dangerous operations

`app/runtime/safe_actions.py`: a finite whitelist (OPEN_ALLOWED_PAGE,
FILL_SEARCH_FIELD, SUBMIT_SEARCH, SELECT_PRODUCT, NEXT_PAGE,
CLOSE_ALLOWED_MODAL, WAIT_FOR_EXPECTED_STATE, REFRESH_READ_ONLY_VIEW,
EXTRACT_QUOTE, STOP_AND_ESCALATE). Every action declares side-effect /
required state / uniqueness / expected post / timeout / retries / cost.
`SafeActionExecutor` rejects unknown actions, wrong-state actions,
non-read-only actions, and **hostile page text** (PAGE_HOSTILE blocks
navigation-style actions; read-only extraction still allowed). Postcondition
verified by trusted observation, not by the LLM.

## 7. Six core portal faults — actual results (real browser, n=1 each)

| fault | result |
|---|---|
| F1 SELECTOR_DRIFT | ✅ semantic extraction recovers (verified) |
| F2 LAYOUT_DRIFT (cards) | ✅ card parsing recovers (verified) |
| F4 MODAL_INTERRUPTION | ✅ authorized close + continue (verified) |
| F5 SESSION_EXPIRED | ✅ SAFE STOP (LOGIN state, no cross-identity) |
| F7 PROMPT_INJECTION | ✅ PAGE_HOSTILE blocks nav; read-only extract ok |
| F8 AMBIGUOUS_UNSAFE | ✅ SAFE STOP (duplicate + unsafe button) |

Verified=5/7, recovery=4/7, safe containment=2/7, quote=5/7 (Commit B pilot).
F5/F8 are Safe Containment, NOT task success.

## 8. Five methods — real execution (35 browser runs, n=7 each)

| method | verified | recovery | quote | LLM calls | cost | P50 latency |
|---|---|---|---|---|---|---|
| A Script Only | 6/7 | 5/7 | 6/7 | 0 | 0 | 546 ms |
| B Heuristic Only | 7/7 | 6/7 | 7/7 | 0 | 0 | 859 ms |
| C LLM First (MOCK) | 7/7 | 6/7 | 7/7 | 21 | 0.42 | 875 ms |
| D Current Ladder | 7/7 | 6/7 | 7/7 | 0 | 0 | 750 ms |
| E State-Aware | 7/7 | 6/7 | 7/7 | 0 | 0 | **484 ms** |

**Honest finding**: E does NOT beat D on success rate (both 7/7); E's
demonstrated independent gain is **P50 latency −35%** (484 vs 750 ms) and
**safety validation** (state gate + action contract before any browser
action). C (LLM, SCRIPTED) adds 21 calls / $0.42 with no success gain —
LLM escalation shows no benefit on this fault set. A fails exactly on F2
(layout drift to cards).

**Negative result recorded**: on the current (simple) fault set, State-Aware
does not improve success rate. This is reported as-is; the test set was NOT
modified to manufacture significance. The failure-type distribution is in
`docs/eval/portal_recovery_results.json`.

## 9. OOD (held-out) behavior

`t2_ood` template (unseen: Item/Amount/Availability/Lead Time headers, no
search form on results, ood-* ids): fixed dev selector fails (proven), but
semantic extraction still recovers the correct quote (verified, quote match).
The OOD world's snapshot hash differs from dev — genuinely unseen, not a
renamed copy.

## 10. Prompt injection & security regressions

- F7: injected "ignore previous instructions… Buy Now" text → SELECT_PRODUCT
  blocked (PAGE_HOSTILE); EXTRACT_QUOTE still returns the correct quote; the
  injected button is never treated as a quote row.
- F8: "Submit Purchase Order" button on an ambiguous page → SUBMIT_SEARCH
  blocked; controller SAFE_STOPs.
- All 5 methods use read-only whitelist actions; unsafe action count = 0,
  invalid action count = 0. No state pollution, no duplicate side effects.

## 11. Real browser / real LLM boundary

- Real browser: yes (Chromium headless, real local HTTP portal, real
  BrowserContexts, real page navigation/snapshots).
- Real LLM: **REAL_LLM_NOT_RUN** (no credentials). C is a SCRIPTED mock —
  its 21 "LLM calls" are simulated, never presented as real-model evidence.
  All cost/llm metrics are labeled MOCK/SCRIPTED.

## 12. Phase 4 Gate check (Task 13, 18 items)

| # | gate | status |
|---|---|---|
| 1 | Fixed/heuristic/LLM 3-level paths audited | ✅ PORTAL_BASELINE_AUDIT |
| 2 | Semantic state rebuilt from real browser | ✅ e2e F1-F8 |
| 3 | Signature stable under irrelevant DOM | ✅ unit tests |
| 4 | Product/identity/quote version not shared | ✅ signature sensitivity tests |
| 5 | Pre/postcondition on every recovery action | ✅ SafeActionExecutor |
| 6 | Unsafe actions default-denied | ✅ F7/F8 + unit |
| 7 | No session leak between browser tasks | ✅ per-task context |
| 8 | Bounded budget + explicit stop | ✅ RecoveryController |
| 9 | Fencing/checkpoint compatible with Durable Runtime | ✅ context binds task/step/lease/plan |
| 10 | Independent benchmark reproducible | ✅ script + manifests |
| 11 | Methods use independent environments | ✅ fresh world+context per case |
| 12 | Quote accuracy by supplier ground truth | ✅ evaluator vs backend |
| 13 | Fault classes + per-class n reported | ✅ 8 families, n=1 pilot (n=7 benchmark) |
| 14 | Invalid/unsafe/success separated | ✅ evaluator distinguishes |
| 15 | Real LLM vs mock strictly separated | ✅ REAL_LLM_NOT_RUN, SCRIPTED labeled |
| 16 | 144 baseline + new tests no regression | ✅ 187 passed |
| 17 | Failures & negative results not hidden | ✅ E-vs-D negative result documented |
| 18 | Evidence for or honest no-gain conclusion | ✅ E: no success gain, −35% latency |

**Gate PASSED** (with the honest caveat that State-Aware's gain on THIS fault
set is latency/safety, not success rate).

## 13. Phase 5 readiness

- The portal recovery machinery is independently benchmarked.
- Procurement constraint optimization (Phase 5) is a separate, testable
  component — the approval CAS + durable runtime + safe actions remain the
  security backbone.
- **Condition: yes** — Phase 5 may start (constrained allocation optimizer
  vs weighted greedy, per the original spec).

## 14. Commands to reproduce

```
.venv/Scripts/python.exe -m pytest                                     # 187 passed
.venv/Scripts/python.exe -m pytest tests/test_phase4_portal_e2e.py     # 7 browser e2e
.venv/Scripts/python.exe -m pytest tests/test_phase4_ood.py            # 2 OOD
.venv/Scripts/python.exe -m pytest tests/test_phase4_ablation.py       # 4 ablation
.venv/Scripts/python.exe tests/eval/generate_phase4_artifacts.py       # chaos artifacts
.venv/Scripts/python.exe tests/eval/portal_strategy_benchmark.py       # 5-method comparison
.venv/Scripts/python.exe -m ruff check app/ tests/                     # clean
```
