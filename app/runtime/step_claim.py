"""Atomic step claim, lease, heartbeat, and fencing (Phase 2).

Implements the durable-runtime ownership protocol on SQLite:

- ``create_logical_step``: INSERT ... ON CONFLICT DO NOTHING — idempotent step
  creation; a second create for the same (task, plan_hash, step_key) is a no-op
  and returns the existing id. Retries can never produce a second logical step.
- ``claim``: single atomic UPDATE ... RETURNING with a WHERE that accepts only
  PENDING or an EXPIRED lease; increments ``lease_epoch`` (fencing token) and
  ``attempt_count``. The short transaction commits BEFORE any external call —
  no lock is held across HTTP/browser work.
- ``heartbeat``: conditional UPDATE only by the current owner+epoch.
- ``complete`` / ``fail`` / ``mark_unknown``: conditional UPDATE fenced by
  owner_id + lease_epoch; a stale worker (wrong owner or expired epoch) gets
  rowcount==0 and is REJECTED — it cannot submit results after losing the lease.

UNKNOWN_OUTCOME is a distinct terminal-ish state: it is NOT auto-retried as a
failure; it requires reconciliation (verify external state) or human review.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from ..infrastructure.database import TaskRepository

# SQLite guarantees: the claim UPDATE is a single statement executed inside
# SQLAlchemy's autobegin transaction; it serializes across processes via the
# write lock. RETURNING gives us the fencing token atomically.

# Logical step statuses.
PENDING = "PENDING"
CLAIMED = "CLAIMED"
SUCCEEDED = "SUCCEEDED"
RETRY_SCHEDULED = "RETRY_SCHEDULED"
FAILED_TERMINAL = "FAILED_TERMINAL"
UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


@dataclass(frozen=True)
class StepClaim:
    """A successful claim with its fencing token."""

    logical_step_id: int
    lease_epoch: int
    attempt_count: int


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of a claim attempt."""

    claimed: bool
    logical_step_id: int | None = None
    lease_epoch: int | None = None
    attempt_count: int | None = None
    reason: str | None = None


