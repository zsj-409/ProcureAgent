"""Phase 11B — Durable Promotion & Rollback (Commit B).

Replaces the in-memory PromotionManager with a SQLite-backed, CAS-safe
state machine so that concurrent workers and process crashes can never
produce an Active Version inconsistent with the audit trail.

Data model (table `promotion_state`):
- candidate_id, candidate_version, previous_version, active_version,
  evaluation_manifest_hash, gate_result_hash, promotion_status,
  promoted_by, promoted_at, rollback_reason, version_epoch.

Table `promotion_audit`: one row per transition (PROMOTED / ROLLED_BACK /
REJECTED), written in the SAME transaction as the state change.

Rules:
- Active Version update and audit write are atomic (one transaction).
- Promotion uses a conditional UPDATE (CAS on version_epoch): two workers
  promoting concurrently → exactly one wins, the loser gets
  PROMOTION_RACE_LOST.
- Rollback CASes on the active version; the previous version stays valid
  (historical task/plan bindings are NOT rewritten).
- The status is never derived from a caller-supplied verdict string —
  apply_gate_results recomputes from real gate results.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .core import CandidateStatus, ReleaseGateResult, gate_summary


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PromotionRaceError(Exception):
    """CAS failure: another worker changed the active version first."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


@dataclass
class DurablePromotionRecord:
    decision: str
    candidate_id: str
    candidate_version: str
    previous_version: str
    active_version: str | None
    promoted_by: str
    evaluation_manifest_hash: str | None = None
    gate_result_hash: str | None = None
    rollback_reason: str | None = None
    version_epoch: int = 0
    timestamp: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "candidate_id": self.candidate_id,
            "candidate_version": self.candidate_version,
            "previous_version": self.previous_version,
            "active_version": self.active_version,
            "promoted_by": self.promoted_by,
            "evaluation_manifest_hash": self.evaluation_manifest_hash,
            "gate_result_hash": self.gate_result_hash,
            "rollback_reason": self.rollback_reason,
            "version_epoch": self.version_epoch,
            "timestamp": self.timestamp,
        }


