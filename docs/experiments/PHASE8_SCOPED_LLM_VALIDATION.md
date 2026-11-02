# Phase 8 — Scoped Real-LLM Validation

- **Date**: 2026-11-02
- **Status**: REAL_LLM_NOT_RUN (no credentials provided this session)
- **Baseline HEAD**: `f50db1f` (Task 0 commit `4d26f67`)

## Scope (Task 1)

Validates the two EXISTING business paths only — no new agent architecture:

| path | component | prompt version |
|---|---|---|
| A. Procurement Requirement Interpreter | `app/agent/interpreter.py` → `ProcurementInterpreter.interpret()` | interpreter-v1 |
| B. Portal LLM Fallback | `app/executors/portal_agent.py` → `LLMPortalAgent.next_action()` | portal-agent-v1 |

Model client: `OpenAICompatibleModelClient` (app/agent/model.py). Already
supports structured output validation, latency, and token usage when the
provider returns it.

## Hard gates (Task 1/7)

- Real API calls are **explicit opt-in**: a key alone is not enough —
  `llm_sandbox_only` (default True) blocks paid calls.
- Conservative budgets in `Settings`: `llm_max_calls=20`,
  `llm_max_tokens=2000`, `llm_retry_limit=1`, `llm_cost_budget_usd=5.0`.
- Credentials come from env/secure config only — nothing committed to git.
- Without credentials the harness outputs `REAL_LLM_NOT_RUN` with a
  reason — never fabricated model results.

## Task 2 — Interpreter validation (harness)

`app/eval/phase8_llm.py`: 10 frozen scenarios (Chinese input, mixed EN/ZH,
quantity variation, hard budget, delivery priority, price priority, missing
product, ambiguous, infeasible, currency/unit expression).

Rule baseline result (deterministic, this session):

| dimension | result |
|---|---|
| scenarios | 10 |
| ok | 7/10 |
| known rule limits | 元→CNY not mapped (2), 万台→10000 not multiplied (1) |
| correct refusal (missing product) | counted as ok |

The rule baseline's 3 failures are REAL extractor limitations — exactly the
comparison Phase 8 exists to quantify against an LLM.

Expected Structured Request verified field-by-field: Product Identity
(CONTAINS), Quantity (exact), Budget Mode, Currency, Delivery Constraints,
User Preferences, Missing Information Handling (refusal), Hallucinated
Fields (never filled in by the harness).

## Task 3 — Portal fallback validation (harness)

`app/eval/phase8_portal.py`: 8 frozen scenarios reusing Phase 4 fault
families on the local simulated supplier portal (tests/eval/portal_world.py),
each with an independent world instance.

| scenario | fault | offline verified |
|---|---|---|
| p01_selector_drift | F1_SELECTOR_DRIFT | ✅ |
| p02_layout_drift | F2_LAYOUT_DRIFT | ✅ |
| p03_pagination | F3_PAGINATION_DRIFT | ✅ |
| p04_modal | F4_MODAL_INTERRUPTION | ✅ |
| p05_injection | F7_PROMPT_INJECTION | ✅ |
| p06_ambiguous_unsafe | F8_AMBIGUOUS_UNSAFE | ✅ |
| p07_session_expiry | F5_SESSION_EXPIRATION | ✅ |
| p08_quote_expiry | QUOTE_EXPIRED | ✅ |

Guards verified: every model-proposed action must be in the whitelist
(open_page/fill_search/submit_search/select_product/next_page/
extract_quote/done); anything else → rejected_unsafe_action. The model can
never execute JS, shell, orders, or payments.

## Task 4 — Independence & reporting

Machine-readable manifests (committed):
- `docs/eval/phase8_manifest_real_llm_not_run.json` (interpreter)
- `docs/eval/phase8_portal_manifest_real_llm_not_run.json` (portal)

Each records: git commit, model id, provider, prompt version, input hash,
world snapshot hash, model config (max_calls/max_tokens/retry/cost budget/
sandbox), mode, calls, tokens (None == UNKNOWN, never fake 0), cost,
per-scenario rows, errors.

Phase 4–7 benchmark artifacts are untouched. REAL vs SCRIPTED/MOCK are
counted separately. Small samples are feasibility validation only — no
significance claims.

## How to run with a real key (when authorized)

```bash
# env (never commit these):
export PROCUREAGENT_LLM_API_KEY=...
export PROCUREAGENT_LLM_SANDBOX_ONLY=false   # explicit opt-out of sandbox

python -m app.eval.phase8_llm    # interpreter validation (REAL mode)
python -m app.eval.phase8_portal # portal fallback validation (REAL mode)
```

Both print a summary and write a REAL manifest. Budgets are enforced by the
harness (max_calls, cost_budget).

## Result

This session: **REAL_LLM_NOT_RUN** — no credentials were provided. The
offline harness, frozen scenarios, budget gates, and honest manifests are
committed and tested (9 new tests). Skipping paid experiments is not a
failure (Task 7).
