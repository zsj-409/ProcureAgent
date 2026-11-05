# Phase 10 — Baseline Audit & Architecture Protection

- **Date**: 2026-11-02
- **HEAD**: `3722cdc`
- **Python**: 3.11.9 (venv `.venv`)
- **pytest baseline**: 311 collected, 0 failures (verified)
- **Ruff**: clean (verified)
- **CI**: none existing — Commit C will add a minimal offline pipeline

## 0.1 Tool Execution Paths (as-built)

```
HTTP API (app/api) → Orchestrator (app/agent/orchestrator.py)
   → ExecutorRouter (app/executors/router.py)
      → ApiExecutor    (supplier.source_type == "api")
      → PortalExecutor (supplier.source_type == "portal")
         → PortalAgentLoop → SafeActionExecutor (whitelist + pre/postconditions)
```

- **Single entry point**: `ExecutorRouter.route()` is the ONLY place an
  executor is chosen; there is no second bypass path in app code.
- The orchestrator drives steps via `DurableWorker` (app/agent/background.py)
  which claims steps atomically and re-routes through the same router.
- **MCP Gateway (Commit A) will sit in FRONT of ExecutorRouter** — it is a
  new public entry that must apply the same capability/side-effect safety
  as the internal path, but it never bypasses the router.

## 0.2 Identity Propagation (as-built)

- `ApprovalGuard.resolve_principal(request)` (app/security/approval_guard.py):
  - `auth_enabled=True` → always `None` (default deny — no identity layer).
  - `auth_enabled=False` + `demo_identity_header` set → `DemoIdentityProvider`
    resolves ONLY the hard-coded `demo-approver` principal (allowlist).
- Identity enters: HTTP API (header) → approval writes. It does NOT
  propagate into Task/Executor/Worker today — those run with no caller
  identity (single-tenant demo).
- **No real Tenant ID / object-level authorization.** Explicitly
  SINGLE_TENANT_DEMO_ONLY (observatory responses already carry this label).
- Phase 10 will NOT build a production identity system; it adds a
  tool-level authorization layer with the same demo identity boundary.

## 0.3 Evaluation Baseline (frozen)

Historical `docs/eval/` artifacts — 30 files, SHA-256 recorded in
`docs/enterprise/PHASE10_BASELINE_AUDIT.md` appendix. These MUST remain
byte-identical after Phase 10 (Task 27 gate #23).

Key frozen results (kept with original interpretation):
- Phase 4: State-Aware 7/7 = Current Ladder 7/7; P50 484ms vs 750ms (-35%).
- Phase 6: Optimizer 18/28 FULLY, 0 violation; Cluster Bootstrap CI
  [0.0, 6.4] — cost saving NOT significant.
- Phase 7: 3 policies equal success (30/40 rec + 10/40 safe_stop); cost
  advantage is counted observations (40→20/0), NOT money.

## Appendix — SHA-256 (16-char prefix) of all docs/eval files

| file | sha256 prefix |
|---|---|
| action_safety_report.json | 7b8dfb0085f71e2b |
| allocation_results.csv | 3819587cc8701689 |
| concurrent_claim_results.json | 0d5085473c1405e1 |
| constraint_violations.json | ca799479133f6985 |
| cost_ledger.json | f3dbc5aba5efc9f6 |
| crash_recovery_results.json | 57394b5351968d1c |
| execution_manifest.json | d38d3460331769e7 |
| experiment_manifest.json | 48c05ea45c9bf434 |
| external_effect_ledger.json | 3f08985c77412829 |
| failure_cases.json | 5b26c49cfa1e5651 |
| fault_injection_results.json | 620915264eac1932 |
| optimization_benchmark.json | c22f21a50b000688 |
| optimization_comparison.csv | 25590b1bf31c3151 |
| paired_bootstrap.json | 8e0d5621e89e0cd8 |
| paired_cluster_bootstrap.json | 6e557684269f1461 |
| paired_method_comparison.csv | d47b66878d771b6d |
| phase8_manifest_real_llm_not_run.json | d06418e55500e7de |
| phase8_portal_manifest_real_llm_not_run.json | 34888ac9b81d0a81 |
| portal_experiment_manifest.json | b5a14520c17acc37 |
| portal_failure_cases.json | 182844afc7ae0aef |
| portal_recovery_results.json | 7dc314c999685c2c |
| portal_world_registry.json | 2c0216b8118f6c63 |
| procurement_world_manifest.json | ad9ebd9232ccad25 |
| procurement_world_registry.json | 31e2c7add5f1a79e |
| quote_extraction_accuracy.json | cf81db2d6337681c |
| recovery_cost_ledger.json | 45b7ab9760294bd6 |
| recovery_policy_pilot.json | 3cf7485d73d7183a |
| recovery_policy_results.json | cbc0d7c8c7b2ce1c |
| solver_performance.json | 886169460d0d6b76 |
| state_consistency_report.json | 5c7ebff740c9e40e |
| strategy_comparison.csv | 625278393060e14c |
