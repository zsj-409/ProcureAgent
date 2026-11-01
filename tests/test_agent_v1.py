"""Tests for the final ProcureAgent v1.0 agent modules."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.agent.context import ContextBuilder
from app.agent.interpreter import ProcurementInterpreter
from app.agent.model import MockModelClient
from app.agent.orchestrator import RecommendationFinalizer
from app.agent.planner import LLMPlanner, RuleBasedPlanner
from app.agent.runtime import ExecutionBudget, TaskRuntime
from app.agent.state import TaskState, TaskStatus
from app.agent.validator import ExecutionValidator, PlanValidator, ValidationDecision
from app.infrastructure.database import TaskRepository, build_session_factory
from app.infrastructure.settings import Settings
from app.main import create_app
from app.observability.trace import TraceRecorder
from app.procurement.schemas import (
    PlanAction,
    PlanStep,
    Preference,
    ProcurementPlan,
    ProcurementRecommendation,
    ProcurementRequest,
    SupplierQuote,
)
from app.suppliers.base import SupplierProfile, SupplierRegistry


def _repo() -> TaskRepository:
    return TaskRepository(build_session_factory("sqlite:///:memory:"))


def _supplier(supplier_id: str = "mock_api_supplier") -> SupplierProfile:
    return SupplierProfile(
        supplier_id=supplier_id,
        display_name="API",
        source_type="api",
        base_url="http://supplier.test",
        search_endpoint="/search",
    )


@pytest.mark.asyncio
async def test_interpreter_structured_output():
    expected = ProcurementRequest(
        product_name="MX Master 3S",
        quantity=20,
        max_budget=Decimal(1500),
        preference=Preference.DELIVERY,
    )
    model = MockModelClient(structured_responses={"ProcurementRequest": expected})
    interpreter = ProcurementInterpreter(model)

    request = await interpreter.interpret("帮我采购20个MX Master 3S，优先交付速度")

    assert request == expected


@pytest.mark.asyncio
async def test_llm_planner_valid_plan():
    plan = ProcurementPlan(
        steps=[
            PlanStep(action=PlanAction.COLLECT_SUPPLIER, supplier_id="mock_api_supplier"),
            PlanStep(action=PlanAction.NORMALIZE),
            PlanStep(action=PlanAction.SCORE),
            PlanStep(action=PlanAction.CHECK_POLICY),
            PlanStep(action=PlanAction.FINALIZE),
        ]
    )
    model = MockModelClient(structured_responses={"ProcurementPlan": plan})
    planner = LLMPlanner(
        model=model,
        context_builder=ContextBuilder(),
        validator=PlanValidator(),
        fallback=RuleBasedPlanner(),
    )

    result = await planner.plan(
        ProcurementRequest(product_name="MX Master 3S", quantity=20),
        [_supplier()],
        "",
    )

    assert result == plan


@pytest.mark.asyncio
async def test_llm_planner_invalid_plan_falls_back():
    invalid = ProcurementPlan(
        steps=[PlanStep(action=PlanAction.COLLECT_SUPPLIER, supplier_id="ghost_supplier")]
    )
    model = MockModelClient(structured_responses={"ProcurementPlan": invalid})
    planner = LLMPlanner(
        model=model,
        context_builder=ContextBuilder(),
        validator=PlanValidator(),
        fallback=RuleBasedPlanner(),
    )

    result = await planner.plan(
        ProcurementRequest(product_name="MX Master 3S", quantity=20),
        [_supplier()],
        "",
    )

    assert [s.supplier_id for s in result.steps if s.action == PlanAction.COLLECT_SUPPLIER] == [
        "mock_api_supplier"
    ]
    assert PlanAction.SCORE in [s.action for s in result.steps]
    assert PlanAction.CHECK_POLICY in [s.action for s in result.steps]


@pytest.mark.asyncio
async def test_model_unavailable_falls_back_to_rules():
    model = MockModelClient(unavailable=True)
    planner = LLMPlanner(
        model=model,
        context_builder=ContextBuilder(),
        validator=PlanValidator(),
        fallback=RuleBasedPlanner(),
    )

    result = await planner.plan(
        ProcurementRequest(product_name="MX Master 3S", quantity=20),
        [_supplier()],
        "",
    )

    assert result.steps


def test_context_builder_does_not_include_full_trace():
    state = TaskState(task_id="t", status=TaskStatus.COLLECTING)
    context = ContextBuilder().build(
        original_goal="buy 20 MX Master 3S",
        request=ProcurementRequest(product_name="MX Master 3S", quantity=20),
        plan=ProcurementPlan(),
        state=state,
        quotes=[],
        failures={},
    )

    assert "trace" not in context.lower()
    assert "<html" not in context.lower()


@pytest.mark.asyncio
async def test_runtime_idempotency():
    runtime = TaskRuntime(_repo(), ExecutionBudget(max_attempts=2))
    calls = 0

    async def action() -> dict:
        nonlocal calls
        calls += 1
        return {"unit_price": "10"}

    first = await runtime.execute(
        task_id="task-1",
        step_name="collect_mock_api_supplier",
        supplier_id="mock_api_supplier",
        timeout_seconds=1,
        action=action,
    )
    second = await runtime.execute(
        task_id="task-1",
        step_name="collect_mock_api_supplier",
        supplier_id="mock_api_supplier",
        timeout_seconds=1,
        action=action,
    )

    assert first.success is True
    assert second.cached is True
    assert calls == 1


@pytest.mark.asyncio
async def test_runtime_retry():
    runtime = TaskRuntime(_repo(), ExecutionBudget(max_attempts=2))
    calls = 0

    async def action() -> dict:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise RuntimeError("transient")
        return {"ok": True}

    result = await runtime.execute(
        task_id="task-2",
        step_name="collect_mock_api_supplier",
        supplier_id="mock_api_supplier",
        timeout_seconds=1,
        action=action,
    )

    assert result.success is True
    assert calls == 2


def test_execution_budget_limits():
    runtime = TaskRuntime(_repo(), ExecutionBudget(max_steps=20, max_replans=1))

    assert runtime.steps_exceeded(19) is False
    assert runtime.steps_exceeded(20) is True
    assert runtime.budget.max_replans == 1


def test_execution_validator():
    request = ProcurementRequest(product_name="MX Master 3S", quantity=20)
    valid = SupplierQuote(
        supplier_id="s",
        product_name="MX Master 3S",
        unit_price=Decimal(10),
        available_stock=20,
        delivery_days=2,
        source_type="api",
    )
    low_stock = valid.model_copy(update={"available_stock": 2})

    assert ExecutionValidator().validate([valid], request).decision == ValidationDecision.CONTINUE
    low_stock_result = ExecutionValidator().validate([low_stock], request)
    assert low_stock_result.decision == ValidationDecision.CONTINUE
    assert low_stock_result.needs_review is True
    assert "split the award" in low_stock_result.reason

    empty = ExecutionValidator().validate([], request)
    assert empty.decision == ValidationDecision.FAIL


@pytest.mark.asyncio
async def test_finalizer_deterministic_template_when_model_missing():
    recommendation = ProcurementRecommendation(
        recommended_supplier="mock_api_supplier",
        quotes=[],
        score=Decimal(85),
        reason="cheapest",
        estimated_total=Decimal("160.00"),
    )
    finalizer = RecommendationFinalizer(model=None)

    text = await finalizer.generate(recommendation, ProcurementRequest(product_name="X", quantity=2))

    assert "mock_api_supplier" in text
    assert "160.00" in text


def test_persistent_trace():
    repository = _repo()
    trace = TraceRecorder(repository)

    trace.record(
        task_id="task-3",
        step="collect_mock_api_supplier",
        component="ApiExecutor",
        action="execute",
        status="SUCCESS",
        duration_ms=120,
        attempt=1,
    )

    rows = repository.list_trace("task-3")
    assert len(rows) == 1
    assert rows[0].duration_ms == 120


def test_natural_language_end_to_end(supplier_api_url):
    registry = SupplierRegistry(
        profiles=[
            SupplierProfile(
                supplier_id="mock_api_supplier",
                display_name="API",
                source_type="api",
                base_url=supplier_api_url,
                search_endpoint="/products/search",
            )
        ]
    )
    model = MockModelClient(
        structured_responses={
            "ProcurementRequest": ProcurementRequest(
                product_name="MX Master 3S",
                quantity=20,
                max_budget=Decimal(1500),
                preference=Preference.DELIVERY,
            ),
            "ProcurementPlan": ProcurementPlan(
                steps=[
                    PlanStep(action=PlanAction.COLLECT_SUPPLIER, supplier_id="mock_api_supplier"),
                    PlanStep(action=PlanAction.NORMALIZE),
                    PlanStep(action=PlanAction.SCORE),
                    PlanStep(action=PlanAction.CHECK_POLICY),
                    PlanStep(action=PlanAction.FINALIZE),
                ]
            ),
        },
        text_response="Fast delivery recommendation for MX Master 3S.",
    )
    settings = Settings(
        database_url="sqlite:///:memory:",
        approval_threshold=Decimal(1000),
        agent_max_steps=20,
        agent_max_attempts=2,
        agent_max_task_seconds=60,
        agent_max_replans=1,
    )
    app = create_app(settings, model_client=model, registry=registry)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/run",
            json={"message": "帮我采购20个MX Master 3S，预算1500美元，优先交付速度"},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "WAITING_APPROVAL"
    assert payload["approval_required"] is True
    assert payload["recommendation"]["recommended_supplier"] == "mock_api_supplier"
    assert "Fast delivery" in payload["summary"]
