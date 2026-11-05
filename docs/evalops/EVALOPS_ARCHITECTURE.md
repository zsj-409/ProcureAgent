# Phase 10 — EvalOps Architecture

- **Date**: 2026-11-02
- **Commit C**: (pending)
- **Scope**: reproducible agent evaluation release gates.

## Components

```
app/evalops/core.py
├── ReleaseCandidate      — versioned candidate from REAL git state
├── build_metric_registry — 20 frozen metric specs (safety/quality/cost)
├── ReleaseGateEngine     — per-gate PASS/FAIL/INSUFFICIENT_EVIDENCE/
│                           NOT_APPLICABLE/ERROR; HARD safety gates fail-closed
├── gate_summary          — verdict: QUALIFIED or REJECTED
├── ShadowEvaluator       — baseline + candidate on ISOLATED snapshots
└── PromotionManager      — DRAFT → EVALUATING → QUALIFIED → PROMOTED /
                            REJECTED / ROLLED_BACK (audited)
```

Versioned candidates: commit_hash, agent_version, recovery_policy_version,
prompt_version, tool_registry_version, tool_policy_version,
model_config_version, evaluator_version, frozen_dataset_version,
environment_snapshot_hash — all from git/config, never invented.

## Design rules

- **Hard Safety Gates fail-closed**: ANY safety violation (unauthorized MCP
  call, cross-task read, prompt-injection success, hard-budget violation,
  expired quote, approval version mismatch, duplicate side effect, blind
  retry, unsafe browser action, stale fencing commit) → FAIL, and NO cost /
  latency / success improvement can offset it.
- **Quality gates** compare to a baseline on frozen worlds via the
  independent evaluator; insufficient samples → INSUFFICIENT_EVIDENCE
  (never PASS).
- **Performance gates** use versioned thresholds (P50/P95 latency, extra
  recovery invocations, REAL token cost); small samples are reported as
  limitations.
- **A gate verdict is a controlled-environment decision, NOT a production
  safety proof.**

## Isolation

- Shadow evaluation: each method gets its own immutable snapshot of the
  same world. The candidate NEVER writes real task state, approvals,
  orders, or payments. Its tool calls still go through the secure MCP
  gateway (shadow mode is not a bypass).
- DEV/CONFIRMATION/TEST splits stay frozen (Phase 6 contract).

## Promotion & Rollback

- Promotion requires QUALIFIED (no FAIL gates). Status is changed ONLY via
  gate verdicts, never by direct field mutation.
- Rollback preserves the previous version binding; running tasks keep
  their historical version/checkpoint rules (never reuse incompatible
  results).
- Full audit trail: promoted_by, candidate/previous version, evaluation
  manifest, decision, timestamp (docs/eval/promotion_history.json).
