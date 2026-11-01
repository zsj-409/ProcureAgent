# External Effect Capability Contract (Phase 3)

- **Version**: `external-effect-capability-v1` (2026-11-01)
- **Module**: `app/executors/base.py` (`ExternalCapability`, `RetryPolicy`)

## 1. Capability taxonomy

| capability | external side effect | idempotency key | queryable outcome | retry allowed |
|---|---|---|---|---|
| `READ_ONLY_RETRYABLE` | none | n/a | n/a | yes (bounded) |
| `REMOTE_IDEMPOTENT` | yes | yes | optional | yes, SAME key only |
| `RECONCILABLE_MUTATION` | yes | optional | yes (request id) | prefer reconcile |
| `NON_IDEMPOTENT_MUTATION` | yes | no | no | **NO** (UNKNOWN_OUTCOME) |
| `UNKNOWN_CAPABILITY` | unknown | unknown | unknown | **NO** (conservative) |

## 2. Declared policy per tool

Every executor declares `capability` + `retry_policy`:

| executor | capability | max_retries | retry_safe |
|---|---|---|---|
| `ApiExecutor` (quote GET) | READ_ONLY_RETRYABLE | 2 | true |
| `PortalExecutor` (quote extract) | READ_ONLY_RETRYABLE | 2 | true |
| base default | UNKNOWN_CAPABILITY | 2 | false |

**Rule**: an unknown tool's retry is NOT safe by default. Never infer safety
from the HTTP method alone (a POST can be non-idempotent; a GET is read-only
only when the implementation is read-only).

## 3. Metrics separation

- **Duplicate Invocation Count** = external_invocations rows per (task, step).
- **Duplicate Committed Side Effect Count** = external_committed_effects rows
  per business intent (idempotency key dedup).
These two are NEVER mixed. A read-only quote retried 3x has invocation=3,
committed=0.

## 4. Ledger (Phase 3, SQLite)

`mock_supplier_effects.db` (separate from the business DB):
- `external_invocations`: every attempt.
- `external_committed_effects`: committed business effects; `UNIQUE(idempotency_key)`.

Same key + same payload → returns the already-committed effect (no new row).
Same key + different payload → `ValueError` (contract violation).
Writes are transactional (per-statement autocommit), WAL + busy_timeout for
cross-process safety.
