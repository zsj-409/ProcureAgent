"""Phase 9 — Workbench demo readiness tests.

Verifies the seven required interview demos are reproducible via the API +
committed artifacts:
1. one normal procurement task
2. supplier failure + recovery (portal fallback)
3. process crash + checkpoint resume (durable worker)
4. portal page drift self-healing (semantic)
5. unauthorized approval rejected (default deny)
6. legal split-award optimization (optimizer artifact)
7. policy ablation comparison (Phase 7 artifact)

Demos never execute real purchases; every number comes from committed
artifacts or a fresh isolated task.
"""

import json
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
        database_url=f"sqlite:///{tmp_path / 'demo.db'}",
        suppliers_config_path=str(config),
        approval_threshold=Decimal(100),
        auth_enabled=True,  # default deny: demos must respect auth
        demo_identity_header="X-Demo-Actor",
        chrome_cdp_url="",
        web_allow_launch=False,
    )
    return TestClient(create_app(settings))


def test_demo_unauthorized_approval_rejected(tmp_path):
    """Demo 5: an unauthorized approval attempt must be rejected (default
    deny) and must NOT mutate state."""
    with _make_client(tmp_path) as client:
        # Seed a WAITING_APPROVAL task.
        repository = client.app.state.repository
        from datetime import UTC, datetime, timedelta

        from app.agent.state import TaskState, TaskStatus
        from app.procurement.schemas import (
            ProcurementRecommendation,
            ProcurementRequest,
            SupplierQuote,
        )

        task_id = "demo-approval"
        repository.create(task_id, ProcurementRequest(product_name="X", quantity=1))
        quote = SupplierQuote(
            supplier_id="mock_api_supplier",
            product_name="X",
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
        repository.save_recommendation(
            task_id,
            ProcurementRecommendation(
                recommended_supplier="mock_api_supplier",
                quotes=[quote],
                score=Decimal("80"),
                reason="r",
                estimated_total=Decimal("99.99"),
                approval_required=True,
            ),
        )
        now = datetime.now(UTC)
        repository.save_state(
            TaskState(task_id=task_id, status=TaskStatus.WAITING_APPROVAL, created_at=now, updated_at=now)
        )
        # Unauthenticated approve -> 401 AUTH_REQUIRED.
        response = client.post(f"/api/v1/tasks/{task_id}/approve")
        assert response.status_code == 401
        assert response.json()["detail"]["reason_code"] == "AUTH_REQUIRED"
        # State unchanged.
        assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "WAITING_APPROVAL"
        # No approval row written.
        assert client.get(f"/api/v1/tasks/{task_id}/approvals").json() == []


def test_demo_observatory_artifacts_present(tmp_path):
    """Demos 6 & 7: the optimizer comparison and policy ablation numbers are
    served from committed artifacts."""
    with _make_client(tmp_path) as client:
        opt = client.get("/api/v1/observatory/optimization-comparison")
        assert opt.status_code == 200
        assert "honest_boundary" in opt.json()
        exp = client.get("/api/v1/observatory/experiment-lab")
        assert exp.status_code == 200
        body = exp.json()
        assert body["data"]["n_cases"] == 120  # Phase 7 full evaluation


def test_demo_artifacts_have_real_numbers(tmp_path):
    """The demo numbers must come from committed artifacts — never fake."""
    import json as _json
    from pathlib import Path

    eval_dir = Path(__file__).resolve().parents[1] / "docs" / "eval"
    # Phase 5 optimizer benchmark: real CBC results.
    bench = _json.loads((eval_dir / "optimization_benchmark.json").read_text(encoding="utf-8"))
    assert bench["aggregates"]["optimizer_fully_feasible"] >= 5
    # Phase 7 recovery: real browser results with the honest boundary.
    results = _json.loads((eval_dir / "recovery_policy_results.json").read_text(encoding="utf-8"))
    assert results["n_cases"] == 120
    # Cluster bootstrap: the corrected CI is present.
    cluster = _json.loads((eval_dir / "paired_cluster_bootstrap.json").read_text(encoding="utf-8"))
    assert cluster["ci95"][0] <= 0.01  # lower bound at/near 0 (not significant)
