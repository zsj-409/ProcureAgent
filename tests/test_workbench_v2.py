"""Tests for split-award scoring, parallel collection, and workbench APIs."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app
from app.procurement.schemas import Preference, SupplierQuote
from app.procurement.scorer import ProcurementScorer


def _quote(supplier_id: str, price: str, stock: int, delivery: int = 3) -> SupplierQuote:
    return SupplierQuote(
        supplier_id=supplier_id,
        product_name="MX Master 3S",
        unit_price=Decimal(price),
        available_stock=stock,
        delivery_days=delivery,
        source_type="api",
    )


def test_single_award_unchanged_when_stock_covers_quantity():
    recommendation = ProcurementScorer().score(
        [_quote("a", "10.00", 50), _quote("b", "12.00", 50)],
        quantity=15,
        preference=Preference.PRICE,
    )
    assert recommendation.award_split == []
    assert recommendation.shortfall == 0
    assert recommendation.recommended_supplier == "a"
    assert recommendation.estimated_total == Decimal("150.00")


def test_split_award_when_best_supplier_stock_insufficient():
    recommendation = ProcurementScorer().score(
        [_quote("a", "10.00", 5), _quote("b", "12.00", 20)],
        quantity=15,
        preference=Preference.PRICE,
    )
    assert recommendation.award_split, "expected a split award"
    assert [(line.supplier_id, line.quantity) for line in recommendation.award_split] == [
        ("a", 5),
        ("b", 10),
    ]
    assert recommendation.estimated_total == Decimal("170.00")
    assert recommendation.shortfall == 0
    assert "Split award" in recommendation.reason


def test_split_award_reports_shortfall_when_total_stock_insufficient():
    recommendation = ProcurementScorer().score(
        [_quote("a", "10.00", 5), _quote("b", "12.00", 4)],
        quantity=15,
    )
    assert recommendation.shortfall == 15 - 9
    assert not recommendation.award_split or sum(
        line.quantity for line in recommendation.award_split
    ) == 9


def test_split_award_caps_line_count():
    quotes = [
        _quote("a", "10.00", 2),
        _quote("b", "11.00", 2),
        _quote("c", "12.00", 2),
        _quote("d", "13.00", 2),
    ]
    recommendation = ProcurementScorer().score(quotes, quantity=10)
    assert len(recommendation.award_split) <= ProcurementScorer.max_split_lines()


@pytest.fixture()
def client(tmp_path, supplier_api_url, supplier_portal_url):
    config = tmp_path / "suppliers.json"
    config.write_text(
        __import__("json").dumps(
            {
                "suppliers": [
                    {
                        "supplier_id": "mock_api_supplier",
                        "display_name": "Mock API Supplier",
                        "source_type": "api",
                        "base_url": supplier_api_url,
                        "search_endpoint": "/products/search",
                        "enabled": True,
                        "priority": 10,
                    },
                    {
                        "supplier_id": "mock_web_supplier_v2",
                        "display_name": "Card Portal",
                        "source_type": "portal",
                        "base_url": supplier_portal_url,
                        "search_path": "/portal?layout=v2",
                        "search_input_selector": None,
                        "search_button_selector": None,
                        "result_row_selector": None,
                        "name_selector": None,
                        "price_selector": None,
                        "stock_selector": None,
                        "delivery_selector": None,
                        "enabled": True,
                        "priority": 20,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        suppliers_config_path=str(config),
        chrome_cdp_url="",
        approval_threshold=Decimal(150),
        portal_frames_dir=str(tmp_path / "frames"),
        collect_concurrency=2,
    )
    return TestClient(create_app(settings))


def test_agent_run_background_flow(client):
    """Background run returns immediately, then polls to a terminal state."""

    response = client.post(
        "/api/v1/agent/run?wait=false",
        json={"message": "buy 20 MX Master 3S"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    task_id = payload["task_id"]
    assert payload["status"] == "PENDING"
    assert payload["interpreted"]["quantity"] == 20
    assert "MX Master 3S" in payload["interpreted"]["product_name"]

    terminal = None
    for _ in range(120):
        state = client.get(f"/api/v1/tasks/{task_id}").json()
        if state["status"] in {"COMPLETED", "WAITING_APPROVAL", "FAILED", "REJECTED"}:
            terminal = state["status"]
            break
        import time

        time.sleep(0.5)
    assert terminal in {"COMPLETED", "WAITING_APPROVAL"}, terminal


def test_task_list_and_overview(client):
    client.post("/api/v1/agent/run?wait=true", json={"message": "buy 2 MX Master 3S"})
    tasks = client.get("/api/v1/tasks").json()
    assert tasks, "task list should not be empty"
    first = tasks[0]
    assert {"task_id", "status", "request", "created_at"} <= set(first)

    overview = client.get("/api/v1/overview").json()
    assert overview["tasks_total"] >= 1
    assert overview["suppliers_total"] >= 2
    assert overview["api_suppliers"] >= 1
    assert overview["portal_suppliers"] >= 1


def test_reject_flow(client):
    created = client.post(
        "/api/v1/agent/run?wait=true", json={"message": "buy 5 Dell UltraSharp U2723QE"}
    ).json()
    assert created["status"] == "WAITING_APPROVAL"
    task_id = created["task_id"]

    rejected = client.post(f"/api/v1/tasks/{task_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"

    again = client.post(f"/api/v1/tasks/{task_id}/reject")
    assert again.status_code == 409

    history = client.get(f"/api/v1/tasks/{task_id}/approvals").json()
    assert any(row["decision"] == "rejected" for row in history)


def test_suppliers_health_probe(client):
    suppliers = client.get("/api/v1/suppliers").json()
    assert len(suppliers) >= 2
    by_id = {item["supplier_id"]: item for item in suppliers}
    assert by_id["mock_api_supplier"]["healthy"] is True
    card_supplier = by_id["mock_web_supplier_v2"]
    assert card_supplier["adaptive"] is True
    assert card_supplier["fixed_script"] is False


def test_portal_steps_replay_after_card_portal_task(client):
    created = client.post(
        "/api/v1/agent/run?wait=true", json={"message": "buy 2 MX Master 3S"}
    ).json()
    task_id = created["task_id"]
    replay = client.get(f"/api/v1/tasks/{task_id}/portal-steps").json()
    assert replay["suppliers"], "card portal task should produce an adaptive replay"
    (_supplier_id, manifest) = next(iter(replay["suppliers"].items()))
    steps = manifest["steps"]
    assert any(step["action"] == "fill" for step in steps)
    assert any(step["action"] == "done" for step in steps)
