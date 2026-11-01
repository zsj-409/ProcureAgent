"""Semantic portal state graph (Phase 4, Commit A).

This module turns a raw browser snapshot into a *semantic* page state that is
stable under irrelevant DOM changes (random css classes, ids, timestamps) but
sensitive to changes that actually affect execution correctness (product id,
quote version, session status, tenant).

Design:
- ``SemanticPortalState``: the structured state (state_type, template,
  session status, product context, visible controls, available actions,
  extracted quote fields, state signature, confidence, timestamp).
- ``state_signature(...)``: a canonical string built ONLY from semantic
  fields. It deliberately ignores: random class names, generated ids,
  timestamps, element ordinals, dynamic DOM hashes.
- ``classify_state(...)``: maps a snapshot + context to a state_type using
  semantic evidence (title, URL path, header semantics, control kinds,
  session-expired markers).
- ``SemanticTransition`` / ``StateTransitionLog``: records every transition
  (before → action → after) with preconditions, expected postconditions,
  observed after-state, result, recovery reason, cost, trace ref.

State types (base set, extensible):
LOGIN, DASHBOARD, SEARCH, RESULTS, QUOTE_DETAIL, SESSION_EXPIRED, ERROR,
UNKNOWN. Extra portal-specific types may be added.

The signature must NOT be a full dynamic DOM hash: two renders of the same
semantic page (different random classes/timestamps) must produce the SAME
signature; a different product id, quote version or tenant must produce a
DIFFERENT one.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class StateType(str, Enum):
    """Semantic page-state kinds used by the recovery controller."""

    LOGIN = "LOGIN"
    DASHBOARD = "DASHBOARD"
    SEARCH = "SEARCH"
    RESULTS = "RESULTS"
    QUOTE_DETAIL = "QUOTE_DETAIL"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Semantic evidence extractors
# ---------------------------------------------------------------------------

_SESSION_EXPIRED_HINTS = (
    "session expired",
    "session has expired",
    "logged out",
    "sign in again",
    "re-authenticate",
    "会话已过期",
    "登录已失效",
    "请重新登录",
    "session timeout",
)

_LOGIN_HINTS = (
    "login",
    "sign in",
    "log in",
    "登录",
    "用户名",
    "password",
    "密码",
)

_ERROR_HINTS = (
    "error",
    "not found",
    "unavailable",
    "server error",
    "系统错误",
    "页面不存在",
    "oops",
)

_QUOTE_DETAIL_HINTS = (
    "quote",
    "quotation",
    "报价",
    "product detail",
    "商品详情",
    "price",
    "库存",
    "delivery",
)

_SEARCH_HINTS = (
    "search",
    "keyword",
    "产品",
    "搜索",
    "query",
)


def _has_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in hints)


def _table_headers_are_quote_like(headers: list[list[str]]) -> bool:
    """A results/quote table has semantic header cells (price/stock/name)."""

    for header_row in headers:
        joined = " ".join(header_row).lower()
        price_like = any(
            hint in joined
            for hint in ("price", "报价", "amount", "单价", "金额", "cost", "费用")
        )
        stock_like = any(
            hint in joined
            for hint in ("stock", "库存", "available", "availability", "quantity", "数量", "inventory")
        )
        delivery_like = any(
            hint in joined
            for hint in ("delivery", "交期", "lead", "天数", "days", "delivery time")
        )
        if price_like and (stock_like or delivery_like):
            return True
    return False


def classify_state(
    *,
    url: str = "",
    title: str = "",
    body_sample: str = "",
    headers: list[list[str]] | None = None,
    has_search_input: bool = False,
    has_submit: bool = False,
    has_login_form: bool = False,
    product_context: str = "",
    card_count: int = 0,
) -> StateType:
    """Classify the semantic page state from observed evidence.

    Evidence priority: session-expired markers first (a session-expired page
    can otherwise look like a login page), then explicit error markers, then
    login forms, then content structure.

    ``card_count``: number of product/card containers on the page. A card
    layout (layout drift from a table) still shows product cards with price —
    that is a RESULTS/QUOTE_DETAIL page, not a SEARCH page.
    """

    text = " ".join(part for part in (title, body_sample) if part)
    headers = headers or []

    if _has_any(text, _SESSION_EXPIRED_HINTS):
        return StateType.SESSION_EXPIRED
    if _has_any(text, _ERROR_HINTS) and not _has_any(text, ("quote", "报价")):
        return StateType.ERROR
    if has_login_form or _has_any(text, _LOGIN_HINTS):
        return StateType.LOGIN

    lowered_url = url.lower()
    if "search" in lowered_url or "results" in lowered_url or "list" in lowered_url:
        if _table_headers_are_quote_like(headers):
            return StateType.RESULTS
        # Card layout (layout drift): product cards with price on a search
        # results URL are RESULTS, not SEARCH.
        if card_count >= 1 and ("price" in text.lower() or "$" in text or "USD" in text):
            return StateType.RESULTS
        return StateType.SEARCH
    if _has_any(text, _QUOTE_DETAIL_HINTS) and (_table_headers_are_quote_like(headers) or card_count >= 1):
        return StateType.QUOTE_DETAIL
    if product_context and _has_any(text, ("search", "产品", "keyword")):
        return StateType.SEARCH
    return StateType.UNKNOWN


# ---------------------------------------------------------------------------
# State signature
# ---------------------------------------------------------------------------

_CONTROL_SEMANTIC_FIELDS = ("kind", "placeholder", "label", "name", "input_type")
_PRODUCT_ID_RE = re.compile(r"(product|sku|item|商品|产品)[-_]?id", re.IGNORECASE)


def _semantic_control_fingerprint(elements: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Reduce interactive controls to semantic fingerprints.

    Each control contributes (kind, placeholder/label/name, input_type) —
    NOT its DOM index, random class, generated id, or ordinal. Two pages with
    the same semantic controls but different random markup produce identical
    fingerprints.
    """

    out: list[tuple[str, str]] = []
    for element in elements[:80]:
        kind = element.get("kind") or element.get("tag") or ""
        semantic = "|".join(
            str(element.get(field) or "") for field in _CONTROL_SEMANTIC_FIELDS
        )
        out.append((kind, semantic))
    return sorted(out)


