"""Integration tests for the task flow and executors."""

import json
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.executors.api_executor import ApiExecutor
from app.executors.base import ExecutorTask
from app.infrastructure.settings import Settings
from app.main import create_app
from app.suppliers.base import SupplierProfile


@pytest.mark.asyncio
async def test_api_executor_queries_supplier():
    supplier = SupplierProfile(
        supplier_id="mock_api_supplier",
        display_name="API",
        source_type="api",
        base_url="https://supplier.test",
        search_endpoint="/products/search",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://supplier.test/products/search?product_name=MX+Master+3S&quantity=20"
        return httpx.Response(
            200,
            json={
                "product_name": "MX Master 3S",
                "unit_price": "$99.99",
                "currency": "USD",
                "stock": 50,
                "delivery_days": 3,
            },
        )

    executor = ApiExecutor(timeout_seconds=5, transport=httpx.MockTransport(handler))
    result = await executor.execute(
        ExecutorTask(
            task_id="task-1",
            supplier=supplier,
            product_name="MX Master 3S",
            quantity=20,
        )
    )

    assert result.success is True
    assert result.quote["unit_price"] == "$99.99"


def _write_config(tmp_path, api_url: str, portal_url: str):
    path = tmp_path / "suppliers.json"
    path.write_text(
        json.dumps(
            {
                "suppliers": [
                    {
                        "supplier_id": "mock_api_supplier",
                        "display_name": "Mock API Supplier",
                        "source_type": "api",
                        "base_url": api_url,
                        "search_endpoint": "/products/search",
                        "enabled": True,
                        "priority": 10,
                    },
                    {
                        "supplier_id": "mock_web_supplier",
                        "display_name": "Mock Portal Supplier",
                        "source_type": "portal",
                        "base_url": portal_url,
                        "search_path": "/",
                        "enabled": True,
                        "priority": 20,
                    },
                    {
                        "supplier_id": "mock_api_supplier_alt",
                        "display_name": "Mock Alternate API Supplier",
                        "source_type": "api",
                        "base_url": api_url,
                        "search_endpoint": "/products/search",
                        "enabled": True,
                        "priority": 30,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _make_client(
    tmp_path,
    supplier_api_url: str,
    supplier_portal_url: str,
    *,
    web_allow_launch: bool = True,
) -> TestClient:
    config_path = _write_config(tmp_path, supplier_api_url, supplier_portal_url)
    settings = Settings(
        database_url="sqlite:///:memory:",
        suppliers_config_path=str(config_path),
        chrome_cdp_url="",
        web_allow_launch=web_allow_launch,
        web_headless=True,
        web_retries=1,
        supplier_api_timeout_seconds=5,
        executor_timeout_seconds=15,
        agent_max_attempts=2,
        agent_max_task_seconds=60,
        agent_max_replans=1,
        approval_threshold=Decimal(1000),
    )
    return TestClient(create_app(settings))


def test_full_task_flow_reaches_waiting_approval_and_completes(
    tmp_path, supplier_api_url, supplier_portal_url
):
    with _make_client(tmp_path, supplier_api_url, supplier_portal_url) as client:
        response = client.post(
            "/api/v1/tasks",
            json={"product_name": "MX Master 3S", "quantity": 20},
        )
        assert response.status_code == 201, response.text
        task_id = response.json()["task_id"]
        assert response.json()["status"] == "WAITING_APPROVAL"

        result = client.get(f"/api/v1/tasks/{task_id}/result")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert len(payload["quotes"]) == 3
        assert payload["partial_result"] is False
        assert payload["recommended_supplier"] in {
            "mock_api_supplier",
            "mock_web_supplier",
            "mock_api_supplier_alt",
        }

        approval = client.post(f"/api/v1/tasks/{task_id}/approve")
        assert approval.status_code == 200, approval.text
        assert approval.json()["status"] == "COMPLETED"


def test_partial_result_when_one_supplier_fails(tmp_path, supplier_portal_url):
    with _make_client(
        tmp_path,
        "http://127.0.0.1:1",
        supplier_portal_url,
    ) as client:
        response = client.post(
            "/api/v1/tasks",
            json={"product_name": "MX Master 3S", "quantity": 20},
        )
        assert response.status_code == 201, response.text
        task_id = response.json()["task_id"]

        result = client.get(f"/api/v1/tasks/{task_id}/result")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert len(payload["quotes"]) == 1
        assert payload["partial_result"] is True
        assert response.json()["status"] == "WAITING_APPROVAL"


def test_task_failed_when_all_suppliers_fail(tmp_path):
    with _make_client(
        tmp_path,
        "http://127.0.0.1:1",
        "http://127.0.0.1:1",
        web_allow_launch=False,
    ) as client:
        response = client.post(
            "/api/v1/tasks",
            json={"product_name": "MX Master 3S", "quantity": 20},
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "FAILED"
