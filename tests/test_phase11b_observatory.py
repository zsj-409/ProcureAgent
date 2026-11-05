"""Phase 11B — Commit C: Observatory evidence endpoints tests.

The Workbench must show the REAL security-regression chain (mutation
evidence + E2E manifest), not hand-assembled PASS/FAIL boards.
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


def test_mutation_evidence_endpoint(tmp_path):
    """The mutation lab shows baseline PASS + mutants FAIL with the
    verifier's real violation reasons."""
    with _client(tmp_path) as client:
        r = client.get("/api/v1/observatory/evalops/mutation")
        assert r.status_code == 200
        body = r.json()
        runs = body["executions"]
        assert len(runs) == 4  # baseline + 3 mutants
        by_mutant = {x["mutant"]: x for x in runs}
        assert by_mutant["baseline"]["gate_status"] == "PASS"
        assert by_mutant["mutant_a_hard_budget"]["gate_status"] == "FAIL"
        assert any(
            "hard_budget_exceeded" in v
            for v in by_mutant["mutant_a_hard_budget"]["verifier"]["violations"]
        )
        assert body["honest_boundary"]


def test_e2e_manifest_endpoint(tmp_path):
    """The E2E lab shows one manifest: gate REJECTED, promotion blocked,
    active version unchanged."""
    with _client(tmp_path) as client:
        r = client.get("/api/v1/observatory/evalops/e2e")
        assert r.status_code == 200
        body = r.json()
        data = body["data"]
        assert data["release_gate"]["verdict"] == "REJECTED"
        assert data["promotion"]["promotion_blocked"] is True
        assert data["promotion"]["active_version_after"] is not None
        assert "otel_spans" in data
        assert body["honest_boundary"]


def test_evalops_endpoints_readonly(tmp_path):
    """All Commit C endpoints are read-only committed artifacts."""
    with _client(tmp_path) as client:
        for path in (
            "/api/v1/observatory/evalops/mutation",
            "/api/v1/observatory/evalops/e2e",
        ):
            assert client.get(path).status_code == 200
