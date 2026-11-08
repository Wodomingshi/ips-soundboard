"""In-memory provider for tests and offline demos."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import pandas as pd

from ips_soundboard.marketdata.base import Quote, SecurityInfo


class FakeProvider:
    def __init__(
        self,
        quotes: Mapping[str, Quote] | None = None,
        fx: Mapping[str, float] | None = None,
        closes: pd.DataFrame | None = None,
        fx_closes: pd.DataFrame | None = None,
        names: Mapping[str, str] | None = None,
    ) -> None:
        self.quotes = dict(quotes or {})
        self.fx = dict(fx or {})
        self.closes = closes if closes is not None else pd.DataFrame()
        self.fx_closes = fx_closes if fx_closes is not None else pd.DataFrame()
        self.names = dict(names or {})

    def latest_prices(self, tickers: Sequence[str]) -> dict[str, Quote]:
        return {t: self.quotes[t] for t in tickers if t in self.quotes}

    def fx_rates(self, currencies: Sequence[str], base: str) -> dict[str, float]:
        return {c: self.fx[c] for c in currencies if c != base and c in self.fx}

    def history(self, tickers: Sequence[str], start: date, end: date) -> pd.DataFrame:
        return _window(self.closes, tickers, start, end)

    def describe(self, tickers: Sequence[str]) -> dict[str, SecurityInfo]:
        return {t: SecurityInfo(self.names.get(t), self.quotes[t].currency) for t in tickers if t in self.quotes}

    def fx_history(self, currencies: Sequence[str], base: str, start: date, end: date) -> pd.DataFrame:
        return _window(self.fx_closes, [c for c in currencies if c != base], start, end)


def _window(frame: pd.DataFrame, columns: Sequence[str], start: date, end: date) -> pd.DataFrame:
    if frame.empty:
        return frame
    window = frame.loc[pd.Timestamp(start):pd.Timestamp(end)]
    return window[[c for c in columns if c in window.columns]]
