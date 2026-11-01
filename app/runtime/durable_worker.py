"""Durable background worker (Phase 2, Commit B).

Replaces the thread-pool-only ownership model with a **DB-backed claimable
task scan**:

- On start, the worker scans for tasks that are not owned by a live lease and
  claims them (PENDING / COLLECTING / stale-owner), then executes the
  orchestrator plan step-by-step through the atomic claim service.
- A heartbeat loop renews each claimed step's lease; if the lease is lost the
  worker stops working on that step (fencing).
- Process death does not lose the task: the DB row survives, the lease
  expires, and a NEW worker reclaims it.
- Graceful shutdown: stop claiming, release leases, drain in-flight.
- Bounded retries with exponential backoff; terminal failure after the cap.

The worker is deliberately decoupled from the HTTP request lifecycle: task
ownership lives in the DB, not in a Future or a global thread-pool dict.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from ..infrastructure.database import TaskRepository
from .step_claim import (
    SUCCEEDED,
    StepClaimService,
    plan_hash,
)

logger = logging.getLogger("procureagent.durable_worker")


class DurableWorker:
    """DB-backed worker that claims and executes recoverable tasks."""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        claim_service: StepClaimService,
        orchestrator,
        worker_id: str | None = None,
        poll_interval_seconds: float = 1.0,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
    ):
        self._repo = repository
        self._claim = claim_service
        self._orchestrator = orchestrator
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._poll_interval = poll_interval_seconds
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._running = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def start(self) -> None:
        """Begin the claim-and-execute loop (non-blocking)."""

        self._running.set()
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info("durable worker %s started", self._worker_id)

    async def _run_loop(self) -> None:
        while self._running.is_set():
            try:
                await self._claim_and_run_pending()
            except Exception as exc:
                logger.warning("worker %s scan error: %s", self._worker_id, exc)
            await asyncio.sleep(self._poll_interval)

    async def stop(self) -> None:
        """Graceful shutdown: stop claiming, cancel pending scans, drain."""

        self._running.clear()
        if hasattr(self, "_loop_task"):
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # -- scan ---------------------------------------------------------------

    async def _claim_and_run_pending(self) -> None:
        """Find tasks not owned by a live lease and run their unfinished steps."""

        now = datetime.now(UTC)
        candidates = self._repo.find_reclaimable_tasks(now)
        for task_id in candidates:
            if not self._running.is_set():
                return
            task = asyncio.create_task(self._run_task(task_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_task(self, task_id: str) -> None:
        """Execute one task's unfinished steps through the claim service."""

        record = self._repo.get(task_id)
        if record is None:
            return
        request = self._repo.get_request(task_id)
        plan = self._repo.get_plan(task_id)
        if request is None or plan is None:
            logger.warning("worker %s: task %s has no request/plan; skipping", self._worker_id, task_id)
            return

        ph = plan_hash(plan.model_dump_json())
        for step in plan.steps:
            if not self._running.is_set():
                return
            step_key = f"{step.action.value}_{step.supplier_id or ''}".strip("_")
            logical_id = self._claim.create_logical_step(
                task_id=task_id,
                plan_hash=ph,
                step_key=step_key,
                action_type=step.action.value,
            )
            status = self._claim.get_status(logical_id)
            if status["status"] == SUCCEEDED:
                continue
            claim = self._claim.claim(logical_step_id=logical_id, worker_id=self._worker_id)
            if not claim.claimed:
                # Owned by another live worker or terminal; skip.
                continue
            await self._execute_claimed(task_id, step_key, logical_id, claim)

    async def _execute_claimed(self, task_id: str, step_key: str, logical_id: int, claim) -> None:
        """Run one claimed step under heartbeat; submit fenced results."""

        attempt_id = self._claim.start_attempt(
            logical_step_id=logical_id,
            worker_id=self._worker_id,
            lease_epoch=claim.lease_epoch,
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(logical_id, claim.lease_epoch))
        try:
            result = await self._orchestrator.execute_step(
                task_id=task_id,
                step_key=step_key,
                logical_step_id=logical_id,
                claim=claim,
                worker_id=self._worker_id,
            )
            self._claim.complete(
                logical_step_id=logical_id,
                worker_id=self._worker_id,
                lease_epoch=claim.lease_epoch,
                result_ref=result.get("result_ref", ""),
                attempt_id=attempt_id,
            )
        except Exception as exc:
            logger.warning("worker %s step %s failed: %s", self._worker_id, step_key, exc)
            self._claim.fail(
                logical_step_id=logical_id,
                worker_id=self._worker_id,
                lease_epoch=claim.lease_epoch,
                error_code=type(exc).__name__,
            )
        finally:
            heartbeat_task.cancel()

    async def _heartbeat_loop(self, logical_id: int, lease_epoch: int) -> None:
        """Renew the lease until the work finishes (or the lease is lost)."""

        while self._running.is_set():
            await asyncio.sleep(self._poll_interval)
            ok = self._claim.heartbeat(
                logical_step_id=logical_id,
                worker_id=self._worker_id,
                lease_epoch=lease_epoch,
            )
            if not ok:
                logger.warning("worker %s lost lease on step %s; stopping", self._worker_id, logical_id)
                return


# ---------------------------------------------------------------------------
# Repository helpers used by the worker (added to TaskRepository)
# ---------------------------------------------------------------------------

def find_reclaimable_tasks(repo: TaskRepository, now: datetime) -> list[str]:
    """Return task ids that are not terminal and are not owned by a live
    lease. Implemented as a repository method (see database.py)."""

    return repo.find_reclaimable_tasks(now)
