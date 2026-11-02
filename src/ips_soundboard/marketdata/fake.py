"""In-memory provider for tests and offline demos."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import pandas as pd

from ips_soundboard.marketdata.base import Quote


class FakeProvider:
    def __init__(
        self,
        quotes: Mapping[str, Quote] | None = None,
        fx: Mapping[str, float] | None = None,
        closes: pd.DataFrame | None = None,
    ) -> None:
        self.quotes = dict(quotes or {})
        self.fx = dict(fx or {})
        self.closes = closes if closes is not None else pd.DataFrame()

    def latest_prices(self, tickers: Sequence[str]) -> dict[str, Quote]:
        return {t: self.quotes[t] for t in tickers if t in self.quotes}

    def fx_rates(self, currencies: Sequence[str], base: str) -> dict[str, float]:
        return {c: self.fx[c] for c in currencies if c != base and c in self.fx}

    def history(self, tickers: Sequence[str], start: date, end: date) -> pd.DataFrame:
        if self.closes.empty:
            return self.closes
        window = self.closes.loc[pd.Timestamp(start):pd.Timestamp(end)]
        return window[[t for t in tickers if t in window.columns]]
