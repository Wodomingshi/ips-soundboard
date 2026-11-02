"""Value holdings in local and base currency.

Prices and FX rates are inputs, not fetched here (that is the market-data layer).
A holding that cannot be valued is not an error: it is returned flagged, left out
of totals, and listed so a report can show it.

Value precedence (the investor's rule):
  1. live price x quantity
  2. manual value
  3. manual price x quantity
  4. cash: quantity is the balance
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from ips_soundboard.schemas.holdings import Holding

PriceSource = Literal["live", "manual_value", "manual_price", "cash"]


@dataclass(frozen=True)
class ValuedHolding:
    holding: Holding
    value_local: float | None
    value_base: float | None
    price_source: PriceSource | None
    problem: str | None = None

    @property
    def is_valued(self) -> bool:
        return self.value_base is not None


def value_holdings(
    holdings: Sequence[Holding],
    prices: Mapping[str, float],
    fx: Mapping[str, float],
    base_currency: str,
) -> list[ValuedHolding]:
    """Value every holding.

    prices: ticker -> price per unit, in the holding's own currency.
    fx: currency -> base-currency units per 1 unit of that currency.
    """
    base = base_currency.strip().upper()
    _check_positive(prices, "price")
    _check_positive(fx, "FX rate")
    return [_value_one(h, prices, fx, base) for h in holdings]


def local_value(holding: Holding, prices: Mapping[str, float]) -> tuple[float | None, PriceSource | None]:
    """Value in the holding's own currency, and where it came from."""
    if holding.ticker and holding.quantity is not None and holding.ticker in prices:
        return holding.quantity * prices[holding.ticker], "live"
    if holding.manual_value is not None:
        return holding.manual_value, "manual_value"
    if holding.manual_price is not None and holding.quantity is not None:
        return holding.quantity * holding.manual_price, "manual_price"
    if holding.instrument_type == "cash" and holding.quantity is not None:
        return holding.quantity, "cash"
    return None, None


def fx_rate(currency: str, fx: Mapping[str, float], base_currency: str) -> float | None:
    """Base-currency units per 1 unit of `currency`; 1.0 for the base itself."""
    if currency == base_currency:
        return 1.0
    return fx.get(currency)


def total_value(valued: Sequence[ValuedHolding]) -> float:
    """Sum of base-currency values. Unvalued holdings are excluded, never counted as zero."""
    return sum(v.value_base for v in valued if v.value_base is not None)


def unvalued(valued: Sequence[ValuedHolding]) -> list[ValuedHolding]:
    return [v for v in valued if not v.is_valued]


def _value_one(holding: Holding, prices: Mapping[str, float], fx: Mapping[str, float], base: str) -> ValuedHolding:
    value, source = local_value(holding, prices)
    if value is None:
        return ValuedHolding(holding, None, None, None, problem="no live price and no manual fallback")
    rate = fx_rate(holding.currency, fx, base)
    if rate is None:
        return ValuedHolding(holding, value, None, source, problem=f"no FX rate for {holding.currency}->{base}")
    return ValuedHolding(holding, value, value * rate, source)


def _check_positive(table: Mapping[str, float], label: str) -> None:
    bad = sorted(key for key, number in table.items() if not number > 0)
    if bad:
        raise ValueError(f"{label} must be positive for: {bad}")
