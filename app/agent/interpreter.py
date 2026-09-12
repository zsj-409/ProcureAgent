"""Natural-language to ``ProcurementRequest`` interpretation."""

import re
from decimal import Decimal

from ..errors import InterpretationError, ModelClientError
from ..procurement.schemas import Preference, ProcurementRequest
from .model import ModelClient
from .prompts import INTERPRETER_SYSTEM


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
            raise InterpretationError(f"Unable to interpret message: {last_error}")

    def _fallback(self, message: str) -> ProcurementRequest:
        """Deterministic fallback for simple messages when no model is configured."""

        quantity_match = re.search(r"(\d+)\s*(?=个|台|件|只|套|pcs|units|\s|$)", message)
        if not quantity_match:
            raise InterpretationError("quantity is missing")

        quantity = int(quantity_match.group(1))
        product_part = message[quantity_match.end():]
        product_part = re.sub(r"^(?:个|台|件|只|套|pcs|units)\s*", "", product_part)
        product_name = re.sub(r"(预算|控制在|以内|优先|价格|交期|库存).*$", "", product_part)
        product_name = re.sub(r"[\s，,。.!！?？]+$", "", product_name).strip(" ，,。")
        if not product_name:
            raise InterpretationError("product_name is missing")

        preference = Preference.BALANCED
        lower = message.lower()
        if any(word in lower for word in ("优先交付", "交期优先", "速度", "交付速度")):
            preference = Preference.DELIVERY
        elif any(word in lower for word in ("价格优先", "便宜", "最低价")):
            preference = Preference.PRICE
        elif "库存" in lower:
            preference = Preference.STOCK

        budget_match = re.search(r"预算[^\d]{0,6}(\d+(?:\.\d+)?)", message)
        max_budget = Decimal(budget_match.group(1)) if budget_match else None
        return ProcurementRequest(
            product_name=product_name,
            quantity=quantity,
            max_budget=max_budget,
            preference=preference,
        )
