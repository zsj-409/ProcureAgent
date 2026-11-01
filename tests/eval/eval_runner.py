"""Small, honest ProcureAgent evaluation runner.

Run from the project root:
    python tests/eval/eval_runner.py
"""

import json
import re
import socket
import threading
import time
from decimal import Decimal
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from app.agent.model import MockModelClient, ModelClient, ModelClientError, ModelResult
from app.agent.validator import PlanValidator, ReviewDecision, ValidationDecision
from app.infrastructure.settings import Settings
from app.main import create_app
from app.procurement.schemas import (
    PlanAction,
    PlanStep,
    Preference,
    ProcurementPlan,
    ProcurementRequest,
)
from app.suppliers.base import SupplierProfile, SupplierRegistry


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(app) -> str:
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}"


class ScriptedModelClient(ModelClient):
    """Deterministic, call-counting model client used only for evaluation."""

    def __init__(self, supplier_ids: list[str]):
        super().__init__(None)
        self.supplier_ids = supplier_ids
        self.calls = 0

    async def _generate(self, *, system, user, json_mode, output_model=None):
        self.calls += 1
        if not json_mode:
            return ModelResult(
                content="Scripted recommendation explanation.",
                model="scripted",
                latency_ms=1,
                token_input=1,
                token_output=1,
            )
        if output_model is ProcurementRequest:
            request = self._parse_request(user)
            if request is None:
                raise ModelClientError("missing quantity")
            return ModelResult(content=request.model_dump_json(), model="scripted", latency_ms=1)
        if output_model is ProcurementPlan:
            plan = ProcurementPlan(
                steps=[
                    PlanStep(action=PlanAction.COLLECT_SUPPLIER, supplier_id=sid)
                    for sid in self.supplier_ids
                ]
                + [
                    PlanStep(action=PlanAction.NORMALIZE),
                    PlanStep(action=PlanAction.SCORE),
                    PlanStep(action=PlanAction.CHECK_POLICY),
                    PlanStep(action=PlanAction.FINALIZE),
                ]
            )
            return ModelResult(content=plan.model_dump_json(), model="scripted", latency_ms=1)
        if output_model is ReviewDecision:
            review = ReviewDecision(decision=ValidationDecision.CONTINUE, reason="ok")
            return ModelResult(content=review.model_dump_json(), model="scripted", latency_ms=1)
        raise ModelClientError(f"unsupported output model {output_model}")

    @staticmethod
    def _parse_request(message: str) -> ProcurementRequest | None:
        match = re.search(r"(\d+)\s*(?=个|台|件|只|套|pcs|units|\s|$)", message)
        if not match:
            return None
        quantity = int(match.group(1))
        product = message[match.end():]
        product = re.sub(r"^(?:个|台|件|只|套|pcs|units)\s*", "", product)
        product = re.sub(r"(预算|优先|价格|交期|库存).*$", "", product).strip(" ，,。")
        preference = Preference.BALANCED
        if "交付" in message or "速度" in message:
            preference = Preference.DELIVERY
        elif "价格" in message:
            preference = Preference.PRICE
        elif "库存" in message:
            preference = Preference.STOCK
        return ProcurementRequest(product_name=product, quantity=quantity, preference=preference)


