"""Phase 10 — EvalOps Observatory endpoints tests.

Verifies the three new release-gate endpoints serve committed artifacts
(read-only), including the REJECTED unsafe candidate — the Workbench must
show a real test-generated rejection, never an all-green board.
"""

import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app


def _client(tmp_path: Path) -> TestClient:
    cfg = tmp_path / "suppliers.json"
    cfg.write_text(
        json.dumps(
            {
                "suppliers": [
                    {
                        "supplier_id": "mock_api_supplier",
                        "display_name": "Mock",
                        "source_type": "api",
                        "base_url": "http://127.0.0.1:1",
                        "search_endpoint": "/s",
                        "enabled": True,
                        "priority": 10,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 't.db'}",
        suppliers_config_path=str(cfg),
        approval_threshold=Decimal(100),
        auth_enabled=False,
        demo_identity_header="X-Demo-Actor",
        chrome_cdp_url="",
        web_allow_launch=False,
    )
    return TestClient(create_app(settings))


def test_release_gates_endpoint_shows_rejected(tmp_path):
    """The EvalOps view must show the REJECTED unsafe candidate (real
    test-generated data, not a mock green board)."""
    with _client(tmp_path) as client:
        r = client.get("/api/v1/observatory/evalops/release-gates")
        assert r.status_code == 200
        body = r.json()
        unsafe = body["data"]["unsafe_candidate"]
        assert unsafe["summary"]["verdict"] == "REJECTED"
        assert "hard_budget_violation" in unsafe["summary"]["failed_gates"]
        assert body["honest_boundary"]


def test_shadow_endpoint_isolated(tmp_path):
    with _client(tmp_path) as client:
        r = client.get("/api/v1/observatory/evalops/shadow")
        assert r.status_code == 200
        body = r.json()
        result = body["data"]["result"]
        assert result["isolated"] is True
        assert result["candidate_id"] == "optimizer-candidate-v1"


def test_promotion_endpoint_history(tmp_path):
    with _client(tmp_path) as client:
        r = client.get("/api/v1/observatory/evalops/promotion")
        assert r.status_code == 200
        body = r.json()
        assert "promotion" in body
        assert any(p["decision"] == "PROMOTED" for p in body["promotion"])


def test_evalops_endpoints_readonly(tmp_path):
    """All three endpoints are read-only — no mutation, no re-run."""
    with _client(tmp_path) as client:
        for path in (
            "/api/v1/observatory/evalops/release-gates",
            "/api/v1/observatory/evalops/shadow",
            "/api/v1/observatory/evalops/promotion",
        ):
            assert client.get(path).status_code == 200
