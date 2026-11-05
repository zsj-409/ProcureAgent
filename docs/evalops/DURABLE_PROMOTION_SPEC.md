# Phase 11B — Durable Promotion & Rollback Spec (Commit B)

- **Date**: 2026-11-05
- **Problem**: the Phase 10 `PromotionManager` was a process-local dict —
  no persistence, no CAS, no active version. A crash or a second worker
  could lose/overwrite the state silently.
- **Fix**: `app/evalops/promotion.py` — SQLite-backed
  `DurablePromotionManager`.

## Data model

`promotion_state` (one row per candidate):
`candidate_id, candidate_version, previous_version, active_version,
evaluation_manifest_hash, gate_result_hash, promotion_status, promoted_by,
promoted_at, rollback_reason, version_epoch`.

`promotion_audit` (append-only): one row per transition (PROMOTED /
ROLLED_BACK), written in the SAME transaction as the state change.

The DB file is the ONLY source of truth — no process-local dict is
authoritative.

## Atomic promotion (CAS)

1. `BEGIN IMMEDIATE` (serializes concurrent writers on SQLite).
2. Read the candidate row INSIDE the transaction (the authoritative
   serialization point).
3. Require `promotion_status == QUALIFIED` — else `NOT_QUALIFIED` error
   (never trusts a caller verdict string; `apply_gate_results` recomputes
   from real gate results, INSUFFICIENT_EVIDENCE → REJECTED).
4. Update `active_version` + bump `version_epoch`.
5. Insert the audit row.
6. `COMMIT` — active version and audit are atomic. On ANY exception the
   transaction `ROLLBACK`s (no half-promoted state).

## Crash semantics

- Crash **before COMMIT** → SQLite rolls the transaction back: active
  version unchanged, epoch unchanged, no audit row (verified by a real
  `os._exit` subprocess).
- Crash **after COMMIT** → a fresh connection sees active version + audit
  both present (consistent).
- Audit write failure → whole transaction rolls back (verified by dropping
  the audit table).

## Concurrency

- 2 or 4 real subprocess workers promoting the same candidate → exactly one
  PROMOTED, the losers get an explicit error, audit has ONE PROMOTED row.
- Two DIFFERENT candidates racing → the last COMMIT wins; exactly one
  active version; both audits present.
- Duplicate promotion of an already-PROMOTED candidate fails and does not
  advance the epoch or add history.

## Rollback

- CAS on the ACTIVE version: rolling back a candidate that is not active →
  `NOT_ACTIVE` / `ACTIVE_VERSION_MISMATCH` error.
- Retires the active pointer (`active_version = NULL`) + audit `ROLLED_BACK`
  with reason — it NEVER rewrites historical task/plan/checkpoint bindings
  (running tasks keep their own version; Phase 2/3 semantics unchanged).
- A version that was rolled back cannot be silently re-promoted
  (NOT_QUALIFIED).

## Artifacts (docs/eval/)

- `promotion_race_results.json` (2-worker race + crash-before-commit)
- `rollback_consistency_results.json`

## Tests

`tests/test_phase11b_promotion.py` (17): atomicity, requires-qualified,
insufficient-evidence blocks, forged-verdict blocked, 2/4-worker races,
two-candidate race, old-epoch CAS, duplicate promotion, crash before/after
commit, audit-write rollback, rollback CAS + history, stale rollback,
task-binding preservation, rollback-vs-promotion race, cross-connection
persistence.

## Honest boundary

- SQLite BEGIN IMMEDIATE serializes writers; a distributed CAS across
  multiple DB nodes is out of scope (single-node runtime).
- `SINGLE_TENANT_DEMO_ONLY` — the identity used for `promoted_by` is the
  caller's claim; no production OIDC.