def default_profiles(api_url: str, portal_url: str) -> list[SupplierProfile]:
    return [
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


def profiles_for(supplier_mode: str, api_url: str, portal_url: str) -> list[SupplierProfile]:
    all_profiles = default_profiles(api_url, portal_url)
    if supplier_mode == "single_api":
        return [all_profiles[0]]
    if supplier_mode == "flaky":
        return [
            SupplierProfile(
                supplier_id="flaky_supplier",
                display_name="Flaky API",
                source_type="api",
                base_url=api_url,
                search_endpoint="/products/search-flaky",
            )
        ]
    return all_profiles


CASES = [
    {"name": "normal_balanced_qty20", "mode": "structured", "payload": {"product_name": "MX Master 3S", "quantity": 20, "preference": "BALANCED"}, "expected": "WAITING_APPROVAL", "supplier": "mock_api_supplier"},
    {"name": "price_priority_qty20", "mode": "structured", "payload": {"product_name": "MX Master 3S", "quantity": 20, "preference": "PRICE"}, "expected": "WAITING_APPROVAL", "supplier": "mock_api_supplier"},
    {"name": "delivery_priority_qty20", "mode": "structured", "payload": {"product_name": "MX Master 3S", "quantity": 20, "preference": "DELIVERY"}, "expected": "WAITING_APPROVAL", "supplier": "mock_api_supplier"},
    {"name": "stock_priority_qty20", "mode": "structured", "payload": {"product_name": "MX Master 3S", "quantity": 20, "preference": "STOCK"}, "expected": "WAITING_APPROVAL", "supplier": "mock_api_supplier"},
    {"name": "low_total_no_approval", "mode": "structured", "payload": {"product_name": "MX Master 3S", "quantity": 1}, "expected": "COMPLETED", "supplier": "mock_api_supplier"},
    {"name": "budget_exceeded", "mode": "structured", "payload": {"product_name": "MX Master 3S", "quantity": 1, "max_budget": 50}, "expected": "WAITING_APPROVAL", "supplier": "mock_api_supplier"},
    {"name": "api_supplier_failure", "mode": "structured", "payload": {"product_name": "Portal Only Keyboard", "quantity": 1}, "expected": "COMPLETED", "supplier": "mock_web_supplier"},
    {"name": "portal_supplier_failure", "mode": "structured", "payload": {"product_name": "API Only Mouse", "quantity": 1}, "expected": "COMPLETED", "supplier": "mock_api_supplier"},
    {"name": "single_supplier_valid", "mode": "structured", "payload": {"product_name": "API Only Mouse", "quantity": 1}, "expected": "COMPLETED", "supplier": "mock_api_supplier", "supplier_mode": "single_api"},
    {"name": "all_suppliers_fail", "mode": "structured", "payload": {"product_name": "No Such Product", "quantity": 1}, "expected": "FAILED", "supplier": None},
    {"name": "low_stock_fails", "mode": "structured", "payload": {"product_name": "Low Stock Mouse", "quantity": 20}, "expected": "FAILED", "supplier": None},
    {"name": "abnormal_price_fails", "mode": "structured", "payload": {"product_name": "Zero Price Mouse", "quantity": 1}, "expected": "FAILED", "supplier": None},
    {"name": "flaky_retry_recovers", "mode": "structured", "payload": {"product_name": "Flaky Recovery Mouse", "quantity": 1}, "expected": "COMPLETED", "supplier": "flaky_supplier", "supplier_mode": "flaky"},
    {"name": "agent_normal", "mode": "agent", "message": "帮我采购20个MX Master 3S，优先交付速度", "expected": "WAITING_APPROVAL", "supplier": "mock_api_supplier"},
    {"name": "agent_missing_quantity", "mode": "agent", "message": "帮我采购MX Master 3S", "expected": "INTERPRETATION_FAILED", "supplier": None},
    {"name": "agent_nonexistent_product", "mode": "agent", "message": "采购10个No Such Product", "expected": "FAILED", "supplier": None},
    {"name": "llm_unavailable_fallback", "mode": "agent", "message": "采购5个MX Master 3S", "expected": "COMPLETED", "supplier": "mock_api_supplier", "model_unavailable": True},
]


def run_plan_validation() -> dict:
    validator = PlanValidator(max_steps=20)
    profiles = [
        SupplierProfile(supplier_id="s1", display_name="S1", source_type="api", base_url="http://x", search_endpoint="/s"),
        SupplierProfile(supplier_id="s2", display_name="S2", source_type="portal", base_url="http://y", search_path="/"),
    ]
    valid = ProcurementPlan(
        steps=[
            PlanStep(action=PlanAction.COLLECT_SUPPLIER, supplier_id="s1"),
            PlanStep(action=PlanAction.COLLECT_SUPPLIER, supplier_id="s2"),
            PlanStep(action=PlanAction.SCORE),
            PlanStep(action=PlanAction.CHECK_POLICY),
        ]
    )
    invalid = ProcurementPlan(steps=[PlanStep(action=PlanAction.COLLECT_SUPPLIER, supplier_id="ghost")])
    attempts = 2
    accepted = int(validator.validate(valid, profiles) is None) + int(validator.validate(invalid, profiles) is None)
    return {"attempts": attempts, "accepted": accepted, "rate": accepted / attempts}


def main() -> None:
    from mock_services.supplier_api.main import app as api_app
    from mock_services.supplier_portal.main import app as portal_app

    api_url = serve(api_app)
    portal_url = serve(portal_app)
    db_path = Path("eval_procure_agent.db")
    results = []

    for case in CASES:
        profiles = profiles_for(case.get("supplier_mode", "default"), api_url, portal_url)
        registry = SupplierRegistry(profiles=profiles)
        supplier_ids = [p.supplier_id for p in profiles]
        if case.get("model_unavailable"):
            model = MockModelClient(unavailable=True)
        elif case["mode"] == "agent":
            model = ScriptedModelClient(supplier_ids)
        else:
            model = None
        settings = Settings(
            database_url=f"sqlite:///{db_path}",
            chrome_cdp_url="",
            web_allow_launch=True,
            web_headless=True,
            web_retries=1,
            supplier_api_timeout_seconds=5,
            executor_timeout_seconds=15,
            agent_max_attempts=2,
            agent_max_task_seconds=60,
            agent_max_replans=1,
            approval_threshold=Decimal(1000),
        )
        app = create_app(settings, model_client=model, registry=registry)
        started = time.perf_counter()
        with TestClient(app) as client:
            if case["mode"] == "agent":
                response = client.post("/api/v1/agent/run", json={"message": case["message"]})
            else:
                response = client.post("/api/v1/tasks", json=case["payload"])
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            status = body.get("status", "INTERPRETATION_FAILED" if response.status_code == 422 else "ERROR")
            recommendation = body.get("recommendation")
            task_id = body.get("task_id")
            if task_id and recommendation is None and case["supplier"] is not None:
                result_response = client.get(f"/api/v1/tasks/{task_id}/result")
                if result_response.status_code == 200:
                    recommendation = result_response.json()
            steps = client.app.state.repository.list_step_executions(task_id) if task_id else []
            completed_steps = {s.step_name for s in steps if s.status == "COMPLETED"}
            plan = body.get("plan") or {}
            plan_valid = any(
                action in [p.get("action") for p in plan.get("steps", [])]
                for action in ("SCORE", "CHECK_POLICY")
            )
            results.append(
                {
                    "name": case["name"],
                    "expected": case["expected"],
                    "expected_supplier": case["supplier"],
                    "actual": status,
                    "success": status == case["expected"],
                    "interpretation_success": case["mode"] != "agent" or response.status_code == 200,
                    "plan_valid": bool(plan_valid) if case["mode"] == "agent" else True,
                    "supplier_match": (
                        case["supplier"] is None
                        or bool(
                            recommendation
                            and recommendation.get("recommended_supplier") == case["supplier"]
                        )
                    ),
                    "steps": len(completed_steps),
                    "executor_attempts": len([s for s in steps if s.status in ("COMPLETED", "FAILED")]),
                    "executor_completed": len([s for s in steps if s.status == "COMPLETED"]),
                    "latency_ms": elapsed_ms,
                    "llm_calls": model.calls if hasattr(model, "calls") else 0,
                }
            )

    tasks = [r for r in results if r["expected"] != "INTERPRETATION_FAILED"]
    interpretation_cases = [r for r in results if any(c["name"] == r["name"] and c["mode"] == "agent" for c in CASES)]
    recovery_cases = [r for r in results if r["name"] == "flaky_retry_recovers"]
    accuracy_cases = [r for r in tasks if r["expected_supplier"] is not None]
    executor_total = sum(r["executor_attempts"] for r in results)
    executor_completed = sum(r["executor_completed"] for r in results)
    metrics = {
        "task_success_rate": round(sum(r["success"] for r in tasks) / len(tasks), 3),
        "interpretation_success_rate": round(sum(r["interpretation_success"] for r in interpretation_cases) / len(interpretation_cases), 3),
        "plan_validation_success_rate": round(sum(r["plan_valid"] for r in results if r["expected"] != "INTERPRETATION_FAILED") / len(tasks), 3),
        "executor_success_rate": round(executor_completed / executor_total, 3) if executor_total else 1.0,
        "recovery_success_rate": round(sum(r["success"] for r in recovery_cases) / len(recovery_cases), 3) if recovery_cases else 1.0,
        "recommendation_accuracy": round(sum(r["supplier_match"] for r in accuracy_cases) / len(accuracy_cases), 3) if accuracy_cases else 1.0,
        "average_steps": round(sum(r["steps"] for r in tasks) / len(tasks), 3),
        "average_latency_ms": round(sum(r["latency_ms"] for r in results) / len(results), 1),
        "average_llm_calls": round(sum(r["llm_calls"] for r in results) / len(results), 3),
    }
    plan_metrics = run_plan_validation()
    output = {"plan_validation": plan_metrics, "metrics": metrics, "cases": results}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print("\nProcureAgent v1.0 Eval")
    print(f"Task Success Rate: {metrics['task_success_rate']:.3f}")
    print(f"Interpretation Success Rate: {metrics['interpretation_success_rate']:.3f}")
    print(f"Plan Validation Success Rate: {metrics['plan_validation_success_rate']:.3f}")
    print(f"Executor Success Rate: {metrics['executor_success_rate']:.3f}")
    print(f"Recovery Success Rate: {metrics['recovery_success_rate']:.3f}")
    print(f"Recommendation Accuracy: {metrics['recommendation_accuracy']:.3f}")
    print(f"Average Steps: {metrics['average_steps']}")
    print(f"Average Latency (ms): {metrics['average_latency_ms']}")
    print(f"Average LLM Calls: {metrics['average_llm_calls']}")


if __name__ == "__main__":
    main()
