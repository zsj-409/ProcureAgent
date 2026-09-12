"""Mock legacy supplier portal with a plain HTML search form.

Run with: ``uvicorn mock_services.supplier_portal.main:app --port 8102``

The portal intentionally uses no JavaScript. Search is a normal GET form, so
WebExecutor can demonstrate ``navigate -> fill -> click -> extract_quote``.
"""

from html import escape

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


def render_page(query: str = "") -> str:
    filtered = PRODUCTS
    if query:
        filtered = [p for p in PRODUCTS if query.strip().lower() in p[0].lower()]

    rows = []
    for name, price, stock, delivery in filtered:
        rows.append(
            "<tr>"
            f'<td class="name">{escape(name)}</td>'
            f'<td class="price">{escape(price)}</td>'
            f'<td class="stock">{stock}</td>'
            f'<td class="delivery">{delivery}</td>'
            "</tr>"
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
  <form action="/portal" method="get">
    <input id="search-input" name="q" type="text" value="{escape(query)}" placeholder="Product name">
    <button id="search-button" type="submit">Search</button>
  </form>
  <table id="results">
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


@app.get("/portal", response_class=HTMLResponse)
def portal(request: Request, q: str = Query(default="")) -> str:
    del request
    return render_page(q)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
