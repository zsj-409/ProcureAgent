"""PortalExecutor resource cleanup tests."""

import asyncio

import pytest

from app.errors import SupplierUnavailableError
from app.executors.base import ExecutorTask
from app.executors.portal_executor import PortalExecutor
from app.infrastructure.settings import Settings
from app.suppliers.base import SupplierProfile


def _executor() -> PortalExecutor:
    return PortalExecutor(
        Settings(
            chrome_cdp_url="",
            web_allow_launch=True,
            web_headless=True,
            web_retries=0,
            executor_timeout_seconds=15,
        )
    )


def _task(supplier_portal_url: str, product_name: str) -> ExecutorTask:
    return ExecutorTask(
        task_id="cleanup-test",
        supplier=SupplierProfile(
            supplier_id="portal",
            display_name="Portal",
            source_type="portal",
            base_url=supplier_portal_url,
            search_path="/",
        ),
        product_name=product_name,
        quantity=1,
    )


def _capture_loop_exceptions():
    loop = asyncio.get_running_loop()
    captured = []
    previous = loop.get_exception_handler()

    def handler(_loop, context):
        captured.append(context)

    loop.set_exception_handler(handler)
    return previous, captured


@pytest.mark.asyncio
async def test_portal_success_cleanup_has_no_unhandled_errors(supplier_portal_url):
    previous, captured = _capture_loop_exceptions()
    try:
        result = await _executor().execute(_task(supplier_portal_url, "MX Master 3S"))
        await asyncio.sleep(0)
    finally:
        asyncio.get_running_loop().set_exception_handler(previous)

    assert result.success is True
    assert not any("TargetClosedError" in str(context) for context in captured)
    assert not any("Task exception was never retrieved" in str(context) for context in captured)


@pytest.mark.asyncio
async def test_portal_failure_cleanup_has_no_target_closed_warning(supplier_portal_url):
    previous, captured = _capture_loop_exceptions()
    try:
        with pytest.raises(SupplierUnavailableError):
            await _executor().execute(_task(supplier_portal_url, "No Such Product"))
        await asyncio.sleep(0)
    finally:
        asyncio.get_running_loop().set_exception_handler(previous)

    assert not any("TargetClosedError" in str(context) for context in captured)
    assert not any("Task exception was never retrieved" in str(context) for context in captured)
