"""Mock legacy supplier portal with several intentionally different layouts.

Run with: ``uvicorn mock_services.supplier_portal.main:app --port 8102``

Pages:
- ``/``            classic table layout (v1) with stable ids — the fixed
                   selector script works here.
- ``/v2``          modern card-grid layout (v2): no ``<table>`` at all, a
                   category select, different ids, paginated results. The
                   fixed script cannot work; the adaptive agent must.
- ``?chaos=<seed>`` chaos mode appended to any portal page: all ids/classes
                   are randomized per seed and an interstitial "session
                   check" page gates the content. Exercises self-healing.

The portal intentionally uses no JavaScript.
"""

import random
from html import escape
from urllib.parse import urlencode

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="Mock Supplier Portal")

PRODUCTS = [
    ("MX Master 3S", "$89.99", 20, 5),
    ("MX Keys S", "$79.50", 18, 2),
    ("Logitech Brio 4K", "$189.00", 6, 4),
    ("Dell UltraSharp U2723QE", "$629.99", 4, 6),
    ("Jabra Evolve2 40", "$169.99", 12, 3),
    ("Portal Only Keyboard", "$59.99", 15, 3),
    ("Low Stock Mouse", "$5.00", 1, 1),
]
CATEGORIES = {"all": "All", "mice": "Mice", "keyboards": "Keyboards", "cams": "Cameras"}
CATEGORY_OF = {
    "MX Master 3S": "mice",
    "Low Stock Mouse": "mice",
    "MX Keys S": "keyboards",
    "Portal Only Keyboard": "keyboards",
    "Logitech Brio 4K": "cams",
    "Dell UltraSharp U2723QE": "cams",
    "Jabra Evolve2 40": "cams",
}
PAGE_SIZE = 4


def _matches(query: str, name: str, category: str) -> bool:
    if category != "all" and CATEGORY_OF.get(name) != category:
        return False
    if not query:
        return True
    return query.strip().lower() in name.lower()


def _chaos_ids(chaos: str | None) -> dict[str, str]:
    """Chaos ids are randomized per seed but stable within one session."""

    if not chaos:
        return {}
    rng = random.Random(chaos)
    return {
        key: "cx" + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8))
        for key in ("input", "button", "table", "name", "price", "stock", "delivery")
    }


def _gate_page(chaos: str, layout: str) -> str:
    params = {"chaos": chaos, "ok": "1", "layout": layout}
    target = f"/portal?{urlencode(params)}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Session Check</title></head>
<body>
  <h1>Session verification</h1>
  <p>To continue, confirm your browser session.</p>
  <button id="continue-btn" type="button"
    onclick="window.location='{escape(target)}'">Continue to portal</button>
