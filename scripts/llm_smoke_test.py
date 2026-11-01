"""Real-LLM smoke test for ProcureAgent.

Run from the project root:
    python scripts/llm_smoke_test.py

Requires LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL environment variables.
"""

import os
import socket
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from fastapi.testclient import TestClient

from app.infrastructure.settings import Settings
from app.main import create_app
from app.suppliers.base import SupplierProfile, SupplierRegistry


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app) -> str:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("mock service did not start")
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}"


def _registry(api_url: str, portal_url: str) -> SupplierRegistry:
    return SupplierRegistry(
        profiles=[
            SupplierProfile(
                supplier_id="mock_api_supplier",
                display_name="API",
                source_type="api",
                base_url=api_url,
                search_endpoint="/products/search",
                priority=10,
            ),
            SupplierProfile(
                supplier_id="mock_web_supplier",
                display_name="Portal",
                source_type="portal",
                base_url=portal_url,
                search_path="/",
                priority=20,
            ),
            SupplierProfile(
                supplier_id="mock_api_supplier_alt",
                display_name="API Alt",
                source_type="api",
                base_url=api_url,
                search_endpoint="/products/search",
                priority=30,
            ),
        ]
    )


def main() -> int:
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL")
    model_name = os.environ.get("LLM_MODEL")
    if not (api_key and base_url and model_name):
        print("SKIPPED: real LLM configuration is incomplete")
        return 0

    from mock_services.supplier_api.main import app as api_app
    from mock_services.supplier_portal.main import app as portal_app

    api_url = _serve(api_app)
    portal_url = _serve(portal_app)
    settings = Settings(
        database_url="sqlite:///:memory:",
        llm_api_key=api_key,
        llm_base_url=base_url,
        llm_model=model_name,
        llm_timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "30")),
        approval_threshold=Decimal("1000"),
        chrome_cdp_url="",
        web_allow_launch=True,
        web_headless=True,
    )
    app = create_app(settings, registry=_registry(api_url, portal_url))

    message = "帮我采购20个MX Master 3S，预算控制在1500美元以内，优先考虑交付速度。"
    with TestClient(app) as client:
        response = client.post("/api/v1/agent/run", json={"message": message})
        if response.status_code != 200:
            print(f"SMOKE TEST FAILED: HTTP {response.status_code}: {response.text}")
            return 1
        payload = response.json()
        request = payload["request"]
        plan = payload["plan"]
        recommendation = payload["recommendation"]
        assert request["product_name"], "empty product_name"
        assert request["quantity"] == 20, request
        assert Decimal(str(request["max_budget"])) == Decimal("1500"), request
        assert request["preference"] == "DELIVERY", request
        assert plan.get("steps"), "empty plan"
        assert any(step.get("action") == "SCORE" for step in plan["steps"]), plan
        assert any(step.get("action") == "CHECK_POLICY" for step in plan["steps"]), plan
        assert recommendation and recommendation.get("recommended_supplier"), recommendation
        assert payload["summary"], "empty summary"

        trace = client.get(f"/api/v1/tasks/{payload['task_id']}/trace").json()
        components = {row["component"] for row in trace}
        assert "ProcurementInterpreter" in components, components
        assert "LLMPlanner" in components, components
        assert "RecommendationFinalizer" in components, components
        llm_rows = [row for row in trace if row.get("model")]

        print("SMOKE TEST OK")
        print(f"model: {model_name}")
        print(f"interpreted request: {request}")
        print(f"plan: {[step['action'] for step in plan['steps']]}")
        print(f"recommended supplier: {recommendation['recommended_supplier']}")
        print(f"task status: {payload['status']}")
        print(f"llm calls: {len(llm_rows)}")
        if llm_rows:
            token_input = sum(row.get("token_input") or 0 for row in llm_rows)
            token_output = sum(row.get("token_output") or 0 for row in llm_rows)
            print(f"token usage: input={token_input}, output={token_output}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
