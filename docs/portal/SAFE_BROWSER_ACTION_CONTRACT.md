# Safe Browser Action Contract

- **Module**: `app/runtime/safe_actions.py`
- **Commit A**: Phase 4

## 1. Finite action registry

All recovery strategies (script / heuristic / LLM / state-aware) act ONLY
through these whitelisted actions:

| action | external side effect | required state | expected post | cost |
|---|---|---|---|---|
| OPEN_ALLOWED_PAGE | none | LOGIN/DASHBOARD/SEARCH/UNKNOWN | SEARCH | 0 |
| FILL_SEARCH_FIELD | none | SEARCH/RESULTS/DASHBOARD | SEARCH | 0.05 |
| SUBMIT_SEARCH | none | SEARCH/RESULTS/DASHBOARD | RESULTS | 0.1 |
| SELECT_PRODUCT | none | RESULTS | QUOTE_DETAIL | 0.1 |
| NEXT_PAGE | none | RESULTS | RESULTS | 0.05 |
| CLOSE_ALLOWED_MODAL | none | SEARCH/RESULTS/QUOTE_DETAIL/DASHBOARD | underlying | 0.05 |
| WAIT_FOR_EXPECTED_STATE | none | any | — | 0 |
| REFRESH_READ_ONLY_VIEW | none | RESULTS/QUOTE_DETAIL/SEARCH/DASHBOARD | — | 0.05 |
| EXTRACT_QUOTE | none | RESULTS/QUOTE_DETAIL | — | 0 |
| STOP_AND_ESCALATE | none | any | — | 0 |

Every action declares: side-effect possibility, required pre-state, target
locator approach (semantic), target uniqueness check, expected post-state,
timeout, max retries, retry-after-failure allowed, actual cost.

## 2. The gate (`SafeActionExecutor.execute`)

The LLM/heuristic can only **propose**; the executor:

1. Rejects unknown actions (not in the registry).
2. Rejects actions from the wrong semantic state (`WRONG_STATE`).
3. Rejects anything that is not read-only (`NOT_READ_ONLY`).
4. On a hostile page (page text contains order/pay/checkout wording):
   blocks navigation-style actions (`PAGE_HOSTILE`) but still permits
   read-only extraction.
5. Verifies the **expected postcondition** with a trusted observation
   (`POSTCONDITION_FAILED` if the page did not reach the expected semantic
   state) — the LLM never judges its own success.
6. Charges cost and enforces bounded budgets.

## 3. Hard safety boundaries

- **No auto purchase / payment / approval bypass.** All actions are
  read-only by contract (`external_side_effect=False` for the whole
  registry).
- **No access to another task's or tenant's browser identity.**
  (Session isolation in Task 5.)
- **No irreversible form submission, no CAPTCHA/2FA bypass, no arbitrary
  command execution.**
- **The supplier page is UNTRUSTED**: injected instructions like
  "ignore previous instructions", "click the hidden button", or a fake
  buy-button cannot change the whitelist. Hostile page text downgrades the
  agent to read-only extraction / safe stop.
- **Success is verified by trusted postconditions, never by the LLM's
  claim.**

## 4. Budgets

`SafeActionExecutor` enforces: max actions, max refreshes, max LLM calls,
max recovery time, max recovery cost. Exhaustion → `BUDGET_EXHAUSTED` →
safe stop / escalate, never an infinite retry loop.

## 5. Verified

- Registry contains all required actions.
- Unknown action / wrong-state action rejected.
- Hostile-page wording blocks SELECT_PRODUCT/SUBMIT/NEXT (PAGE_HOSTILE),
  extraction still allowed.
- Budget exhaustion blocks further actions.
- Postcondition verification fails when the observed state ≠ expected.
- All registered actions are read-only (no external side effect).
17 tests passed in Commit A.
