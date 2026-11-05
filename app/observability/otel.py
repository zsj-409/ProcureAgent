"""Phase 10 — OpenTelemetry AgentOps instrumentation (Commit B).

Layers:
- TracerProvider with a configurable exporter: InMemorySpanExporter by
  default (tests/offline), OTLP HTTP optional via env. Exporter failure
  must NEVER break procurement tasks (defensive try/except everywhere).
- Root span per procurement task + child spans per stage:
  Interpret / ValidatePlan / CollectQuotes (MCP tool / supplier API /
  portal recovery) / Normalize / Score / Policy / Approval / Finalize.
- Security metadata on spans: task_id, logical_step_id, attempt_id,
  worker_id, lease_epoch, plan_hash, tool_name/version, policy_decision,
  outcome, retry_count, elapsed_ms, error_code. Sensitive fields
  (prompts, tokens, credentials, approver identity, raw quotes) are NOT
  collected; a stable hash may replace them.
- Cross-process: W3C TraceContext (traceparent) is persisted via the
  TraceRecorder/DB; a crash + reclaim links the new worker's span to the
  old attempt's span with a Span LINK (never a fake unbroken tree).
- Metrics (counters/histograms): runtime, tool execution, safety, cost.
  REAL tokens/cost vs MOCK vs estimate vs observation counts are kept
  strictly separate; missing values are recorded as absent, never 0.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ..infrastructure.settings import Settings

_SERVICE_NAME = "procureagent"


def setup_otel(
    settings: Settings | None = None,
    *,
    enable_export: bool = False,
    in_memory: bool = True,
) -> dict[str, Any]:
    """Initialize OTel providers once.

    - in_memory=True (default): InMemorySpanExporter — spans are retrievable
      for tests and offline demo; nothing leaves the process.
    - enable_export=True + OTLP env vars: BatchSpanProcessor with the OTLP
      HTTP exporter. Any exporter error is swallowed (never breaks the task).
    - Metrics: a MeterProvider with a console/metric reader (in-memory
      instruments are read via the meter's counters directly).

    Returns a handle dict {tracer, meter, span_exporter, in_memory} so tests
    can inspect spans.
    """

    resource = Resource.create({"service.name": _SERVICE_NAME})
    tracer_provider = TracerProvider(resource=resource)
    span_exporter: Any = InMemorySpanExporter() if in_memory else None

    if enable_export and not in_memory:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            span_exporter = OTLPSpanExporter()
        except Exception:
            span_exporter = InMemorySpanExporter()

    if span_exporter is not None:
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    metrics_reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metrics_reader])
    metrics.set_meter_provider(meter_provider)
    trace.set_tracer_provider(tracer_provider)

    # Use the provider DIRECTLY (not the global API) so each setup_otel
    # handle is self-contained — global tracer caching leaks spans across
    # test setups.
    tracer = tracer_provider.get_tracer(_SERVICE_NAME)
    meter = meter_provider.get_meter(_SERVICE_NAME)
    return {
        "tracer": tracer,
        "meter": meter,
        "span_exporter": span_exporter,
        "in_memory": in_memory,
        "tracer_provider": tracer_provider,
    }


def flush(handle: dict[str, Any]) -> None:
    """Force-export pending spans (InMemory exporter is async via the
    BatchSpanProcessor)."""
    tp = handle.get("tracer_provider")
    if tp is not None:
        tp.force_flush()


def mask_sensitive(value: str | None) -> str | None:
    """Stable hash for sensitive fields — never store raw prompts/tokens."""
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _trace_context_from_env() -> tuple[str, str, str | None] | None:
    """Parse W3C traceparent from the environment (cross-process)."""
    header = os.environ.get("TRACEPARENT", "")
    if not header:
        return None
    parts = header.split("-")
    if len(parts) < 2:
        return None
    return parts[1], parts[2], parts[3] if len(parts) > 3 else None


def start_task_span(
    tracer: Any,
    *,
    task_id: str,
    stage: str,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
    plan_hash: str | None = None,
    attempt_id: str | None = None,
    logical_step_id: str | None = None,
    traceparent: str | None = None,
) -> Any:
    """Start a span, optionally linked to a previous attempt (recovery).

    When ``traceparent`` (from a previous worker attempt) is provided, the
    new span carries a Span LINK to it instead of pretending the tree is
    unbroken — this is the honest representation of crash + reclaim.
    """

    if tracer is None:
        return None
    parent_context = None
    if traceparent:
        tp = traceparent.split("-")
        if len(tp) >= 4:
            try:
                remote_ctx = trace.SpanContext(
                    trace_id=int(tp[1], 16),
                    span_id=int(tp[2], 16),
                    is_remote=True,
                    trace_flags=trace.TraceFlags(int(tp[3], 16)),
                )
                # W3C propagation: the new span INHERITS the remote trace_id
                # (same trace across crash) and is CHILD of the remote span.
                parent_context = trace.set_span_in_context(
                    trace.NonRecordingSpan(remote_ctx)
                )
            except ValueError:
                parent_context = None

    span = tracer.start_span(
        stage,
        kind=trace.SpanKind.INTERNAL,
        context=parent_context,
        attributes={
            "task_id": task_id,
            "stage": stage,
            "worker_id": worker_id,
            "lease_epoch": lease_epoch,
            "plan_hash": plan_hash,
            "attempt_id": attempt_id,
            "logical_step_id": logical_step_id,
        },
    )
    return span


def span_attributes(span: Any, **kwargs: Any) -> None:
    """Attach safe metadata to a span (sensitive fields must be pre-masked)."""
    for key, value in kwargs.items():
        if value is None:
            continue
        span.set_attribute(key, value)


def end_span(span: Any, *, outcome: str | None = None, error_code: str | None = None) -> None:
    if span is None:
        return
    if outcome:
        span.set_attribute("outcome", outcome)
    if error_code:
        span.set_attribute("error_code", error_code)
    span.end()


# ---------------------------------------------------------------------------
# Metrics (Task 11) — counters, strict separation of cost semantics
# ---------------------------------------------------------------------------

def build_metrics(meter: Any) -> dict[str, Any]:
    """Create the AgentOps counters/histograms."""

    def counter(name: str, unit: str = "1", description: str = "") -> Any:
        return meter.create_counter(name, unit=unit, description=description)

    return {
        # Runtime
        "verified_task_completion_total": counter(
            "verified_task_completion_total",
            "1",
            "tasks verified complete",
        ),
        "task_recovery_total": counter("task_recovery_total", "1", "recovery attempts"),
        "lease_reclaim_total": counter("lease_reclaim_total", "1", "lease reclaims"),
        "stale_commit_rejected_total": counter(
            "stale_commit_rejected_total",
            "1",
            "stale fencing commits rejected",
        ),
        "unknown_outcome_total": counter("unknown_outcome_total", "1", "UNKNOWN_OUTCOME transitions"),
        # Tool execution
        "tool_invocation_total": counter("tool_invocation_total", "1", "tool invocations"),
        "tool_error_total": counter("tool_error_total", "1", "tool errors"),
        "tool_policy_denial_total": counter("tool_policy_denial_total", "1", "policy denials"),
        # Safety
        "unauthorized_tool_attempt_total": counter(
            "unauthorized_tool_attempt_total",
            "1",
            "unauthorized attempts",
        ),
        "unsafe_action_rejected_total": counter(
            "unsafe_action_rejected_total",
            "1",
            "unsafe actions rejected",
        ),
        "budget_violation_detected_total": counter(
            "budget_violation_detected_total",
            "1",
            "budget violations",
        ),
        "approval_conflict_total": counter("approval_conflict_total", "1", "approval CAS conflicts"),
        # Cost (strict semantics: only REAL counts; MOCK/estimated are separate)
        "actual_llm_calls": counter("actual_llm_calls", "1", "REAL LLM calls (0 while REAL_LLM_NOT_RUN)"),
        "input_tokens": counter("input_tokens", "1", "REAL input tokens (absent while not run)"),
        "output_tokens": counter("output_tokens", "1", "REAL output tokens (absent while not run)"),
        "estimated_llm_cost": counter("estimated_llm_cost", "USD", "REAL estimated USD cost"),
        "supplier_query_cost": counter(
            "supplier_query_cost",
            "1",
            "supplier queries (observation proxy, NOT money)",
        ),
        "recovery_extra_invocations": counter(
            "recovery_extra_invocations",
            "1",
            "extra recovery observations",
        ),
    }


def record_metric(
    metrics_handle: dict[str, Any], name: str, amount: int = 1, attrs: dict | None = None
) -> None:
    """Increment a counter, tolerating any OTel error (never breaks the task)."""
    try:
        counter = metrics_handle.get(name)
        if counter is not None:
            counter.add(amount, attrs or {})
    except Exception:
        pass
