"""Bounded Playwright adapter for legacy supplier portals.

Collection runs on a three-level escalation ladder:

1. **Fixed script** — the configuration-driven selector sequence. Fastest and
   fully deterministic; used whenever the supplier profile supplies selectors.
2. **Heuristic adaptive agent** — the observe-act loop in ``portal_agent``;
   needs no selectors and no LLM. Used when the script fails (structure
   drift, unknown layout) or when no selectors are configured.
3. **LLM adaptive agent** — the same loop driven by the model over the
   numbered snapshot. Only used when both previous levels fail and an LLM is
   configured.

Every escalation and every adaptive step is recorded into the trace, and the
adaptive loop saves JPEG frames for UI replay.
"""

import asyncio
import logging
from enum import Enum
from typing import Any

from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field

from ..errors import SupplierUnavailableError, WebExecutionError
from ..infrastructure.settings import Settings
from ..observability.trace import TraceRecorder
from .base import BaseExecutor, ExecutorResult, ExecutorTask
from .portal_agent import (
    HeuristicPortalAgent,
    LLMPortalAgent,
    PortalAgentLoop,
    write_manifest,
)

logger = logging.getLogger("procureagent.portal")


class PortalActionType(str, Enum):
    """The only actions the fixed portal script supports."""

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
    """A bounded portal collection task for the fixed script."""

    task_id: str
    supplier_id: str
    url: str
    product_name: str
    selectors: dict[str, str]
    actions: list[PortalAction]
    timeout_ms: int = Field(default=15000)


class PortalResult(BaseModel):
    """Structured result returned by one portal strategy."""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    strategy: str = "script"
    portal_steps: int = 0


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
    """Collect one raw quote mapping from a legacy supplier portal."""

    def __init__(
        self,
        settings: Settings,
        trace: TraceRecorder | None = None,
        model: Any = None,
    ):
        self._settings = settings
        self._trace = trace
        self._model = model

    async def execute(self, task: ExecutorTask) -> ExecutorResult:
        """Collect via one shared browser session across the escalation ladder.

        Structural failures (drifted markup, missing table) skip retries and
        escalate immediately; only transient failures (network blips) consume
        a retry. All levels reuse the same browser session so escalation costs
        one browser launch instead of one per level.
        """

        portal_task = self._build_portal_task(task)
        url = portal_task.url if portal_task else self._portal_url(task)
        session = PortalSession(self._settings)
        last_error: str | None = None
        try:
            page = await session.open()

            # Level 1: deterministic fixed script.
            if portal_task is not None:
                attempts = self._settings.web_retries + 1
                for attempt in range(attempts):
                    try:
                        result = await self._run_once(page, portal_task)
                        if result.success:
                            return ExecutorResult(success=True, quote=result.data)
                        last_error = result.error
                        # Missing table = structural drift; retrying cannot help.
                        break
                    except (WebExecutionError, PlaywrightError) as exc:
                        # Selector-level failures mean the page does not match
                        # the configured structure; retrying cannot fix drift.
                        last_error = str(exc) or type(exc).__name__
                        break
                    except Exception as exc:
                        last_error = str(exc)
                    if attempt < attempts - 1:
                        await asyncio.sleep(0.25 * (attempt + 1))
                        await page.goto(url, wait_until="domcontentloaded")
                self._trace_step(task, "script_failed", f"fixed script failed: {last_error}")
            else:
                self._trace_step(
                    task, "script_skipped", "no selectors configured; using adaptive agent"
                )

            # Level 2/3: adaptive observe-act loop on the same session.
            if not self._settings.portal_adaptive:
                raise SupplierUnavailableError(
                    f"Portal supplier {task.supplier.supplier_id} failed and adaptive mode is disabled"
                )
            return await self._run_adaptive(page, task, url)
        except (SupplierUnavailableError, WebExecutionError):
            raise
        except Exception as exc:
            raise SupplierUnavailableError(
                f"Portal supplier {task.supplier.supplier_id} failed: {exc}"
            ) from exc
        finally:
            await session.close()

    # -- adaptive escalation -------------------------------------------------

    async def _run_adaptive(
        self, page: Any, task: ExecutorTask, url: str
    ) -> ExecutorResult:
        agents: list[tuple[str, Any]] = [("heuristic", HeuristicPortalAgent())]
        if self._model is not None:
            agents.append(("llm", LLMPortalAgent(self._model)))

        last_error: str | None = None
        for level_name, agent in agents:
            try:
                loop = PortalAgentLoop(
                    self._settings,
                    agent,
                    task_id=task.task_id,
                    supplier_id=task.supplier.supplier_id,
                )
                quote = await loop.run(page, url, task.product_name)
                write_manifest(
                    self._settings,
                    task.task_id,
                    task.supplier.supplier_id,
                    loop.manifest,
                )
                self._trace_step(
                    task,
                    f"adaptive_{level_name}_success",
                    f"extracted via {level_name} agent in {quote.get('portal_steps')} steps",
                )
                return ExecutorResult(success=True, quote=quote)
            except Exception as exc:
                last_error = str(exc)
                self._trace_step(
                    task, f"adaptive_{level_name}_failed", f"{level_name} agent failed: {exc}"
                )

        raise SupplierUnavailableError(
            f"Portal supplier {task.supplier.supplier_id} failed after script + adaptive "
            f"escalation: {last_error}"
        )

    def _portal_url(self, task: ExecutorTask) -> str:
        supplier = task.supplier
        return supplier.base_url.rstrip("/") + "/" + (supplier.search_path or "").lstrip("/")

    def _trace_step(self, task: ExecutorTask, action: str, detail: str) -> None:
        if self._trace is None:
            return
        succeeded = "success" in action or "skipped" in action
        self._trace.record(
            task_id=task.task_id,
            step=f"collect_{task.supplier.supplier_id}",
            component="PortalExecutor",
            action=action,
            status="SUCCESS" if succeeded else "ERROR",
            duration_ms=0,
            error=None if succeeded else detail,
        )

    # -- fixed script --------------------------------------------------------

    def _build_portal_task(self, task: ExecutorTask) -> PortalTask | None:
        supplier = task.supplier
        if supplier.search_input_selector is None:
            return None
        selectors = {
            "input": supplier.search_input_selector,
            "button": supplier.search_button_selector,
            "row": supplier.result_row_selector,
            "name": supplier.name_selector,
            "price": supplier.price_selector,
            "stock": supplier.stock_selector,
            "delivery": supplier.delivery_selector,
        }
        url = self._portal_url(task)
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

    async def _run_once(self, page: Any, portal_task: PortalTask) -> PortalResult:
        for action in portal_task.actions:
            if action.action == PortalActionType.NAVIGATE:
                await page.goto(action.value or portal_task.url, wait_until="domcontentloaded")
            elif action.action == PortalActionType.FILL:
                await self._require_selector(page, action.selector)
                await page.fill(action.selector or "", action.value or "")
            elif action.action == PortalActionType.CLICK:
                await self._require_selector(page, action.selector)
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

    @staticmethod
    async def _require_selector(page: Any, selector: str | None, timeout_ms: int = 3000) -> None:
        """Fail fast when a configured selector is absent (structural drift)."""

        if not selector:
            raise WebExecutionError("script action has no selector configured")
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms, state="visible")
        except PlaywrightError as exc:
            raise WebExecutionError(
                f"script selector not found within {timeout_ms}ms: {selector}"
            ) from exc

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
