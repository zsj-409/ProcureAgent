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

Current v1.0 Final verification result:

```text
23 passed
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