class Clock:
    """Injectable clock for testable lease/expiry logic."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class RealClock(Clock):
    pass


class StepClaimService:
    """Atomic ownership protocol over logical_steps / step_attempts."""

    def __init__(self, repository: TaskRepository, clock: Clock | None = None, lease_seconds: int = 30):
        self._repo = repository
        self._clock = clock or RealClock()
        self._lease_seconds = lease_seconds

    # -- creation (idempotent) ----------------------------------------------

    def create_logical_step(
        self,
        *,
        task_id: str,
        plan_hash: str,
        step_key: str,
        action_type: str,
        action_input_hash: str = "",
    ) -> int:
        """Insert the logical step, or return the existing one.

        Uniqueness is (task_id, plan_hash, step_key). A concurrent duplicate
        insert is a no-op (ON CONFLICT DO NOTHING) — retries cannot create a
        second logical step.
        """

        now = self._clock.now()
        with self._repo._session_factory() as session:
            existing = session.execute(
                text(
                    "SELECT id FROM logical_steps "
                    "WHERE task_id=:t AND plan_hash=:p AND step_key=:k"
                ),
                {"t": task_id, "p": plan_hash, "k": step_key},
            ).fetchone()
            if existing is not None:
                return int(existing[0])
            session.execute(
                text(
                    "INSERT INTO logical_steps "
                    "(task_id, plan_hash, step_key, action_type, action_input_hash, "
                    "status, owner_id, lease_epoch, lease_expires_at, attempt_count, "
                    "result_ref, created_at, updated_at) "
                    "VALUES (:t, :p, :k, :a, :h, 'PENDING', NULL, 0, NULL, 0, NULL, :now, :now) "
                    "ON CONFLICT(task_id, plan_hash, step_key) DO NOTHING"
                ),
                {
                    "t": task_id,
                    "p": plan_hash,
                    "k": step_key,
                    "a": action_type,
                    "h": action_input_hash,
                    "now": now,
                },
            )
            session.commit()
            row = session.execute(
                text(
                    "SELECT id FROM logical_steps "
                    "WHERE task_id=:t AND plan_hash=:p AND step_key=:k"
                ),
                {"t": task_id, "p": plan_hash, "k": step_key},
            ).fetchone()
            return int(row[0])

    # -- claim --------------------------------------------------------------

    def claim(
        self,
        *,
        logical_step_id: int,
        worker_id: str,
        now: datetime | None = None,
        task_id: str | None = None,
        plan_hash: str | None = None,
    ) -> ClaimResult:
        """Atomically claim a step.

        Accepts PENDING steps and steps whose lease has EXPIRED (reclaim).
        Increments ``lease_epoch`` (fencing token) and ``attempt_count``.
        Returns the new epoch; the caller must use it to complete/fail the step.

        Phase 4 (0.2): optional ``task_id`` / ``plan_hash`` expected values —
        when provided they are added to the WHERE, so a caller holding another
        task's step id cannot claim it (defense in depth beyond the caller
        always using the id it just created).
        """

        now = now or self._clock.now()
        expiry = now + timedelta(seconds=self._lease_seconds)
        where = (
            "WHERE id=:id AND (status IN ('PENDING', 'RETRY_SCHEDULED') "
            "   OR (status='CLAIMED' AND lease_expires_at < :now))"
        )
        params: dict = {"id": logical_step_id, "w": worker_id, "now": now, "expiry": expiry}
        if task_id is not None:
            where += " AND task_id=:task_id"
            params["task_id"] = task_id
        if plan_hash is not None:
            where += " AND plan_hash=:plan_hash"
            params["plan_hash"] = plan_hash
        with self._repo._session_factory() as session:
            row = session.execute(
                text(
                    "UPDATE logical_steps "
                    "SET status='CLAIMED', owner_id=:w, "
                    "    lease_epoch=lease_epoch+1, lease_expires_at=:expiry, "
                    "    attempt_count=attempt_count+1, updated_at=:now "
                    f"{where} "
                    "RETURNING id, lease_epoch, attempt_count"
                ),
                params,
            ).fetchone()
            session.commit()
        if row is None:
            return ClaimResult(claimed=False, reason="not_claimable")
        return ClaimResult(
            claimed=True,
            logical_step_id=int(row[0]),
            lease_epoch=int(row[1]),
            attempt_count=int(row[2]),
        )

    # -- heartbeat ----------------------------------------------------------

    def heartbeat(
        self,
        *,
        logical_step_id: int,
        worker_id: str,
        lease_epoch: int,
        now: datetime | None = None,
    ) -> bool:
        """Extend the lease, but ONLY if the caller is still the owner.

        Returns False when the worker lost the lease (stale) — it should stop
        work immediately.
        """

        now = now or self._clock.now()
        expiry = now + timedelta(seconds=self._lease_seconds)
        with self._repo._session_factory() as session:
            result = session.execute(
                text(
                    "UPDATE logical_steps SET lease_expires_at=:expiry, updated_at=:now "
                    "WHERE id=:id AND owner_id=:w AND lease_epoch=:e"
                ),
                {"id": logical_step_id, "w": worker_id, "e": lease_epoch, "now": now, "expiry": expiry},
            )
            session.commit()
            return result.rowcount == 1

    # -- conditional terminal writes (fenced) -------------------------------

    def complete(
        self,
        *,
        logical_step_id: int,
        worker_id: str,
        lease_epoch: int,
        result_ref: str,
        result_json: dict | None = None,
        attempt_id: int | None = None,
    ) -> bool:
        """Mark SUCCEEDED, fenced by owner+epoch. Stale worker -> False."""

        now = self._clock.now()
        with self._repo._session_factory() as session:
            result = session.execute(
                text(
                    "UPDATE logical_steps SET status='SUCCEEDED', result_ref=:ref, "
                    "    owner_id=NULL, lease_epoch=lease_epoch, lease_expires_at=NULL, "
                    "    updated_at=:now "
                    "WHERE id=:id AND owner_id=:w AND lease_epoch=:e"
                ),
                {"id": logical_step_id, "w": worker_id, "e": lease_epoch, "ref": result_ref, "now": now},
            )
            if result.rowcount == 1 and attempt_id is not None:
                session.execute(
                    text(
                        "UPDATE step_attempts SET attempt_status='SUCCEEDED', completed_at=:now "
                        "WHERE id=:aid"
                    ),
                    {"aid": attempt_id, "now": now},
                )
            session.commit()
            return result.rowcount == 1

    def fail(
        self,
        *,
        logical_step_id: int,
        worker_id: str,
        lease_epoch: int,
        error_code: str,
        terminal: bool = False,
        attempt_id: int | None = None,
    ) -> bool:
        """Mark RETRY_SCHEDULED (or FAILED_TERMINAL), fenced by owner+epoch."""

        now = self._clock.now()
        status = FAILED_TERMINAL if terminal else RETRY_SCHEDULED
        with self._repo._session_factory() as session:
            result = session.execute(
                text(
                    "UPDATE logical_steps SET status=:s, owner_id=NULL, "
                    "    lease_expires_at=NULL, updated_at=:now "
                    "WHERE id=:id AND owner_id=:w AND lease_epoch=:e"
                ),
                {"id": logical_step_id, "w": worker_id, "e": lease_epoch, "s": status, "now": now},
            )
            if result.rowcount == 1 and attempt_id is not None:
                session.execute(
                    text(
                        "UPDATE step_attempts SET attempt_status=:s, completed_at=:now, error_code=:ec "
                        "WHERE id=:aid"
                    ),
                    {"aid": attempt_id, "s": status, "now": now, "ec": error_code},
                )
            session.commit()
            return result.rowcount == 1

    def mark_unknown(
        self,
        *,
        logical_step_id: int,
        worker_id: str,
        lease_epoch: int,
        reason: str,
        attempt_id: int | None = None,
    ) -> bool:
        """Mark UNKNOWN_OUTCOME — external effect uncertain. NOT auto-retried."""

        now = self._clock.now()
        with self._repo._session_factory() as session:
            result = session.execute(
                text(
                    "UPDATE logical_steps SET status='UNKNOWN_OUTCOME', owner_id=NULL, "
                    "    lease_expires_at=NULL, updated_at=:now "
                    "WHERE id=:id AND owner_id=:w AND lease_epoch=:e"
                ),
                {"id": logical_step_id, "w": worker_id, "e": lease_epoch, "now": now},
            )
            if result.rowcount == 1 and attempt_id is not None:
                session.execute(
                    text(
                        "UPDATE step_attempts SET attempt_status='UNKNOWN_OUTCOME', "
                        "    completed_at=:now, error_code=:r WHERE id=:aid"
                    ),
                    {"aid": attempt_id, "now": now, "r": reason},
                )
            session.commit()
            return result.rowcount == 1

    # -- attempt bookkeeping --------------------------------------------------

    def start_attempt(
        self,
        *,
        logical_step_id: int,
        worker_id: str,
        lease_epoch: int,
        external_idempotency_key: str | None = None,
        tool_cost: dict | None = None,
    ) -> int:
        """Record a StepAttempt row (RUNNING)."""

        now = self._clock.now()
        with self._repo._session_factory() as session:
            result = session.execute(
                text(
                    "INSERT INTO step_attempts "
                    "(logical_step_id, worker_id, lease_epoch, started_at, "
                    " attempt_status, external_idempotency_key, tool_cost) "
                    "VALUES (:lid, :w, :e, :now, 'RUNNING', :key, :cost)"
                ),
                {
                    "lid": logical_step_id,
                    "w": worker_id,
                    "e": lease_epoch,
                    "now": now,
                    "key": external_idempotency_key,
                    "cost": json.dumps(tool_cost) if tool_cost else None,
                },
            )
            session.commit()
            return int(result.lastrowid)

    # -- queries ---------------------------------------------------------------

    def get_status(self, logical_step_id: int) -> dict | None:
        with self._repo._session_factory() as session:
            row = session.execute(
                text(
                    "SELECT id, task_id, plan_hash, step_key, action_type, status, "
                    "owner_id, lease_epoch, attempt_count, result_ref "
                    "FROM logical_steps WHERE id=:id"
                ),
                {"id": logical_step_id},
            ).fetchone()
            if row is None:
                return None
            return {
                "id": int(row[0]),
                "task_id": row[1],
                "plan_hash": row[2],
                "step_key": row[3],
                "action_type": row[4],
                "status": row[5],
                "owner_id": row[6],
                "lease_epoch": int(row[7]) if row[7] is not None else 0,
                "attempt_count": int(row[8]),
                "result_ref": row[9],
            }

    def list_attempts(self, logical_step_id: int) -> list[dict]:
        with self._repo._session_factory() as session:
            rows = session.execute(
                text(
                    "SELECT id, worker_id, lease_epoch, attempt_status, error_code, "
                    "external_idempotency_key, started_at, completed_at "
                    "FROM step_attempts WHERE logical_step_id=:lid ORDER BY id"
                ),
                {"lid": logical_step_id},
            ).fetchall()
            return [
                {
                    "id": int(r[0]),
                    "worker_id": r[1],
                    "lease_epoch": int(r[2]),
                    "attempt_status": r[3],
                    "error_code": r[4],
                    "external_idempotency_key": r[5],
                    "started_at": r[6].isoformat() if r[6] else None,
                    "completed_at": r[7].isoformat() if r[7] else None,
                }
                for r in rows
            ]


# ---------------------------------------------------------------------------
# Hash helpers for checkpoint versioning
# ---------------------------------------------------------------------------

def sha256_hex(payload: Any) -> str:
    """Stable content hash for plan/input/result versioning."""

    if not isinstance(payload, str):
        payload = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:16]


def plan_hash(plan_json: str) -> str:
    return sha256_hex(plan_json)


def action_input_hash(
    *, product_name: str, quantity: int, supplier_id: str, extra: dict | None = None
) -> str:
    payload = {"product_name": product_name, "quantity": quantity, "supplier_id": supplier_id}
    if extra:
        payload.update(extra)
    return sha256_hex(payload)
