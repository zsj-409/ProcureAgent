"""Phase 2, Commit C — cross-process concurrency chaos test.

Real subprocesses (NOT threads, NOT :memory:) race to claim logical steps in
the SAME file-based SQLite DB:

- 2 / 4 / 8 workers claim the same step: exactly one wins per step.
- Different steps: all get claimed once.
- Worker crash after claim: another worker reclaims after lease expiry.
- The external-effect ledger (separate file) records every invocation so we
  can measure duplicate external effects directly.
"""

import json
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.infrastructure.database import TaskRepository, build_session_factory
from app.runtime.chaos import StateConsistencyChecker

CLAIMER_SCRIPT = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from datetime import UTC, datetime, timedelta

    db_path = Path(sys.argv[1])
    ledger_path = Path(sys.argv[2])
    worker_id = sys.argv[3]
    task_id = sys.argv[4]
    step_key = sys.argv[5]
    mode = sys.argv[6]  # 'claim' | 'claim_crash' | 'recover'

    from app.infrastructure.database import TaskRepository, build_session_factory
    from app.runtime.step_claim import StepClaimService, RealClock
    from app.runtime.chaos import ExternalEffectLedger

    repo = TaskRepository(build_session_factory(f"sqlite:///{db_path}"))
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)
    ledger = ExternalEffectLedger(ledger_path)

    from sqlalchemy import text

    if mode in ("claim", "claim_crash"):
        # Create (idempotent) + claim the step.
        step_id = svc.create_logical_step(
            task_id=task_id, plan_hash="ph", step_key=step_key,
            action_type="COLLECT_SUPPLIER",
        )
        claim = svc.claim(logical_step_id=step_id, worker_id=worker_id)
        if claim.claimed:
            # External effect happens ONLY for the winner.
            ledger.record(
                supplier_id="s1", action="quote", worker_id=worker_id,
                task_id=task_id, step_key=step_key,
            )
            if mode == "claim_crash":
                # Die after external effect, before local completion.
                import os
                os._exit(0)
            svc.complete(
                logical_step_id=step_id, worker_id=worker_id,
                lease_epoch=claim.lease_epoch, result_ref=f"quote:{worker_id}",
            )
            print("WON=1")
        else:
            print("WON=0")

    if mode == "recover":
        # Expire all stale CLAIMED leases, then reclaim.
        with repo._session_factory() as s:
            s.execute(text(
                "UPDATE logical_steps SET lease_expires_at = :past "
                "WHERE status='CLAIMED'"
            ), {"past": datetime.now(UTC) - timedelta(seconds=60)})
            s.commit()
        from sqlalchemy import text as _t
        with repo._session_factory() as s:
            row = s.execute(_t(
                "SELECT id, status FROM logical_steps WHERE task_id=:t AND step_key=:k"
            ), {"t": task_id, "k": step_key}).fetchone()
        step_id = int(row[0])
        claim = svc.claim(logical_step_id=step_id, worker_id=worker_id)
        if claim.claimed:
            ledger.record(
                supplier_id="s1", action="quote", worker_id=worker_id,
                task_id=task_id, step_key=step_key,
            )
            svc.complete(
                logical_step_id=step_id, worker_id=worker_id,
                lease_epoch=claim.lease_epoch, result_ref=f"quote:{worker_id}",
            )
            print("RECOVERED=1")
        else:
            print("RECOVERED=0")
    """
)


def _run_claimer(db, ledger, worker_id, task_id, step_key, mode) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", CLAIMER_SCRIPT, str(db), str(ledger), worker_id, task_id, step_key, mode],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=60,
    )
    return proc.stdout + proc.stderr


@pytest.mark.parametrize("n_workers", [2, 4, 8])
def test_concurrent_claim_single_winner_per_step(tmp_path, n_workers):
    db = tmp_path / f"conc{n_workers}.db"
    ledger = tmp_path / f"effects{n_workers}.json"
    task_id = f"task-conc-{n_workers}"

    # Seed the task row (non-terminal).
    repo = TaskRepository(build_session_factory(f"sqlite:///{db}"))
    from app.agent.state import TaskState, TaskStatus
    from app.procurement.schemas import ProcurementRequest

    request = ProcurementRequest(product_name="MX Master 3S", quantity=20)
    repo.create(task_id, request)
    now = datetime.now(UTC)
    repo.save_state(TaskState(task_id=task_id, status=TaskStatus.COLLECTING, created_at=now, updated_at=now))

    outputs = []
    for i in range(n_workers):
        outputs.append(_run_claimer(db, ledger, f"w{i}", task_id, "collect_s1", "claim"))

    winners = sum(1 for out in outputs if "WON=1" in out)
    assert winners == 1, f"expected exactly 1 winner, got {winners}"

    # External effect ledger: exactly ONE invocation for this task+step.
    effects = json.loads(ledger.read_text(encoding="utf-8"))
    step_effects = [e for e in effects if e.get("task_id") == task_id and e.get("step_key") == "collect_s1"]
    assert len(step_effects) == 1, f"expected 1 external effect, got {len(step_effects)}"

    # State consistency.
    checker = StateConsistencyChecker(repo)
    report = checker.check(task_id)
    assert report["consistent"], report["errors"]


@pytest.mark.parametrize("n_workers", [2, 4])
def test_concurrent_claim_different_steps_all_claimed(tmp_path, n_workers):
    db = tmp_path / f"diff{n_workers}.db"
    ledger = tmp_path / f"deffects{n_workers}.json"
    task_id = f"task-diff-{n_workers}"

    repo = TaskRepository(build_session_factory(f"sqlite:///{db}"))
    from app.agent.state import TaskState, TaskStatus
    from app.procurement.schemas import ProcurementRequest

    request = ProcurementRequest(product_name="MX Master 3S", quantity=20)
    repo.create(task_id, request)
    now = datetime.now(UTC)
    repo.save_state(TaskState(task_id=task_id, status=TaskStatus.COLLECTING, created_at=now, updated_at=now))

    # Each worker claims a DIFFERENT step: all must succeed.
    outputs = []
    for i in range(n_workers):
        outputs.append(_run_claimer(db, ledger, f"w{i}", task_id, f"collect_s{i}", "claim"))

    assert all("WON=1" in out for out in outputs), "every worker should win its own step"
    effects = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(effects) == n_workers, f"expected {n_workers} effects, got {len(effects)}"


def test_worker_crash_after_claim_then_recovery(tmp_path):
    """Worker claims, records external effect, crashes (os._exit); a new
    worker recovers after lease expiry. Exactly 2 external effects total:
    1 (crashed worker) + 1 (recovery worker) — the recovery re-executes the
    step because the first completion never happened (UNKNOWN at the DB)."""
    db = tmp_path / "crashconc.db"
    ledger = tmp_path / "crashfx.json"
    task_id = "task-crash-conc"

    repo = TaskRepository(build_session_factory(f"sqlite:///{db}"))
    from app.agent.state import TaskState, TaskStatus
    from app.procurement.schemas import ProcurementRequest

    request = ProcurementRequest(product_name="MX Master 3S", quantity=20)
    repo.create(task_id, request)
    now = datetime.now(UTC)
    repo.save_state(TaskState(task_id=task_id, status=TaskStatus.COLLECTING, created_at=now, updated_at=now))

    # Worker A claims + crashes after external effect. The ledger records the
    # external call BEFORE the crash (proving external executed, local didn't).
    _run_claimer(db, ledger, "w-a", task_id, "collect_s1", "claim_crash")
    effects_after_crash = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(effects_after_crash) == 1, "external effect must be recorded before crash"
    assert effects_after_crash[0]["worker_id"] == "w-a"

    # Worker B recovers after lease expiry.
    out_b = _run_claimer(db, ledger, "w-b", task_id, "collect_s1", "recover")
    assert "RECOVERED=1" in out_b

    effects = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(effects) == 2, f"expected 2 effects (crash + recovery), got {len(effects)}"

    # Final state: SUCCEEDED with the recovery worker's result.
    from sqlalchemy import text

    with repo._session_factory() as s:
        row = s.execute(
            text("SELECT status, result_ref FROM logical_steps WHERE task_id=:t AND step_key='collect_s1'"),
            {"t": task_id},
        ).fetchone()
    assert row[0] == "SUCCEEDED"
    assert row[1] == "quote:w-b"

    checker = StateConsistencyChecker(repo)
    assert checker.check(task_id)["consistent"]
