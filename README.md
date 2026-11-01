# ProcureAgent

> A production-oriented procurement agent with LLM planning, reliable execution, API/portal integrations, checkpoint recovery, deterministic decision workflows, and full execution tracing.

## Why ProcureAgent?

Enterprise procurement usually spans several very different supplier channels:

- modern supplier REST APIs,
- internal business systems,
- legacy supplier portals that expose no standard API at all.

A plain chatbot can only produce advice. A generic browser agent is often hard to control: execution paths are unpredictable, suppliers can be called repeatedly, failures are hard to recover, and long tasks can restart from scratch. ProcureAgent is not an attempt to build a browser agent that can operate any website.

It solves a narrower problem: how to let an LLM participate in procurement decisions while keeping execution reliable, recoverable, traceable, testable, and controllable. ProcureAgent combines LLM-based uncertainty handling with a deterministic enterprise execution runtime.

## Key Design Principles

### 1. LLM for uncertainty, code for certainty

The LLM handles natural-language understanding, limited planning, and recommendation explanation.

Deterministic code owns scoring, budget, retry, checkpointing, policy, and approval. The LLM never holds final business-execution authority.

### 2. Reliable Agent Runtime

`TaskRuntime` manages retry, timeout, step state, idempotency, checkpoints, resume, and execution budgets.

For example, if supplier A and supplier C already succeeded while supplier B failed, resuming the task reuses A and C and continues with only B. Successful suppliers are not called again.

### 3. API-first, Portal-fallback

Real systems should not route every supplier through browser automation. When a supplier exposes an API, ProcureAgent uses `ApiExecutor`. The fixed-action `PortalExecutor` is only used for legacy portals without an API. This is more stable, faster, and easier to test than treating every action as a browser-agent task.

### 4. Deterministic procurement decisions

Supplier recommendations are not produced by asking an LLM which option "feels better". `ProcurementScorer` evaluates price, delivery, and stock with deterministic rules. `ProcurementPolicy` controls approval thresholds. The LLM only explains the already-computed result.

### 5. Recoverable and observable

Every step is persisted. Failed attempts are not overwritten. `StepExecution` and `TraceRecord` support `FAILED task -> Resume -> Continue from checkpoint`.

### 6. Built-in evaluation

ProcureAgent is validated with `pytest` and an Agent Eval Runner. The evaluation suite measures task success, plan validation, recovery, recommendation accuracy, executor behavior, latency, and LLM calls. Fault injection is intentional and is not hidden.

## Architecture

```text
Natural Language
      |
      v
ProcurementInterpreter
      |
      v
Planner
      |
      v
PlanValidator
      |
      v
ProcurementOrchestrator
      |
      v
TaskRuntime
      |
      v
ExecutorRouter
   /       \
  v         v
API       Portal
Executor  Executor
   \       /
      v
QuoteNormalizer
      |
      v
ExecutionValidator
      |
      v
ProcurementScorer
      |
      v
ProcurementPolicy
      |
      v
RecommendationFinalizer
```

> Planner decides what to do, Executor performs the operation, and TaskRuntime ensures that execution remains reliable and recoverable.

## Feature Highlights

* Natural-language procurement requests
* Structured LLM planning with validation
* API and legacy portal supplier integrations
* Retry and execution budgets
* Step-level checkpoints
* Idempotent execution
* Failed-task resume
* Deterministic supplier scoring
* Human approval gate
* Persistent execution trace
* LLM fallback strategy
* Built-in Agent evaluation suite

## Quick Start

```bash
git clone https://github.com/zsj-409/ProcureAgent.git
cd ProcureAgent

python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

Start the services:

```bash
uvicorn mock_services.supplier_api.main:app --host 127.0.0.1 --port 8101
```

```bash
uvicorn mock_services.supplier_portal.main:app --host 127.0.0.1 --port 8102
```

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Swagger: `http://localhost:8000/docs`

Workbench: `http://localhost:8000/` — the interactive web UI ships with the app itself (no build step, no CDN).

## Web Workbench (v2)

The self-contained SPA at `app/webui/static/` presents and operates the whole system:

- **总览 Overview** — live counters, the execution pipeline, and the design principles.
- **新建采购 New Task** — natural-language input (bilingual), live per-step progress polling, then the full result: recommendation card, quote comparison, split-award table, and the approval gate (approve/reject).
- **任务记录 Tasks** — every task with status pills, search, and one-click resume for failed tasks.
- **Task detail** — interpreter output, validated plan, scored quotes with the best offer highlighted, approval actions, execution trace, and the **portal automation replay**: per-step JPEG frames captured by the adaptive portal agent, so self-healing is visible (`click gate → fill → done`).
- **供应商 Suppliers** — channels with live `/health` probes and latency.

