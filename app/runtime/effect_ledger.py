"""External effect ledger — SQLite-backed, transactional (Phase 3).

Replaces the read-modify-write JSON ledger (which could lose updates across
processes) with a dedicated SQLite database SEPARATE from the ProcureAgent
business DB (``mock_supplier_effects.db``).

Two tables:

- ``external_invocations``: every external call attempt (invocation count).
- ``external_committed_effects``: effects that actually took effect on the
  mock supplier side. Idempotency: the same (idempotency_key, payload_hash)
  returns the already-committed result; the same key with a DIFFERENT payload
  is rejected.

Metrics are separated:
- Duplicate Invocation Count = rows in external_invocations per (task,step).
- Duplicate Committed Side Effect Count = committed effects per business
  intent — a committed row with the same idempotency_key is NOT a new effect.

All writes are transactional (autocommit per statement) so concurrent
processes cannot lose updates.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


class SqliteEffectLedger:
    """Transactional, cross-process-safe external effect ledger."""

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._path), timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init_schema(self) -> None:
        con = self._connect()
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS external_invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                supplier_id TEXT NOT NULL,
                action TEXT NOT NULL,
                worker_id TEXT,
                task_id TEXT,
                step_key TEXT,
                request_id TEXT,
                outcome TEXT
            );
            CREATE TABLE IF NOT EXISTS external_committed_effects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_operation_id TEXT NOT NULL,
                idempotency_key TEXT,
                payload_hash TEXT,
                result_hash TEXT,
                task_id TEXT,
                step_key TEXT,
                committed_at TEXT NOT NULL,
                UNIQUE(idempotency_key)
            );
            """
        )
        con.commit()
        con.close()

    # -- invocations --------------------------------------------------------

    def record_invocation(
        self,
        *,
        supplier_id: str,
        action: str,
        worker_id: str | None = None,
        task_id: str | None = None,
        step_key: str | None = None,
        request_id: str | None = None,
        outcome: str = "executed",
    ) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO external_invocations "
                "(ts, supplier_id, action, worker_id, task_id, step_key, request_id, outcome) "
                "VALUES (datetime('now'), :s, :a, :w, :t, :k, :r, :o)",
                {
                    "s": supplier_id,
                    "a": action,
                    "w": worker_id,
                    "t": task_id,
                    "k": step_key,
                    "r": request_id,
                    "o": outcome,
                },
            )
            con.commit()
        finally:
            con.close()

    # -- committed effects (idempotent) -------------------------------------

    def commit_effect(
        self,
        *,
        external_operation_id: str,
        idempotency_key: str | None,
        payload: Any,
        result_hash: str,
        task_id: str | None = None,
        step_key: str | None = None,
    ) -> tuple[str, bool]:
        """Commit (or return the already-committed) external effect.

        Returns (operation_id, committed_now):
        - committed_now=True: this call created the effect.
        - committed_now=False: the same idempotency_key already committed —
          the previous effect is returned (no duplicate business effect).

        Raises ValueError when the same idempotency_key arrives with a
        DIFFERENT payload (a contract violation).
        """

        ph = _payload_hash(payload)
        con = self._connect()
        try:
            existing = con.execute(
                "SELECT id, payload_hash, external_operation_id, result_hash "
                "FROM external_committed_effects WHERE idempotency_key=:k",
                {"k": idempotency_key},
            ).fetchone()
            if existing is not None:
                if existing[1] != ph:
                    raise ValueError(
                        f"idempotency key {idempotency_key} reused with a different payload "
                        f"(stored {existing[1]}, got {ph})"
                    )
                return existing[2], False
            con.execute(
                "INSERT INTO external_committed_effects "
                "(external_operation_id, idempotency_key, payload_hash, result_hash, "
                " task_id, step_key, committed_at) "
                "VALUES (:op, :k, :ph, :rh, :t, :s, datetime('now'))",
                {
                    "op": external_operation_id,
                    "k": idempotency_key,
                    "ph": ph,
                    "rh": result_hash,
                    "t": task_id,
                    "s": step_key,
                },
            )
            con.commit()
            return external_operation_id, True
        finally:
            con.close()

    # -- queries -------------------------------------------------------------

    def invocation_count(self, *, task_id: str | None = None, step_key: str | None = None) -> int:
        con = self._connect()
        try:
            if task_id and step_key:
                row = con.execute(
                    "SELECT COUNT(*) FROM external_invocations WHERE task_id=:t AND step_key=:k",
                    {"t": task_id, "k": step_key},
                ).fetchone()
            else:
                row = con.execute("SELECT COUNT(*) FROM external_invocations").fetchone()
            return int(row[0])
        finally:
            con.close()

    def committed_effect_count(self, *, task_id: str | None = None, step_key: str | None = None) -> int:
        con = self._connect()
        try:
            if task_id and step_key:
                row = con.execute(
                    "SELECT COUNT(*) FROM external_committed_effects WHERE task_id=:t AND step_key=:k",
                    {"t": task_id, "k": step_key},
                ).fetchone()
            else:
                row = con.execute("SELECT COUNT(*) FROM external_committed_effects").fetchone()
            return int(row[0])
        finally:
            con.close()

    def invocations(self) -> list[dict]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT ts, supplier_id, action, worker_id, task_id, step_key, request_id, outcome "
                "FROM external_invocations ORDER BY id"
            ).fetchall()
            return [
                {
                    "ts": r[0],
                    "supplier_id": r[1],
                    "action": r[2],
                    "worker_id": r[3],
                    "task_id": r[4],
                    "step_key": r[5],
                    "request_id": r[6],
                    "outcome": r[7],
                }
                for r in rows
            ]
        finally:
            con.close()

    def committed_effects(self) -> list[dict]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT external_operation_id, idempotency_key, payload_hash, result_hash, "
                "task_id, step_key, committed_at FROM external_committed_effects ORDER BY id"
            ).fetchall()
            return [
                {
                    "external_operation_id": r[0],
                    "idempotency_key": r[1],
                    "payload_hash": r[2],
                    "result_hash": r[3],
                    "task_id": r[4],
                    "step_key": r[5],
                    "committed_at": r[6],
                }
                for r in rows
            ]
        finally:
            con.close()

    def hash(self) -> str:
        """World snapshot hash — changes when any ledger row changes."""

        payload = json.dumps(
            {"invocations": self.invocations(), "committed": self.committed_effects()},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
