# Phase 10 — AgentOps Architecture (OpenTelemetry)

- **Date**: 2026-11-02
- **Commit B**: (pending)
- **SDK**: opentelemetry-sdk / api / exporter-otlp-proto-http **1.45.2**
- **Default**: InMemorySpanExporter (offline demo, tests). OTLP HTTP export
  only when explicitly enabled (`otel_export_enabled=true`). Exporter
  failure NEVER breaks procurement tasks (defensive try/except everywhere).

## Layering

```
Task execution (orchestrator/worker)
   │  start_task_span(stage, task_id, worker_id, lease_epoch, ...)
   ▼
OTel TracerProvider (service.name=procureagent)
   │  BatchSpanProcessor
   ▼
InMemorySpanExporter (default)  │  OTLP HTTP (opt-in)
   ▼                              ▼
tests / offline demo             Jaeger / collector (optional, local)
```

- The **database remains the source of truth** for tasks, steps, approvals,
  and external effects. OTel is the trace/metrics OUTPUT layer only — no
  second, incompatible facts store (per the spec).
- GenAI semantic conventions: only stable fields are claimed; development-
  stage fields are marked with their applicable version (not asserted as
  final standards).

## Span model (Task 9)

Root span per procurement task: `ProcurementTask` (or `ResumeAttempt` for a
resumed task). Child stages:

- Interpret Request
- Validate Plan
- Collect Supplier Quotes (MCP tool invocation / supplier API / portal
  recovery)
- Normalize Quotes
- Score / Optimize
- Policy Check
- Approval
- Finalize

Security metadata on every span: task_id, stage, worker_id, lease_epoch,
plan_hash, attempt_id, logical_step_id, tool_name, tool_version,
policy_decision, outcome, retry_count, elapsed_ms, error_code.

**Sensitive-field minimization**: prompts, tokens, credentials, approver
identities, and raw quote payloads are NOT collected. If needed, a stable
sha256 prefix (16 hex chars) is stored instead (see `mask_sensitive`).

Task ID is a span attribute (fine — spans are per-task). It is NOT used as
a high-cardinality metric label (counters carry no task_id dimension).

## Cross-process propagation (Task 10)

- W3C TraceContext: `traceparent` (00-trace_id-span_id-flags).
- The TraceRecorder/DB now persists `trace_id`, `span_id`, `parent_span_id`,
  `lease_epoch` on every trace row.
- On crash, the NEW worker attempt calls `start_task_span(..., traceparent=prev)`
  which INHERITS the same trace_id (parent context) — the honest
  representation of crash + reclaim as ONE trace with TWO worker spans, NOT
  a fabricated unbroken span tree.
- Stale fencing commits / UNKNOWN_OUTCOME transitions are recorded as
  traceable events with reason codes.

## Metrics (Task 11) — strict cost semantics

Counters: runtime (verified_task_completion, task_recovery, lease_reclaim,
stale_commit_rejected, unknown_outcome), tool (invocation, error, duration,
retry, policy_denial), safety (unauthorized_tool_attempt,
unsafe_action_rejected, budget_violation, approval_conflict), cost.

**Cost semantics are strictly separated**:
- `actual_llm_calls` / `input_tokens` / `output_tokens` / `estimated_llm_cost`
  are ONLY incremented by REAL LLM runs. While REAL_LLM_NOT_RUN they are
  ABSENT (never fake 0).
- `supplier_query_cost` / `recovery_extra_invocations` are OBSERVATION
  PROXIES (counts), explicitly not money.
- Mock / estimate / missing values are labeled, never conflated.

## Local observability stack (Task 12)

- Default: fully offline (InMemorySpanExporter).
- Optional: OTLP exporter to a local collector/Jaeger — configured via env,
  never required. No Kubernetes, no cloud dependency.
- Bounded resources: BatchSpanProcessor with default queue/batch limits.

## Workbench integration (Task 13)

- Flight Recorder rows now carry trace_id/span_id so the Workbench can show
  the task timeline with worker ownership, lease epoch, step attempts, MCP
  tool calls, policy denials, recovery attempts, unknown outcomes, approval
  transitions, and cost summary.
- The Observatory remains read-only; live-data endpoints still require auth
  (regression-tested).
