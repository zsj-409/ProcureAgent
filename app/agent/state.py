"""Task state and allowed status transitions."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """The only statuses a procurement task may use."""

    PENDING = "PENDING"
    PLANNING = "PLANNING"
    COLLECTING = "COLLECTING"
    SCORING = "SCORING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskState(BaseModel):
    """Current checkpoint of a procurement task."""

    task_id: str
    status: TaskStatus
    current_step: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    error: str | None = None
    partial_result: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
