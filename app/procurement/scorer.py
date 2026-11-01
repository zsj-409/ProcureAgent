"""Deterministic procurement scoring."""

from decimal import Decimal

from pydantic import BaseModel

from ..errors import ValidationError
from .schemas import AwardLine, Preference, ProcurementRecommendation, SupplierQuote


class ScoredQuote(BaseModel):
    """A quote and its deterministic component scores."""

    quote: SupplierQuote
    price_score: Decimal
    delivery_score: Decimal
    stock_score: Decimal
    total_score: Decimal


class ProcurementScorer:
    """Score quotes using fixed, explainable weights."""

    # Weights are expressed as percentages so the final score is easy to read
    # on a 0-100 scale: price 50%, delivery 30%, stock 20%.
    def score(
        self,
        quotes: list[SupplierQuote],
        quantity: int,
        preference: Preference = Preference.BALANCED,
    ) -> ProcurementRecommendation:
        """Return the highest-scoring quote and a recommendation.

        Each component is normalized against the best value observed in the
        quote set, then multiplied by its weight. Stock is scored by coverage
        of the requested quantity.
        """

        if not quotes:
            raise ValidationError("Cannot score an empty quote list")
        if quantity <= 0:
            raise ValidationError("quantity must be greater than zero")

        price_weight, delivery_weight, stock_weight = self._weights(preference)
        min_price = min(q.unit_price for q in quotes)
        min_delivery = min(q.delivery_days for q in quotes)

        scored: list[ScoredQuote] = []
        for quote in quotes:
            price_score = price_weight * (min_price / quote.unit_price)
            delivery_score = (
                delivery_weight
                if quote.delivery_days == 0
                else delivery_weight * (Decimal(min_delivery) / Decimal(quote.delivery_days))
            )
            stock_coverage = min(Decimal(quote.available_stock) / Decimal(quantity), Decimal(1))
            stock_score = stock_weight * stock_coverage
            total_score = price_score + delivery_score + stock_score
            scored.append(
                ScoredQuote(
                    quote=quote,
                    price_score=price_score.quantize(Decimal("0.0001")),
                    delivery_score=delivery_score.quantize(Decimal("0.0001")),
                    stock_score=stock_score.quantize(Decimal("0.0001")),
                    total_score=total_score.quantize(Decimal("0.0001")),
                )
            )

        best = max(scored, key=lambda item: item.total_score)
        estimated_total = (best.quote.unit_price * quantity).quantize(Decimal("0.01"))
        reason = (
            f"Selected {best.quote.supplier_id}: best weighted score "
            f"{best.total_score} (price {best.price_score}, delivery {best.delivery_score}, "
            f"stock {best.stock_score}) using {preference.value} weights; "
            f"estimated total {estimated_total} USD."
        )
        recommendation = ProcurementRecommendation(
            recommended_supplier=best.quote.supplier_id,
            quotes=quotes,
            score=best.total_score,
            reason=reason,
            estimated_total=estimated_total,
        )
        if best.quote.available_stock < quantity:
            self._apply_split_award(recommendation, scored, quantity)
        return recommendation

    @staticmethod
    def max_split_lines() -> int:
        """Hard cap on how many suppliers one order may be split across."""

        return 3

    def _apply_split_award(
        self,
        recommendation: ProcurementRecommendation,
        scored: list[ScoredQuote],
        quantity: int,
    ) -> None:
        """Deterministically split the order when stock cannot cover it.

        Greedy by weighted score (ties broken by lower unit price, then
        supplier id for reproducibility); each supplier contributes up to its
        available stock; at most ``max_split_lines`` lines. Any unfulfillable
        remainder is reported as ``shortfall`` instead of being hidden.
        """

        remaining = quantity
        lines: list[AwardLine] = []
        ordered = sorted(
            scored,
            key=lambda item: (-item.total_score, item.quote.unit_price, item.quote.supplier_id),
        )
        for item in ordered:
            if remaining <= 0 or len(lines) >= self.max_split_lines():
                break
            take = min(item.quote.available_stock, remaining)
            if take <= 0:
                continue
            lines.append(
                AwardLine(
                    supplier_id=item.quote.supplier_id,
                    quantity=take,
                    unit_price=item.quote.unit_price,
                    line_total=(item.quote.unit_price * take).quantize(Decimal("0.01")),
                    delivery_days=item.quote.delivery_days,
                )
            )
            remaining -= take

        if len(lines) <= 1:
            # A single line cannot beat the straightforward single award; keep
            # the recommendation untouched (still reports the shortfall below).
            recommendation.shortfall = max(quantity - sum(line.quantity for line in lines), 0)
            if recommendation.shortfall:
                recommendation.reason += (
                    f" Note: requested {quantity} units but the best supplier stocks only"
                    f" {recommendation.quotes[0].available_stock if recommendation.quotes else 0};"
                    f" shortfall {recommendation.shortfall} unit(s)."
                )
            return

        recommendation.award_split = lines
        recommendation.shortfall = remaining
        recommendation.estimated_total = sum(
            (line.line_total for line in lines), Decimal(0)
        ).quantize(Decimal("0.01"))
        recommendation.recommended_supplier = lines[0].supplier_id
        split_text = "; ".join(
            f"{line.supplier_id} x{line.quantity} @ {line.unit_price} = {line.line_total}"
            for line in lines
        )
        recommendation.reason += (
            f" Split award across {len(lines)} suppliers (best supplier stock cannot cover"
            f" {quantity} units): {split_text}. Estimated split total {recommendation.estimated_total} USD."
        )
        if remaining > 0:
            recommendation.reason += f" Unfilled remainder: {remaining} unit(s)."

    @staticmethod
    def _weights(preference: Preference) -> tuple[Decimal, Decimal, Decimal]:
        """Return explicit scoring weights for a user preference."""

        return {
            Preference.BALANCED: (Decimal(50), Decimal(30), Decimal(20)),
            Preference.PRICE: (Decimal(70), Decimal(20), Decimal(10)),
            Preference.DELIVERY: (Decimal(30), Decimal(60), Decimal(10)),
            Preference.STOCK: (Decimal(30), Decimal(20), Decimal(50)),
        }[preference]
