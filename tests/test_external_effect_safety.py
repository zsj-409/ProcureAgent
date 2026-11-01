"""Phase 3, Commit A — external effect safety tests.

Covers:
- claim SQL scope: expired lease of step B cannot be claimed via step A's id;
  different tasks / plan hashes do not cross-reuse.
- tool capability contract: executors declare READ_ONLY / etc.
- UNKNOWN_OUTCOME recovery: non-idempotent tool crash → UNKNOWN_OUTCOME,
  never blind retry.
- SQLite effect ledger: transactional, idempotent-committed dedup,
  cross-process no-lost-update.
"""

import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.executors.api_executor import ApiExecutor
from app.executors.base import (
    ExternalCapability,
)
from app.infrastructure.database import TaskRepository, build_session_factory
from app.runtime.effect_ledger import SqliteEffectLedger
from app.runtime.step_claim import (
    PENDING,
    SUCCEEDED,
    UNKNOWN_OUTCOME,
    RealClock,
    StepClaimService,
)


@pytest.fixture()
def repo(tmp_path):
    factory = build_session_factory(f"sqlite:///{tmp_path / 'claim.db'}")
    return TaskRepository(factory)


@pytest.fixture()
def svc(repo):
    return StepClaimService(repo, clock=RealClock(), lease_seconds=30)


# ---------------------------------------------------------------------------
# Task 1: claim SQL scope
# ---------------------------------------------------------------------------

def test_expired_lease_of_other_step_not_claimed_via_this_id(tmp_path):
    """Claiming step A must never touch step B even if B's lease expired."""
    repo = TaskRepository(build_session_factory(f"sqlite:///{tmp_path / 's.db'}"))
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)

    a = svc.create_logical_step(
        task_id="t1", plan_hash="ph", step_key="collect_a", action_type="COLLECT_SUPPLIER"
    )
    b = svc.create_logical_step(
        task_id="t1", plan_hash="ph", step_key="collect_b", action_type="COLLECT_SUPPLIER"
    )
    svc.claim(logical_step_id=a, worker_id="wa")  # A claimed by wa (live lease)

    # Force B's lease to expire, then try to claim A again — must fail
    # (A is live), and B must remain untouched.
    from sqlalchemy import text

    with repo._session_factory() as s:
        s.execute(
            text("UPDATE logical_steps SET lease_expires_at=:past WHERE id=:id"),
            {"id": b, "past": datetime.now(UTC) - timedelta(seconds=60)},
        )
        s.commit()

    claim_a = svc.claim(logical_step_id=a, worker_id="wa")
    assert claim_a.claimed is False  # A's lease is live
    status_b = svc.get_status(b)
    # B was never claimed and must stay PENDING — the failed claim on A did
    # not touch B even though B's lease had expired.
    assert status_b["status"] == PENDING


def test_same_step_name_different_tasks_do_not_interfere(tmp_path):
    repo = TaskRepository(build_session_factory(f"sqlite:///{tmp_path / 't.db'}"))
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)

    a = svc.create_logical_step(
        task_id="task-1", plan_hash="ph", step_key="collect_s1", action_type="COLLECT_SUPPLIER"
    )
    b = svc.create_logical_step(
        task_id="task-2", plan_hash="ph", step_key="collect_s1", action_type="COLLECT_SUPPLIER"
    )

    svc.claim(logical_step_id=a, worker_id="w1")
    # B is PENDING and independently claimable even though A is live.
    claim_b = svc.claim(logical_step_id=b, worker_id="w2")
    assert claim_b.claimed is True
    assert svc.get_status(b)["owner_id"] == "w2"


