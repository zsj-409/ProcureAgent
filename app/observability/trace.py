"""Persistent, structured trace recorder."""

import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("procureagent.trace")


class TraceEntry(BaseModel):
    """One step-level execution record."""

    task_id: str
    step: str
    component: str
    action: str
    status: Literal["SUCCESS", "ERROR"]
    duration_ms: int
    attempt: int = 1
    error: str | None = None
    model: str | None = None
    token_input: int | None = None
    token_output: int | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TraceRecorder:
    """Trace store that writes logs and persists rows through the repository."""

    def __init__(self, repository=None) -> None:
        self._repository = repository
        self._entries: list[TraceEntry] = []

    def record(
        self,
        *,
        task_id: str,
        step: str,
        component: str,
        action: str,
        status: Literal["SUCCESS", "ERROR"],
        duration_ms: int,
        attempt: int = 1,
        error: str | None = None,
        model: str | None = None,
        token_input: int | None = None,
        token_output: int | None = None,
    ) -> TraceEntry:
        """Record and log one trace entry."""

        entry = TraceEntry(
            task_id=task_id,
            step=step,
            component=component,
            action=action,
            status=status,
            duration_ms=duration_ms,
            attempt=attempt,
            error=error,
            model=model,
            token_input=token_input,
            token_output=token_output,
        )
        self._entries.append(entry)
        logger.info(
            "trace task=%s step=%s component=%s action=%s status=%s duration_ms=%s error=%s",
            task_id,
            step,
            component,
            action,
            status,
            duration_ms,
            error,
        )
        if self._repository is not None:
            self._repository.record_trace(
                {
                    "task_id": task_id,
                    "step_name": step,
                    "component": component,
                    "action": action,
                    "status": status,
                    "duration_ms": duration_ms,
                    "attempt": attempt,
                    "error": error,
                    "model": model,
                    "token_input": token_input,
                    "token_output": token_output,
                }
            )
        return entry

    def entries_for(self, task_id: str) -> list[TraceEntry]:
        """Return all recorded entries for one task."""

        return [entry for entry in self._entries if entry.task_id == task_id]
