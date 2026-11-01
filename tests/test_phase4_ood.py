"""Phase 4, Commit C — OOD (out-of-distribution) portal template test.

The t2_ood template has never been seen during development:
- different header words (Item/Amount/Availability/Lead Time)
- different URL shape (no /search form on the results page)
- different element ids/classes (ood-*, data-pid)

Semantic extraction must still find the correct quote; the fixed CSS
selector (tr[data-sku]) must fail — proving the OOD sample is genuinely
unseen, not just a renamed copy of the dev template.
"""

import asyncio

from app.runtime.portal_state import StateType, classify_state
from tests.eval.portal_evaluator import PortalEvaluator
from tests.eval.portal_world import make_world


def test_ood_template_semantic_extraction():
    async def run():
        from playwright.async_api import async_playwright

        from app.executors.portal_agent import parse_quote_rows, take_snapshot

        world = make_world(
            name="portal-ood",
            fault="NONE",
            fault_seed=99,
            template_version="t2_ood",
        )
        base_url = world.start()
        evaluator = PortalEvaluator()
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(5000)
        try:
            # Login (the OOD portal has the same login form).
            await page.goto(f"{base_url}/login")
            await page.fill("input[name='user']", "tester")
            await page.fill("input[name='pass']", "secret")
            await page.click("button[type='submit']")
            await page.wait_for_url("**/search**")

            # OOD results page.
            await page.goto(f"{base_url}/search?q=MX Master 3S")
            snapshot = await take_snapshot(page)
            state = classify_state(
                url=snapshot.url,
                title=snapshot.title,
                body_sample=snapshot.body_sample,
                headers=[t.headers for t in snapshot.tables],
                has_search_input=any(e.kind == "input" for e in snapshot.elements),
                has_submit=any(e.kind == "button" for e in snapshot.elements),
                card_count=len(snapshot.cards),
            )
            # The OOD page has quote-like headers -> RESULTS.
            print("OOD state:", state, "headers:", [t.headers for t in snapshot.tables])
            assert state == StateType.RESULTS, f"got {state}"

            # Fixed CSS selector (tr[data-sku]) FAILS on OOD (ids are ood-*).
            script_ok = False
            try:
                row = page.locator("tr[data-sku]").first
                await row.wait_for(state="visible", timeout=1500)
                script_ok = True
            except Exception:
                script_ok = False
            assert not script_ok, "OOD template must NOT be extractable by the dev selector"

            # Semantic extraction works on the unseen template.
            extraction = parse_quote_rows(snapshot, "MX Master 3S")
            assert extraction is not None
            result = evaluator.verify(
                method="state-aware-ood",
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

    asyncio.run(run())


def test_ood_template_is_genuinely_unseen():
    """The OOD world's snapshot hash must differ from the dev world's —
    proving the template is not a copy of the dev template."""

    dev = make_world(name="dev", template_version="t1", fault_seed=1)
    ood = make_world(name="ood", template_version="t2_ood", fault_seed=99)
    assert dev.snapshot_hash() != ood.snapshot_hash()