def test_different_plan_hash_creates_separate_steps(tmp_path):
    repo = TaskRepository(build_session_factory(f"sqlite:///{tmp_path / 'p.db'}"))
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)

    a = svc.create_logical_step(
        task_id="t1", plan_hash="ph1", step_key="collect_s1", action_type="COLLECT_SUPPLIER"
    )
    b = svc.create_logical_step(
        task_id="t1", plan_hash="ph2", step_key="collect_s1", action_type="COLLECT_SUPPLIER"
    )
    assert a != b, "different plan hash must create a different logical step"

    svc.claim(logical_step_id=a, worker_id="w1")
    # ph2 step is independent (PENDING) — claimable.
    assert svc.claim(logical_step_id=b, worker_id="w2").claimed is True


def test_completed_step_not_reclaimable(tmp_path):
    repo = TaskRepository(build_session_factory(f"sqlite:///{tmp_path / 'c.db'}"))
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)
    step = svc.create_logical_step(
        task_id="t1", plan_hash="ph", step_key="collect_s1", action_type="COLLECT_SUPPLIER"
    )
    w = svc.claim(logical_step_id=step, worker_id="w1")
    svc.complete(logical_step_id=step, worker_id="w1", lease_epoch=w.lease_epoch, result_ref="r")
    assert svc.get_status(step)["status"] == SUCCEEDED
    assert svc.claim(logical_step_id=step, worker_id="w2").claimed is False


def test_unknown_outcome_not_claimable(tmp_path):
    repo = TaskRepository(build_session_factory(f"sqlite:///{tmp_path / 'u.db'}"))
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)
    step = svc.create_logical_step(
        task_id="t1", plan_hash="ph", step_key="collect_s1", action_type="COLLECT_SUPPLIER"
    )
    w = svc.claim(logical_step_id=step, worker_id="w1")
    svc.mark_unknown(logical_step_id=step, worker_id="w1", lease_epoch=w.lease_epoch, reason="uncertain")
    assert svc.get_status(step)["status"] == UNKNOWN_OUTCOME
    assert svc.claim(logical_step_id=step, worker_id="w2").claimed is False


# ---------------------------------------------------------------------------
# Task 2: capability contract
# ---------------------------------------------------------------------------

def test_api_executor_declares_read_only_capability():
    executor = ApiExecutor(timeout_seconds=5)
    assert executor.capability == ExternalCapability.READ_ONLY_RETRYABLE
    assert executor.retry_policy.retry_safe is True
    assert executor.retry_policy.max_retries >= 1


def test_base_executor_defaults_conservative():
    from app.executors.base import BaseExecutor

    assert BaseExecutor.capability == ExternalCapability.UNKNOWN_CAPABILITY
    assert BaseExecutor.retry_policy.retry_safe is False


# ---------------------------------------------------------------------------
# Task 4: SQLite effect ledger
# ---------------------------------------------------------------------------

def test_ledger_transactional_and_idempotent(tmp_path):
    ledger = SqliteEffectLedger(tmp_path / "effects.db")

    ledger.record_invocation(supplier_id="s1", action="quote", task_id="t1", step_key="collect_s1")
    ledger.record_invocation(supplier_id="s1", action="quote", task_id="t1", step_key="collect_s1")

    assert ledger.invocation_count(task_id="t1", step_key="collect_s1") == 2
    assert ledger.committed_effect_count() == 0

    # Idempotent commit: same key + same payload -> same effect, no new row.
    _op1, committed1 = ledger.commit_effect(
        external_operation_id="op-1",
        idempotency_key="k1",
        payload={"action": "place_order", "qty": 20},
        result_hash="r1",
        task_id="t1",
        step_key="collect_s1",
    )
    assert committed1 is True
    _op2, committed2 = ledger.commit_effect(
        external_operation_id="op-1",
        idempotency_key="k1",
        payload={"action": "place_order", "qty": 20},
        result_hash="r1",
        task_id="t1",
        step_key="collect_s1",
    )
    assert committed2 is False  # duplicate key -> returns previous, no new effect
    assert ledger.committed_effect_count(task_id="t1", step_key="collect_s1") == 1


