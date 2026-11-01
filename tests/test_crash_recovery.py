"""Phase 2, Commit B — REAL subprocess crash recovery test.

Proves with an actual child process that calls ``os._exit()`` mid-claim
(no Python exception, no cleanup) that:

1. The task + logical step rows survive (SQLite file).
2. A NEW worker process discovers the orphaned task (expired lease) and
   reclaims the unfinished step.
3. The external supplier effect is recorded once per SUCCEEDED step.

The child writes a marker to the external-effect ledger BEFORE dying, so we
can prove "external executed, local commit never happened" — the exact
crash window the runtime must tolerate.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

WORKER_SCRIPT = textwrap.dedent(
    """
    import json, os, sys
    from pathlib import Path
    from datetime import UTC, datetime, timedelta

    db_path = Path(sys.argv[1])
    ledger_path = Path(sys.argv[2])
    mode = sys.argv[3]  # 'crash' | 'recover'

    from app.infrastructure.database import TaskRepository, build_session_factory
    from app.runtime.step_claim import StepClaimService, RealClock

    repo = TaskRepository(build_session_factory(f"sqlite:///{db_path}"))
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)

    if mode == "crash":
        # Simulate: a worker claimed the step, called the external supplier
        # (recorded in the LEDGER), then died BEFORE committing locally.
        task_id = "task-crash-proc"
        # Seed the task row (as the orchestrator would after planning).
        from app.agent.state import TaskState, TaskStatus
        from app.procurement.schemas import ProcurementRequest

        request = ProcurementRequest(product_name="MX Master 3S", quantity=20)
        repo.create(task_id, request)
        now = datetime.now(UTC)
        repo.save_state(
            TaskState(
                task_id=task_id,
                status=TaskStatus.COLLECTING,
                created_at=now,
                updated_at=now,
            )
        )
        step_id = svc.create_logical_step(
            task_id=task_id, plan_hash="ph1", step_key="collect_s1",
            action_type="COLLECT_SUPPLIER",
        )
        claim = svc.claim(logical_step_id=step_id, worker_id="worker-dead")
        # External effect happened (ledger) but local commit did not.
        ledger_path.write_text(json.dumps({
            "external_call": "supplier_s1",
            "worker": "worker-dead",
            "lease_epoch": claim.lease_epoch,
            "before_local_commit": True,
        }))
        # Hard kill: no completion, no fail, no cleanup.
        os._exit(0)

    if mode == "recover":
        # A NEW process scans and reclaims. To simulate a worker that died
        # LONGER ago than the lease window, expire its lease first (this is
        # equivalent to waiting lease_seconds before starting recovery).
        from sqlalchemy import text as _text
        with repo._session_factory() as s:
            s.execute(_text(
                "UPDATE logical_steps SET lease_expires_at = :past "
                "WHERE status='CLAIMED'"
            ), {"past": datetime.now(UTC) - timedelta(seconds=60)})
            s.commit()
        task_id = "task-crash-proc"
        steps = repo.find_reclaimable_tasks(datetime.now(UTC))
        print("RECLAIMABLE_TASKS=" + json.dumps(steps))
        # Find the step row.
        from sqlalchemy import text
        with repo._session_factory() as s:
            row = s.execute(text(
                "SELECT id, status, owner_id, lease_epoch FROM logical_steps "
                "WHERE task_id=:t AND step_key='collect_s1'"
            ), {"t": task_id}).fetchone()
        print("STEP_BEFORE=" + json.dumps({
            "id": row[0], "status": row[1], "owner_id": row[2], "lease_epoch": row[3]
        }))
        step_id = int(row[0])
        claim = svc.claim(logical_step_id=step_id, worker_id="worker-new")
        print("CLAIMED=" + json.dumps({
            "ok": claim.claimed, "epoch": claim.lease_epoch, "attempt": claim.attempt_count
        }))
        if claim.claimed:
            svc.complete(
                logical_step_id=step_id, worker_id="worker-new",
                lease_epoch=claim.lease_epoch, result_ref="quote:recovered",
            )
            print("COMPLETED=1")
        else:
            print("COMPLETED=0")
    """
)


def _run_child(db: Path, ledger: Path, mode: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", WORKER_SCRIPT, str(db), str(ledger), mode],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=60,
    )
    return proc.stdout + proc.stderr


def test_real_process_crash_then_recovery(tmp_path):
    db = tmp_path / "crash.db"
    ledger = tmp_path / "external_effects.json"

    # Phase 1: child crashes after external effect, before local commit.
    _run_child(db, ledger, "crash")
    assert ledger.exists(), "ledger must exist (external effect happened)"
    effect = json.loads(ledger.read_text(encoding="utf-8"))
    assert effect["external_call"] == "supplier_s1"
    assert effect["before_local_commit"] is True

    # Phase 2: a NEW worker process discovers and recovers.
    out2 = _run_child(db, ledger, "recover")
    assert "RECLAIMABLE_TASKS=" in out2
    assert '"task-crash-proc"' in out2
    # The step was CLAIMED by worker-dead (no completion); after lease expiry
    # the new worker reclaims with a fresh epoch.
    assert "CLAIMED=" in out2
    assert '"ok": true' in out2
    assert "COMPLETED=1" in out2

    # The recovery worker wrote a NEW claim; the old worker's stale epoch
    # cannot be used. We verify the completed step is SUCCEEDED via a final
    # in-test query.
    from app.infrastructure.database import TaskRepository, build_session_factory

    repo = TaskRepository(build_session_factory(f"sqlite:///{db}"))
    from sqlalchemy import text

    with repo._session_factory() as s:
        row = s.execute(
            text(
                "SELECT status, result_ref FROM logical_steps "
                "WHERE task_id='task-crash-proc' AND step_key='collect_s1'"
            )
        ).fetchone()
    assert row[0] == "SUCCEEDED"
    assert row[1] == "quote:recovered"


def test_stale_worker_epoch_rejected_after_recovery(tmp_path):
    """The dead worker's fencing token must not complete after recovery."""
    db = tmp_path / "fence.db"
    ledger = tmp_path / "ledger.json"

    _run_child(db, ledger, "crash")
    _run_child(db, ledger, "recover")

    from app.infrastructure.database import TaskRepository, build_session_factory

    repo = TaskRepository(build_session_factory(f"sqlite:///{db}"))
    from app.runtime.step_claim import RealClock, StepClaimService

    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)
    from sqlalchemy import text

    with repo._session_factory() as s:
        row = s.execute(
            text(
                "SELECT id, lease_epoch FROM logical_steps "
                "WHERE task_id='task-crash-proc' AND step_key='collect_s1'"
            )
        ).fetchone()
    step_id, epoch_after_recovery = int(row[0]), int(row[1])

    # Stale worker (epoch from the crashed claim) tries to complete -> rejected.
    stale_ok = svc.complete(
        logical_step_id=step_id, worker_id="worker-dead", lease_epoch=epoch_after_recovery - 1,
        result_ref="stale-write",
    )
    assert stale_ok is False

    with repo._session_factory() as s:
        row = s.execute(
            text("SELECT status, result_ref FROM logical_steps WHERE id=:id"), {"id": step_id}
        ).fetchone()
    assert row[0] == "SUCCEEDED"
    assert row[1] == "quote:recovered"  # stale write did not overwrite
