"""Adaptive portal agent: observe page state, choose bounded actions, extract.

This module restores the core ``browser-use`` insight that was lost in the
MVP: a legacy portal should not require a hard-coded selector script. Instead
the agent

1. takes a compact, numbered DOM snapshot (inputs, buttons, selects, tables,
   card grids) — the snapshot itself is the addressing scheme, so randomized
   or drifting markup cannot break it;
2. decides the next action from a strict vocabulary (``fill`` / ``select`` /
   ``click`` / ``press_enter`` / ``done``) either by deterministic heuristics
   or, as a second escalation level, by an LLM;
3. extracts the quote deterministically — table columns are mapped by header
   keywords, card text falls back to anchored regex parsing;
4. runs inside hard guards: bounded actions, form values restricted to the
   product name and enumerated select options (page text is untrusted and can
   never inject a form value), no navigation actions at all.

The loop records a JPEG frame after every action so the workbench UI can
replay portal automation visually.
"""

import asyncio
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..errors import WebExecutionError
from ..infrastructure.settings import Settings

logger = logging.getLogger("procureagent.portal_agent")

MAX_SNAPSHOT_ELEMENTS = 14
MAX_TABLE_ROWS = 10
MAX_CARDS = 12
PRICE_RE = re.compile(r"(?:[$¥€£]|USD|CNY|usd|cny)\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
STOCK_RE = re.compile(r"(?:库存|in stock|stock|available|available units)[^0-9]{0,6}([0-9]+)", re.IGNORECASE)
DELIVERY_RE = re.compile(r"([0-9]+)\s*(?:天|日|days?)", re.IGNORECASE)
HEADER_HINTS = {
    "name": ("name", "product", "商品", "产品", "名称"),
    "price": ("price", "单价", "价格", "amount"),
    "stock": ("stock", "库存", "available", "数量"),
    "delivery": ("delivery", "lead", "交期", "天数", "days"),
}


class SnapshotElement(BaseModel):
    """One interactive element in the numbered page snapshot.

    ``index`` is the global snapshot position (used in prompts); ``ordinal``
    is the element's position within its own kind, which is how the loop
    addresses it with Playwright locators.
    """

    index: int
    ordinal: int = 0
    kind: str  # input | select | button | link
    tag: str
    text: str | None = None
    placeholder: str | None = None
    label: str | None = None
    name: str | None = None
    input_type: str | None = None
    options: list[str] = Field(default_factory=list)
    value: str | None = None


class SnapshotTable(BaseModel):
    """One table-like structure with rows of raw cell text."""

    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class PortalPageSnapshot(BaseModel):
    """Compact semantic observation of the current page."""

    url: str
    title: str
    elements: list[SnapshotElement] = Field(default_factory=list)
    tables: list[SnapshotTable] = Field(default_factory=list)
    cards: list[str] = Field(default_factory=list)
    body_sample: str = ""

    def render(self) -> str:
        """Render the snapshot as bounded text for an LLM prompt."""

        lines = [f"URL: {self.url}", f"TITLE: {self.title}"]
        if self.elements:
            lines.append("INTERACTIVE ELEMENTS:")
            for element in self.elements:
                bits = [f"#{element.index}", element.kind.upper(), element.tag]
                if element.text:
                    bits.append(f"text={element.text!r}")
                if element.placeholder:
                    bits.append(f"placeholder={element.placeholder!r}")
                if element.label:
                    bits.append(f"label={element.label!r}")
                if element.name:
                    bits.append(f"name={element.name!r}")
                if element.input_type:
                    bits.append(f"type={element.input_type}")
                if element.value:
                    bits.append(f"value={element.value!r}")
                if element.options:
                    bits.append(f"options={element.options}")
                lines.append("  " + " ".join(str(bit) for bit in bits))
        for pos, table in enumerate(self.tables):
            lines.append(f"TABLE[{pos}] headers={table.headers}")
            for row in table.rows:
                lines.append(f"  row: {row}")
        for pos, card in enumerate(self.cards):
            lines.append(f"CARD[{pos}]: {card}")
        if self.body_sample:
            lines.append(f"BODY SAMPLE: {self.body_sample}")
        return "\n".join(lines[:80])


class PortalAgentAction(BaseModel):
    """One strictly-validated agent action."""

    action: str  # fill | select | click | press_enter | done
    element_index: int | None = None
    select_value: str | None = None
    note: str | None = None


class PortalAgentStep(BaseModel):
    """Record of one executed loop step (for the UI replay)."""

    sequence: int
    action: str
    detail: str
    url: str
    frame_path: str | None = None


class PortalQuoteExtraction(BaseModel):
    """Deterministic quote parsed from page rows/cards."""

    product_name: str
    unit_price: str
    currency: str
    available_stock: str
    delivery_days: str


_SNAPSHOT_JS = r"""
() => {
  const visible = (el) => !!(el.offsetParent || el.getClientRects().length);
  const cap = (s, n) => (s || '').trim().slice(0, n);
  const elements = [];
  const kindCounters = { input: 0, select: 0, button: 0, link: 0 };
  const nodes = [...document.querySelectorAll(
    'input, textarea, select, button, [role="button"], input[type="submit"], a[href]'
  )].filter(visible);
  for (const el of nodes.slice(0, 40)) {
    const tag = el.tagName.toLowerCase();
    const text = cap(el.innerText || el.value || '', 40);
    if (tag === 'a' && !text) continue;
    if (tag === 'input' && el.type === 'hidden') continue;
    const label = el.labels && el.labels[0] ? cap(el.labels[0].innerText, 40) : null;
    const kind = (tag === 'input' || tag === 'textarea')
      ? 'input'
      : (tag === 'select' ? 'select' : (tag === 'a' ? 'link' : 'button'));
    elements.push({
      index: elements.length,
      ordinal: kindCounters[kind]++,
      kind,
      tag,
      text: kind === 'input' ? null : (text || null),
      placeholder: cap(el.getAttribute('placeholder'), 40) || null,
      label,
      name: cap(el.getAttribute('name'), 30) || null,
      input_type: el.type || null,
      options: tag === 'select'
        ? [...el.options].slice(0, 8).map((o) => (o.value || o.textContent.trim()).slice(0, 30))
        : [],
      value: el.value ? cap(el.value, 30) : null,
    });
    if (elements.length >= """ + str(MAX_SNAPSHOT_ELEMENTS) + r""") break;
  }
  const tables = [...document.querySelectorAll('table')].slice(0, 3).map((t) => ({
    headers: [...t.querySelectorAll('th')].map((th) => cap(th.innerText, 24)).slice(0, 8),
    rows: [...t.querySelectorAll('tr')].slice(0, """ + str(MAX_TABLE_ROWS + 1) + r""")
      .filter((tr) => tr.querySelector('td'))
      .map((tr) => [...tr.querySelectorAll('td')].map((td) => cap(td.innerText, 40)).slice(0, 8)),
  }));
  let cards = [];
  for (const sel of ['[class*="card"]', '[class*="product"]', '[class*="item"]', '[class*="result"]']) {
    const nodes = [...document.querySelectorAll(sel)].filter(visible);
    if (nodes.length >= 1 && nodes.length <= 30) {
      cards = nodes.slice(0, """ + str(MAX_CARDS) + r""")
        .map((n) => cap(n.innerText, 180).replace(/[\r\n]+/g, ' | '));
      if (cards.length) break;
    }
  }
  return {
    url: location.href,
    title: cap(document.title, 80),
    elements,
    tables,
    cards,
    body_sample: cap(document.body.innerText, 300).replace(/[\r\n]+/g, ' | '),
  };
}
"""


async def take_snapshot(page: Any) -> PortalPageSnapshot:
    """Evaluate the snapshot script against the current page."""

    raw = await page.evaluate(_SNAPSHOT_JS)
    return PortalPageSnapshot.model_validate(raw)


class FrameRecorder:
    """Saves one JPEG frame per agent step for UI replay."""

    def __init__(self, settings: Settings, task_id: str, supplier_id: str):
        self._enabled = settings.portal_frames
        self._root = Path(settings.portal_frames_dir) / task_id / supplier_id
        self.steps: list[PortalAgentStep] = []

    async def capture(
        self, page: Any, *, sequence: int, action: str, detail: str
    ) -> PortalAgentStep:
        """Save one frame and register the step."""

        frame_path: str | None = None
        if self._enabled:
            try:
                self._root.mkdir(parents=True, exist_ok=True)
                target = self._root / f"step-{sequence:02d}.jpg"
                await page.screenshot(path=str(target), type="jpeg", quality=55)
                frame_path = target.as_posix()
            except Exception as exc:
                logger.debug("frame capture failed: %s", exc)
        step = PortalAgentStep(
            sequence=sequence,
            action=action,
            detail=detail,
            url=str(getattr(page, "url", "")),
            frame_path=frame_path,
        )
        self.steps.append(step)
        return step

    def manifest(self) -> dict[str, Any]:
        """Return the replay manifest for the API layer."""

        return {"steps": [step.model_dump() for step in self.steps]}


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[\s\-_,，、|]+", text.lower()) if len(token) >= 2}


def _match_score(text: str, wanted: set[str]) -> float:
    available = _tokens(text)
    if not wanted:
        return 0.0
    return len(wanted & available) / len(wanted)


def _first_match(pattern: re.Pattern, text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _map_headers(headers: list[str]) -> dict[str, int]:
    """Map semantic field names to column indexes via header keywords."""

    mapping: dict[str, int] = {}
    for pos, header in enumerate(headers):
        lowered = header.lower()
        for field, hints in HEADER_HINTS.items():
            if field in mapping:
                continue
            if any(hint in lowered for hint in hints):
                mapping[field] = pos
                break
    return mapping


def _row_name(text: str) -> str:
    """The row name is the leading chunk before the first price."""

    match = PRICE_RE.search(text)
    head = text[: match.start()] if match else text
    head = head.split("|")[0]
    return head.strip(" |:：,，") or text.strip()


def _parse_card(card: str) -> dict[str, str] | None:
    """Regex-parse a free-form card text (price + anchored stock/delivery)."""

    price_match = PRICE_RE.search(card)
    if price_match is None:
        return None
    price_value = price_match.group(1).replace(",", "")
    try:
        Decimal(price_value)
    except InvalidOperation:
        return None
    remainder = card[: price_match.start()] + card[price_match.end():]
    stock_match = STOCK_RE.search(remainder)
    delivery_match = DELIVERY_RE.search(remainder)
    return {
        "name": _row_name(card),
        "price": price_value,
        "stock": stock_match.group(1) if stock_match else "0",
        "delivery": delivery_match.group(1) if delivery_match else "0",
    }


def parse_quote_rows(
    snapshot: PortalPageSnapshot,
    product_name: str,
    supplier_id: str = "",
) -> PortalQuoteExtraction | None:
    """Deterministically extract the best-matching quote from rows/cards.

    Table rows are mapped by header keywords when headers are usable; card
    text falls back to anchored regex parsing. Candidate ranking is a fuzzy
    token-overlap score against the requested product name, so minor wording
    differences do not break extraction.
    """

    candidates: list[tuple[float, dict[str, str]]] = []
    wanted = _tokens(product_name)

    for table in snapshot.tables:
        mapping = _map_headers(table.headers)
        for row in table.rows:
            if not row:
                continue
            if len(mapping) >= 2:

                def cell(field: str, *, mapping=mapping, row=row) -> str:
                    position = mapping.get(field)
                    return row[position].strip() if position is not None and position < len(row) else ""

                name = cell("name") or _row_name(" | ".join(row))
                price_text = cell("price")
                price_match = PRICE_RE.search(price_text)
                fields = {
                    "name": name,
                    "price": price_match.group(1).replace(",", "") if price_match else "",
                    "stock": _first_match(STOCK_RE, cell("stock")) or cell("stock") or "0",
                    "delivery": _first_match(DELIVERY_RE, cell("delivery")) or cell("delivery") or "0",
                }
            else:
                joined = " | ".join(cell for cell in row if cell)
                fields = _parse_card(joined)
                if fields is None:
                    continue
            if not fields["price"]:
                continue
            try:
                Decimal(fields["price"])
            except InvalidOperation:
                continue
            score = _match_score(" ".join(row), wanted)
            if score > 0:
                candidates.append((score, fields))

    for card in snapshot.cards:
        fields = _parse_card(card)
        if fields is None:
            continue
        score = _match_score(card, wanted)
        if score > 0:
            candidates.append((score, fields))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    fields = candidates[0][1]
    return PortalQuoteExtraction(
        product_name=fields["name"],
        unit_price=fields["price"],
        currency="CNY" if ("¥" in str(fields) or "CNY" in str(fields)) else "USD",
        available_stock=fields["stock"],
        delivery_days=fields["delivery"],
    )


class BasePortalAgent:
    """Decision policy for the observe-act loop."""

    async def next_action(
        self, snapshot: PortalPageSnapshot, product_name: str, history: list[PortalAgentAction]
    ) -> PortalAgentAction:
        raise NotImplementedError


class HeuristicPortalAgent(BasePortalAgent):
    """Deterministic search-extract policy that needs no LLM.

    Plan: find the search input → fill the product name → submit (click the
    search button or press Enter) → extract from the resulting rows/cards.
    The heuristic addresses elements purely by snapshot ordinal, so randomized
    or drifting ids/classes cannot break it.
    """

    async def next_action(
        self, snapshot: PortalPageSnapshot, product_name: str, history: list[PortalAgentAction]
    ) -> PortalAgentAction:
        submitted = any(action.action in {"press_enter", "click"} for action in history)
        filled = any(action.action in {"fill", "select"} for action in history)

        if submitted and filled:
            if parse_quote_rows(snapshot, product_name) is not None:
                return PortalAgentAction(action="done", note="rows extracted")
            if self._gate_button(snapshot) is not None:
                gate = self._gate_button(snapshot)
                return PortalAgentAction(action="click", element_index=gate, note="interstitial gate")
            return PortalAgentAction(action="done", note="no rows matched after search")

        if not filled:
            target = self._find_search_input(snapshot)
            if target is None:
                if self._gate_button(snapshot) is not None:
                    return PortalAgentAction(
                        action="click",
                        element_index=self._gate_button(snapshot),
                        note="interstitial gate",
                    )
                raise WebExecutionError("Adaptive agent found no usable search input")
            return PortalAgentAction(action="fill", element_index=target, note="fill product name")

        button = self._find_submit(snapshot)
        if button is not None:
            return PortalAgentAction(action="click", element_index=button, note="submit search")
        target = self._find_search_input(snapshot)
        if target is not None:
            return PortalAgentAction(action="press_enter", element_index=target, note="submit via Enter")
        raise WebExecutionError("Adaptive agent found no way to submit the search")

    @staticmethod
    def _gate_button(snapshot: PortalPageSnapshot) -> int | None:
        """Detect interstitial 'continue/verify' gates injected by chaos portals."""

        for element in snapshot.elements:
            if element.kind != "button":
                continue
            text = (element.text or "").lower()
            if any(word in text for word in ("continue", "verify", "proceed", "继续", "进入")):
                return element.index
        return None

    @staticmethod
    def _find_search_input(snapshot: PortalPageSnapshot) -> int | None:
        """Prefer search-hinted inputs; ignore checkbox/radio/hidden/file."""

        best: tuple[int, int] | None = None  # (score, index)
        for element in snapshot.elements:
            if element.kind != "input":
                continue
            if (element.input_type or "text") in {"checkbox", "radio", "hidden", "file", "submit"}:
                continue
            hints = " ".join(
                part
                for part in (element.placeholder, element.label, element.name)
                if part
            ).lower()
            if any(word in hints for word in ("search", "keyword", "产品", "搜索", "query")):
                score = 4
            elif (element.input_type or "text") in {"search", ""}:
                score = 3
            elif element.input_type in {None, "text"} and not element.options:
                score = 2
            else:
                score = 0
            if score and (best is None or score > best[0]):
                best = (score, element.index)
        return best[1] if best else None

    @staticmethod
    def _find_submit(snapshot: PortalPageSnapshot) -> int | None:
        for element in snapshot.elements:
            if element.kind != "button":
                continue
            text = (element.text or "").lower()
            if element.input_type == "submit":
                return element.index
            if any(word in text for word in ("search", "go", "查询", "搜索", "submit", "find")):
                return element.index
        for element in snapshot.elements:
            if element.kind == "button" and element.tag == "button":
                return element.index
        return None


class LLMPortalAgent(BasePortalAgent):
    """LLM decision policy over the numbered snapshot (second escalation level)."""

    def __init__(self, model: Any, max_actions: int = 10):
        self._model = model
        self._max_actions = max_actions

    async def next_action(
        self, snapshot: PortalPageSnapshot, product_name: str, history: list[PortalAgentAction]
    ) -> PortalAgentAction:
        from .portal_prompts import PORTAL_AGENT_SYSTEM  # local import avoids a cycle

        history_text = "\n".join(
            f"{pos + 1}. {action.action}#{action.element_index} {action.note or ''}".strip()
            for pos, action in enumerate(history)
        )
        user = (
            f"GOAL: find the quote for product {product_name!r} then stop.\n"
            f"ACTIONS SO FAR:\n{history_text or '(none)'}\n\n"
            f"PAGE SNAPSHOT:\n{snapshot.render()}\n\n"
            "Reply with ONE next action as strict JSON."
        )
        try:
            return await self._model.generate_structured(
                system=PORTAL_AGENT_SYSTEM,
                user=user,
                output_model=PortalAgentAction,
                step="portal_agent",
                component="LLMPortalAgent",
            )
        except Exception as exc:
            logger.warning("LLM portal agent failed: %s", exc)
            return PortalAgentAction(action="done", note=f"llm error: {exc}")


class PortalAgentLoop:
    """Runs an observe-act loop under hard guards and records replay frames."""

    def __init__(
        self,
        settings: Settings,
        agent: BasePortalAgent,
        task_id: str,
        supplier_id: str,
    ):
        self._settings = settings
        self._agent = agent
        self._task_id = task_id
        self._supplier_id = supplier_id
        self._recorder = FrameRecorder(settings, task_id, supplier_id)
        self.history: list[PortalAgentAction] = []

    async def run(self, page: Any, start_url: str, product_name: str) -> dict[str, Any]:
        """Execute the bounded loop and return a raw quote mapping."""

        await page.goto(start_url, wait_until="domcontentloaded")
        max_actions = self._settings.portal_max_actions
        extraction: PortalQuoteExtraction | None = None

        for sequence in range(1, max_actions + 1):
            snapshot = await take_snapshot(page)
            action = await self._agent.next_action(snapshot, product_name, self.history)
            if action.action == "done":
                await self._recorder.capture(
                    page, sequence=sequence, action="done", detail=action.note or "done"
                )
                extraction = parse_quote_rows(snapshot, product_name)
                break
            await self._apply(page, snapshot, action, product_name)
            await self._recorder.capture(
                page,
                sequence=sequence,
                action=action.action,
                detail=self._describe(action, snapshot),
            )
            if action.action in {"press_enter", "click"}:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception as exc:
                    logger.debug("load state after %s: %s", action.action, exc)
                await asyncio.sleep(0.25)
        else:
            raise WebExecutionError(
                f"Adaptive portal agent exceeded {max_actions} actions without extraction"
            )

        if extraction is None:
            raise WebExecutionError("Adaptive portal agent found no matching quote rows")
        self._last_manifest = self._recorder.manifest()
        return {
            "supplier_id": self._supplier_id,
            "product_name": extraction.product_name,
            "unit_price": extraction.unit_price,
            "currency": extraction.currency,
            "available_stock": extraction.available_stock,
            "delivery_days": extraction.delivery_days,
            "source_type": "portal",
            "extraction": "adaptive",
            "portal_steps": len(self.history) + 1,
        }

    async def _apply(
        self,
        page: Any,
        snapshot: PortalPageSnapshot,
        action: PortalAgentAction,
        product_name: str,
    ) -> None:
        index = action.element_index
        if index is None:
            raise WebExecutionError(f"Action {action.action} requires element_index")
        element = next((item for item in snapshot.elements if item.index == index), None)
        if element is None:
            raise WebExecutionError(f"Element #{index} no longer exists in snapshot")

        # Guard: form values are only ever the product name or a listed option.
        # Page content is untrusted; it must not be able to drive form input.
        if action.action == "fill":
            await self._locator(page, element).fill(product_name)
        elif action.action == "select":
            if action.select_value not in element.options:
                raise WebExecutionError(
                    f"select value {action.select_value!r} is not an enumerated option"
                )
            await self._locator(page, element).select_option(action.select_value)
        elif action.action == "click":
            await self._locator(page, element).click()
        elif action.action == "press_enter":
            await self._locator(page, element).press("Enter")
        else:
            raise WebExecutionError(f"Unsupported action {action.action!r}")
        self.history.append(action)

    @staticmethod
    def _describe(action: PortalAgentAction, snapshot: PortalPageSnapshot) -> str:
        element = next(
            (item for item in snapshot.elements if item.index == action.element_index), None
        )
        if element is None:
            return action.action
        target = element.text or element.placeholder or element.label or element.tag
        return f"{action.action} → {target} ({action.note or ''})".strip()

    @staticmethod
    def _locator(page: Any, element: SnapshotElement) -> Any:
        """Address an element by kind and snapshot ordinal (markup-independent)."""

        # ``:visible`` mirrors the snapshot's visibility filter so the
        # per-kind ordinal addresses the same element at action time.
        if element.kind == "input":
            selector = "input:visible, textarea:visible"
        elif element.kind == "select":
            selector = "select:visible"
        elif element.kind == "link":
            selector = "a[href]:visible"
        else:
            selector = 'button:visible, [role="button"]:visible, input[type="submit"]:visible'
        return page.locator(selector).nth(element.ordinal)

    @property
    def manifest(self) -> dict[str, Any]:
        """Replay manifest of the last run (empty before the first run)."""

        return getattr(self, "_last_manifest", {"steps": []})


def write_manifest(settings: Settings, task_id: str, supplier_id: str, manifest: dict) -> None:
    """Persist the replay manifest next to the frames."""

    root = Path(settings.portal_frames_dir) / task_id
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{supplier_id}.steps.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
