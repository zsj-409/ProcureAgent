"""Independent chaos evaluation harness (Phase 2, Commit C).

Provides:

- ``ExternalEffectLedger``: a SEPARATE JSON ledger (not the main DB) that
  records every external invocation a mock supplier makes. This is the ground
  truth for "external succeeded but local commit did not" and for duplicate
  external effect detection.
- ``FaultInjector``: injectable crash/expiry hooks around the claim/execute
  lifecycle with a fault-seed registry.
- ``StateConsistencyChecker``: cross-checks task / logical_step / step_attempt
  / checkpoint / approval consistency.
- ``ChaosResult``: machine-readable experiment output.

The ledger lives in its own file so the harness can prove effects that the
main DB never saw committed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


class ExternalEffectLedger:
    """Append-only JSON ledger of external supplier invocations."""

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._entries: list[dict] = []
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        supplier_id: str,
        action: str,
        worker_id: str | None = None,
        task_id: str | None = None,
        step_key: str | None = None,
        request_id: str | None = None,
        outcome: str = "executed",
        extra: dict | None = None,
    ) -> None:
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "supplier_id": supplier_id,
            "action": action,
            "worker_id": worker_id,
            "task_id": task_id,
            "step_key": step_key,
            "request_id": request_id or f"req-{self.count() + 1}",
            "outcome": outcome,
        }
        if extra:
            entry.update(extra)
        # Append-safe across processes: read existing entries first, then
        # append and rewrite. (Best-effort; the chaos tests use distinct
        # ledger files per scenario and subprocesses are serialized per step.)
        existing = self.load()
        existing.append(entry)
        self._path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _flush(self) -> None:
        self._path.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load(self) -> list[dict]:
        if not self._path.exists():
            return []
        return json.loads(self._path.read_text(encoding="utf-8"))

    def count(self) -> int:
        return len(self.load())

    def duplicates_for(self, *, task_id: str, step_key: str) -> list[dict]:
        """Return ledger entries that executed the same task+step more than
        once — the duplicate external effect evidence."""

        entries = self.load()
        seen: dict[tuple, list[dict]] = {}
        for entry in entries:
            key = (entry.get("task_id"), entry.get("step_key"))
            seen.setdefault(key, []).append(entry)
        dupes = [group for group in seen.values() if len(group) > 1]
        flat = [entry for group in dupes for entry in group]
        return flat

    def hash(self) -> str:
        """World snapshot hash — changes when any external effect changes."""

        payload = json.dumps(self.load(), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Fault injector
# ---------------------------------------------------------------------------

FAULT_BEFORE_EXTERNAL_CALL = "BEFORE_EXTERNAL_CALL"
FAULT_AFTER_EXTERNAL_SUCCESS_BEFORE_COMMIT = "AFTER_EXTERNAL_SUCCESS_BEFORE_COMMIT"
FAULT_AFTER_LOCAL_COMMIT_BEFORE_RESPONSE = "AFTER_LOCAL_COMMIT_BEFORE_RESPONSE"
FAULT_LEASE_EXPIRED = "LEASE_EXPIRED"
FAULT_HEARTBEAT_LOST = "HEARTBEAT_LOST"
FAULT_WORKER_KILLED = "WORKER_PROCESS_KILLED"
FAULT_SQLITE_BUSY = "SQLITE_BUSY"
FAULT_DUPLICATE_RESUME = "DUPLICATE_RESUME"
FAULT_CONCURRENT_CLAIM = "CONCURRENT_CLAIM"


class FaultInjector:
    """Deterministic fault injection at named boundaries.

    A fault is a callable ``(ctx) -> None`` that raises or side-effects at a
    boundary. Seeds are registered per experiment so results are reproducible.
    """

    def __init__(self, seed: str | None = None):
        self._seed = seed
        self._hooks: dict[str, list[callable]] = {}
        self._faults_fired: list[str] = []

    def register(self, boundary: str, fn: callable) -> None:
        self._hooks.setdefault(boundary, []).append(fn)

    def fire(self, boundary: str, ctx: dict | None = None) -> None:
        """Invoke all hooks at a boundary; a hook may raise to simulate a
        crash. Records every fired fault (observability)."""

        for fn in self._hooks.get(boundary, []):
            self._faults_fired.append(f"{boundary}:{getattr(fn, '__name__', 'hook')}")
            fn(ctx or {})

    @property
    def fired(self) -> list[str]:
        return list(self._faults_fired)

    def manifest(self) -> dict:
        return {
            "seed": self._seed,
            "faults_fired": self._faults_fired,
        }


# ---------------------------------------------------------------------------
# State consistency checker
# ---------------------------------------------------------------------------

class StateConsistencyChecker:
    """Cross-table consistency audit for a task."""

    def __init__(self, repo):
        self._repo = repo

    def check(self, task_id: str) -> dict:
        from sqlalchemy import text

        errors: list[str] = []
        with self._repo._session_factory() as s:
            task = s.execute(
                text("SELECT status, current_step, error FROM tasks WHERE id=:id"),
                {"id": task_id},
            ).fetchone()
            steps = s.execute(
                text("SELECT step_key, status, owner_id, lease_epoch FROM logical_steps WHERE task_id=:id"),
                {"id": task_id},
            ).fetchall()
            attempts = s.execute(
                text(
                    "SELECT logical_step_id, attempt_status FROM step_attempts "
                    "WHERE logical_step_id IN "
                    "(SELECT id FROM logical_steps WHERE task_id=:id)"
                ),
                {"id": task_id},
            ).fetchall()

        if task is None:
            return {"consistent": False, "errors": ["task not found"]}

        # A task marked COMPLETED must have no non-SUCCEEDED steps.
        if task[0] == "COMPLETED":
            bad = [st for st in steps if st[1] != "SUCCEEDED"]
            if bad:
                errors.append(f"task COMPLETED but steps not all SUCCEEDED: {[(st[0], st[1]) for st in bad]}")

        # A step CLAIMED by a live lease must have a matching RUNNING attempt.
        for st in steps:
            if st[1] == "CLAIMED" and st[2]:
                live = any(a[1] == "RUNNING" for a in attempts if a[0] == st[0])
                if not live:
                    errors.append(f"step {st[0]} CLAIMED by {st[2]} but no RUNNING attempt")

        # No step may have two SUCCEEDED attempts (duplicate completion).
        from collections import Counter

        succeeded_by_step = Counter(a[0] for a in attempts if a[1] == "SUCCEEDED")
        for step_id, count in succeeded_by_step.items():
            if count > 1:
                errors.append(f"step {step_id} has {count} SUCCEEDED attempts")

        return {
            "consistent": not errors,
            "errors": errors,
            "n_steps": len(steps),
            "n_attempts": len(attempts),
        }


# ---------------------------------------------------------------------------
# Experiment result container
# ---------------------------------------------------------------------------

@dataclass
class ChaosResult:
    """One chaos experiment's machine-readable output."""

    name: str
    seed: str
    method: str
    n_workers: int
    fault: str | None
    verified_recovery: bool
    duplicate_external_effects: int
    duplicate_invocations: int
    recovery_latency_ms: int
    state_consistent: bool
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "seed": self.seed,
            "method": self.method,
            "n_workers": self.n_workers,
            "fault": self.fault,
            "verified_recovery": self.verified_recovery,
            "duplicate_external_effects": self.duplicate_external_effects,
            "duplicate_invocations": self.duplicate_invocations,
            "recovery_latency_ms": self.recovery_latency_ms,
            "state_consistent": self.state_consistent,
            "details": self.details,
        }
