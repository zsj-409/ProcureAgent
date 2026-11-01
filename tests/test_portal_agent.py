"""Tests for the adaptive portal agent and its escalation ladder."""

import json

import pytest
from playwright.async_api import async_playwright

from app.executors.base import ExecutorTask
from app.executors.portal_agent import (
    HeuristicPortalAgent,
    PortalAgentLoop,
    parse_quote_rows,
)
from app.executors.portal_executor import PortalExecutor
from app.infrastructure.settings import Settings
from app.observability.trace import TraceRecorder
from app.suppliers.base import SupplierProfile


def _supplier(**overrides) -> SupplierProfile:
    base = {
        "supplier_id": "web_supplier",
        "display_name": "Web Supplier",
        "source_type": "portal",
        "base_url": "http://portal.test",
    }
    base.update(overrides)
    return SupplierProfile(**base)


def _settings(tmp_path, portal_url: str, **overrides) -> Settings:
    return Settings(
        chrome_cdp_url="",
        web_allow_launch=True,
        web_headless=True,
        portal_frames_dir=str(tmp_path / "frames"),
        portal_frames=True,
        executor_timeout_seconds=10,
        **overrides,
    )


def _task(supplier: SupplierProfile, product: str = "MX Master 3S") -> ExecutorTask:
    return ExecutorTask(
        task_id="task-portal-1", supplier=supplier, product_name=product, quantity=5
    )


async def _extractquote_via_loop(settings, url: str, product: str, supplier_id: str):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            loop = PortalAgentLoop(settings, HeuristicPortalAgent(), "task-portal-1", supplier_id)
            return await loop.run(page, url, product)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_heuristic_agent_handles_card_layout_v2(supplier_portal_url, tmp_path):
    """The adaptive agent extracts from the table-less v2 card layout."""

    settings = _settings(tmp_path, supplier_portal_url)
    quote = await _extractquote_via_loop(
        settings, f"{supplier_portal_url}/portal?layout=v2", "MX Master 3S", "web_v2"
    )
    assert quote["extraction"] == "adaptive"
    assert "89.99" in quote["unit_price"]
    assert quote["available_stock"] == "20"
    assert quote["delivery_days"] == "5"
    assert "MX Master 3S" in quote["product_name"]


@pytest.mark.asyncio
async def test_chaos_markup_escalates_and_self_heals(supplier_portal_url, tmp_path):
    """Chaos ids + interstitial gate: fixed script fails, heuristic agent recovers."""

    settings = _settings(tmp_path, supplier_portal_url)
    trace = TraceRecorder()
    supplier = _supplier(
        base_url=supplier_portal_url,
        search_path="/portal?chaos=demo&layout=v2",
    )
    executor = PortalExecutor(settings, trace=trace)
    result = await executor.execute(_task(supplier))

    assert result.success is True
    assert result.quote["extraction"] == "adaptive"
    actions = [(entry.action, entry.status) for entry in trace.entries_for("task-portal-1")]
    assert ("script_failed", "ERROR") in actions
    assert ("adaptive_heuristic_success", "SUCCESS") in actions

    # Frames were captured for the UI replay.
    frame_dir = tmp_path / "frames" / "task-portal-1" / "web_supplier"
    frames = sorted(frame_dir.glob("step-*.jpg"))
    assert frames, "adaptive loop should capture replay frames"
    manifest_path = tmp_path / "frames" / "task-portal-1" / "web_supplier.steps.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert any(step["action"] == "click" for step in manifest["steps"])


@pytest.mark.asyncio
async def test_no_selectors_skips_script_entirely(supplier_portal_url, tmp_path):
    """Suppliers without configured selectors go straight to the adaptive loop."""

    settings = _settings(tmp_path, supplier_portal_url)
    trace = TraceRecorder()
    supplier = _supplier(
        base_url=supplier_portal_url,
        search_path="/portal?layout=v2",
        search_input_selector=None,
        search_button_selector=None,
        result_row_selector=None,
        name_selector=None,
        price_selector=None,
        stock_selector=None,
        delivery_selector=None,
    )
    executor = PortalExecutor(settings, trace=trace)
    result = await executor.execute(_task(supplier))
    assert result.success is True
    actions = [entry.action for entry in trace.entries_for("task-portal-1")]
    assert "script_skipped" in actions
    assert "script_failed" not in actions


def test_parse_quote_rows_maps_table_columns_by_header():
    from app.executors.portal_agent import PortalPageSnapshot, SnapshotTable

    snapshot = PortalPageSnapshot(
        url="http://portal.test",
        title="t",
        tables=[
            SnapshotTable(
                headers=["Product", "Unit price", "Stock", "Delivery days"],
                rows=[["MX Master 3S", "$89.99", "20", "5"]],
            )
        ],
    )
    extraction = parse_quote_rows(snapshot, "mx master 3s")
    assert extraction is not None
    assert extraction.unit_price == "89.99"
    assert extraction.available_stock == "20"
    assert extraction.delivery_days == "5"


def test_parse_quote_rows_parses_card_text():
    from app.executors.portal_agent import PortalPageSnapshot

    snapshot = PortalPageSnapshot(
        url="http://portal.test",
        title="t",
        cards=["MX Keys S | $79.50 | In stock: 18 units | Lead time: 2 days"],
    )
    extraction = parse_quote_rows(snapshot, "MX Keys S")
    assert extraction is not None
    assert extraction.unit_price == "79.50"
    assert extraction.available_stock == "18"
    assert extraction.delivery_days == "2"


def test_parse_quote_rows_rejects_unrelated_rows():
    from app.executors.portal_agent import PortalPageSnapshot, SnapshotTable

    snapshot = PortalPageSnapshot(
        url="http://portal.test",
        title="t",
        tables=[
            SnapshotTable(
                headers=["Product", "Unit price", "Stock", "Delivery days"],
                rows=[["Dell UltraSharp U2723QE", "$629.99", "4", "6"]],
            )
        ],
    )
    assert parse_quote_rows(snapshot, "MX Master 3S") is None