def test_ledger_rejects_key_with_different_payload(tmp_path):
    ledger = SqliteEffectLedger(tmp_path / "fx.db")
    ledger.commit_effect(
        external_operation_id="op-1",
        idempotency_key="k1",
        payload={"qty": 20},
        result_hash="r1",
    )
    with pytest.raises(ValueError, match="different payload"):
        ledger.commit_effect(
            external_operation_id="op-2",
            idempotency_key="k1",
            payload={"qty": 30},  # different payload, same key -> rejected
            result_hash="r2",
        )


def test_ledger_multiprocess_no_lost_update(tmp_path):
    """Two subprocesses write invocations concurrently; no lost update."""
    db = tmp_path / "mp_effects.db"
    ledger_path = str(db)

    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
        from app.runtime.effect_ledger import SqliteEffectLedger
        ledger = SqliteEffectLedger(sys.argv[1])
        for i in range(20):
            ledger.record_invocation(
                supplier_id="s1", action="quote",
                task_id="mp", step_key=f"step-{{sys.argv[2]}}",
            )
        """
    )

    procs = []
    for idx in range(4):
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", script, ledger_path, str(idx)],
                cwd=str(Path(__file__).resolve().parents[1]),
            )
        )
    for p in procs:
        p.wait(timeout=60)
        assert p.returncode == 0

    ledger = SqliteEffectLedger(db)
    assert ledger.invocation_count() == 80, "no lost updates across 4 processes x 20 writes"


# ---------------------------------------------------------------------------
# Task 3/5: UNKNOWN_OUTCOME recovery for non-idempotent tool
# ---------------------------------------------------------------------------

MUTATION_WORKER_SCRIPT = textwrap.dedent(
    """
    import json, os, sys
    from pathlib import Path
    from datetime import UTC, datetime, timedelta

    db_path = Path(sys.argv[1])
    ledger_path = Path(sys.argv[2])
    mode = sys.argv[3]  # 'dispatch_crash' | 'recover_nonidem' | 'recover_idem'

    from app.infrastructure.database import TaskRepository, build_session_factory
    from app.runtime.step_claim import StepClaimService, RealClock
    from app.runtime.effect_ledger import SqliteEffectLedger
    from sqlalchemy import text

    repo = TaskRepository(build_session_factory(f"sqlite:///{db_path}"))
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)
    ledger = SqliteEffectLedger(ledger_path)

    if mode == "dispatch_crash":
        # Seed a NON_IDEMPOTENT mutation step, claim it, commit the external
        # effect, then crash before local completion.
        task_id = "task-mut"
        from app.agent.state import TaskState, TaskStatus
        from app.procurement.schemas import (
            ProcurementRequest,
        )
        request = ProcurementRequest(product_name="MX", quantity=1)
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
            task_id=task_id, plan_hash="ph", step_key="place_order_s1",
            action_type="PLACE_ORDER",
        )
        svc.claim(logical_step_id=step_id, worker_id="w-dead")
        # External effect committed (non-idempotent!).
        ledger.commit_effect(
            external_operation_id="op-1",
            idempotency_key=None,
            payload={"action": "place_order"},
            result_hash="r1",
            task_id=task_id, step_key="place_order_s1",
        )
        os._exit(0)

    if mode.startswith("recover"):
        # Expire the dead worker's lease; a NEW worker decides.
        with repo._session_factory() as s:
            s.execute(text(
                "UPDATE logical_steps SET lease_expires_at = :past WHERE status='CLAIMED'"
            ), {"past": datetime.now(UTC) - timedelta(seconds=60)})
            s.commit()
        from sqlalchemy import text as _t
        with repo._session_factory() as s:
            row = s.execute(_t(
                "SELECT id, status FROM logical_steps WHERE task_id='task-mut' AND step_key='place_order_s1'"
            )).fetchone()
        step_id = int(row[0])
        if mode == "recover_nonidem":
            # Non-idempotent: worker must NOT re-execute; mark UNKNOWN_OUTCOME.
            claim = svc.claim(logical_step_id=step_id, worker_id="w-new")
            if claim.claimed:
                svc.mark_unknown(
                    logical_step_id=step_id, worker_id="w-new",
                    lease_epoch=claim.lease_epoch,
                    reason="non-idempotent mutation, outcome uncertain",
                )
                print("UNKNOWN=1")
            else:
                print("UNKNOWN=0")
        else:
            # Idempotent: same idempotency key -> committed effect dedup.
            claim = svc.claim(logical_step_id=step_id, worker_id="w-new")
            if claim.claimed:
                _op, committed = ledger.commit_effect(
                    external_operation_id="op-2",
                    idempotency_key="idem-1",
                    payload={"action": "place_order"},
                    result_hash="r2",
                    task_id="task-mut", step_key="place_order_s1",
                )
                svc.complete(
                    logical_step_id=step_id, worker_id="w-new",
                    lease_epoch=claim.lease_epoch, result_ref="quote:ok",
                )
                print("IDEM_COMPLETED=%d" % (1 if not committed else 0))
    """
)


def _run_mutation(db, ledger, mode) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", MUTATION_WORKER_SCRIPT, str(db), str(ledger), mode],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=60,
    )
    return proc.stdout + proc.stderr


def test_non_idempotent_crash_enters_unknown_not_retried(tmp_path):
    db = tmp_path / "nonidem.db"
    ledger_path = tmp_path / "nonidem_effects.db"

    _run_mutation(db, ledger_path, "dispatch_crash")

    ledger = SqliteEffectLedger(ledger_path)
    assert ledger.committed_effect_count(task_id="task-mut", step_key="place_order_s1") == 1

    out = _run_mutation(db, ledger_path, "recover_nonidem")
    assert "UNKNOWN=1" in out

    # The step is UNKNOWN_OUTCOME, NOT re-executed, NOT SUCCEEDED.
    repo = TaskRepository(build_session_factory(f"sqlite:///{db}"))
    from sqlalchemy import text

    with repo._session_factory() as s:
        row = s.execute(
            text("SELECT status FROM logical_steps WHERE task_id='task-mut' AND step_key='place_order_s1'")
        ).fetchone()
    assert row[0] == UNKNOWN_OUTCOME

    # No second committed effect (the mutation was NOT repeated).
    assert ledger.committed_effect_count(task_id="task-mut", step_key="place_order_s1") == 1


def test_idempotent_crash_dedups_committed_effect(tmp_path):
    db = tmp_path / "idem.db"
    ledger_path = tmp_path / "idem_effects.db"

    # Phase 1: a REMOTE_IDEMPOTENT mutation commits with idempotency key, then
    # crashes BEFORE local completion (step left CLAIMED by dead worker).
    ledger = SqliteEffectLedger(ledger_path)
    ledger.commit_effect(
        external_operation_id="op-1",
        idempotency_key="idem-1",
        payload={"action": "place_order"},
        result_hash="r1",
        task_id="task-mut",
        step_key="place_order_s1",
    )
    repo = TaskRepository(build_session_factory(f"sqlite:///{db}"))
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)
    from app.agent.state import TaskState, TaskStatus
    from app.procurement.schemas import (
        ProcurementRequest,
    )

    request = ProcurementRequest(product_name="MX", quantity=1)
    repo.create("task-mut", request)
    now = datetime.now(UTC)
    repo.save_state(
        TaskState(
            task_id="task-mut",
            status=TaskStatus.COLLECTING,
            created_at=now,
            updated_at=now,
        )
    )
    step_id = svc.create_logical_step(
        task_id="task-mut", plan_hash="ph", step_key="place_order_s1", action_type="PLACE_ORDER"
    )
    svc.claim(logical_step_id=step_id, worker_id="w-dead")  # no complete -> crashed

    # Recover with the SAME idempotency key -> no new committed effect.
    out = _run_mutation(db, ledger_path, "recover_idem")
    assert "IDEM_COMPLETED=1" in out  # committed=False -> deduped
    assert ledger.committed_effect_count(task_id="task-mut", step_key="place_order_s1") == 1
