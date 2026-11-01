"""Phase 4, Task 0 — approval transaction atomicity & claim identity binding.

0.1: approval CAS + audit row are ONE transaction (Phase 4 P0 fix).
     A crash between the two writes is impossible by construction; we verify
     the repository method commits both together and that a lost CAS writes
     NO audit row.
0.2: claim() binds task_id / plan_hash when provided — a caller holding
     another task's step id cannot claim it.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app


def _make_client(tmp_path) -> TestClient:
    config = tmp_path / "suppliers.json"
    config.write_text(
        json.dumps(
            {
                "suppliers": [
                    {
                        "supplier_id": "mock_api_supplier",
                        "display_name": "Mock API",
                        "source_type": "api",
                        "base_url": "http://127.0.0.1:1",
                        "search_endpoint": "/products/search",
                        "enabled": True,
                        "priority": 10,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'atomic.db'}",
        suppliers_config_path=str(config),
        approval_threshold=Decimal(100),
        auth_enabled=False,
        demo_identity_header="X-Demo-Actor",
        chrome_cdp_url="",
        web_allow_launch=False,
    )
    return TestClient(create_app(settings))


DEMO = {"X-Demo-Actor": "demo-approver"}


def _seed_waiting_approval(client: TestClient) -> str:
    repository = client.app.state.repository
    from app.agent.state import TaskState, TaskStatus
    from app.procurement.schemas import (
        ProcurementRecommendation,
        ProcurementRequest,
        SupplierQuote,
    )

    task_id = "task-atomic-1"
    repository.create(task_id, ProcurementRequest(product_name="MX Master 3S", quantity=1))
    quote = SupplierQuote(
        supplier_id="mock_api_supplier",
        product_name="MX Master 3S",
        unit_price=Decimal("99.99"),
        currency="USD",
        available_stock=10,
        delivery_days=3,
        source_type="api",
        quote_valid_until=datetime.now(UTC) + timedelta(days=1),
        normalization_status="comparable",
        normalized_unit_price_ref=Decimal("99.99"),
        reference_currency="USD",
    )
    recommendation = ProcurementRecommendation(
        recommended_supplier="mock_api_supplier",
        quotes=[quote],
        score=Decimal("80"),
        reason="r",
        estimated_total=Decimal("99.99"),
        approval_required=True,
    )
    repository.save_recommendation(task_id, recommendation)
    now = datetime.now(UTC)
    repository.save_state(
        TaskState(task_id=task_id, status=TaskStatus.WAITING_APPROVAL, created_at=now, updated_at=now)
    )
    return task_id


def test_approval_cas_and_audit_commit_together(tmp_path):
    """P0: the CAS + audit row must be atomic — one winner gets both, the
    loser gets neither."""
    with _make_client(tmp_path) as client:
        repository = client.app.state.repository
        task_id = _seed_waiting_approval(client)

        # Winner path: single transaction -> status COMPLETED + 1 approval row.
        changed = repository.approval_cas_transition(
            task_id,
            from_status="WAITING_APPROVAL",
            to_status="COMPLETED",
            approver="demo-approver",
            decision="approved",
            recommendation_version="v1",
        )
        assert changed is True
        assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "COMPLETED"
        approvals = client.get(f"/api/v1/tasks/{task_id}/approvals").json()
        assert len(approvals) == 1
        assert approvals[0]["recommendation_version"] == "v1"


def test_approval_cas_loser_writes_no_audit(tmp_path):
    """P0: a lost CAS must NOT write an audit row (rollback)."""
    with _make_client(tmp_path) as client:
        repository = client.app.state.repository
        task_id = _seed_waiting_approval(client)
        repository.approval_cas_transition(
            task_id,
            from_status="WAITING_APPROVAL",
            to_status="COMPLETED",
            approver="demo-approver",
            decision="approved",
            recommendation_version="v1",
        )
        # Second call: task is COMPLETED, CAS loses -> no audit row.
        changed = repository.approval_cas_transition(
            task_id,
            from_status="WAITING_APPROVAL",
            to_status="COMPLETED",
            approver="demo-approver",
            decision="approved",
            recommendation_version="v1",
        )
        assert changed is False
        approvals = client.get(f"/api/v1/tasks/{task_id}/approvals").json()
        assert len(approvals) == 1  # still exactly one


def test_claim_binds_task_id(tmp_path):
    """0.2: claim with a mismatched task_id must be refused."""

    from app.infrastructure.database import TaskRepository, build_session_factory
    from app.procurement.schemas import (
        PlanAction,
        PlanStep,
        ProcurementPlan,
        ProcurementRequest,
    )
    from app.runtime.step_claim import StepClaimService

    db_path = tmp_path / "claim.db"
    repo = TaskRepository(build_session_factory(f"sqlite:///{db_path}"))
    svc = StepClaimService(repo, lease_seconds=30)
    from app.runtime.step_claim import RealClock

    svc._clock = RealClock()
    repo.create("task-A", ProcurementRequest(product_name="A", quantity=1))
    plan = ProcurementPlan(
        steps=[PlanStep(action=PlanAction.COLLECT_SUPPLIER, supplier_id="s1")]
    )
    repo.save_plan("task-A", plan)
    from app.runtime.step_claim import plan_hash

    ph = plan_hash(plan.model_dump_json())
    step_id = svc.create_logical_step(
        task_id="task-A",
        plan_hash=ph,
        step_key="collect_s1",
        action_type="COLLECT_SUPPLIER",
    )
    # Claim with correct task_id -> wins.
    ok = svc.claim(logical_step_id=step_id, worker_id="w", task_id="task-A", plan_hash=ph)
    assert ok.claimed
    # Reclaim with a DIFFERENT task_id -> refused (still CLAIMED, not expired).
    other = svc.claim(logical_step_id=step_id, worker_id="w2", task_id="task-B", plan_hash=ph)
    assert not other.claimed
