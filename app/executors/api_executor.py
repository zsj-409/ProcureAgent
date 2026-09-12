"""HTTP executor for modern supplier APIs."""

import httpx

from ..errors import SupplierUnavailableError
from .base import BaseExecutor, ExecutorResult, ExecutorTask


class ApiExecutor(BaseExecutor):
    """Query a supplier REST API and return a raw quote mapping."""

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def execute(self, task: ExecutorTask) -> ExecutorResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                url = (
                    task.supplier.base_url.rstrip("/")
                    + "/"
                    + (task.supplier.search_endpoint or "").lstrip("/")
                )
                response = await client.get(
                    url,
                    params={
                        "product_name": task.product_name,
                        "quantity": task.quantity,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SupplierUnavailableError(
                f"Supplier {task.supplier.supplier_id} request failed: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise SupplierUnavailableError(
                f"Supplier {task.supplier.supplier_id} returned a non-object response"
            )

        return ExecutorResult(
            success=True,
            quote={
                "supplier_id": task.supplier.supplier_id,
                "product_name": data.get("product_name") or task.product_name,
                "unit_price": data.get("unit_price"),
                "currency": data.get("currency") or "USD",
                "available_stock": data.get("stock"),
                "delivery_days": data.get("delivery_days"),
                "source_type": "api",
            },
        )