New API endpoints in v2: `GET /api/v1/tasks` (list), `POST /api/v1/tasks/{id}/reject`, `GET /api/v1/tasks/{id}/approvals`, `GET /api/v1/tasks/{id}/portal-steps`, `GET /api/v1/suppliers`, `GET /api/v1/overview`. `POST /api/v1/agent/run` now defaults to synchronous behavior for compatibility; pass `?wait=false` to start a background task (dedicated worker thread with its own event loop) and poll.

## What v2 Changed (Deep-Dive)

The v1 MVP implemented the idea only at its surface. v2 deepens it along the original intent:

1. **Adaptive portal agent** (`app/executors/portal_agent.py`) — the browser-use core is back, bounded and safe. The agent takes a numbered DOM snapshot (inputs, selects, buttons, tables, card grids), chooses actions from a strict vocabulary (`fill`/`select`/`click`/`press_enter`/`done`), and extracts deterministically (table columns mapped by header keywords; card text by anchored regex). Elements are addressed by per-kind ordinal, so randomized or drifted markup cannot break it. **Guards**: bounded action budget, no navigation actions, and form values restricted to the product name and enumerated select options — page text is untrusted and can never drive form input.
2. **Three-level escalation ladder** (`PortalExecutor`) — fixed script (fast, deterministic) → heuristic agent (no LLM needed) → LLM agent (snapshot in, structured action out). Structural failures (selector drift, interstitial gates) escalate immediately without wasting retries; only transient failures retry. One browser session is shared across the whole ladder. Every escalation and every adaptive step is traced, and each step saves a JPEG frame for UI replay.
3. **Chaos portal** (`mock_services`) — a second card-layout portal (no `<table>`, category select, pagination) plus chaos mode with per-seed randomized ids/classes and an interstitial session-check gate. The fixed script provably fails; the adaptive agent provably recovers.
4. **Split-award optimization** (`ProcurementScorer`) — when no single supplier covers the quantity, the order is split deterministically (greedy by weighted score, capped at 3 lines) with an explicit `shortfall`. Stock shortfalls no longer kill the task in `ExecutionValidator`; a shortfall always routes to human approval instead of auto-approving a partial order.
5. **Parallel collection** — supplier collection runs with bounded concurrency (semaphore, `PROCUREAGENT_COLLECT_CONCURRENCY`), preserving per-supplier checkpoints and idempotency. A 5-supplier task dropped from ~80 s to ~5 s after fail-fast escalation and shared sessions.
6. **Background execution** — `agent/run?wait=false` submits to a `BackgroundTaskRunner` (worker thread + own event loop), so browser tasks never block the HTTP call, in uvicorn and in tests alike.
7. **Bilingual interpreter fallback** — the no-LLM regex fallback now strips English preference/budget phrases (`prefer lowest price`, `budget under 1200`) as well as Chinese ones, so zero-credential demos parse correctly.

## Example

```text
Help me procure 20 MX Master 3S units with a budget of $1,500.
Prioritize delivery speed.
```

The system interprets the request, plans the workflow, collects supplier quotes, validates them, scores them, applies the approval policy, and generates a recommendation.

## Testing

```bash
pytest -q
```

Current v2 verification result:

```text
38 passed
```

Evaluation:

```bash
python -m tests.eval.eval_runner
```

> The evaluation suite intentionally includes API failures, portal failures, all-supplier failures, malformed inputs, and recovery scenarios.

The current recorded results include:

```text
Recovery Success Rate = 1.000
Recommendation Accuracy = 1.000
Plan Validation Success Rate = 1.000
```

`Executor Success Rate` is also reported, but it includes intentionally injected supplier failures, so it should not be read as a normal-case success percentage.

## Scope

ProcureAgent currently generates procurement recommendations and approval workflows.

It does not perform:

* real purchases
* payments
* automatic checkout

## Production Quality Checks

* Real LLM smoke test: `python scripts/llm_smoke_test.py` (requires user-configured `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`).
* Resume E2E: `pytest tests/test_resume_e2e.py`.
* Portal resource cleanup: `pytest tests/test_portal_cleanup.py`.

Retry handles transient step failures within one execution cycle. Resume restores a previously failed task from persisted checkpoints.
