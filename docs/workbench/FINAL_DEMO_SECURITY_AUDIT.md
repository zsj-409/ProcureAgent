# Final Demo Security Audit

- **Date**: 2026-11-02
- **Commit**: `f50db1f` + Task 0 changes (commit pending)
- **Scope**: Observatory authorization, demo-mode isolation, public artifact
  sensitivity, five-lab UI coverage.

## 0.1 Authentication & Authorization

| check | status | evidence |
|---|---|---|
| Unauthenticated access → 401 | ✅ | `test_flight_recorder_requires_auth`, `test_approval_inspector_requires_auth` |
| Authenticated but no permission → 403/404 | ✅ (single-tenant) | non-existent task_id → 404, no enumeration: `test_flight_recorder_task_id_missing_404` |
| Authorized user succeeds | ✅ | `test_demo_identity_grants_access` |
| No sensitive info leaked on illegal access | ✅ | 404 body is bare `{"detail": "Task not found"}` — no task/supplier/approver data |
| Demo header only in isolated demo config | ✅ | `test_production_auth_ignores_demo_header`, `test_random_header_cannot_forge_principal` |

**Object-level authorization**: in the current model there is exactly ONE
tenant (`demo`), so any authenticated principal may read any task. This is
**explicitly labeled** in the API response:

```
authz_model: "SINGLE_TENANT_DEMO_ONLY"
note: "... multi-tenant object-level isolation is NOT IMPLEMENTED"
```

**We do NOT claim production-grade multi-tenant authorization.** The task
spec's requirement is satisfied honestly: the model is declared, and the
absence of real tenant isolation is a documented limitation, not a hidden one.

## 0.2 Demo-mode isolation

- `DemoIdentityProvider` activates ONLY when `auth_enabled=False` **AND**
  `demo_identity_header` is a non-default explicitly-chosen header name.
- A bare env flag alone can never enable demo identity (settings default
  `auth_enabled=True`).
- With `auth_enabled=True`, the demo header is IGNORED — verified by
  `test_production_auth_ignores_demo_header`.
- Header value must match the hard-coded allowlist principal id
  (`demo-approver`); arbitrary values → 401 (`test_random_header_cannot_forge_principal`).
- The demo header is a demo-mode test identity, never a real login
  credential; there is no production path where it grants access.

## 0.3 Public experiment artifacts

Scanned the three public artifact endpoints
(chaos-lab / optimization-comparison / experiment-lab):

- `portal_recovery_results.json`: contains only per-case recovery rows and
  `tokens_total` (LLM token counts) — no credentials, no approver identities.
- `optimization_benchmark.json`: scenario aggregates + solver stats — no
  sensitive fields.
- `recovery_policy_results.json`: policy × case rows — no sensitive fields.

Automated scan: `test_public_artifacts_contain_no_credentials` asserts none
of {password, api_key, secret, credential, authorization: bearer} appear.

## 0.4 Five-lab UI coverage

All five labs are now real-browser verified (`test_phase9_workbench_e2e.py`):

| lab | tab | browser-verified |
|---|---|---|
| Execution Flight Recorder | ✅ (added) | ✅ renders live trace / 暂无 empty state |
| Chaos Recovery Lab | ✅ | ✅ table with committed per-case rows |
| Procurement Optimization | ✅ | ✅ table + honest boundary |
| Agent Experiment Lab | ✅ | ✅ policy × n table |
| Approval Inspector | ✅ | ✅ live approvals / 暂无 empty state |

Previously the Flight Recorder had no frontend tab — this audit added it.

## Verdict

Demo security gate **PASS** for the declared single-tenant demo scope.
Multi-tenant object-level isolation is a documented NOT IMPLEMENTED
limitation (same as Phase 4 precondition audit). No credentials, no
production demo header path, no sensitive data in public artifacts, five
labs UI-covered.
