"""Planner unit tests."""

import pytest

from app.agent.planner import RuleBasedPlanner
from app.procurement.schemas import PlanAction, ProcurementRequest
from app.suppliers.base import SupplierProfile


@pytest.mark.asyncio
async def test_rule_based_planner_generates_expected_steps():
    planner = RuleBasedPlanner()
    request = ProcurementRequest(product_name="MX Master 3S", quantity=20)
    suppliers = [
        SupplierProfile(
            supplier_id="mock_api_supplier",
            display_name="API",
            source_type="api",
            base_url="http://api",
            search_endpoint="/search",
        ),
        SupplierProfile(
            supplier_id="mock_web_supplier",
            display_name="Portal",
            source_type="portal",
            base_url="http://portal",
            search_path="/",
        ),
    ]

    plan = await planner.plan(request, suppliers, "")

    assert [(s.action, s.supplier_id) for s in plan.steps] == [
        (PlanAction.COLLECT_SUPPLIER, "mock_api_supplier"),
        (PlanAction.COLLECT_SUPPLIER, "mock_web_supplier"),
        (PlanAction.NORMALIZE, None),
        (PlanAction.SCORE, None),
        (PlanAction.CHECK_POLICY, None),
        (PlanAction.FINALIZE, None),
    ]
