# Approval Integrity V3 (Phase 3)

- **Module**: `app/api/approvals.py`, `app/api/workbench.py` (reject),
  `app/infrastructure/database.py` (`cas_transition`)
- **Phase 1 base**: ApprovalGuard (default-deny, body-approver never trusted,
  recommendation_version binding)

## 1. Roles & tenant

Phase 1 roles: Requester / Procurement Operator / Approver / Auditor.
`ApprovalGuard` requires `Role.APPROVER` for approve/reject. Demo identity is
a controlled allowlisted header (`demo-approver`) — explicitly NOT production
auth. Tenant boundary: `Principal.tenant` exists; cross-tenant enforcement is
Phase 3+ (the demo has a single tenant).

## 2. Approval preconditions (checked in order)

1. Caller authenticated + approver role (401/403).
2. Task exists (404).
3. **Quote validity**: any quote with `quote_valid_until < now` → 409
   `QUOTE_EXPIRED` (the premise the approver saw is stale).
4. Recommendation version computed (content hash) → bound to the approval row.

## 3. CAS transition (concurrent approve/reject)

```sql
UPDATE tasks SET status=:to, updated_at=:now WHERE id=:id AND status=:from
```

- approve: WAITING_APPROVAL → COMPLETED
- reject: WAITING_APPROVAL → REJECTED
- rowcount==1 → winner; rowcount==0 → 409 `APPROVAL_RACE_LOST`.
- Concurrent approve + reject: exactly ONE legal terminal state; the loser
  gets 409; exactly one approval row is written.

## 4. Verified tests

- Concurrent approve/reject → 1×200 + 1×409, final status COMPLETED or
  REJECTED, exactly 1 approval row. ✅
- Expired quote → 409 QUOTE_EXPIRED, state unchanged, no approval row. ✅
- Version binding → approval row carries recommendation_version. ✅
- Unauthenticated approve → 401 AUTH_REQUIRED (Phase 1 regression). ✅
- Non-approver demo header → 401. ✅
- Reject after COMPLETED → 409 APPROVAL_RACE_LOST. ✅

## 5. Limits

- No real OIDC/token auth; demo identity is a controlled header, never
  claimed as production-grade authentication.
- Cross-tenant enforcement beyond the single demo tenant is not exercised.
- Hard-budget re-check at approval time uses the stored recommendation
  (already policy-validated at scoring); a live re-query of suppliers is not
  performed (quote freshness gate covers the main drift).
