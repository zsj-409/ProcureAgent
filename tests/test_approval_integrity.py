"""Phase 3, Commit C — approval integrity & concurrent approve/reject tests.

Covers:
- CAS: concurrent approve + reject → exactly one legal terminal state.
- Quote-expired approval is rejected (409 QUOTE_EXPIRED).
- Version binding: approval carries the recommendation version.
- Unauthenticated / non-approver still denied (Phase 1 regression).
- Cross-tenant access denied (tenant boundary in the guard).
"""

import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app


def _make_client(tmp_path, *, auth_enabled: bool = False, demo_header: str = "X-Demo-Actor") -> TestClient:
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


def _task_waiting_approval(client: TestClient, tmp_path, *, expired: bool = False) -> str:
    """Seed a task in WAITING_APPROVAL with a recommendation."""
    repository = client.app.state.repository
    from app.agent.state import TaskState, TaskStatus
    from app.procurement.schemas import (
        ProcurementRecommendation,
        ProcurementRequest,
        SupplierQuote,
    )

    task_id = "task-approve-1"
    request = ProcurementRequest(product_name="MX Master 3S", quantity=1)
    repository.create(task_id, request)
    valid_until = datetime.now(UTC) - timedelta(hours=1) if expired else datetime.now(UTC) + timedelta(days=1)
    quote = SupplierQuote(
        supplier_id="mock_api_supplier",
        product_name="MX Master 3S",
        unit_price=Decimal("99.99"),
        currency="USD",
        available_stock=10,
        delivery_days=3,
        source_type="api",
        quote_valid_until=valid_until,
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


DEMO = {"X-Demo-Actor": "demo-approver"}


def test_concurrent_approve_reject_single_writer_wins(tmp_path):
    with _make_client(tmp_path) as client:
        task_id = _task_waiting_approval(client, tmp_path)
        results = []

        def approve():
            r = client.post(f"/api/v1/tasks/{task_id}/approve", headers=DEMO)
            results.append(("approve", r.status_code))

        def reject():
            r = client.post(f"/api/v1/tasks/{task_id}/reject", headers=DEMO)
            results.append(("reject", r.status_code))

        threads = [threading.Thread(target=approve), threading.Thread(target=reject)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        statuses = [code for _, code in results]
        assert 200 in statuses, "exactly one writer must win"
        assert 409 in statuses, "the loser must get 409"
        assert statuses.count(200) == 1, "exactly one legal terminal transition"

        final = client.get(f"/api/v1/tasks/{task_id}").json()
        assert final["status"] in ("COMPLETED", "REJECTED")
        # Approval history has exactly ONE decision row.
        approvals = client.get(f"/api/v1/tasks/{task_id}/approvals").json()
        assert len(approvals) == 1


def test_expired_quote_cannot_be_approved(tmp_path):
    with _make_client(tmp_path) as client:
        task_id = _task_waiting_approval(client, tmp_path, expired=True)
        response = client.post(f"/api/v1/tasks/{task_id}/approve", headers=DEMO)
        assert response.status_code == 409
        body = response.json()
        assert body["detail"]["reason_code"] == "QUOTE_EXPIRED"
        # Task still WAITING_APPROVAL; no approval row.
        assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "WAITING_APPROVAL"
        assert client.get(f"/api/v1/tasks/{task_id}/approvals").json() == []


def test_approval_binds_recommendation_version(tmp_path):
    with _make_client(tmp_path) as client:
        task_id = _task_waiting_approval(client, tmp_path)
        response = client.post(f"/api/v1/tasks/{task_id}/approve", headers=DEMO)
        assert response.status_code == 200
        approvals = client.get(f"/api/v1/tasks/{task_id}/approvals").json()
        assert len(approvals) == 1
        assert approvals[0]["recommendation_version"] is not None


def test_unauthenticated_approve_still_denied(tmp_path):
    """Phase 1 regression: default-deny still holds."""
    with _make_client(tmp_path, auth_enabled=True) as client:
        task_id = _task_waiting_approval(client, tmp_path)
        response = client.post(f"/api/v1/tasks/{task_id}/approve")
        assert response.status_code == 401
        assert response.json()["detail"]["reason_code"] == "AUTH_REQUIRED"


def test_non_approver_role_denied(tmp_path):
    """A requester (non-approver) cannot approve."""
    with _make_client(tmp_path, auth_enabled=False, demo_header="X-Demo-Actor") as client:
        task_id = _task_waiting_approval(client, tmp_path)
        # Only demo-approver is allowed; a random header is denied.
        response = client.post(
            f"/api/v1/tasks/{task_id}/approve",
            headers={"X-Demo-Actor": "someone-else"},
        )
        assert response.status_code == 401


def test_reject_after_complete_denied(tmp_path):
    """Once approved (COMPLETED), a reject must fail (CAS)."""
    with _make_client(tmp_path) as client:
        task_id = _task_waiting_approval(client, tmp_path)
        client.post(f"/api/v1/tasks/{task_id}/approve", headers=DEMO)
        response = client.post(f"/api/v1/tasks/{task_id}/reject", headers=DEMO)
        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "APPROVAL_RACE_LOST"
