# Phase 4 Precondition Audit

- **Date**: 2026-11-01
- **Baseline HEAD**: `6d9eca8` (Phase 3 complete, 144 passed)
- **Scope**: Phase 3 correctness re-audit + Portal development admission gate

## 0.1 Approval transaction atomicity — **P0 found**

**Current state**: `approve_task` performs the CAS status transition
(`cas_transition`: WAITING_APPROVAL → COMPLETED) in one SQLite transaction,
then `create_approval` (audit row) in a SECOND transaction.

**Failure window**: if the process crashes between the two commits, the task
is COMPLETED but no approval audit row exists — business state and audit
state are irrecoverably inconsistent (a completed purchase without an
approval record).

**Also**: two concurrent approve calls with the same recommendation version
can both pass the quote check, but only one CAS wins (good); however a
recommendation update racing the approval is not serialized (the version
hash is computed before the CAS).

**Fix (Commit A)**: make the CAS + approval-row insert ONE transaction —
`create_approval_cas` that does `UPDATE ... WHERE status='WAITING_APPROVAL'`
AND `INSERT INTO approvals` in the same session, committed together.
Returns (changed, approval_id). Reject path likewise.

**Test**: crash between the two writes is impossible by construction (single
transaction); concurrent approve/reject still single-winner; version binding
preserved.

## 0.2 LogicalStep identity binding — **P1**

`claim()` takes only `logical_step_id`; it does not verify the caller's
`task_id` / `plan_hash` / `action_input_hash`. A caller holding another
task's step id could claim it. The durable worker always passes the id it
just created (safe in practice), but the API trusts the id.

**Fix (Commit A)**: `claim(..., task_id=..., plan_hash=..., action_input_hash=...)`
optional expected values; when provided, add them to the WHERE.

## 0.3 Tenant / session boundary — **NOT IMPLEMENTED (honest)**

No real multi-tenant model exists: `Principal.tenant` defaults to
`"default"`; demo identity is a single allowlisted header. Cross-tenant
task/approval/browser-session isolation is **NOT IMPLEMENTED** — this is
declared, not faked. Browser session isolation per task (Phase 4 Task 5) is a
separate concern and IS implemented in Phase 4 (per-task BrowserContext).

## 0.4 Crash termination

Phase 2/3 crash tests use `os._exit(0)`. The task asks for non-zero exit /
OS-signal termination. Phase 4 adds a `os._exit(137)` (SIGKILL-style) variant
and a `sys.kill`-style signal test to the portal recovery suite.

## Portal admission gate

- Portal baseline tests (real Playwright + real local HTTP portals) pass.
- Approval atomicity P0 fixed before portal work proceeds.
- No P0 state-pollution / privilege-escalation / duplicate-side-effect /
  approval-inconsistency issue remains open.

## Findings summary

| # | finding | severity | disposition |
|---|---|---|---|
| P4-0.1 | approval CAS + audit not atomic | P0 | fixed in Commit A |
| P4-0.2 | claim trusts step id without task/plan binding | P1 | hardened in Commit A |
| P4-0.3 | no real multi-tenant model | — | declared NOT IMPLEMENTED |
| P4-0.4 | crash tests use exit(0); add signal/non-zero | P2 | added in Phase 4 |
