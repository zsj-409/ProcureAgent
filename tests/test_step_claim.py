"""Phase 2 — atomic step claim, lease, heartbeat, fencing unit tests.

Covers:
- idempotent logical-step creation (no duplicate on retry)
- atomic claim (PENDING + expired lease)
- fencing: stale worker (wrong epoch / lost lease) cannot complete
- heartbeat only by current owner
- UNKNOWN_OUTCOME is a distinct state
- concurrent claim via threads against the same file DB
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.infrastructure.database import TaskRepository, build_session_factory
from app.runtime.step_claim import (
    CLAIMED,
    FAILED_TERMINAL,
    PENDING,
    RETRY_SCHEDULED,
    SUCCEEDED,
    UNKNOWN_OUTCOME,
    RealClock,
    StepClaimService,
    action_input_hash,
    plan_hash,
)


class FixedClock:
    """Test clock: fixed now, with a step() helper to advance."""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@pytest.fixture()
def repo(tmp_path):
    db = tmp_path / "claim.db"
    factory = build_session_factory(f"sqlite:///{db}")
    return TaskRepository(factory)


@pytest.fixture()
def service(repo):
    clock = FixedClock()
    return StepClaimService(repo, clock=clock, lease_seconds=30), clock


def _mkstep(svc, key: str = "collect_s1", plan_hash: str = "ph1") -> int:
    """Short helper: create the canonical test logical step."""
    return svc.create_logical_step(
        task_id="t1",
        plan_hash=plan_hash,
        step_key=key,
        action_type="COLLECT_SUPPLIER",
    )


def test_create_logical_step_idempotent(repo, service):
    svc, _ = service
    step_id_1 = _mkstep(svc)
    step_id_2 = _mkstep(svc)
    assert step_id_1 == step_id_2
    assert svc.get_status(step_id_1)["status"] == PENDING


def test_claim_success_returns_fencing_token(repo, service):
    svc, _ = service
    step_id = _mkstep(svc)
    claim = svc.claim(logical_step_id=step_id, worker_id="w1")
    assert claim.claimed is True
    assert claim.lease_epoch == 1
    assert claim.attempt_count == 1
    status = svc.get_status(step_id)
    assert status["status"] == CLAIMED
    assert status["owner_id"] == "w1"
    assert status["lease_epoch"] == 1


def test_claim_twice_by_different_worker_fails_until_lease_expiry(repo, service):
    svc, clock = service
    step_id = _mkstep(svc)
    first = svc.claim(logical_step_id=step_id, worker_id="w1")
    assert first.claimed

    # Second worker cannot claim while lease is live.
    second = svc.claim(logical_step_id=step_id, worker_id="w2")
    assert second.claimed is False
    assert second.reason == "not_claimable"

    # After lease expiry, w2 can reclaim; epoch increments (fencing).
    clock.advance(31)
    reclaim = svc.claim(logical_step_id=step_id, worker_id="w2")
    assert reclaim.claimed is True
    assert reclaim.lease_epoch == 2
    assert reclaim.attempt_count == 2


def test_stale_worker_cannot_complete(repo, service):
    svc, clock = service
    step_id = _mkstep(svc)
    w1 = svc.claim(logical_step_id=step_id, worker_id="w1")
    clock.advance(31)
    w2 = svc.claim(logical_step_id=step_id, worker_id="w2")  # reclaim -> epoch 2

    # w1 tries to complete with its stale epoch 1 -> rejected.
    ok = svc.complete(logical_step_id=step_id, worker_id="w1", lease_epoch=w1.lease_epoch, result_ref="r1")
    assert ok is False
    status = svc.get_status(step_id)
    assert status["status"] == CLAIMED  # still owned by w2

    # w2 completes with epoch 2 -> accepted.
    ok = svc.complete(logical_step_id=step_id, worker_id="w2", lease_epoch=w2.lease_epoch, result_ref="r2")
    assert ok is True
    status = svc.get_status(step_id)
    assert status["status"] == SUCCEEDED
    assert status["result_ref"] == "r2"


def test_completed_step_not_reclaimable(repo, service):
    svc, clock = service
    step_id = _mkstep(svc)
    w1 = svc.claim(logical_step_id=step_id, worker_id="w1")
    svc.complete(logical_step_id=step_id, worker_id="w1", lease_epoch=w1.lease_epoch, result_ref="r1")
    clock.advance(100)
    reclaim = svc.claim(logical_step_id=step_id, worker_id="w2")
    assert reclaim.claimed is False  # SUCCEEDED is never reclaimable


def test_heartbeat_only_by_owner(repo, service):
    svc, clock = service
    step_id = _mkstep(svc)
    w1 = svc.claim(logical_step_id=step_id, worker_id="w1")
    assert svc.heartbeat(logical_step_id=step_id, worker_id="w1", lease_epoch=w1.lease_epoch) is True
    assert svc.heartbeat(logical_step_id=step_id, worker_id="w2", lease_epoch=w1.lease_epoch) is False
    # Advance but keep heartbeating -> lease stays live.
    clock.advance(20)
    assert svc.heartbeat(logical_step_id=step_id, worker_id="w1", lease_epoch=w1.lease_epoch) is True
    # Without heartbeat for > lease duration (30s) -> w2 can reclaim.
    clock.advance(35)  # now = t+20+35 = t+55; lease expired at t+20+30 = t+50
    reclaim = svc.claim(logical_step_id=step_id, worker_id="w2")
    assert reclaim.claimed is True
    assert reclaim.lease_epoch == 2


def test_fail_and_terminal_semantics(repo, service):
    svc, _ = service
    step_id = _mkstep(svc)
    w1 = svc.claim(logical_step_id=step_id, worker_id="w1")
    ok = svc.fail(logical_step_id=step_id, worker_id="w1", lease_epoch=w1.lease_epoch, error_code="E1")
    assert ok is True
    assert svc.get_status(step_id)["status"] == RETRY_SCHEDULED

    # Retry -> claim again -> terminal fail.
    w2 = svc.claim(logical_step_id=step_id, worker_id="w2")
    assert w2.claimed is True
    svc.fail(
        logical_step_id=step_id,
        worker_id="w2",
        lease_epoch=w2.lease_epoch,
        error_code="E2",
        terminal=True,
    )
    assert svc.get_status(step_id)["status"] == FAILED_TERMINAL
    # Terminal steps are NOT reclaimable.
    assert svc.claim(logical_step_id=step_id, worker_id="w3").claimed is False


def test_unknown_outcome_distinct_state(repo, service):
    svc, _ = service
    step_id = _mkstep(svc)
    w1 = svc.claim(logical_step_id=step_id, worker_id="w1")
    ok = svc.mark_unknown(
        logical_step_id=step_id,
        worker_id="w1",
        lease_epoch=w1.lease_epoch,
        reason="timeout-uncertain",
    )
    assert ok is True
    status = svc.get_status(step_id)
    assert status["status"] == UNKNOWN_OUTCOME
    # UNKNOWN_OUTCOME is not auto-claimed by the normal claim path.
    w2 = svc.claim(logical_step_id=step_id, worker_id="w2")
    assert w2.claimed is False


def test_concurrent_claim_single_winner(tmp_path):
    """Two threads racing to claim the same step -> exactly one winner."""
    db = tmp_path / "race.db"
    factory = build_session_factory(f"sqlite:///{db}")
    repo = TaskRepository(factory)
    svc = StepClaimService(repo, clock=RealClock(), lease_seconds=30)
    step_id = _mkstep(svc)

    winners = []
    barrier = threading.Barrier(2)

    def worker(name: str):
        barrier.wait()
        claim = svc.claim(logical_step_id=step_id, worker_id=name)
        if claim.claimed:
            winners.append(name)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1
    status = svc.get_status(step_id)
    assert status["owner_id"] == winners[0]
    assert status["lease_epoch"] == 1


def test_hash_helpers_stable():
    h1 = plan_hash('{"steps": []}')
    h2 = plan_hash('{"steps": []}')
    assert h1 == h2
    a1 = action_input_hash(product_name="MX", quantity=20, supplier_id="s1")
    a2 = action_input_hash(product_name="MX", quantity=20, supplier_id="s1")
    assert a1 == a2
    a3 = action_input_hash(product_name="MX", quantity=21, supplier_id="s1")
    assert a1 != a3
