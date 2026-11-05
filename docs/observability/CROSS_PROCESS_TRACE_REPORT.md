# Phase 10 — Cross-Process Trace Report

- **Date**: 2026-11-02
- **Commit B**: (pending)
- **Method**: W3C TraceContext (traceparent) persisted via TraceRecorder/DB.

## The problem

A Durable Worker can crash mid-task (real `os._exit` in
`test_full_task_recovery.py`). After the lease expires, a NEW worker
process reclaims the task. The observability question: how do we relate
the old attempt's execution to the new attempt's execution WITHOUT lying
about causality?

## The approach

1. Worker A starts `ProcurementTask` span → trace_id T, span_id SA.
2. Every TraceRecorder row for that task stores trace_id T + span_id SA
   (+ lease_epoch).
3. Worker A crashes (span ends with outcome=CRASHED).
4. Worker B reclaims; `resume()` reads the persisted traceparent
   (`00-T-SA-01`) and calls `start_task_span(..., traceparent=...)`.
5. The new span INHERITS trace_id T (W3C parent context) and gets its own
   span_id SB — ONE trace, TWO worker spans. This is the honest
   representation: the same business task, two execution attempts, linked
   by the trace, not a fabricated unbroken span tree.

## Verified (tests)

| scenario | test | result |
|---|---|---|
| cross-process traceparent inheritance | test_cross_process_traceparent_link | same trace_id across crash |
| crash + reclaim same trace | test_crash_recovery_shares_trace_id | PASS |
| distinct worker spans | (same test) | span_id differs |
| fencing rejection traceable | test_fencing_rejection_event | STALE_FENCING_EPOCH recorded |
| UNKNOWN_OUTCOME traceable | test_unknown_outcome_trace | outcome=UNKNOWN_OUTCOME |
| exporter unavailable → task ok | test_exporter_failure_does_not_break_task | PASS |
| masking (no prompts/identities) | test_trace_masking | PASS |
| workbench trace auth regression | test_workbench_trace_requires_auth | 401 without principal |

## Real trace sample

`docs/eval/agent_trace_manifest.json` — a real task run
(`phase10-trace-demo`) emitting the `ProcurementTask` root span with
task_id + outcome; recorded from the live orchestrator with OTel enabled.

`docs/eval/cross_process_trace_results.json` — machine-readable summary.
`docs/eval/agentops_metrics.json` — metric registry with strict cost
semantics (REAL vs observation proxy separation).

## Honest boundary

- The trace tree across a crash is ONE trace with sibling worker spans —
  it does NOT claim a continuous span tree (the crash interrupted it).
- Trace context survives only when the DB write happened before the crash
  (the trace row is committed with the step state).
- This is local-process crash simulation; distributed brokers would add
  network-level propagation (same W3C scheme, not implemented here).
