# Phase 10 — Trace & Metric Contract

- **Date**: 2026-11-02
- **Commit B**: (pending)
- **SDK**: OpenTelemetry Python 1.45.2

## Trace contract

| field | span attribute | card |
|---|---|---|
| trace_id | (span context) | 1 |
| span_id | (span context) | 1 |
| task_id | `task_id` | 1 |
| logical_step_id | `logical_step_id` | optional |
| attempt_id | `attempt_id` | optional |
| worker_id | `worker_id` | optional |
| lease_epoch | `lease_epoch` | optional (fencing token) |
| plan_hash | `plan_hash` | optional |
| tool_name / tool_version | `tool_name` / `tool_version` | MCP tool spans |
| policy_decision | `decision` + `reason_code` | gateway spans |
| outcome | `outcome` | end of span |
| error_code | `error_code` | on failure |
| elapsed_ms | span duration | implicit |

**Never collected** (minimization): raw prompts, tokens, credentials,
approver identity, raw quote payloads, API keys. A masked sha256 prefix may
replace a sensitive value when correlation is needed.

## Metric contract

Every metric has: name, definition, unit, semantic class (REAL / MOCK /
estimate / observation-proxy), and cardinality rule (no task_id labels).

### Runtime
- verified_task_completion_total (1)
- task_recovery_total (1)
- task_recovery_duration (ms, histogram)
- lease_reclaim_total (1)
- stale_commit_rejected_total (1)
- unknown_outcome_total (1)

### Tool execution
- tool_invocation_total (1)
- tool_error_total (1)
- tool_duration (ms, histogram)
- tool_retry_total (1)
- tool_policy_denial_total (1)

### Safety
- unauthorized_tool_attempt_total (1)
- unsafe_action_rejected_total (1)
- budget_violation_detected_total (1)
- approval_conflict_total (1)

### Cost — STRICT semantics
- actual_llm_calls (1, **REAL only**; absent while REAL_LLM_NOT_RUN)
- input_tokens / output_tokens (1, **REAL only**; absent while not run)
- estimated_llm_cost (USD, **REAL only**)
- supplier_query_cost (1, **observation proxy — NOT money**)
- recovery_extra_invocations (1, **observation proxy — NOT money**)

A counter's value is a telemetry signal, never the executor's database
truth (per spec: "不得把 Counter 的统计变化直接当成执行器的数据库状态真值").

## W3C TraceContext persistence

- `trace_records` table now stores: trace_id (32 hex), span_id (16 hex),
  parent_span_id (16 hex), lease_epoch.
- A resumed attempt looks up the previous attempt's traceparent and passes
  it to `start_task_span` → the new span inherits the SAME trace_id.
- A crash + reclaim is ONE trace with TWO worker spans — no fabricated
  unbroken tree.