</body></html>"""


def render_page(
    query: str = "",
    *,
    variant: str = "v1",
    chaos: str | None = None,
    gate: bool = False,
    category: str = "all",
    page: int = 1,
) -> str:
    if gate:
        return _gate_page(chaos or "", variant)
    ids = _chaos_ids(chaos)
    filtered = [p for p in PRODUCTS if _matches(query, p[0], category)]
    pages = max(1, -(-len(filtered) // PAGE_SIZE))
    page = max(1, min(page, pages))
    shown = filtered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    def link(target_page: int) -> str:
        params: dict[str, object] = {"q": query, "page": target_page, "layout": variant}
        if chaos:
            params.update({"chaos": chaos, "ok": "1"})
        return f"/portal?{urlencode(params)}"

    if variant == "v2":
        rows = []
        for name, price, stock, delivery in shown:
            rows.append(
                f'<div class="product-card {ids.get("name", "card")}">'
                f'<h3 class="pname">{escape(name)}</h3>'
                f'<span class="amount">{escape(price)}</span> '
                f'<span class="availability">In stock: <b class="pstock">{stock}</b> units</span> '
                f'<span class="lead">Lead time: <b class="pdelivery">{delivery}</b> days</span>'
                f"</div>"
            )
        body_cards = "".join(rows) or '<div class="empty">No matching products</div>'
        pager = (
            "<nav>"
            + " ".join(
                f'<a class="page" href="{escape(link(n))}">page {n}</a>'
                for n in range(1, pages + 1)
            )
            + "</nav>"
        )
        options = "".join(
            f'<option value="{key}"{" selected" if key == category else ""}>{escape(label)}</option>'
            for key, label in CATEGORIES.items()
        )
        hidden = (
            f'<input type="hidden" name="chaos" value="{escape(chaos)}">'
            '<input type="hidden" name="ok" value="1">'
            if chaos
            else ""
        )
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Supplier Catalog</title>
  <style>
    body {{ font-family: sans-serif; margin: 2rem; }}
    .product-card {{ border: 1px solid #bbb; border-radius: 8px; padding: .8rem 1rem;
      margin: .6rem 0; max-width: 460px; }}
    .amount {{ font-weight: 700; margin-right: 1rem; }}
    .availability, .lead {{ display: block; color: #555; font-size: .9rem; }}
    nav a {{ margin-right: .6rem; }}
  </style>
</head>
<body>
  <h1>Supplier Catalog</h1>
  <form action="/portal" method="get">
    <input type="hidden" name="layout" value="v2">{hidden}
    <select id="{ids.get("table", "category")}" name="category">{options}</select>
    <input id="{ids.get("input", "kw-input")}" name="q" type="text"
      value="{escape(query)}" placeholder="Keyword">
    <button id="{ids.get("button", "kw-go")}" type="submit">Go</button>
  </form>
  <section id="catalog">
    {body_cards}
  </section>
  {pager}
</body>
</html>"""

    rows = []
    for name, price, stock, delivery in shown:
        rows.append(
            "<tr>"
            f'<td class="{ids.get("name", "name")}">{escape(name)}</td>'
            f'<td class="{ids.get("price", "price")}">{escape(price)}</td>'
            f'<td class="{ids.get("stock", "stock")}">{stock}</td>'
            f'<td class="{ids.get("delivery", "delivery")}">{delivery}</td>'
            "</tr>"
        )
    hidden = (
        f'<input type="hidden" name="chaos" value="{escape(chaos)}">'
        '<input type="hidden" name="ok" value="1">'
        if chaos
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Mock Supplier Portal</title>
  <style>
    body {{ font-family: sans-serif; margin: 2rem; }}
    table {{ border-collapse: collapse; margin-top: 1rem; }}
    td, th {{ border: 1px solid #ccc; padding: 0.5rem 1rem; text-align: left; }}
  </style>
</head>
<body>
  <h1>Supplier Portal</h1>
  <form action="/portal" method="get">{hidden}
    <input id="{ids.get("input", "search-input")}" name="q" type="text"
      value="{escape(query)}" placeholder="Product name">
    <button id="{ids.get("button", "search-button")}" type="submit">Search</button>
  </form>
  <table id="{ids.get("table", "results")}">
    <thead>
      <tr><th>Product</th><th>Unit price</th><th>Stock</th><th>Delivery days</th></tr>
    </thead>
    <tbody>
      {''.join(rows) or '<tr><td colspan="4">No matching products</td></tr>'}
    </tbody>
  </table>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return render_page()


@app.get("/v2", response_class=HTMLResponse)
def home_v2() -> str:
    return render_page(variant="v2")


@app.get("/portal", response_class=HTMLResponse)
def portal(
    request: Request,
    q: str = Query(default=""),
    layout: str = Query(default="v1"),
    chaos: str | None = Query(default=None),
    ok: str | None = Query(default=None),
    category: str = Query(default="all"),
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    del request
    if chaos and ok != "1":
        # Interstitial session check: fixed scripts hit this wall; the
        # adaptive agent clicks through it.
        return HTMLResponse(_gate_page(chaos, layout))
    return HTMLResponse(
        render_page(
            q,
            variant="v2" if layout == "v2" else "v1",
            chaos=chaos,
            category=category if category in CATEGORIES else "all",
            page=page,
        )
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
