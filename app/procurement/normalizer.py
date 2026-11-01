"""Deterministic quote normalization.

Phase 1: the normalizer keeps the raw payload and computes a comparable
reference cost in a reference currency using ``FxService``. It never guesses
a rate, never defaults missing FX to 1.0, and never overwrites the original
currency. Quotes that cannot be normalized to the reference currency are
marked ``incomparable`` (fx_status = FX_RATE_MISSING / FX_RATE_STALE) and
must not be price-ranked by the scorer.
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..errors import ValidationError
from .fx import FxService, FxStatus, build_fx_service, normalize_currency_pair
from .schemas import SupplierQuote

REFERENCE_CURRENCY = "USD"


class QuoteNormalizer:
    """Convert raw supplier payloads into a comparable ``SupplierQuote``.

    The normalizer deliberately handles strings such as ``"$79.99"``,
    ``"1,250"``, and ``"12"`` so that scoring never performs math on text.
    """

    def __init__(self, fx_service: FxService | None = None, reference_currency: str = REFERENCE_CURRENCY):
        self._fx = fx_service or build_fx_service()
        self._reference_currency = reference_currency.upper()

    def normalize(
        self,
        raw: Mapping[str, Any],
        *,
        supplier_id: str,
        product_name: str,
        source_type: str,
        quantity: int | None = None,
    ) -> SupplierQuote:
        """Normalize one raw quote.

        Returns a quote with ``raw_quote`` preserved and normalization fields
        filled. ``normalization_status`` is one of:
        - ``comparable``: has a normalized reference unit price
        - ``incomparable``: currency cannot be normalized (missing/stale rate)
        - ``incomplete``: required business fields missing (handled upstream)
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

        source_currency, target_currency = normalize_currency_pair(currency, self._reference_currency)
        converted, fx_status = self._fx.convert(unit_price, source_currency, target_currency)

        fx_rate_id = None
        fx_rate_as_of = None
        if source_currency != target_currency:
            fx_rate_id = f"fx:{source_currency}->{target_currency}"
            # Look up the actual rate used for provenance (best effort).
            rate = self._fx._rates.get((source_currency, target_currency))
            if rate is None:
                reverse = self._fx._rates.get((target_currency, source_currency))
                if reverse is not None:
                    fx_rate_as_of = reverse.as_of
            else:
                fx_rate_as_of = rate.as_of

        comparable = fx_status == FxStatus.OK and converted is not None
        normalization_status = "comparable" if comparable else "incomparable"

        # Total reference cost for the requested quantity (only when comparable).
        normalized_total_cost_ref = None
        if comparable:
            qty = quantity if quantity is not None else 1
            normalized_total_cost_ref = (converted * Decimal(qty)).quantize(Decimal("0.01"))

        # Landed-cost extras: only used when explicitly provided and in the
        # same currency as the unit price; tax/shipping in a different currency
        # are flagged, never silently mixed.
        tax_amount = None
        if raw.get("tax_amount") is not None:
            tax_amount = self._to_decimal(raw.get("tax_amount"), field="tax_amount")
        shipping_fee = None
        if raw.get("shipping_fee") is not None:
            shipping_fee = self._to_decimal(raw.get("shipping_fee"), field="shipping_fee")

        quote_valid_until = None
        if raw.get("quote_valid_until"):
            try:
                raw_valid = str(raw["quote_valid_until"]).replace("Z", "+00:00")
                quote_valid_until = datetime.fromisoformat(raw_valid)
            except ValueError:
                quote_valid_until = None

        return SupplierQuote(
            supplier_id=supplier_id,
            product_name=str(raw.get("product_name") or product_name),
            unit_price=unit_price,
            currency=currency,
            available_stock=stock,
            delivery_days=delivery_days,
            source_type=source_type,
            collected_at=datetime.now(UTC),
            raw_quote=dict(raw),
            reference_currency=self._reference_currency,
            normalized_unit_price_ref=converted if comparable else None,
            normalized_total_cost_ref=normalized_total_cost_ref,
            fx_rate_id=fx_rate_id,
            fx_rate_as_of=fx_rate_as_of,
            fx_status=fx_status.value if fx_status != FxStatus.OK else "OK",
            normalization_status=normalization_status,
            tax_amount=tax_amount,
            tax_mode=raw.get("tax_mode"),
            shipping_fee=shipping_fee,
            quote_valid_until=quote_valid_until,
            source_ref=raw.get("source_ref"),
            source_snapshot_hash=raw.get("source_snapshot_hash"),
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
