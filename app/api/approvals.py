"""Human approval endpoint."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from ..agent.state import TaskState, TaskStatus

router = APIRouter(tags=["approvals"])


@router.post("/{task_id}/approve", response_model=TaskState)
async def approve_task(task_id: str, request: Request) -> TaskState:
    """Approve a task that is waiting for human review."""

    repository = request.app.state.repository
    record = repository.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")

    state = repository.to_state(record)
    if state.status != TaskStatus.WAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"Task cannot be approved from status {state.status.value}",
        )

    repository.create_approval(task_id, approver="human", decision="approved")
    state.status = TaskStatus.APPROVED
    state.updated_at = datetime.now(timezone.utc)
    repository.save_state(state)

    state.status = TaskStatus.COMPLETED
    state.updated_at = datetime.now(timezone.utc)
    repository.save_state(state)
    return state
