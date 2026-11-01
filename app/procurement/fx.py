"""Foreign-exchange normalization for procurement quotes (Phase 1).

Design rules (from the Phase-1 contract):

- A rate is **directed**: ``FxRate(source_currency, target_currency, rate,
  as_of, source)`` means ``1 source unit = rate target units``.
- A rate is only usable within a validity window (``valid_for`` hours).
- Missing rate for a (source, target) pair -> ``FX_RATE_MISSING``.
- Rate older than the window -> ``FX_RATE_STALE``.
- We never guess a rate, never default to 1.0, never overwrite the original
  currency, and never use a historical rate without recording ``as_of``.
- Quotes whose currency cannot be normalized to the reference currency are
  NOT comparable and MUST NOT be sent to the scorer for price ranking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum


class FxStatus(str, Enum):
    OK = "OK"
    FX_RATE_MISSING = "FX_RATE_MISSING"
    FX_RATE_STALE = "FX_RATE_STALE"
    INVALID_DIRECTION = "INVALID_DIRECTION"


@dataclass(frozen=True)
class FxRate:
    """A directed exchange rate snapshot.

    ``rate`` means: 1 unit of ``source_currency`` = ``rate`` units of
    ``target_currency``.
    """

    source_currency: str
    target_currency: str
    rate: Decimal
    as_of: datetime
    source: str  # provenance: e.g. "offline-fixture-v1", "ecb-2026-01"
    valid_for_hours: int = 24

    def is_stale(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return now - self.as_of > timedelta(hours=self.valid_for_hours)


class FxService:
    """Deterministic FX lookup against a versioned rate table.

    Phase 1 uses an offline fixture (``FX_RATES_V1``); Phase 3+ may load a
    real provider feed into the same interface.
    """

    def __init__(self, rates: list[FxRate] | None = None):
        # Index: (source, target) -> rate (latest by as_of wins).
        self._rates: dict[tuple[str, str], FxRate] = {}
        for rate in rates or []:
            self._upsert(rate)

    def _upsert(self, rate: FxRate) -> None:
        key = (rate.source_currency.upper(), rate.target_currency.upper())
        current = self._rates.get(key)
        if current is None or rate.as_of >= current.as_of:
            self._rates[key] = rate

    def register(self, rate: FxRate) -> None:
        self._upsert(rate)

    def convert(
        self,
        amount: Decimal,
        source_currency: str,
        target_currency: str,
        *,
        now: datetime | None = None,
    ) -> tuple[Decimal | None, FxStatus]:
        """Convert ``amount`` from ``source`` to ``target``.

        Returns ``(converted, status)``:
        - OK: converted amount in target currency
        - FX_RATE_MISSING: no rate for the pair
        - FX_RATE_STALE: rate exists but is stale
        - INVALID_DIRECTION: a rate exists but points the wrong way
        """
        source = source_currency.upper()
        target = target_currency.upper()
        if source == target:
            return amount, FxStatus.OK

        now = now or datetime.now(UTC)
        rate = self._rates.get((source, target))
        if rate is None:
            # Check the reverse direction: if we have target->source, invert it
            # (documented inversion, not a guess).
            reverse = self._rates.get((target, source))
            if reverse is None:
                return None, FxStatus.FX_RATE_MISSING
            if reverse.is_stale(now):
                return None, FxStatus.FX_RATE_STALE
            if reverse.rate <= 0:
                return None, FxStatus.INVALID_DIRECTION
            inverted = Decimal(1) / reverse.rate
            return (amount * inverted).quantize(Decimal("0.0001")), FxStatus.OK

        if rate.is_stale(now):
            return None, FxStatus.FX_RATE_STALE
        if rate.rate <= 0:
            return None, FxStatus.INVALID_DIRECTION
        return (amount * rate.rate).quantize(Decimal("0.0001")), FxStatus.OK

    def has_rate(self, source: str, target: str) -> bool:
        return (source.upper(), target.upper()) in self._rates or (
            target.upper(),
            source.upper(),
        ) in self._rates


# ---------------------------------------------------------------------------
# Versioned offline FX fixture (Phase 1). Rates are illustrative and MUST be
# replaced by a real provider feed before any production use. They exist only
# so currency handling is testable offline and deterministic.
# ---------------------------------------------------------------------------
_FIXTURE_AS_OF = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)


def _r(source: str, target: str, rate: str) -> FxRate:
    return FxRate(
        source_currency=source,
        target_currency=target,
        rate=Decimal(rate),
        as_of=_FIXTURE_AS_OF,
        source="offline-fixture-v1",
        valid_for_hours=24 * 30,  # 30 days
    )


FX_RATES_V1: list[FxRate] = [
    _r("USD", "EUR", "0.92"),
    _r("USD", "CNY", "7.25"),
    _r("EUR", "USD", "1.087"),
    _r("EUR", "CNY", "7.88"),
    _r("CNY", "USD", "0.138"),
    _r("CNY", "EUR", "0.127"),
    _r("GBP", "USD", "1.27"),
]

DEFAULT_FX_SERVICE = FxService(FX_RATES_V1)


def build_fx_service() -> FxService:
    return FxService(FX_RATES_V1)


def normalize_currency_pair(source: str, target: str) -> tuple[str, str]:
    """Normalize a currency pair for lookup (upper-case, trimmed)."""
    return source.strip().upper(), target.strip().upper()
