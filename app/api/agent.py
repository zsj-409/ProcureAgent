"""Natural-language agent endpoint.

``POST /agent/run`` interprets the message and starts the task in the
background so browsers and slow suppliers never block the HTTP call; the UI
polls ``GET /tasks/{task_id}`` for state transitions. Pass ``?wait=true`` to
get the original synchronous behavior (used by tests and CLI callers).
"""

import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..procurement.schemas import (
    AgentExecutionResult,
    ProcurementPlan,
    ProcurementRequest,
)

logger = logging.getLogger("procureagent.api.agent")

router = APIRouter(tags=["agent"])


class AgentRunRequest(BaseModel):
    """A natural-language procurement request."""

    message: str = Field(min_length=1)


class AgentRunAccepted(BaseModel):
    """Immediate response for a background agent run."""

    task_id: str
    status: str = "PENDING"
    interpreted: ProcurementRequest
    poll_url: str


@router.post("/run")
async def run_agent(
    payload: AgentRunRequest,
    request: Request,
    wait: bool = Query(
        default=True,
        description="Run synchronously and return the full result (default). "
        "Pass wait=false to start a background task and poll /tasks/{id}.",
    ),
) -> AgentRunAccepted | AgentExecutionResult:
    """Interpret a message and start (or synchronously run) a procurement task."""

    repository = request.app.state.repository
    orchestrator = request.app.state.orchestrator
    interpreter = request.app.state.interpreter

    try:
        procurement_request = await interpreter.interpret(payload.message)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    task_id = uuid4().hex
    repository.create(task_id, procurement_request)

    if not wait:
        request.app.state.background.submit(task_id, procurement_request)
        return AgentRunAccepted(
            task_id=task_id,
            interpreted=procurement_request,
            poll_url=f"/api/v1/tasks/{task_id}",
        )

    state = await orchestrator.run(task_id, procurement_request)
    plan = repository.get_plan(task_id) or ProcurementPlan()
    recommendation = repository.get_recommendation(task_id)
    quotes = recommendation.quotes if recommendation else []
    return AgentExecutionResult(
        task_id=task_id,
        status=state.status.value,
        request=procurement_request,
        plan=plan,
        quotes=quotes,
        recommendation=recommendation,
        approval_required=recommendation.approval_required if recommendation else False,
        partial_result=state.partial_result,
        summary=recommendation.summary if recommendation else state.error or "",
    )