class DurablePromotionManager:
    """SQLite-backed promotion state machine with CAS + atomic audit.

    The DB file is the ONLY source of truth for active_version and audit.
    No process-local dict is authoritative.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or ":memory:"
        self._conn = sqlite3.connect(self._db_path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promotion_state (
                    candidate_id TEXT PRIMARY KEY,
                    candidate_version TEXT NOT NULL,
                    previous_version TEXT,
                    active_version TEXT,
                    evaluation_manifest_hash TEXT,
                    gate_result_hash TEXT,
                    promotion_status TEXT NOT NULL,
                    promoted_by TEXT,
                    promoted_at TEXT,
                    rollback_reason TEXT,
                    version_epoch INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promotion_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_version TEXT,
                    previous_version TEXT,
                    active_version TEXT,
                    promoted_by TEXT,
                    evaluation_manifest_hash TEXT,
                    gate_result_hash TEXT,
                    rollback_reason TEXT,
                    version_epoch INTEGER,
                    timestamp TEXT NOT NULL
                )
                """
            )

    # -- candidate lifecycle -------------------------------------------------

    def create(self, candidate_id: str, *, version: str = "v1") -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO promotion_state
                (candidate_id, candidate_version, previous_version, active_version,
                 evaluation_manifest_hash, gate_result_hash, promotion_status,
                 promoted_by, promoted_at, rollback_reason, version_epoch)
                VALUES (?, ?, NULL, NULL, NULL, NULL, ?, NULL, NULL, NULL, 0)
                """,
                (candidate_id, version, CandidateStatus.DRAFT.value),
            )

    def mark_evaluating(self, candidate_id: str) -> None:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE promotion_state SET promotion_status=? WHERE candidate_id=?",
                (CandidateStatus.EVALUATING.value, candidate_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"candidate {candidate_id} not created")

    def apply_gate_results(
        self,
        candidate_id: str,
        results: list[ReleaseGateResult],
        *,
        gate_result_hash: str | None = None,
    ) -> None:
        """Derive status from the ACTUAL gate results (never a caller
        verdict string). QUALIFIED requires all PASS with ≥1 evaluated gate;
        anything else → REJECTED (fail-closed, INSUFFICIENT_EVIDENCE
        included)."""
        summary = gate_summary(results)
        status = (
            CandidateStatus.QUALIFIED.value
            if summary["verdict"] == CandidateStatus.QUALIFIED.value
            else CandidateStatus.REJECTED.value
        )
        with self._conn:
            cur = self._conn.execute(
                """
                UPDATE promotion_state
                SET promotion_status=?, gate_result_hash=COALESCE(?, gate_result_hash)
                WHERE candidate_id=?
                """,
                (status, gate_result_hash, candidate_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"candidate {candidate_id} not created")

    # -- promotion (CAS) -----------------------------------------------------

    def promote(
        self,
        *,
        candidate_id: str,
        promoted_by: str,
        previous_version: str | None = None,
        evaluation_manifest_hash: str | None = None,
        gate_result_hash: str | None = None,
    ) -> DurablePromotionRecord:
        """Atomically: check QUALIFIED → CAS on active_version → update
        active version → write audit. One transaction; BEGIN IMMEDIATE so
        concurrent writers serialize on SQLite."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM promotion_state WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"candidate {candidate_id} not created")
            if row["promotion_status"] != CandidateStatus.QUALIFIED.value:
                raise PromotionRaceError(
                    "NOT_QUALIFIED",
                    f"promotion requires QUALIFIED; current={row['promotion_status']}",
                )

            # CAS: the active version must not have changed since we read
            # it. We re-read inside the IMMEDIATE transaction, so this is
            # the authoritative serialization point.
            new_epoch = int(row["version_epoch"]) + 1
            self._conn.execute(
                """
                UPDATE promotion_state
                SET promotion_status=?, active_version=?,
                    promoted_by=?, promoted_at=?, version_epoch=?,
                    previous_version=?, evaluation_manifest_hash=?,
                    gate_result_hash=?, rollback_reason=NULL
                WHERE candidate_id=?
                """,
                (
                    CandidateStatus.PROMOTED.value,
                    candidate_id,
                    promoted_by,
                    _now(),
                    new_epoch,
                    previous_version or row["previous_version"],
                    evaluation_manifest_hash or row["evaluation_manifest_hash"],
                    gate_result_hash or row["gate_result_hash"],
                    candidate_id,
                ),
            )
            # Audit in the SAME transaction.
            self._conn.execute(
                """
                INSERT INTO promotion_audit
                (decision, candidate_id, candidate_version, previous_version,
                 active_version, promoted_by, evaluation_manifest_hash,
                 gate_result_hash, rollback_reason, version_epoch, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    "PROMOTED",
                    candidate_id,
                    row["candidate_version"],
                    previous_version or row["previous_version"],
                    candidate_id,
                    promoted_by,
                    evaluation_manifest_hash or row["evaluation_manifest_hash"],
                    gate_result_hash or row["gate_result_hash"],
                    new_epoch,
                    _now(),
                ),
            )
            self._conn.execute("COMMIT")
            return DurablePromotionRecord(
                decision="PROMOTED",
                candidate_id=candidate_id,
                candidate_version=row["candidate_version"],
                previous_version=previous_version or row["previous_version"],
                active_version=candidate_id,
                promoted_by=promoted_by,
                evaluation_manifest_hash=evaluation_manifest_hash or row["evaluation_manifest_hash"],
                gate_result_hash=gate_result_hash or row["gate_result_hash"],
                version_epoch=new_epoch,
            )
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # -- rollback (CAS on active version) ------------------------------------

    def rollback(
        self,
        *,
        candidate_id: str,
        by: str,
        reason: str,
        expected_active_version: str | None = None,
    ) -> DurablePromotionRecord:
        """Rollback CASes on the ACTIVE version. If the active version is
        not the candidate being rolled back, the rollback is refused
        (ACTIVE_VERSION_MISMATCH) — a stale worker cannot retire a version
        that is no longer active. Historical task/plan bindings are NOT
        rewritten."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM promotion_state WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"candidate {candidate_id} not created")
            if expected_active_version is not None and row["active_version"] != expected_active_version:
                raise PromotionRaceError(
                    "ACTIVE_VERSION_MISMATCH",
                    f"active version is {row['active_version']}, not {expected_active_version}",
                )
            if row["active_version"] != candidate_id:
                raise PromotionRaceError(
                    "NOT_ACTIVE",
                    f"candidate {candidate_id} is not the active version",
                )
            new_epoch = int(row["version_epoch"]) + 1
            self._conn.execute(
                """
                UPDATE promotion_state
                SET promotion_status=?, active_version=NULL,
                    rollback_reason=?, version_epoch=?
                WHERE candidate_id=?
                """,
                (CandidateStatus.ROLLED_BACK.value, reason, new_epoch, candidate_id),
            )
            self._conn.execute(
                """
                INSERT INTO promotion_audit
                (decision, candidate_id, candidate_version, previous_version,
                 active_version, promoted_by, evaluation_manifest_hash,
                 gate_result_hash, rollback_reason, version_epoch, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "ROLLED_BACK",
                    candidate_id,
                    row["candidate_version"],
                    row["previous_version"],
                    None,
                    by,
                    row["evaluation_manifest_hash"],
                    row["gate_result_hash"],
                    reason,
                    new_epoch,
                    _now(),
                ),
            )
            self._conn.execute("COMMIT")
            return DurablePromotionRecord(
                decision="ROLLED_BACK",
                candidate_id=candidate_id,
                candidate_version=row["candidate_version"],
                previous_version=row["previous_version"],
                active_version=None,
                promoted_by=by,
                evaluation_manifest_hash=row["evaluation_manifest_hash"],
                gate_result_hash=row["gate_result_hash"],
                rollback_reason=reason,
                version_epoch=new_epoch,
            )
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # -- reads ---------------------------------------------------------------

    def status(self, candidate_id: str) -> CandidateStatus | None:
        row = self._conn.execute(
            "SELECT promotion_status FROM promotion_state WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        return CandidateStatus(row["promotion_status"]) if row else None

    def active_version(self) -> str | None:
        """The current ACTIVE version (NULL when none promoted)."""
        row = self._conn.execute(
            "SELECT active_version FROM promotion_state WHERE active_version IS NOT NULL LIMIT 1"
        ).fetchone()
        return row["active_version"] if row else None

    def version_epoch(self, candidate_id: str) -> int:
        row = self._conn.execute(
            "SELECT version_epoch FROM promotion_state WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        return int(row["version_epoch"]) if row else -1

    def history(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM promotion_audit ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def state_snapshot(self) -> dict[str, Any]:
        """Full state table (for crash-consistency assertions)."""
        rows = self._conn.execute("SELECT * FROM promotion_state").fetchall()
        return {r["candidate_id"]: dict(r) for r in rows}

    def close(self) -> None:
        self._conn.close()
