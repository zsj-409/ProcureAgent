# Phase 10 — Promotion & Rollback

- **Date**: 2026-11-02
- **Commit C**: (pending)

## State machine

```
DRAFT → EVALUATING → QUALIFIED → PROMOTED
                 ↘ REJECTED
PROMOTED → ROLLED_BACK (on incident)
```

- Status transitions happen ONLY via `PromotionManager` methods:
  - `create` → DRAFT
  - `mark_evaluating` → EVALUATING
  - `apply_gate_results` → QUALIFIED (all PASS, no INSUFFICIENT_EVIDENCE,
    at least one evaluated gate) or REJECTED (any FAIL / any
    INSUFFICIENT_EVIDENCE / no evaluated gates) — derived from the ACTUAL
    gate results, never a caller-supplied verdict string.
  - `promote` → PROMOTED (requires QUALIFIED, else ValueError)
  - `rollback` → ROLLED_BACK (records the reason/previous version)

Directly mutating the status field is impossible by construction (the
manager owns the state).

## Promotion record (audit)

- promoted_by
- candidate_version / previous_version
- evaluation_manifest (release_gate_results.json hash)
- decision
- timestamp

Stored in `docs/eval/promotion_history.json` (and rollback_history.json).

## Rules

- **Promotion requires Qualification** — a candidate with any FAIL gate
  cannot promote (verified: unsafe candidate → REJECTED → promote raises
  ValueError).
- **Rollback preserves historical version bindings** — running tasks keep
  their version/checkpoint rules; a rollback retires the promoted candidate
  and leaves the previous version available; incompatible results are never
  reused.
- No trusted production admin identity exists yet → promotion is exercised
  only in the controlled local environment (documented limitation).

## Task 21 demo (release-blocking proof)

The deliberately unsafe candidate (relaxed hard budget) runs the full gate
pipeline:

- Evaluator detects the violation (hard_budget_violation = 3).
- The HARD safety gate → FAIL.
- Verdict → REJECTED; promotion attempt raises ValueError.
- Failure reason + trace/manifest are preserved.
- The baseline remains QUALIFIED and can promote.

Artifacts: `release_gate_results.json`, `release_candidate_manifest.json`,
`promotion_history.json`, `rollback_history.json`.
Workbench: Observatory → EvalOps Release Gates shows the REJECTED
candidate (never an all-green board).
