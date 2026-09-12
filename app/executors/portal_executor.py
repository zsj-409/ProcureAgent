"""Bounded Playwright adapter for legacy supplier portals.

This is the renamed ``WebExecutor`` from the MVP. It keeps the same narrow
action vocabulary and is now configuration-driven via ``SupplierProfile``.
"""

import asyncio
import logging
from enum import Enum
from typing import Any

from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field

from ..errors import SupplierUnavailableError, WebExecutionError
from ..infrastructure.settings import Settings
from .base import BaseExecutor, ExecutorResult, ExecutorTask

logger = logging.getLogger("procureagent.portal")


class PortalActionType(str, Enum):
    """The only actions PortalExecutor supports."""

    NAVIGATE = "navigate"
    FILL = "fill"
    CLICK = "click"
    READ_TEXT = "read_text"
    EXTRACT_QUOTE = "extract_quote"


class PortalAction(BaseModel):
    """One fixed portal action."""

    action: PortalActionType
    selector: str | None = None
    value: str | None = None


class PortalTask(BaseModel):
    """A bounded portal collection task."""

    task_id: str
    supplier_id: str
    url: str
    product_name: str
    selectors: dict[str, str]
    actions: list[PortalAction]
    timeout_ms: int = Field(default=15000)


class PortalResult(BaseModel):
    """Structured result returned by PortalExecutor."""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class PortalSession:
    """Owns a Playwright browser/page for one task and closes it afterward."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._owns_browser = False
        self._owns_context = False
        self._owns_page = False

    async def open(self) -> Any:
        """Open a browser session, preferring CDP and falling back to launch."""

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        browser = None
        launched_by_us = False

        if self._settings.chrome_cdp_url:
            try:
                browser = await self._playwright.chromium.connect_over_cdp(
                    self._settings.chrome_cdp_url,
                    timeout=self._settings.executor_timeout_seconds * 1000,
                )
            except Exception:
                browser = None

        if browser is None:
            if not self._settings.web_allow_launch:
                raise WebExecutionError(
                    "CDP browser unavailable and local launch is disabled"
                )
            browser = await self._playwright.chromium.launch(
                headless=self._settings.web_headless
            )
            launched_by_us = True

        self._browser = browser
        self._owns_browser = launched_by_us
        if browser.contexts:
            self._context = browser.contexts[0]
            self._owns_context = False
        else:
            self._context = await browser.new_context()
            self._owns_context = True
        self._page = await self._context.new_page()
        self._owns_page = True
        self._page.set_default_timeout(self._settings.executor_timeout_seconds * 1000)
        return self._page

    async def close(self) -> None:
        """Close the page, context, and browser, then stop Playwright."""

        if self._page is not None and self._owns_page:
            await self._safe_close(self._page.close(), "page")
        if self._context is not None and self._owns_context:
            await self._safe_close(self._context.close(), "context")
        if self._browser is not None:
            # For an external CDP browser, ``close()`` disconnects without
            # terminating the user-owned browser process.
            await self._safe_close(self._browser.close(), "browser")
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except PlaywrightError as exc:
                logger.debug("Playwright already stopped: %s", exc)

    async def _safe_close(self, coro, label: str) -> None:
        """Close an owned resource, treating already-closed as a normal state."""

        try:
            await coro
        except PlaywrightError as exc:
            logger.debug("Resource %s already closed during cleanup: %s", label, exc)


class PortalExecutor(BaseExecutor):
    """Run the fixed portal flow and return one raw quote mapping."""

    def __init__(self, settings: Settings):
        self._settings = settings

    async def execute(self, task: ExecutorTask) -> ExecutorResult:
        portal_task = self._build_portal_task(task)
        attempts = self._settings.web_retries + 1
        last_error: str | None = None

        for attempt in range(attempts):
            session = PortalSession(self._settings)
            try:
                result = await self._run_once(session, portal_task)
                if result.success:
                    return ExecutorResult(success=True, quote=result.data)
                last_error = result.error
            except Exception as exc:  # noqa: BLE001 - bounded and re-raised at end
                last_error = str(exc)
            finally:
                await session.close()

            if attempt < attempts - 1:
                await asyncio.sleep(0.25 * (attempt + 1))

        raise SupplierUnavailableError(
            f"Portal supplier {task.supplier.supplier_id} failed after {attempts} attempts: {last_error}"
        )

    def _build_portal_task(self, task: ExecutorTask) -> PortalTask:
        supplier = task.supplier
        selectors = {
            "input": supplier.search_input_selector,
            "button": supplier.search_button_selector,
            "row": supplier.result_row_selector,
            "name": supplier.name_selector,
            "price": supplier.price_selector,
            "stock": supplier.stock_selector,
            "delivery": supplier.delivery_selector,
        }
        url = supplier.base_url.rstrip("/") + "/" + (supplier.search_path or "").lstrip("/")
        return PortalTask(
            task_id=task.task_id,
            supplier_id=supplier.supplier_id,
            url=url,
            product_name=task.product_name,
            selectors=selectors,
            actions=[
                PortalAction(action=PortalActionType.NAVIGATE, value=url),
                PortalAction(
                    action=PortalActionType.FILL,
                    selector=selectors["input"],
                    value=task.product_name,
                ),
                PortalAction(action=PortalActionType.CLICK, selector=selectors["button"]),
                PortalAction(action=PortalActionType.EXTRACT_QUOTE),
            ],
            timeout_ms=int(self._settings.executor_timeout_seconds * 1000),
        )

    async def _run_once(self, session: PortalSession, portal_task: PortalTask) -> PortalResult:
        page = await session.open()
        for action in portal_task.actions:
            if action.action == PortalActionType.NAVIGATE:
                await page.goto(action.value or portal_task.url, wait_until="domcontentloaded")
            elif action.action == PortalActionType.FILL:
                await page.fill(action.selector or "", action.value or "")
            elif action.action == PortalActionType.CLICK:
                await page.click(action.selector or "")
                await page.wait_for_load_state("domcontentloaded")
            elif action.action == PortalActionType.READ_TEXT:
                await page.inner_text(action.selector or "body")
            elif action.action == PortalActionType.EXTRACT_QUOTE:
                data = await self._extract_quote(page, portal_task)
                if data is None:
                    return PortalResult(
                        success=False,
                        error=f"No quote found for {portal_task.product_name!r}",
                    )
                return PortalResult(success=True, data=data)

        return PortalResult(success=False, error="Portal task completed without extraction")

    async def _extract_quote(self, page: Any, portal_task: PortalTask) -> dict[str, Any] | None:
        row_selector = portal_task.selectors["row"]
        try:
            await page.wait_for_selector(row_selector, timeout=portal_task.timeout_ms)
        except Exception as exc:
            raise WebExecutionError("Result table did not appear") from exc

        rows = await page.locator(row_selector).all()
        for row in rows:
            name_locator = row.locator(portal_task.selectors["name"])
            if await name_locator.count() == 0:
                continue
            name = (await name_locator.inner_text()).strip()
            if portal_task.product_name.strip().lower() not in name.lower():
                continue
            price = (await row.locator(portal_task.selectors["price"]).inner_text()).strip()
            stock = (await row.locator(portal_task.selectors["stock"]).inner_text()).strip()
            delivery = (await row.locator(portal_task.selectors["delivery"]).inner_text()).strip()
            return {
                "supplier_id": portal_task.supplier_id,
                "product_name": name,
                "unit_price": price,
                "currency": "USD",
                "available_stock": stock,
                "delivery_days": delivery,
                "source_type": "portal",
            }
        return None
