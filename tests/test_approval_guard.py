"""Phase 1 approval security tests.

Covers the P0 anonymous-approval fix:

- unauthenticated approve/reject are denied (default deny)
- body-supplied ``approver`` is never trusted as identity
- denied requests do not mutate DB state and do not write success approvals
- the workbench (reject) path does not bypass the guard
- the isolated demo identity works only through the configured header allowlist
"""

import json
from decimal import Decimal

from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app


def _make_client(tmp_path, *, auth_enabled: bool = True, demo_header: str = "") -> TestClient:
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
        database_url=f"sqlite:///{tmp_path / 'guard.db'}",
        suppliers_config_path=str(config),
        approval_threshold=Decimal(100),
        auth_enabled=auth_enabled,
        demo_identity_header=demo_header,
        chrome_cdp_url="",
        web_allow_launch=False,
    )
    return TestClient(create_app(settings))


def _task_waiting_approval(client: TestClient, tmp_path) -> str:
    """Create a task that lands in WAITING_APPROVAL via the mock API supplier.

    The mock API supplier base URL is a dead port (127.0.0.1:1); we instead
    drive the task directly through the repository to a WAITING_APPROVAL state
    with a recommendation present, which is what the approval endpoint needs.
    """

    repository = client.app.state.repository
    from datetime import UTC, datetime

    from app.agent.state import TaskState, TaskStatus
    from app.procurement.schemas import (
        ProcurementRecommendation,
        ProcurementRequest,
        SupplierQuote,
    )

    task_id = "task-guard-1"
    request = ProcurementRequest(product_name="MX Master 3S", quantity=1)
    repository.create(task_id, request)
    quote = SupplierQuote(
        supplier_id="mock_api_supplier",
        product_name="MX Master 3S",
        unit_price=Decimal("99.99"),
        currency="USD",
        available_stock=10,
        delivery_days=3,
        source_type="api",
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
    state = TaskState(
        task_id=task_id,
        status=TaskStatus.WAITING_APPROVAL,
        created_at=now,
        updated_at=now,
    )
    repository.save_state(state)
    return task_id


def test_unauthenticated_approve_denied(tmp_path):
    with _make_client(tmp_path, auth_enabled=True) as client:
        task_id = _task_waiting_approval(client, tmp_path)
        response = client.post(f"/api/v1/tasks/{task_id}/approve")
        assert response.status_code == 401
        body = response.json()
        assert body["detail"]["reason_code"] == "AUTH_REQUIRED"
        # State unchanged: still WAITING_APPROVAL, no approval row written.
        state = client.get(f"/api/v1/tasks/{task_id}").json()
        assert state["status"] == "WAITING_APPROVAL"
        assert client.get(f"/api/v1/tasks/{task_id}/approvals").json() == []


def test_unauthenticated_reject_denied(tmp_path):
    with _make_client(tmp_path, auth_enabled=True) as client:
        task_id = _task_waiting_approval(client, tmp_path)
        response = client.post(f"/api/v1/tasks/{task_id}/reject")
        assert response.status_code == 401
        body = response.json()
        assert body["detail"]["reason_code"] == "AUTH_REQUIRED"
        state = client.get(f"/api/v1/tasks/{task_id}").json()
        assert state["status"] == "WAITING_APPROVAL"
        assert client.get(f"/api/v1/tasks/{task_id}/approvals").json() == []


def test_body_approver_not_trusted(tmp_path):
    with _make_client(tmp_path, auth_enabled=True) as client:
        task_id = _task_waiting_approval(client, tmp_path)
        # Send a forged approver in the body; the guard must still deny.
        response = client.post(
            f"/api/v1/tasks/{task_id}/approve",
            json={"approver": "alice", "decision": "approved"},
        )
        assert response.status_code == 401
        state = client.get(f"/api/v1/tasks/{task_id}").json()
        assert state["status"] == "WAITING_APPROVAL"
        assert client.get(f"/api/v1/tasks/{task_id}/approvals").json() == []


def test_denied_request_does_not_mutate_db(tmp_path):
    with _make_client(tmp_path, auth_enabled=True) as client:
        task_id = _task_waiting_approval(client, tmp_path)
        before = client.get(f"/api/v1/tasks/{task_id}").json()
        client.post(f"/api/v1/tasks/{task_id}/approve")
        client.post(f"/api/v1/tasks/{task_id}/reject")
        after = client.get(f"/api/v1/tasks/{task_id}").json()
        assert after["status"] == before["status"] == "WAITING_APPROVAL"
        assert client.get(f"/api/v1/tasks/{task_id}/approvals").json() == []


def test_workbench_reject_does_not_bypass_guard(tmp_path):
    with _make_client(tmp_path, auth_enabled=True) as client:
        task_id = _task_waiting_approval(client, tmp_path)
        response = client.post(f"/api/v1/tasks/{task_id}/reject")
        assert response.status_code == 401
        state = client.get(f"/api/v1/tasks/{task_id}").json()
        assert state["status"] == "WAITING_APPROVAL"


def test_demo_identity_works_only_with_explicit_header(tmp_path):
    """With auth disabled AND an explicit demo header, the allowlisted header
    value grants approval; a random value cannot."""
    with _make_client(tmp_path, auth_enabled=False, demo_header="X-Demo-Actor") as client:
        task_id = _task_waiting_approval(client, tmp_path)

        # Wrong header value -> denied.
        bad = client.post(
            f"/api/v1/tasks/{task_id}/approve",
            headers={"X-Demo-Actor": "someone-else"},
        )
        assert bad.status_code == 401

        # Correct allowlisted demo identity -> approved.
        ok = client.post(
            f"/api/v1/tasks/{task_id}/approve",
            headers={"X-Demo-Actor": "demo-approver"},
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "COMPLETED"

        approvals = client.get(f"/api/v1/tasks/{task_id}/approvals").json()
        assert len(approvals) == 1
        assert approvals[0]["approver"] == "demo-approver"
        assert approvals[0]["recommendation_version"] is not None


def test_demo_mode_requires_both_flags(tmp_path):
    """auth_enabled=False alone (no demo header) must NOT open approval."""
    with _make_client(tmp_path, auth_enabled=False, demo_header="") as client:
        task_id = _task_waiting_approval(client, tmp_path)
        response = client.post(f"/api/v1/tasks/{task_id}/approve")
        assert response.status_code == 401
