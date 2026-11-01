"""Background task execution for the agent API.

Background procurement tasks run on a dedicated worker thread with their own
event loop. This is deliberately not ``asyncio.create_task`` on the request's
loop: background work must survive the response that started it, regardless
of whether the server is uvicorn or a TestClient portal.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from .state import TaskStatus

logger = logging.getLogger("procureagent.background")


class BackgroundTaskRunner:
    """Runs orchestrator tasks off-loop with bounded parallelism."""

    def __init__(self, orchestrator, repository, max_workers: int = 2):
        self._orchestrator = orchestrator
        self._repository = repository
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="procureagent-task"
        )

    def bind(self, orchestrator) -> None:
        """Attach the orchestrator after component assembly."""

        self._orchestrator = orchestrator

    def submit(self, task_id: str, request) -> None:
        """Schedule one procurement task execution."""

        self._executor.submit(self._run, task_id, request)

    def _run(self, task_id: str, request) -> None:
        try:
            asyncio.run(self._orchestrator.run(task_id, request))
        except Exception as exc:
            logger.exception("Background task %s crashed", task_id)
            record = self._repository.get(task_id)
            if record is not None and record.status not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.WAITING_APPROVAL.value,
                TaskStatus.REJECTED.value,
            }:
                record.status = TaskStatus.FAILED.value
                record.error = f"Unhandled orchestrator error: {exc}"
                record.updated_at = datetime.now(UTC)
                self._repository.save_state_record(record)

    def shutdown(self) -> None:
        """Stop accepting work and wait briefly for running tasks."""

        self._executor.shutdown(wait=False, cancel_futures=True)
