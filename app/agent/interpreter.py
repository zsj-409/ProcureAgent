"""Natural-language to ``ProcurementRequest`` interpretation."""

import re
from decimal import Decimal

from ..errors import InterpretationError, ModelClientError
from ..procurement.schemas import Preference, ProcurementRequest
from .model import ModelClient
from .prompts import INTERPRETER_SYSTEM

# Trailing preference/budget phrases, in both Chinese and English. Everything
# from the first match onward is treated as metadata, not as the product name.
_PREFERENCE_TAIL = re.compile(
    r"(?:预算|控制在|以内|优先|价格|交期|库存|便宜|最低价|最快交付|越快越好|越便宜越好"
    r"|prefer\w*|lowest price|cheapest|fast\w* delivery|asap|in stock|best price"
    r"|within (?:the )?budget|budget\b|under \$?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_BUDGET_PATTERNS = (
    re.compile(r"预算[^\d]{0,6}(\d+(?:\.\d+)?)"),
    re.compile(r"budget(?: of| under| within|:)?\s*\$?\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"under\s*\$\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
)


class ProcurementInterpreter:
    """Interpret natural language into strict Pydantic procurement data."""

    def __init__(self, model: ModelClient | None = None):
        self.model = model

    async def interpret(self, message: str) -> ProcurementRequest:
        """Interpret a message, with one repair attempt when validation fails."""

        if self.model is None:
            return self._fallback(message)

        last_error: str | None = None
        for _ in range(2):
            user = message
            if last_error:
                user = f"{message}\nPrevious parse error: {last_error}"
            try:
                return await self.model.generate_structured(
                    system=INTERPRETER_SYSTEM,
                    user=user,
                    output_model=ProcurementRequest,
                    step="interpret",
                    component="ProcurementInterpreter",
                )
            except ModelClientError as exc:
                last_error = str(exc)

        # A deterministic regex fallback keeps simple natural-language requests
        # usable when the model is unavailable or repeatedly returns invalid JSON.
        try:
            return self._fallback(message)
        except InterpretationError:
            raise InterpretationError(f"Unable to interpret message: {last_error}") from None

    def _fallback(self, message: str) -> ProcurementRequest:
        """Deterministic bilingual fallback for simple messages when no model is configured."""

        quantity_match = re.search(r"(\d+)\s*(?=个|台|件|只|套|pcs|units|rows|\s|$)", message)
        if not quantity_match:
            raise InterpretationError("quantity is missing")

        quantity = int(quantity_match.group(1))
        product_part = message[quantity_match.end():]
        product_part = re.sub(r"^(?:个|台|件|只|套|pcs|units|rows)\s*", "", product_part)
        product_name = _PREFERENCE_TAIL.split(product_part, maxsplit=1)[0]
        product_name = re.sub(r"[\s，,。.!！?？、]+$", "", product_name).strip(" ，,。")
        # Drop dangling joiners left behind when a preference phrase was split off.
        product_name = re.sub(r"[\s]+(?:的|,|，|and|with)$", "", product_name).strip(" ，,。")
        if not product_name:
            raise InterpretationError("product_name is missing")

        preference = self._preference(message)
        max_budget = self._budget(message)
        return ProcurementRequest(
            product_name=product_name,
            quantity=quantity,
            max_budget=max_budget,
            preference=preference,
        )

    @staticmethod
    def _preference(message: str) -> Preference:
        """Map bilingual preference wording onto the strict preference enum."""

        lower = message.lower()
        delivery_words = (
            "优先交付", "交期优先", "交付速度", "速度", "最快", "fast delivery", "asap",
        )
        price_words = (
            "价格优先", "最低价", "便宜", "价格最低", "lowest price", "cheapest", "best price",
        )
        if any(word in lower for word in delivery_words):
            return Preference.DELIVERY
        if any(word in lower for word in price_words):
            return Preference.PRICE
        if "库存" in lower or "in stock" in lower or "stock" in lower:
            return Preference.STOCK
        return Preference.BALANCED

    @staticmethod
    def _budget(message: str) -> Decimal | None:
        """Extract a budget number from either Chinese or English phrasing."""

        for pattern in _BUDGET_PATTERNS:
            match = pattern.search(message)
            if match:
                return Decimal(match.group(1))
        return None
