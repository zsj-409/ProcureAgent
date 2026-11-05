# Phase 11B — Final Report

- **Date**: 2026-11-05
- **Commits**: A `0604ad2` (independent regression detection) · B `bfc68fd`
  (durable promotion) · C (trusted execution & integration)
- **pytest / ruff**: see commit sections

## What was delivered

### Commit A — Real candidate safety regression detection
- Candidate Mutation Harness (`app/evalops/mutation.py`): baseline (real
  greedy) + 3 mutants (hard-budget over-spend / expired quote / inventory
  over-allocation) on isolated fixed-seed worlds.
- `AllocationConstraintVerifier` independently recomputes all 11
  constraints from the problem + actual candidate lines — never trusts
  candidate `is_valid`/`feasible`/`verdict`.
- Result: baseline PASS; all 3 mutants FAIL with concrete violation
  reasons (hard_budget_exceeded / line_quote_expired /
  line_inventory_exceeded).

### Commit B — Durable promotion & rollback
- `DurablePromotionManager` (SQLite): promotion_state + promotion_audit
  written in ONE BEGIN IMMEDIATE transaction; CAS on version_epoch;
  active_version persisted (no process-local dict).
- Real subprocess races: 2/4 workers → exactly one PROMOTED winner, audit
  has one row; crash before COMMIT rolls back (os._exit subprocess);
  audit-write failure rolls back; rollback CASes on the active version
  and never rewrites task/plan bindings.

### Commit C — Object authorization & end-to-end validation
- `TaskRecord.owner_id` + `repository.get_owner()`; the MCP gateway's
  task_owner now resolves the REAL owner (the old hard-coded
  `demo-approver` made ownership checks vacuous).
- Object-level authorization verified: alice→own task ALLOWED, alice→bob's
  task DENIED, ownerless task DENIED (fail-closed), demo principal denied
  on others' tasks, missing task 404.
- Worker identity boundary: `resume(task_id)` accepts only the task id;
  the demo header is not a durable background identity.
- Schema correctness fix: `TASK_STATUS_OUTPUT` allows null
  current_step/timestamps (fresh PENDING tasks).
- End-to-end demo (`phase11b_e2e_demo.py`): one manifest linking real
  candidate execution → verifier INVALID → gate REJECTED → promotion
  BLOCKED (active version stays baseline) → OTel spans recorded.

## Acceptance gate (Task 10)

| # | criterion | status |
|---|---|---|
| 1 | unsafe candidate regression from REAL execution | ✅ (mutation harness) |
| 2 | verifier does not trust candidate pre-filled results | ✅ |
| 3 | hard-budget regression independently identified | ✅ |
| 4 | expired-quote regression independently identified | ✅ |
| 5 | missing evidence → default reject | ✅ (gate_summary 11A) |
| 6 | evaluator exception → default reject | ✅ (ERROR status) |
| 7 | candidate cannot modify gate / hidden ground truth | ✅ (isolated worlds) |
| 8 | non-QUALIFIED cannot promote | ✅ (CAS) |
| 9 | concurrent promotion → only legal transitions | ✅ (subprocess races) |
| 10 | active version + audit atomic | ✅ (BEGIN IMMEDIATE) |
| 11 | crash → final version determinable | ✅ (rollback tests) |
| 12 | rollback preserves task version bindings | ✅ |
| 13 | MCP cannot cross-read task data | ✅ (owner checks) |
| 14 | worker resume does not escalate identity | ✅ |
| 15 | rejection reproducible via trace/manifest/output | ✅ (E2E manifest) |
| 16 | Phase 1-11A safety contracts not regressed | ✅ (full suite) |
| 17 | demo auth not claimed as production IAM | ✅ (labeled) |
| 18 | mutation experiment not a production safety proof | ✅ (labeled) |

## Artifacts (docs/eval/)

candidate_mutation_manifest.json · candidate_execution_results.json ·
independent_constraint_results.json · release_gate_decisions.json ·
promotion_race_results.json · rollback_consistency_results.json ·
authorization_test_results.json · phase11b_execution_manifest.json

## Remaining risks (honest)

- SINGLE_TENANT_DEMO_ONLY: no OIDC; tenant/role are labels.
- SQLite serialization is single-node; a distributed CAS is out of scope.
- REAL_LLM_NOT_RUN persists (Phase 11C decides based on credentials).
- Controlled mutation ≠ production safety statistics.
