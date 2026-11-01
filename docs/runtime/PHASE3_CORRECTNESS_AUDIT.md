# Phase 3 Correctness Audit

- **Date**: 2026-11-01
- **Baseline HEAD**: `6dd0210` (Phase 2 complete, 125 passed)
- **Scope**: Phase 2 claims re-audit — atomic claim SQL scope, external-effect
  semantics, UNKNOWN_OUTCOME wiring, crash-window classification.

## 1. Claim SQL scope audit (Task 1)

The claim statement (verified against the live code):

```sql
UPDATE logical_steps
SET status='CLAIMED', owner_id=:w, lease_epoch=lease_epoch+1,
    lease_expires_at=:expiry, attempt_count=attempt_count+1, updated_at=:now
WHERE id=:id AND (status IN ('PENDING','RETRY_SCHEDULED')
   OR (status='CLAIMED' AND lease_expires_at < :now))
RETURNING id, lease_epoch, attempt_count
```

**AND/OR precedence**: the `OR` branch is parenthesized and sits under
`id=:id AND (...)`. The expired-lease branch is therefore **scoped to the
same logical step id** — a claim for step A cannot accidentally claim step B
even if B's lease is expired. ✅ No scope hole at the SQL level.

**Remaining gaps (call-layer, not SQL)**:
1. `claim()` takes only `logical_step_id` — it does not verify the caller's
   `task_id` / `plan_hash` / `action_input_hash` expectations. The durable
   worker passes `logical_id` it just created from the CURRENT plan hash, so
   cross-plan reuse is prevented at the create step (new plan → new
   (task, plan_hash, step_key) row). But a caller with a stale `logical_id`
   from a DIFFERENT task could claim a wrong step — the API trusts the id.
   **Fix (Phase 3)**: claim should accept an expected `task_id` (+ optionally
   plan_hash/action_input_hash) and include them in the WHERE.
2. `find_reclaimable_tasks` returns task ids; the worker then re-creates
   logical steps by plan hash. A task whose plan changed since the last crash
   would create new step rows — old SUCCEEDED rows are untouched (correct),
   but a partially-executed OLD plan's steps are abandoned (no cleanup).

## 2. External-effect semantics audit (the Phase 3 core issue)

**Phase 2 crash-window classification** (from `CRASH_RECOVERY_REPORT.md`):

```
worker claims → external effect recorded (ledger) → os._exit → new worker
reclaims after lease expiry → re-executes → ledger now has 2 entries
```

The Phase 2 report labeled this "correct recovery, not duplicate effect".
**That conclusion lacks an external-ACTION-TYPE proof.** For a read-only quote
GET, 2 invocations are acceptable (both are queries, no business mutation).
For a NON-IDEMPOTENT mutation, re-executing after lease expiry would create a
**second business side effect** — unacceptable.

**Current state**:
- All executors (ApiExecutor/PortalExecutor) are READ-ONLY quote fetchers —
  they never mutate. So Phase 2's re-execution was safe **by accident of the
  mock's read-only nature**, not by enforcement.
- `UNKNOWN_OUTCOME` exists as a state but is **not wired into the worker's
  recovery path**: the worker's `_execute_claimed` on exception calls
  `fail()` (→ RETRY_SCHEDULED / FAILED_TERMINAL), never `mark_unknown()`. A
  crash after dispatch with uncertain external outcome would, on recovery,
  simply re-run the step (RETRY path) — correct only for read-only tools.
- `ExternalEffectLedger` is a **read-modify-write JSON file** — cross-process
  lost-update risk (two workers appending concurrently can overwrite each
  other). Phase 2 tests used per-scenario ledgers and serialized subprocesses,
  so it was not exposed.

## 3. P0 list for Phase 3

| # | Problem | Severity |
|---|---|---|
| P3-1 | No tool-capability contract: executors don't declare READ_ONLY / IDEMPOTENT / NON_IDEMPOTENT; recovery treats everything as retryable | P0 |
| P3-2 | UNKNOWN_OUTCOME not wired into worker recovery; crash-after-dispatch of a non-idempotent tool would re-execute | P0 |
| P3-3 | ExternalEffectLedger is a non-transactional JSON read-modify-write; cross-process lost updates possible | P0 |
| P3-4 | Claim API trusts `logical_step_id` without verifying task/plan identity | P1 (call-layer scope) |
| P3-5 | Durable worker drives single steps only; task-level COMPLETED/finalization still owned by orchestrator; full-task crash recovery untested | P1 |
| P3-6 | Approval version binding exists (Phase 1) but no concurrent approve/reject CAS; no quote-expiry check at approval time | P1 |

## 4. What Phase 2 got right (no rework needed)

- Atomic claim (single UPDATE + RETURNING, transaction-bounded). ✅
- Fencing: stale epoch cannot complete (unit + subprocess evidence). ✅
- UNKNOWN_OUTCOME as a distinct, non-claimable state. ✅ (state exists;
  recovery wiring is the gap)
- Idempotent step creation (ON CONFLICT DO NOTHING). ✅
- Cross-process 2/4/8-worker single-winner races. ✅
- Independent ledger as ground truth for "external executed, local not
  committed". ✅ (but storage needs hardening)

## 5. Phase 2 report corrections (erratum, not rewrite)

`CRASH_RECOVERY_REPORT.md` §2 stated "2 effects total is the CORRECT count
for crash+recovery, NOT a duplicate business effect". **This is only correct
for READ_ONLY tools.** The Phase 3 reports add an erratum: the statement is
action-type-dependent; the ledger's two entries for a read-only quote are
2 invocations / 0 committed side effects, which is fine; for a mutation they
would be 2 committed side effects (a bug). Phase 2's original text is
preserved; the correction is recorded here and in the Phase 3 docs.
