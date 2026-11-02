"""Phase 9 — Experiment Observatory API tests.

Verifies the 5 labs serve committed artifacts (no re-run), with honest
experiment-boundary labels.
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
        database_url=f"sqlite:///{tmp_path / 'obs.db'}",
        suppliers_config_path=str(config),
        approval_threshold=Decimal(100),
        auth_enabled=False,
        demo_identity_header="X-Demo-Actor",
        chrome_cdp_url="",
        web_allow_launch=False,
    )
    return TestClient(create_app(settings))


def test_observatory_lists_committed_artifacts(tmp_path):
    with _make_client(tmp_path) as client:
        response = client.get("/api/v1/observatory/artifacts")
        assert response.status_code == 200
        body = response.json()
        names = {a["name"] for a in body["artifacts"]}
        # Phase 4-7 committed artifacts.
        assert "recovery_policy_results.json" in names
        assert "optimization_benchmark.json" in names
        assert "paired_cluster_bootstrap.json" in names


def test_chaos_lab_serves_phase4_results(tmp_path):
    with _make_client(tmp_path) as client:
        response = client.get("/api/v1/observatory/chaos-lab")
        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "manifest" in body
        # The chaos lab data has per-case rows with policy/fault fields.
        rows = body["data"].get("per_case", [])
        assert isinstance(rows, list)


def test_optimization_comparison_has_honest_boundary(tmp_path):
    with _make_client(tmp_path) as client:
        response = client.get("/api/v1/observatory/optimization-comparison")
        assert response.status_code == 200
        body = response.json()
        assert "honest_boundary" in body
        assert "jointly fully-feasible" in body["honest_boundary"]
        assert "not significant" in body["honest_boundary"]


def test_experiment_lab_serves_phase7_with_boundary(tmp_path):
    with _make_client(tmp_path) as client:
        response = client.get("/api/v1/observatory/experiment-lab")
        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "honest_boundary" in body
        assert "success saturated" in body["honest_boundary"]
        assert "REAL_LLM_NOT_RUN" in body["honest_boundary"]


def test_flight_recorder_lists_tasks(tmp_path):
    with _make_client(tmp_path) as client:
        response = client.get("/api/v1/observatory/flight-recorder")
        assert response.status_code == 200
        body = response.json()
        assert "flights" in body
        assert body["source"] == "docs/eval/live-trace"


def test_approval_inspector_empty_ok(tmp_path):
    with _make_client(tmp_path) as client:
        response = client.get("/api/v1/observatory/approval-inspector")
        assert response.status_code == 200
        body = response.json()
        assert body["approval_count"] == 0  # fresh DB, no approvals yet
