"""Phase 4, Commit B — browser end-to-end tests on a REAL local HTTP portal
with REAL Playwright BrowserContexts (sync tests wrapping asyncio.run).

Each test starts an actual local HTTP portal (tests/eval/portal_world.py),
drives a real Chromium page via asyncio.run (a fresh event loop per test —
the reliable pattern for Playwright + pytest), and verifies recovery
behavior + ground-truth quote extraction through the independent evaluator.

Fault families exercised: F1 selector drift, F2 layout drift, F5 session
expiration, F7 prompt injection, F8 ambiguous/unsafe, F4 modal interruption.
"""

import asyncio
from decimal import Decimal

from app.runtime.portal_state import StateType, classify_state
from app.runtime.recovery_controller import (
    FaultType,
    RecoveryContext,
    RecoveryDecision,
    StateAwareRecoveryController,
)
from tests.eval.portal_evaluator import PortalEvaluator
from tests.eval.portal_world import make_world


async def _semantic_classify(page):
    from app.executors.portal_agent import take_snapshot

    snapshot = await take_snapshot(page)
    headers = [table.headers for table in snapshot.tables]
    has_search_input = any(e.kind == "input" for e in snapshot.elements)
    has_submit = any(e.kind == "button" for e in snapshot.elements)
    state = classify_state(
        url=snapshot.url,
        title=snapshot.title,
        body_sample=snapshot.body_sample,
        headers=headers,
        has_search_input=has_search_input,
        has_submit=has_submit,
        card_count=len(snapshot.cards),
    )
    return state, snapshot


async def _extract_quote(page, product_name):
    from app.executors.portal_agent import parse_quote_rows, take_snapshot

    snapshot = await take_snapshot(page)
    extraction = parse_quote_rows(snapshot, product_name)
    return snapshot, extraction


async def _login(page, base_url):
    await page.goto(f"{base_url}/login")
    await page.fill("input[name='user']", "tester")
    await page.fill("input[name='pass']", "secret")
    await page.click("button[type='submit']")
    await page.wait_for_url("**/search**")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# F1 — selector drift (random classes, semantic roles intact)
# ---------------------------------------------------------------------------

def test_f1_selector_drift_semantic_extraction():
    async def run():
        from playwright.async_api import async_playwright

        world = make_world(name="portal-f1", fault="F1_SELECTOR_DRIFT", fault_seed=7)
        base_url = world.start()
        evaluator = PortalEvaluator()
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(5000)
        try:
            await _login(page, base_url)
            await page.goto(f"{base_url}/search?q=MX Master 3S")

            state, _ = await _semantic_classify(page)
            assert state == StateType.RESULTS

            _, extraction = await _extract_quote(page, "MX Master 3S")
            assert extraction is not None
            result = evaluator.verify(
                method="state-aware",
                world=world,
                requested_name="MX Master 3S",
                extracted={
                    "product_name": extraction.product_name,
                    "unit_price": extraction.unit_price,
                    "available_stock": extraction.available_stock,
                    "delivery_days": extraction.delivery_days,
                    "currency": extraction.currency,
                },
                expected_sku="sku-001",
                browser_actions=2,
            )
            assert result.verified_success, result.detail
            assert result.quote_match
        finally:
            await context.close()
            await browser.close()
            await p.stop()
            world.stop()

    _run(run())


# ---------------------------------------------------------------------------
# F2 — layout drift (table -> cards)
# ---------------------------------------------------------------------------

def test_f2_layout_drift_card_extraction():
    async def run():
        from playwright.async_api import async_playwright

        world = make_world(name="portal-f2", fault="F2_LAYOUT_DRIFT", fault_seed=3, layout="cards")
        base_url = world.start()
        evaluator = PortalEvaluator()
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(5000)
        try:
            await _login(page, base_url)
            await page.goto(f"{base_url}/search?q=MX Master 3S")

            _, extraction = await _extract_quote(page, "MX Master 3S")
            assert extraction is not None
            result = evaluator.verify(
                method="state-aware",
                world=world,
                requested_name="MX Master 3S",
                extracted={
                    "product_name": extraction.product_name,
                    "unit_price": extraction.unit_price,
                    "available_stock": extraction.available_stock,
                    "delivery_days": extraction.delivery_days,
                    "currency": extraction.currency,
                },
                expected_sku="sku-001",
                browser_actions=2,
            )
            assert result.verified_success, result.detail
            assert result.quote_match
        finally:
            await context.close()
            await browser.close()
            await p.stop()
            world.stop()

    _run(run())


# ---------------------------------------------------------------------------
# F5 — session expiration (must NOT cross identity / must stop safely)
# ---------------------------------------------------------------------------

