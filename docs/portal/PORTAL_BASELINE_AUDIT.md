# Portal Baseline Audit

- **Date**: 2026-11-01
- **Baseline HEAD**: `6d9eca8` + Phase 4 Task 0 fixes (approval atomicity,
  claim identity binding)
- **Baseline tests**: `pytest` = **147 passed** (144 + 3 new Task-0 tests)

## 1. Browser engine & Playwright version

```bash
.venv/Scripts/python.exe -c "import playwright; print(playwright.__version__)"
```
Playwright is installed in the project venv; Chromium browser binary available
(verified via `playwright install chromium` dry-run). `app/executors/portal_executor.py`
uses `playwright.async_api.async_playwright`.

## 2. CDP context reuse (session boundary)

**`PortalSession.open()` (portal_executor.py:102-131)**:
- If `chrome_cdp_url` is configured → `connect_over_cdp`, then
  `self._context = browser.contexts[0]` — **reuses the FIRST existing CDP
  context** (P1-5 from Phase 2, now addressed in Phase 4 Task 5: per-task
  isolated BrowserContext; CDP context identity + authorization checked).
- Otherwise launches its own Chromium (headless per settings) and owns the
  context.
- `close()` closes page/context/browser and stops Playwright. For an external
  CDP browser, `browser.close()` only disconnects (does not kill the user's
  browser).

**Session state sharing risk**: the current CDP path reuses `contexts[0]`,
which may carry another task's cookies/localStorage. Phase 4 Task 5 replaces
this with an isolated per-task context and a "context identity check"
(never blindly pick the first context).

## 3. Page locator types

- **Fixed script** (`_run_once`): CSS selectors from supplier config
  (`search_input_selector`, `result_row_selector`, `name_selector`,
  `price_selector`, `stock_selector`, `delivery_selector`) — brittle to
  selector drift.
- **Adaptive loop** (`_apply` in portal_agent.py:645): markup-independent
  `kind`+`ordinal` addressing (`input:visible`, `select:visible`,
  `a[href]:visible`, `button:visible` …) — resilient to random ids/classes.
- No Playwright `get_by_role` / `get_by_label` usage yet.

## 4. Pre/post checks

- **Pre**: `_require_selector` (fail-fast if a configured selector is absent)
  in fixed script; adaptive loop validates `element_index` still exists in the
  snapshot before acting.
- **Post**: fixed script waits `domcontentloaded` after click; adaptive loop
  waits `domcontentloaded` after press_enter/click. **No semantic
  postcondition verification** (does the page now show the expected
  QUOTE_DETAIL / RESULTS?). Phase 4 Task 3/4 adds this.

## 5. Failure classification

- Fixed script: retries ONLY on generic exceptions; structural failures
  (missing selector/table) break immediately → escalate to adaptive.
- Adaptive loop: `WebExecutionError` on exceeded actions / missing extraction /
  unsupported action; heuristics classify gates (interstitial) as clickable.
- **No systematic fault taxonomy** (SELECTOR_CHANGED / LAYOUT_CHANGED /
  MODAL_BLOCKED / SESSION_EXPIRED / DYNAMIC_DOM_UPDATE / TIMEOUT /
  AMBIGUOUS_ELEMENT / UNEXPECTED_NAVIGATION / UNSAFE_ACTION / UNKNOWN_STATE).
  Phase 4 Task 6.2 adds this.

## 6. Execution budget

- `portal_max_actions` bounds the adaptive loop; fixed-script attempts =
  `web_retries + 1`.
- **No per-recovery budget** (max re-observations / refreshes / LLM calls /
  recovery time / recovery cost). Phase 4 Task 6.4 adds bounded recovery.

## 7. Screenshot / DOM / trace recording

- `FrameRecorder.capture` saves one JPEG per adaptive step
  (`portal_frames_dir/task_id/supplier_id/step-NN.jpg`) + replay manifest
  (`PortalAgentStep`). No full DOM dump, no CDP trace, no PII scrubbing of
  frame paths. Phase 4 Task 12 keeps traces but scrubs auth secrets.

## 8. LLM availability

- `LLMPortalAgent` calls `self._model.generate_structured(...)` — requires a
  real configured model client. In this offline repo `settings.llm_api_key` is
  unset → **REAL_LLM_NOT_RUN**. All LLM-path results in this phase are
  SCRIPTED / MOCK unless explicitly marked REAL_LLM_VERIFIED.

## 9. Real invocation paths per recovery level

| level | trigger | real call path | cost |
|---|---|---|---|
| Fixed script | selectors configured | `page.goto → fill → click → extract` | 1 browser session |
| Heuristic agent | script failed / no selectors | snapshot → `next_action` (deterministic) → apply | 0 LLM |
| LLM agent | heuristic failed + model set | snapshot.render() → `model.generate_structured` | ≥1 LLM call |
| escalation | heuristic+LLM both fail | `SupplierUnavailableError` → step fail/retry | — |

One browser session is shared across levels (`PortalExecutor.execute` opens
once, `_run_adaptive` loops levels).

## 10. Audit findings (Task 1)

| # | finding | severity | disposition |
|---|---|---|---|
| P4-1.1 | CDP reuse `contexts[0]` (session leak risk) | P1 | Task 5 isolation |
| P4-1.2 | no semantic state graph / signature | P1 | Task 3 |
| P4-1.3 | no pre/postcondition verification of actions | P1 | Task 3/4 |
| P4-1.4 | no bounded recovery budget | P2 | Task 6.4 |
| P4-1.5 | no fault taxonomy | P2 | Task 6.2 |
| P4-1.6 | traces don't scrub auth secrets | P2 | Task 12 |
| P4-1.7 | REAL_LLM_NOT_RUN (offline) | — | honest marker |

Baseline portal tests exercised: `test_portal_agent.py` (heuristic card
layout, chaos markup escalation + self-heal, no-selectors skip, parse_quote_rows
table/card/reject), `test_portal_cleanup.py` (success/failure cleanup).
All pass on baseline.
