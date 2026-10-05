"""Deterministic quote normalization."""

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from ..errors import ValidationError
from .schemas import SupplierQuote


class QuoteNormalizer:
    """Convert raw supplier payloads into a single ``SupplierQuote`` shape."""

    def normalize(
        self,
        raw: Mapping[str, Any],
        *,
        supplier_id: str,
        product_name: str,
        source_type: str,
    ) -> SupplierQuote:
        """Normalize one raw quote.

        The normalizer deliberately handles strings such as ``"$79.99"``,
        ``"1,250"``, and ``"12"`` so that scoring never performs math on text.
        """

        if source_type not in {"api", "portal"}:
            raise ValidationError(f"Unsupported source_type: {source_type}")

        unit_price = self._to_decimal(raw.get("unit_price"), field="unit_price")
        stock = self._to_int(raw.get("available_stock", raw.get("stock")), field="stock")
        delivery_days = self._to_int(raw.get("delivery_days"), field="delivery_days")
        currency = (raw.get("currency") or "USD").strip().upper() or "USD"

        if unit_price <= 0:
            raise ValidationError("unit_price must be greater than zero")
        if stock < 0:
            raise ValidationError("stock must be zero or greater")
        if delivery_days < 0:
            raise ValidationError("delivery_days must be zero or greater")

        return SupplierQuote(
            supplier_id=supplier_id,
            product_name=str(raw.get("product_name") or product_name),
            unit_price=unit_price,
            currency=currency,
            available_stock=stock,
            delivery_days=delivery_days,
            source_type=source_type,
        )

    @staticmethod
    def _to_decimal(value: Any, *, field: str) -> Decimal:
        if value is None or value == "":
            raise ValidationError(f"{field} is required")
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        text = re.sub(r"[^\d.\-]", "", str(value))
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise ValidationError(f"Invalid {field}: {value!r}") from exc

    @staticmethod
    def _to_int(value: Any, *, field: str) -> int:
        if value is None or value == "":
            raise ValidationError(f"{field} is required")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = re.sub(r"[^\d\-]", "", str(value))
        try:
            return int(text)
        except ValueError as exc:
            raise ValidationError(f"Invalid {field}: {value!r}") from exc