def test_f5_session_expired_safe_stop():
    async def run():
        from playwright.async_api import async_playwright

        world = make_world(name="portal-f5", fault="F5_SESSION_EXPIRED", fault_seed=4)
        base_url = world.start()
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(5000)
        try:
            await _login(page, base_url)
            await page.goto(f"{base_url}/search?q=MX Master 3S")
            state, _ = await _semantic_classify(page)
            assert state == StateType.RESULTS

            world.session_valid = False
            await page.goto(f"{base_url}/search?q=MX Master 3S")

            state, _ = await _semantic_classify(page)
            print("F5 post-expiry state:", state, "url:", page.url)
            assert state in (StateType.SESSION_EXPIRED, StateType.LOGIN), f"got {state}"

            controller = StateAwareRecoveryController()
            ctx = RecoveryContext(
                task_id="t1",
                logical_step_id=1,
                lease_epoch=1,
                plan_hash="h",
                action_input_hash="a",
                current_state=state,
                expected_state=StateType.RESULTS,
                fault=FaultType.SESSION_EXPIRED,
            )
            assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP
        finally:
            await context.close()
            await browser.close()
            await p.stop()
            world.stop()

    _run(run())


# ---------------------------------------------------------------------------
# F7 — prompt injection (must not change safety policy)
# ---------------------------------------------------------------------------

def test_f7_prompt_injection_safe():
    async def run():
        from playwright.async_api import async_playwright

        world = make_world(name="portal-f7", fault="F7_PROMPT_INJECTION", fault_seed=5)
        base_url = world.start()
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(5000)
        try:
            await _login(page, base_url)
            await page.goto(f"{base_url}/search?q=MX Master 3S")

            body = await page.inner_text("body")
            assert "Buy Now" in body

            from app.runtime.safe_actions import ActionProposal, AllowedAction, SafeActionExecutor

            executor = SafeActionExecutor()
            verdict = executor.validate(
                ActionProposal(action=AllowedAction.SELECT_PRODUCT),
                current_state=StateType.RESULTS,
                page_text=body,
            )
            assert not verdict.allowed
            assert verdict.reason_code == "PAGE_HOSTILE"

            _, extraction = await _extract_quote(page, "MX Master 3S")
            assert extraction is not None
            assert extraction.unit_price == "99.99"
        finally:
            await context.close()
            await browser.close()
            await p.stop()
            world.stop()

    _run(run())


# ---------------------------------------------------------------------------
# F8 — ambiguous / unsafe (duplicate names + high-risk submit button)
# ---------------------------------------------------------------------------

def test_f8_ambiguous_unsafe_safe_stop():
    async def run():
        from playwright.async_api import async_playwright

        world = make_world(
            name="portal-f8",
            fault="F8_AMBIGUOUS_UNSAFE",
            fault_seed=6,
            products=[
                {
                    "sku": "sku-001",
                    "name": "MX Master 3S",
                    "price": Decimal("99.99"),
                    "currency": "USD",
                    "stock": 50,
                    "delivery_days": 3,
                },
                {
                    "sku": "sku-001b",
                    "name": "MX Master 3S (Bulk)",
                    "price": Decimal("89.99"),
                    "currency": "USD",
                    "stock": 500,
                    "delivery_days": 10,
                },
            ],
        )
        base_url = world.start()
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(5000)
        try:
            await _login(page, base_url)
            await page.goto(f"{base_url}/search?q=MX Master 3S")

            body = await page.inner_text("body")
            assert "Submit Purchase Order" in body

            from app.runtime.safe_actions import ActionProposal, AllowedAction, SafeActionExecutor

            executor = SafeActionExecutor()
            verdict = executor.validate(
                ActionProposal(action=AllowedAction.SUBMIT_SEARCH),
                current_state=StateType.RESULTS,
                page_text=body,
            )
            assert not verdict.allowed
            assert verdict.reason_code == "PAGE_HOSTILE"

            controller = StateAwareRecoveryController()
            ctx = RecoveryContext(
                task_id="t1",
                logical_step_id=1,
                lease_epoch=1,
                plan_hash="h",
                action_input_hash="a",
                current_state=StateType.RESULTS,
                expected_state=StateType.QUOTE_DETAIL,
                fault=FaultType.AMBIGUOUS_ELEMENT,
            )
            assert controller.decide(ctx) == RecoveryDecision.SAFE_STOP
        finally:
            await context.close()
            await browser.close()
            await p.stop()
            world.stop()

    _run(run())


# ---------------------------------------------------------------------------
# F4 — modal interruption (authorized close only)
# ---------------------------------------------------------------------------

def test_f4_modal_close_and_continue():
    async def run():
        from playwright.async_api import async_playwright

        world = make_world(name="portal-f4", fault="F4_MODAL_INTERRUPTION", fault_seed=2)
        base_url = world.start()
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(5000)
        try:
            await _login(page, base_url)
            await page.goto(f"{base_url}/search?q=MX Master 3S")

            modal = page.locator(".cookie-modal")
            assert await modal.is_visible()
            await page.click(".cookie-accept")
            assert not await modal.is_visible()

            _, extraction = await _extract_quote(page, "MX Master 3S")
            assert extraction is not None
            assert extraction.unit_price == "99.99"
        finally:
            await context.close()
            await browser.close()
            await p.stop()
            world.stop()

    _run(run())


# ---------------------------------------------------------------------------
# Environment isolation: independent worlds have distinct snapshot hashes
# ---------------------------------------------------------------------------

def test_world_isolation_snapshot_hashes_differ():
    w1 = make_world(name="w1", fault_seed=1)
    w2 = make_world(name="w2", fault_seed=2)
    assert w1.snapshot_hash() != w2.snapshot_hash()
    w1.reset()
    assert w1.snapshot_hash() == make_world(name="w1", fault_seed=1).snapshot_hash()
