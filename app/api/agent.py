"""Natural-language agent endpoint."""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..procurement.schemas import AgentExecutionResult, ProcurementPlan, ProcurementRequest

router = APIRouter(tags=["agent"])


class AgentRunRequest(BaseModel):
    """A natural-language procurement request."""

    message: str = Field(min_length=1)


@router.post("/run", response_model=AgentExecutionResult)
async def run_agent(payload: AgentRunRequest, request: Request) -> AgentExecutionResult:
    """Interpret a message, run the task, and return the full agent result."""

    repository = request.app.state.repository
    orchestrator = request.app.state.orchestrator
    interpreter = request.app.state.interpreter

    try:
        procurement_request = await interpreter.interpret(payload.message)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clear API error
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    task_id = uuid4().hex
    repository.create(task_id, procurement_request)
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
