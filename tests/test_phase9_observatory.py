"""Phase 9 — Experiment Observatory API tests.

Verifies the 5 labs serve committed artifacts (no re-run), with honest
experiment-boundary labels.
"""

import json
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
        database_url=f"sqlite:///{tmp_path / 'obs.db'}",
        suppliers_config_path=str(config),
        approval_threshold=Decimal(100),
        auth_enabled=auth_enabled,
        demo_identity_header=demo_header,
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


def test_all_14_artifacts_present(tmp_path):
    """P1 fix: every committed artifact must be reported present — CSV files
    included (they are marked as present, format csv, not 'missing')."""
    with _make_client(tmp_path) as client:
        response = client.get("/api/v1/observatory/artifacts")
        body = response.json()
        present = [a for a in body["artifacts"] if a["present"]]
        missing = [a["name"] for a in body["artifacts"] if not a["present"]]
        assert len(present) == 14, f"missing: {missing}"
        assert missing == []
        # CSV artifacts are marked present with format csv.
        csv_artifacts = [a for a in body["artifacts"] if a["name"].endswith(".csv")]
        for a in csv_artifacts:
            assert a["present"] is True


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
        response = client.get(
            "/api/v1/observatory/flight-recorder", headers={"X-Demo-Actor": "demo-approver"}
        )
        assert response.status_code == 200
        body = response.json()
        assert "flights" in body
        assert body["source"] == "docs/eval/live-trace"


def test_approval_inspector_empty_ok(tmp_path):
    with _make_client(tmp_path) as client:
        response = client.get(
            "/api/v1/observatory/approval-inspector", headers={"X-Demo-Actor": "demo-approver"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["approval_count"] == 0  # fresh DB, no approvals yet


# ---------------------------------------------------------------------------
# P0 — access control on live-data endpoints
# ---------------------------------------------------------------------------

def test_flight_recorder_requires_auth(tmp_path):
    """P0: live task trace must NOT be anonymous-readable."""
    with _make_client(tmp_path, auth_enabled=True) as client:
        response = client.get("/api/v1/observatory/flight-recorder")
        assert response.status_code == 401
        assert response.json()["detail"]["reason_code"] == "AUTH_REQUIRED"


def test_approval_inspector_requires_auth(tmp_path):
    """P0: live approval records must NOT be anonymous-readable."""
    with _make_client(tmp_path, auth_enabled=True) as client:
        response = client.get("/api/v1/observatory/approval-inspector")
        assert response.status_code == 401
        assert response.json()["detail"]["reason_code"] == "AUTH_REQUIRED"


def test_demo_identity_grants_access(tmp_path):
    """In the isolated demo config, the allowlisted demo header grants
    access to live-data endpoints."""
    with _make_client(tmp_path, auth_enabled=False, demo_header="X-Demo-Actor") as client:
        headers = {"X-Demo-Actor": "demo-approver"}
        response = client.get("/api/v1/observatory/flight-recorder", headers=headers)
        assert response.status_code == 200
        response = client.get("/api/v1/observatory/approval-inspector", headers=headers)
        assert response.status_code == 200


def test_artifact_endpoints_open_but_committed(tmp_path):
    """Committed experiment artifacts (no live business data) remain
    readable; they are NOT the sensitive surface."""
    with _make_client(tmp_path, auth_enabled=True) as client:
        assert client.get("/api/v1/observatory/chaos-lab").status_code == 200
        assert client.get("/api/v1/observatory/optimization-comparison").status_code == 200
        assert client.get("/api/v1/observatory/experiment-lab").status_code == 200


# ---------------------------------------------------------------------------
# Finalization Task 0 — demo security gate
# ---------------------------------------------------------------------------

def test_flight_recorder_task_id_missing_404(tmp_path):
    """Object-level check: a non-existent task_id returns 404 without
    revealing anything (no ID enumeration in the single-tenant demo)."""
    with _make_client(tmp_path) as client:
        headers = {"X-Demo-Actor": "demo-approver"}
        response = client.get(
            "/api/v1/observatory/flight-recorder?task_id=does-not-exist", headers=headers
        )
        assert response.status_code == 404


def test_flight_recorder_marks_single_tenant(tmp_path):
    """The authz model must be explicit: SINGLE_TENANT_DEMO_ONLY, not a
    claim of production multi-tenant isolation."""
    with _make_client(tmp_path) as client:
        headers = {"X-Demo-Actor": "demo-approver"}
        body = client.get("/api/v1/observatory/flight-recorder", headers=headers).json()
        assert body["authz_model"] == "SINGLE_TENANT_DEMO_ONLY"
        assert "NOT IMPLEMENTED" in body["note"]


def test_production_auth_ignores_demo_header(tmp_path):
    """0.2: with auth_enabled=True (production-ish config), the demo header
    must NOT grant access — it is not a real login credential."""
    with _make_client(tmp_path, auth_enabled=True, demo_header="X-Demo-Actor") as client:
        headers = {"X-Demo-Actor": "demo-approver"}
        for path in ("/api/v1/observatory/flight-recorder", "/api/v1/observatory/approval-inspector"):
            response = client.get(path, headers=headers)
            assert response.status_code == 401
            assert response.json()["detail"]["reason_code"] == "AUTH_REQUIRED"


def test_random_header_cannot_forge_principal(tmp_path):
    """A random header value must never forge a principal (allowlist)."""
    with _make_client(tmp_path) as client:
        response = client.get(
            "/api/v1/observatory/flight-recorder",
            headers={"X-Demo-Actor": "attacker"},
        )
        assert response.status_code == 401


def test_public_artifacts_contain_no_credentials(tmp_path):
    """0.3: the public artifact endpoints must not leak credentials,
    supplier secrets, or real approver identities."""
    with _make_client(tmp_path) as client:
        for path in (
            "/api/v1/observatory/chaos-lab",
            "/api/v1/observatory/optimization-comparison",
            "/api/v1/observatory/experiment-lab",
        ):
            body = client.get(path).json()
            s = json.dumps(body).lower()
            for secret in ("password", "api_key", "secret", "credential", "authorization: bearer"):
                assert secret not in s, f"{path} leaked {secret}"
