# Phase 11C — Scoped Real-LLM Validation & Final Release

- **Date**: 2026-11-05
- **Status**: **NOT_EXECUTED_NO_CREDENTIALS** (real LLM) · Final demo + docs ✅

## Task 1 — Real-LLM Preflight

Inspected the existing Phase 8 harness (`app/eval/phase8_llm.py`,
`app/eval/phase8_portal.py`) and provider client
(`app/agent/model.py: OpenAICompatibleModelClient`):

| check | result |
|---|---|
| model SDK initializes | `OpenAICompatibleModelClient` builds from settings; `httpx.AsyncClient` |
| structured output schema | `generate_structured` uses the business `ProcurementRequest` schema |
| tool calling bounded | portal fallback only proposes whitelisted actions → `SafeActionExecutor` |
| model cannot mutate budget/approval/policy/gate | model output is a *proposal*; mutation goes through Policy/ApprovalGuard/EvalOps |
| call/token/latency/cost caps | `llm_max_calls=20`, `llm_max_tokens=2000`, `llm_retry_limit=1`, `llm_cost_budget_usd=5.0` |
| paid calls off by default | `llm_sandbox_only` gates even a configured key → REAL_LLM_NOT_RUN |
| credentials not in code/git/chat | key read from env only (`llm_api_key`) |

**Credentials status (Task 1 gate):**
- Environment contains an `ARK_API_KEY` (Volcano Ark), but the configured
  `llm_base_url`/`llm_model` point at OpenAI defaults — **provider mismatch**.
- There is **no explicit budget authorization** for a paid run.
- Per the task book: no matching credentials + no budget authorization →
  **no real calls; REAL_LLM_NOT_RUN stays; Phase 11C real-LLM part =
  NOT_EXECUTED_NO_CREDENTIALS**.

## Task 2 — Frozen Real-LLM Evaluation

**NOT_EXECUTED_NO_CREDENTIALS.** The 10 Interpreter + 8 Portal frozen
scenarios remain in the Phase 8 harness; the sandbox gate would refuse a
real call (`llm_sandbox_only=True` → REAL_LLM_NOT_RUN manifest). No mock
benchmark was re-run (task book: don't repeat unnecessary mock runs).

## Task 3 — Final Enterprise Demo

`app/evalops/final_demo.py` — a reproducible offline demo of the complete
security loop (all numbers from REAL execution records):

| step | what happened (verified) |
|---|---|
| 1 | procurement task created in the real repository (owner alice, HARD budget 162) |
| 2 | MCP `get_procurement_policy` ALLOWED (real handler + policy) |
| 3a | MCP `get_task_execution_status` on own task ALLOWED |
| 3b | MCP on another user's task DENIED (TASK_FORBIDDEN) |
| 4 | unsafe candidate injected (hard-budget mutant, REAL allocation) |
| 5 | independent verifier: INVALID, `hard_budget_exceeded:180>162` |
| 6 | release gate: REJECTED |
| 7 | promotion blocked; active version unchanged (baseline-7) |
| 8 | OTel spans (candidate_execution=FAIL, independent_verification=INVALID, release_gate=REJECTED, promotion_rejection=BLOCKED, tool spans ALLOWED/DENIED) + promotion audit + manifest |

Artifact: `docs/eval/final_enterprise_demo_manifest.json`.

## Task 4 — Documentation

README updated (architecture, offline run, demo runbook, claims table,
risks) — see README "Phase 11B/11C" sections. `docs/experiments/
PHASE11C_REAL_LLM_VALIDATION.md` is this file.

## Task 5 — Final Acceptance

- pytest **414 passed**, ruff clean (verified).
- Browser E2E (`test_phase9_workbench_e2e.py`) verified earlier phases.
- Unsafe candidate cannot promote ✅ (mutation + CAS tests).
- Cross-task MCP read denied ✅ (authorization tests).
- Promotion/rollback transactional ✅ (17 tests incl. subprocess races).
- Tool overreach → no external side effect ✅ (read-only registry).
- Trace ↔ manifest correspondence ✅ (E2E demo).
- Default no paid calls ✅ (sandbox gate).
- Historical benchmarks byte-identical ✅ (SHA-256 verified).

## Final position

The project is **shippable as an offline enterprise agent platform
prototype**: complete security loop demonstrable offline, honest
distinction between real-model validation (not run — no credentials) and
controlled/simulated validation (extensive).
