# PlanValidator Phase 1 Report

- **Date**: 2026-11-01
- **Module**: `app/agent/validator.py`, `app/agent/orchestrator.py` (runtime gate)
- **Tests**: `tests/test_plan_validator_phase1.py` (13 cases)

## 1. Vulnerability fixed

The old validator only checked that `SCORE` and `CHECK_POLICY` were *present*
and that COLLECT_SUPPLIER steps were well-formed. It accepted:

- `[SCORE, CHECK_POLICY]` (empty plan, no sourcing) — valid under old code
- `[COLLECT, SCORE]` (missing NORMALIZE and CHECK_POLICY)
- `[COLLECT, CHECK_POLICY, SCORE]` (policy before score)
- any plan missing FINALIZE (FINALIZE was never enforced)

## 2. New validation (dependency-ordered)

Valid skeleton: `(COLLECT_SUPPLIER* → NORMALIZE* → SCORE → CHECK_POLICY [→ FINALIZE])?`

| Rule | Error |
|---|---|
| Empty plan | `Plan has no steps` |
| No COLLECT_SUPPLIER | `Plan must include at least one COLLECT_SUPPLIER` |
| Illegal action | `Illegal action: ...` (also blocked at pydantic enum level) |
| Unknown / duplicate supplier | `Unknown supplier` / `Duplicate supplier` |
| SCORE before a COLLECT | `SCORE must come after all COLLECT_SUPPLIER steps` |
| NORMALIZE after SCORE / missing | `NORMALIZE must come before SCORE` |
| CHECK_POLICY before SCORE | `CHECK_POLICY must come after SCORE` |
| FINALIZE not last | `FINALIZE must be the last step of the plan` |

**Legitimate review path**: a plan ending at CHECK_POLICY (no FINALIZE) is
VALID — it represents shortfall / budget-exception → WAITING_APPROVAL. We do
not force FINALIZE on review tasks.

## 3. Runtime enforcement (defense in depth)

- `Planner.plan` (LLM) validates internally and falls back to the rule-based
  planner on failure.
- `ProcurementOrchestrator.run`: the plan is re-validated by
  `_validate_plan_for_execution` **before** `save_plan`; an invalid plan fails
  the task (`Plan rejected before execution: ...`).
- `ProcurementOrchestrator.resume`: a persisted plan (possibly from an older
  schema) is re-validated on resume; an invalid persisted plan marks the task
  FAILED and never reaches the executor.
- API-constructed plans pass through the same `run` path.

## 4. Results

```
13 new plan-validator tests: all pass (11 rejection rules + 2 valid paths)
Existing test_planner.py: still passes (valid plans unchanged)
```

Negative result: pydantic blocks illegal *string* actions at construction
(`ValidationError`), so the validator's `Illegal action` branch is defense in
depth rather than the primary gate — this is a stronger guarantee, not a gap.

## 5. Known limitations

- No semantic check that a COLLECT step's supplier actually exists at runtime
  beyond the registry (registry is authoritative at plan time).
- The review path is permissive by design (no FINALIZE); the orchestrator's
  policy result still decides the final state.
