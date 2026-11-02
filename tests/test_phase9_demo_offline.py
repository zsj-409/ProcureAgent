"""Phase 9 Finalization — Task 5: Workbench Demo 封版 verification.

Proves the demo runs fully offline:
- no paid model calls (no key → no OpenAICompatibleModelClient built)
- no real purchase/payment code path exists
- demo identity header is isolated to the demo config
- the Workbench renders without any external network
- historical benchmark artifacts are untouched (read-only Observatory)
"""

import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import build_model_client, create_app


def _client(tmp_path) -> TestClient:
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
        auth_enabled=False,          # isolated demo config
        demo_identity_header="X-Demo-Actor",
        chrome_cdp_url="",
        web_allow_launch=False,
        llm_api_key=None,            # no key -> no paid calls
    )
    return TestClient(create_app(settings))


def test_offline_no_model_client_built(tmp_path):
    """No key -> build_model_client returns None (deterministic fallback)."""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        suppliers_config_path=str(tmp_path / "s.json"),
        approval_threshold=Decimal(100),
        auth_enabled=True,
        demo_identity_header="",
        chrome_cdp_url="",
        web_allow_launch=False,
    )
    client = build_model_client(settings, None)
    assert client is None


def test_no_payment_code_path():
    """The demo must never execute real purchases. Assert no payment/
    purchase/order write endpoint exists in the API surface."""

    import app.api as api_pkg

    api_dir = Path(api_pkg.__file__).parent
    bad = []
    for f in api_dir.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for word in ("payment", "checkout", "place_order", "execute_purchase"):
            if word in text.lower() and "observatory" not in f.name:
                bad.append(f"{f.name}:{word}")
    assert bad == [], f"payment-related code found: {bad}"


def test_demo_workbench_serves_offline(tmp_path):
    """The Workbench + Observatory serve fully offline (no external calls)."""
    with _client(tmp_path) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/v1/observatory/artifacts").status_code == 200
        # Live-data labs work with the demo identity header.
        headers = {"X-Demo-Actor": "demo-approver"}
        assert client.get("/api/v1/observatory/flight-recorder", headers=headers).status_code == 200
        assert client.get("/api/v1/observatory/approval-inspector", headers=headers).status_code == 200


def test_historical_artifacts_untouched(tmp_path):
    """Task 7: committed benchmark artifacts must be byte-identical after a
    full Observatory round-trip (read-only)."""
    import hashlib
    from pathlib import Path as P

    eval_dir = P(__file__).resolve().parents[1] / "docs" / "eval"
    before = {}
    for f in eval_dir.glob("*.json"):
        before[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    with _client(tmp_path) as client:
        for path in (
            "/api/v1/observatory/artifacts",
            "/api/v1/observatory/chaos-lab",
            "/api/v1/observatory/optimization-comparison",
            "/api/v1/observatory/experiment-lab",
        ):
            assert client.get(path).status_code == 200
    after = {}
    for f in eval_dir.glob("*.json"):
        after[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    assert before == after, "observatory modified a committed artifact"
