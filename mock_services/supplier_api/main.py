"""Mock modern supplier REST API.

Run with: ``uvicorn mock_services.supplier_api.main:app --port 8101``
"""

from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="Mock Supplier API")

PRODUCTS = [
    {
        "product_name": "MX Master 3S",
        "unit_price": "$99.99",
        "currency": "USD",
        "stock": 50,
        "delivery_days": 3,
    },
    {
        "product_name": "MX Keys S",
        "unit_price": "$89.50",
        "currency": "USD",
        "stock": 30,
        "delivery_days": 2,
    },
    {
        "product_name": "Logitech Brio 4K",
        "unit_price": "$199.00",
        "currency": "USD",
        "stock": 12,
        "delivery_days": 5,
    },
    {
        "product_name": "Dell UltraSharp U2723QE",
        "unit_price": "$649.99",
        "currency": "USD",
        "stock": 8,
        "delivery_days": 7,
    },
    {
        "product_name": "Jabra Evolve2 40",
        "unit_price": "$179.99",
        "currency": "USD",
        "stock": 25,
        "delivery_days": 4,
    },
    {
        "product_name": "API Only Mouse",
        "unit_price": "$49.99",
        "currency": "USD",
        "stock": 30,
        "delivery_days": 2,
    },
    {
        "product_name": "Low Stock Mouse",
        "unit_price": "$5.00",
        "currency": "USD",
        "stock": 1,
        "delivery_days": 1,
    },
    {
        "product_name": "Zero Price Mouse",
        "unit_price": "$0.00",
        "currency": "USD",
        "stock": 10,
        "delivery_days": 1,
    },
]

_flaky_failed_once = False


@app.get("/products/search")
def search_products(product_name: str = Query(min_length=1), quantity: int = Query(gt=0)) -> dict:
    """Return a quote for a known product, or 404 when not found."""

    for product in PRODUCTS:
        if product_name.strip().lower() in product["product_name"].lower():
            return {**product, "supplier_id": "mock_api_supplier", "quantity": quantity}
    raise HTTPException(status_code=404, detail=f"Product {product_name!r} not found")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/products/search-flaky")
def search_flaky(product_name: str = Query(min_length=1), quantity: int = Query(gt=0)) -> dict:
    """Fail once, then succeed. Used to verify bounded runtime retry/recovery."""

    global _flaky_failed_once
    if not _flaky_failed_once:
        _flaky_failed_once = True
        raise HTTPException(status_code=500, detail="transient supplier failure")
    return {
        "product_name": product_name,
        "unit_price": "$29.99",
        "currency": "USD",
        "stock": 20,
        "delivery_days": 2,
        "supplier_id": "flaky_supplier",
        "quantity": quantity,
    }
