"""Provider-independent market data types."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

# Sub-unit currencies some exchanges quote in: code -> (major currency, divisor).
# London quotes many lines in pence (GBp / GBX).
_SUBUNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


@dataclass(frozen=True)
class Quote:
    price: float
    currency: str  # ISO code of the major currency, after normalisation


def normalise_quote(price: float, currency: str) -> Quote:
    """Convert sub-unit quotes to the major currency: 1,250 GBp -> 12.50 GBP."""
    if currency in _SUBUNITS:
        major, divisor = _SUBUNITS[currency]
        return Quote(price / divisor, major)
    return Quote(price, currency.upper())


class MarketData(Protocol):
    """Anything that can supply prices, FX and history, keyed by canonical ticker."""

    def latest_prices(self, tickers: Sequence[str]) -> dict[str, Quote]:
        """Missing or failed tickers are omitted, never raised."""
        ...

    def fx_rates(self, currencies: Sequence[str], base: str) -> dict[str, float]:
        """Base-currency units per 1 unit of each currency. Missing pairs are omitted."""
        ...

    def history(self, tickers: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Daily closes: index = dates, columns = canonical tickers, in each ticker's quote currency."""
        ...

    def fx_history(self, currencies: Sequence[str], base: str, start: date, end: date) -> pd.DataFrame:
        """Daily FX: index = dates, columns = currencies, values = base units per 1 unit."""
        ...
