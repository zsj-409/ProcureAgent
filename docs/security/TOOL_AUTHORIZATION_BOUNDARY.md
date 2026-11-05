# Phase 11B — Tool Authorization Boundary (Commit C)

- **Date**: 2026-11-05

## Identity model

- `SINGLE_TENANT_DEMO_ONLY` — no production OIDC/multi-tenant IAM. The
  demo header (`X-Demo-Actor`) resolves a DEMO principal in an explicitly
  isolated local config only; it is NOT a durable credential.
- Real (non-demo) principals carry scopes in `metadata.scopes`
  (e.g. `task:read`, `procurement:read`); the gateway's `_has_scope`
  checks them per tool contract.

## Object-level authorization (Phase 11B fix)

- `TaskRecord.owner_id` — each task stores its REAL owner principal.
- `repository.get_owner(task_id)` returns the owner; the MCP gateway's
  `task_owner` callback is wired to it (previously hard-coded
  `demo-approver` for every existing task — that made ownership checks
  vacuous).
- The gateway denies `TASK_FORBIDDEN` when `owner != principal.id`
  (the old `demo`-owner special case is removed; demo principals obey the
  same object-level rule).
- A task with NO owner (legacy/NULL) is denied — fail-closed.
- A nonexistent task id yields `TASK_NOT_FOUND` (404) — no existence
  leakage.

## Worker identity boundary

- `orchestrator.resume(task_id)` takes ONLY the task id — no request-body
  role, no header identity can be injected into the worker.
- The demo header is never a long-lived background identity; the durable
  worker runs as the orchestrator and all tool access still goes through
  the gateway's ownership check.
- Permission revocation handling: an owner whose task was reassigned is
  denied on the next call (ownership is re-checked per invocation from
  the DB, not cached).

## Verified (authorization_test_results.json)

| scenario | decision |
|---|---|
| alice reads her own task | ALLOWED |
| alice reads bob's task | DENIED (TASK_FORBIDDEN) |
| bob reads his own task | ALLOWED |
| alice reads an ownerless task | DENIED (fail-closed) |
| demo principal reads alice's task | DENIED (TASK_FORBIDDEN) |
| anyone reads a missing task | DENIED (TASK_NOT_FOUND, 404) |

## Honest boundary

- This is object-level authorization within a single tenant — it is NOT
  a production multi-tenant isolation system. Tenant/role are labels; the
  enforcement is ownership-based.
- No insecure production token algorithm was invented; no OIDC is
  claimed.