def _semantic_table_fingerprint(tables: list[dict[str, Any]]) -> list[str]:
    """Reduce tables to (headers + row cell texts) — excludes classes/ids."""

    out: list[str] = []
    for table in tables[:3]:
        header_text = " | ".join(table.get("headers") or [])
        rows = []
        for row in (table.get("rows") or [])[:10]:
            rows.append(" | ".join(cell for cell in row if cell))
        out.append(header_text + "\n" + "\n".join(rows))
    return out


def state_signature(
    *,
    state_type: StateType,
    url: str,
    title: str,
    product_context: str = "",
    session_status: str = "",
    elements: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    quote_fields: dict[str, str] | None = None,
    tenant_id: str = "",
    portal_version: str = "",
) -> str:
    """Canonical semantic signature of a page state.

    Stability: random css classes, generated ids, timestamps, ordinals and
    dynamic DOM hashes are EXCLUDED.
    Sensitivity: product id / quote version / tenant / session status ARE
    included — a different product, quote version or tenant must yield a
    different signature.

    Signature is a SHA-256 over the sorted semantic components.
    """

    components: dict[str, str] = {
        "state_type": state_type.value,
        "url_path": url.split("?")[0].rstrip("/"),  # query params are volatile
        "title": (title or "").strip(),
        "product_context": (product_context or "").strip().lower(),
        "session_status": (session_status or "").strip(),
        "tenant_id": (tenant_id or "").strip(),
        "portal_version": (portal_version or "").strip(),
        "quote_fields": json.dumps(quote_fields or {}, sort_keys=True),
    }
    if elements:
        # Only controls whose semantic fields carry information.
        controls = [
            f"{kind}:{semantic}"
            for kind, semantic in _semantic_control_fingerprint(elements)
            if semantic.strip("|")
        ]
        components["controls"] = json.dumps(controls, sort_keys=True)
    if tables:
        components["tables"] = json.dumps(_semantic_table_fingerprint(tables), sort_keys=True)

    payload = json.dumps(components, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Transition log
# ---------------------------------------------------------------------------


@dataclass
class SemanticTransition:
    """One executed transition in the state graph."""

    before_state: StateType
    selected_action: str
    preconditions_ok: bool
    expected_post: StateType
    observed_after: StateType
    result: str  # SUCCESS | FAILED | SAFE_STOP
    recovery_reason: str = ""
    actual_cost: float = 0.0
    trace_ref: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class StateTransitionLog:
    """Append-only in-memory + JSON-persisted transition log."""

    def __init__(self) -> None:
        self.transitions: list[SemanticTransition] = []

    def record(self, transition: SemanticTransition) -> None:
        self.transitions.append(transition)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "before_state": item.before_state.value,
                "selected_action": item.selected_action,
                "preconditions_ok": item.preconditions_ok,
                "expected_post": item.expected_post.value,
                "observed_after": item.observed_after.value,
                "result": item.result,
                "recovery_reason": item.recovery_reason,
                "actual_cost": item.actual_cost,
                "trace_ref": item.trace_ref,
                "at": item.at.isoformat(),
            }
            for item in self.transitions
        ]

    def save(self, path: str) -> None:
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(self.to_dicts(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
