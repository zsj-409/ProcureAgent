"""SQLite persistence and repository layer."""

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from ..agent.state import TaskState, TaskStatus
from ..procurement.schemas import ProcurementPlan, ProcurementRecommendation, ProcurementRequest


class Base(DeclarativeBase):
    """Declarative base for SQLAlchemy models."""


class TaskRecord(Base):
    """A persisted procurement task and its current checkpoint state."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_steps: Mapped[str] = mapped_column(Text, default="[]")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    partial_result: Mapped[bool] = mapped_column(Boolean, default=False)
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRecord(Base):
    """A human approval decision attached to a task."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    approver: Mapped[str] = mapped_column(String(64), default="human")
    decision: Mapped[str] = mapped_column(String(16), default="approved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StepExecution(Base):
    """One attempt of one runtime step. Failed attempts are never overwritten."""

    __tablename__ = "step_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    step_name: Mapped[str] = mapped_column(String(64), index=True)
    supplier_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class TraceRecord(Base):
    """A persistent execution trace row."""

    __tablename__ = "trace_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    step_name: Mapped[str] = mapped_column(String(64), index=True)
    component: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def build_session_factory(database_url: str) -> sessionmaker:
    """Create an engine and session factory for the given database URL."""

    connect_args: dict[str, Any] = {}
    engine_kwargs: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in database_url:
            engine_kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskRepository:
    """Small persistence service that isolates SQLAlchemy from domain logic."""

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def create(self, task_id: str, request: ProcurementRequest) -> None:
        """Persist a new task in PENDING state."""

        now = _utcnow()
        with self._session_factory() as session:
            record = TaskRecord(
                id=task_id,
                status=TaskStatus.PENDING.value,
                current_step=None,
                completed_steps="[]",
                error=None,
                partial_result=False,
                request_json=request.model_dump_json(),
                plan_json=None,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.commit()

    def get(self, task_id: str) -> TaskRecord | None:
        """Return a task record or ``None``."""

        with self._session_factory() as session:
            return session.get(TaskRecord, task_id)

    def to_state(self, record: TaskRecord) -> TaskState:
        """Convert a persisted record into a domain ``TaskState``."""

        return TaskState(
            task_id=record.id,
            status=TaskStatus(record.status),
            current_step=record.current_step,
            completed_steps=json.loads(record.completed_steps or "[]"),
            error=record.error,
            partial_result=record.partial_result,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def save_plan(self, task_id: str, plan: ProcurementPlan) -> None:
        """Persist a validated procurement plan."""

        with self._session_factory() as session:
            record = session.get(TaskRecord, task_id)
            if record is None:
                raise ValueError(f"Task {task_id} does not exist")
            record.plan_json = plan.model_dump_json()
            record.updated_at = _utcnow()
            session.commit()

    def get_plan(self, task_id: str) -> ProcurementPlan | None:
        """Return a persisted plan or ``None``."""

        record = self.get(task_id)
        if record is None or not record.plan_json:
            return None
        return ProcurementPlan.model_validate_json(record.plan_json)

    def get_request(self, task_id: str) -> ProcurementRequest | None:
        """Return the original request for a task."""

        record = self.get(task_id)
        if record is None or not record.request_json:
            return None
        return ProcurementRequest.model_validate_json(record.request_json)

    def save_state(self, state: TaskState) -> None:
        """Persist the current checkpoint of a task."""

        with self._session_factory() as session:
            record = session.get(TaskRecord, state.task_id)
            if record is None:
                raise ValueError(f"Task {state.task_id} does not exist")
            record.status = state.status.value
            record.current_step = state.current_step
            record.completed_steps = json.dumps(state.completed_steps)
            record.error = state.error
            record.partial_result = state.partial_result
            record.updated_at = state.updated_at
            session.commit()

    def save_recommendation(self, task_id: str, recommendation: ProcurementRecommendation) -> None:
        """Store a completed recommendation against a task."""

        with self._session_factory() as session:
            record = session.get(TaskRecord, task_id)
            if record is None:
                raise ValueError(f"Task {task_id} does not exist")
            record.recommendation_json = recommendation.model_dump_json()
            record.partial_result = recommendation.partial_result
            record.updated_at = _utcnow()
            session.commit()

    def get_recommendation(self, task_id: str) -> ProcurementRecommendation | None:
        """Return a stored recommendation or ``None``."""

        record = self.get(task_id)
        if record is None or not record.recommendation_json:
            return None
        return ProcurementRecommendation.model_validate_json(record.recommendation_json)

    def create_approval(self, task_id: str, approver: str = "human", decision: str = "approved") -> None:
        """Persist a human approval record."""

        with self._session_factory() as session:
            session.add(
                ApprovalRecord(
                    task_id=task_id,
                    approver=approver,
                    decision=decision,
                    created_at=_utcnow(),
                )
            )
            session.commit()

    def create_step_execution(
        self,
        task_id: str,
        step_name: str,
        supplier_id: str | None,
        attempt: int,
    ) -> int:
        """Create a RUNNING step attempt and return its id."""

        with self._session_factory() as session:
            row = StepExecution(
                task_id=task_id,
                step_name=step_name,
                supplier_id=supplier_id,
                status="RUNNING",
                attempt=attempt,
                started_at=_utcnow(),
            )
            session.add(row)
            session.commit()
            return row.id

    def complete_step_execution(self, execution_id: int, result: dict) -> None:
        """Mark a step attempt as COMPLETED."""

        with self._session_factory() as session:
            row = session.get(StepExecution, execution_id)
            if row is None:
                return
            row.status = "COMPLETED"
            row.finished_at = _utcnow()
            row.result_json = json.dumps(result, default=str)
            session.commit()

    def fail_step_execution(self, execution_id: int, error: str) -> None:
        """Mark a step attempt as FAILED."""

        with self._session_factory() as session:
            row = session.get(StepExecution, execution_id)
            if row is None:
                return
            row.status = "FAILED"
            row.finished_at = _utcnow()
            row.error = error
            session.commit()

    def skip_step_execution(self, task_id: str, step_name: str, supplier_id: str | None) -> None:
        """Persist a SKIPPED step."""

        with self._session_factory() as session:
            session.add(
                StepExecution(
                    task_id=task_id,
                    step_name=step_name,
                    supplier_id=supplier_id,
                    status="SKIPPED",
                    attempt=1,
                    started_at=_utcnow(),
                    finished_at=_utcnow(),
                )
            )
            session.commit()

    def get_completed_step_result(self, task_id: str, step_name: str, supplier_id: str | None) -> dict | None:
        """Return a cached COMPLETED result for an idempotency key."""

        with self._session_factory() as session:
            row = (
                session.query(StepExecution)
                .filter(
                    StepExecution.task_id == task_id,
                    StepExecution.step_name == step_name,
                    StepExecution.supplier_id == supplier_id,
                    StepExecution.status == "COMPLETED",
                )
                .order_by(StepExecution.id.desc())
                .first()
            )
            if row is None or not row.result_json:
                return None
            return json.loads(row.result_json)

    def max_step_attempt(self, task_id: str, step_name: str, supplier_id: str | None) -> int:
        """Return the highest existing attempt number for an idempotency key."""

        with self._session_factory() as session:
            value = (
                session.query(StepExecution.attempt)
                .filter(
                    StepExecution.task_id == task_id,
                    StepExecution.step_name == step_name,
                    StepExecution.supplier_id == supplier_id,
                )
                .order_by(StepExecution.attempt.desc())
                .first()
            )
            return int(value[0]) if value else 0

    def list_step_executions(self, task_id: str) -> list[StepExecution]:
        """Return all step attempts for a task."""

        with self._session_factory() as session:
            return (
                session.query(StepExecution)
                .filter(StepExecution.task_id == task_id)
                .order_by(StepExecution.id)
                .all()
            )

    def record_trace(self, entry: dict) -> None:
        """Persist one trace record."""

        with self._session_factory() as session:
            session.add(
                TraceRecord(
                    task_id=entry["task_id"],
                    step_name=entry["step_name"],
                    component=entry["component"],
                    action=entry["action"],
                    status=entry["status"],
                    duration_ms=entry["duration_ms"],
                    attempt=entry["attempt"],
                    error=entry.get("error"),
                    model=entry.get("model"),
                    token_input=entry.get("token_input"),
                    token_output=entry.get("token_output"),
                    created_at=_utcnow(),
                )
            )
            session.commit()

    def list_trace(self, task_id: str) -> list[TraceRecord]:
        """Return all trace rows for a task."""

        with self._session_factory() as session:
            return (
                session.query(TraceRecord)
                .filter(TraceRecord.task_id == task_id)
                .order_by(TraceRecord.id)
                .all()
            )
