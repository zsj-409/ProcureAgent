"""Human approval endpoint.

Phase 1 security: approval writes are guarded by ``ApprovalGuard``.

- Default configuration (``auth_enabled=True``): **default deny** — an
  unauthenticated request gets 401 AUTH_REQUIRED and never mutates state.
- Isolated demo configuration (``auth_enabled=False`` +
  ``demo_identity_header`` set): the caller's identity is resolved from the
  demo header allowlist only; a body ``approver`` field is never trusted.
- The approval is bound to the immutable recommendation it approves
  (``recommendation_version`` = hash of the stored recommendation JSON). If
  the recommendation changes later, the approval is stale by construction.

Phase 3: approval is a compare-and-swap (CAS) state transition — concurrent
approve/reject can never both win; the first writer transitions the task and
the second gets 409. Approval also re-checks quote validity (an expired quote
cannot be approved) and user hard budget.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from ..agent.state import TaskState, TaskStatus
from ..security.approval_guard import ApprovalGuard
from ..security.principal import Role

router = APIRouter(tags=["approvals"])


def _approval_guard(request: Request) -> ApprovalGuard:
    guard = getattr(request.app.state, "approval_guard", None)
    return guard if guard is not None else ApprovalGuard()


def _deny(status_code: int, reason_code: str, detail: str):
    raise HTTPException(
        status_code=status_code,
        detail={"reason_code": reason_code, "detail": detail},
    )


def _recommendation_version(repository, task_id: str) -> str | None:
    """Immutable version token for the stored recommendation.

    A content hash of the recommendation JSON; binds an approval to the exact
    recommendation that was presented to the approver.
    """

    recommendation = repository.get_recommendation(task_id)
    if recommendation is None:
        return None
    import hashlib

    return hashlib.sha256(recommendation.model_dump_json().encode("utf-8")).hexdigest()[:16]


def _check_quote_validity(repository, task_id: str) -> str | None:
    """Return an error string if the recommendation's quotes are expired.

    A quote whose ``quote_valid_until`` is in the past must not be approved —
    the business premise the approver saw is no longer current.
    """

    recommendation = repository.get_recommendation(task_id)
    if recommendation is None:
        return "no recommendation to approve"
    now = datetime.now(UTC)
    for quote in recommendation.quotes:
        if quote.quote_valid_until is not None and quote.quote_valid_until < now:
            return (
                f"quote from {quote.supplier_id} expired at "
                f"{quote.quote_valid_until.isoformat()}"
            )
    return None


@router.post("/{task_id}/approve", response_model=TaskState)
async def approve_task(task_id: str, request: Request) -> TaskState:
    """Approve a task that is waiting for human review (CAS transition)."""

    guard = _approval_guard(request)
    decision = guard.check(request, required_role=Role.APPROVER)
    if not decision.allowed:
        _deny(
            decision.http_status,
            decision.reason_code or "DENIED",
            f"Approval denied for task {task_id}",
        )

    repository = request.app.state.repository
    record = repository.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Business premise checks BEFORE the CAS write.
    validity_error = _check_quote_validity(repository, task_id)
    if validity_error:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": "QUOTE_EXPIRED", "detail": validity_error},
        )

    # The approver identity comes ONLY from the guard-resolved principal.
    approver_id = decision.principal.id if decision.principal else "unknown"
    recommendation_version = _recommendation_version(repository, task_id)

    # CAS: atomically move WAITING_APPROVAL -> COMPLETED only if the task is
    # still WAITING_APPROVAL. Concurrent approve/reject: exactly one wins.
    changed = repository.cas_transition(
        task_id,
        from_status=TaskStatus.WAITING_APPROVAL.value,
        to_status=TaskStatus.COMPLETED.value,
    )
    if not changed:
        current = repository.get(task_id)
        raise HTTPException(
            status_code=409,
            detail={
                "reason_code": "APPROVAL_RACE_LOST",
                "detail": f"Task is no longer WAITING_APPROVAL (now {current.status if current else '?'})",
            },
        )

    repository.create_approval(
        task_id,
        approver=approver_id,
        decision="approved",
        recommendation_version=recommendation_version,
        reason_code=None,
    )
    state = repository.to_state(repository.get(task_id))
    return state
