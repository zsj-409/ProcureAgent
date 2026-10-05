"""Deterministic execution layer between the orchestrator and executors."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import Enum

from pydantic import BaseModel

from ..infrastructure.database import TaskRepository


class StepExecutionStatus(str, Enum):
    """The only statuses a step attempt may use."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ExecutionBudget(BaseModel):
    """Hard limits that prevent unbounded tool loops and retries."""

    max_steps: int = 20
    max_attempts: int = 2
    max_task_seconds: float = 60.0
    max_replans: int = 1


class RuntimeResult(BaseModel):
    """The result of one runtime step execution."""

    success: bool
    result: dict | None = None
    error: str | None = None
    cached: bool = False


class TaskRuntime:
    """Owns retry, timeout, checkpoint, idempotency, and budget enforcement."""

    def __init__(self, repository: TaskRepository, budget: ExecutionBudget):
        self.repository = repository
        self.budget = budget

    async def execute(
        self,
        *,
        task_id: str,
        step_name: str,
        supplier_id: str | None,
        timeout_seconds: float,
        action: Callable[[], Awaitable[dict]],
    ) -> RuntimeResult:
        """Run one step idempotently with bounded retries.

        The idempotency key is ``(task_id, step_name, supplier_id)``. A prior
        COMPLETED attempt is reused instead of contacting the supplier again.
        """

        cached = self.repository.get_completed_step_result(task_id, step_name, supplier_id)
        if cached is not None:
            return RuntimeResult(success=True, result=cached, cached=True)

        last_error: str | None = None
        base_attempt = self.repository.max_step_attempt(task_id, step_name, supplier_id)
        for attempt in range(base_attempt + 1, base_attempt + self.budget.max_attempts + 1):
            execution_id = self.repository.create_step_execution(
                task_id=task_id,
                step_name=step_name,
                supplier_id=supplier_id,
                attempt=attempt,
            )
            try:
                result = await asyncio.wait_for(action(), timeout=timeout_seconds)
                self.repository.complete_step_execution(execution_id, result)
                return RuntimeResult(success=True, result=result)
            except Exception as exc:
                last_error = str(exc)
                self.repository.fail_step_execution(execution_id, last_error)
                if attempt < self.budget.max_attempts:
                    await asyncio.sleep(0.2 * attempt)

        return RuntimeResult(success=False, error=last_error)

    def mark_skipped(self, task_id: str, step_name: str, supplier_id: str | None) -> None:
        """Persist a skipped step without executing it."""

        self.repository.skip_step_execution(task_id, step_name, supplier_id)

    def time_budget_exceeded(self, started_at: float) -> bool:
        """Return whether the task-level time budget has been exhausted."""

        return (time.perf_counter() - started_at) >= self.budget.max_task_seconds

    def steps_exceeded(self, executed_steps: int) -> bool:
        """Return whether the step budget has been exhausted."""

        return executed_steps >= self.budget.max_steps
