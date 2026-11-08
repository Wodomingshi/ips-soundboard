"""Yahoo Finance provider (via the unofficial yfinance package).

The network calls sit behind a small client so tests can replace them with
recorded data. Failures for one symbol never stop the others.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Protocol

import pandas as pd

from ips_soundboard.marketdata.base import Quote, SecurityInfo, normalise_quote
from ips_soundboard.marketdata.tickers import to_provider_symbol

log = logging.getLogger(__name__)


def quiet_yfinance_warnings() -> None:
    """Hide pandas deprecation warnings raised inside yfinance's own code (for CLIs and apps).

    yfinance re-enables DeprecationWarnings for its modules when imported, and the
    newest filter wins. So yfinance is imported first, then this narrower filter is added.
    """
    import yfinance  # noqa: F401  (import for its side effect on warning filters)

    warnings.filterwarnings("ignore", message=r"The inplace keyword", category=DeprecationWarning)


class YahooClient(Protocol):
    def last_price(self, symbol: str) -> tuple[float, str] | None:
        """(price, quote currency as Yahoo reports it), or None."""
        ...

    def closes(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Daily closes, columns = Yahoo symbols."""
        ...

    def name(self, symbol: str) -> str | None:
        """The security's official name, or None."""
        ...


class YFinanceClient:
    """The real client. yfinance is imported lazily so tests never need the network."""

    def last_price(self, symbol: str) -> tuple[float, str] | None:
        import yfinance as yf

        try:
            info = yf.Ticker(symbol).fast_info
            price, currency = info["last_price"], info["currency"]
        except Exception as error:  # yfinance raises many types; one bad symbol must not stop the run
            log.warning("Yahoo: no quote for %s (%s)", symbol, error)
            return None
        if price is None or currency is None or not price > 0:
            return None
        return float(price), str(currency)

    def name(self, symbol: str) -> str | None:
        import yfinance as yf

        try:
            info = yf.Ticker(symbol).info
        except Exception as error:  # one bad symbol must not stop the run
            log.warning("Yahoo: no name for %s (%s)", symbol, error)
            return None
        name = info.get("longName") or info.get("shortName")
        return str(name).strip() if name else None

    def closes(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        import yfinance as yf

        # end is exclusive in yfinance; add a day so `end` itself is included.
        # auto_adjust=True: closes adjusted for splits and dividends, so returns
        # computed from them are total returns (what risk measures should use).
        data = yf.download(list(symbols), start=start, end=end + timedelta(days=1),
                           auto_adjust=True, progress=False, group_by="column")
        if data.empty:
            return pd.DataFrame()
        closes = data["Close"]
        return closes.to_frame(symbols[0]) if isinstance(closes, pd.Series) else closes


class YahooProvider:
    def __init__(self, client: YahooClient | None = None) -> None:
        self.client = client or YFinanceClient()

    def latest_prices(self, tickers: Sequence[str]) -> dict[str, Quote]:
        quotes = {}
        for ticker in dict.fromkeys(tickers):  # de-duplicate, keep order
            result = self.client.last_price(to_provider_symbol(ticker, "yahoo"))
            if result is not None:
                quotes[ticker] = normalise_quote(*result)
        return quotes

    def describe(self, tickers: Sequence[str]) -> dict[str, SecurityInfo]:
        found = {}
        for ticker, quote in self.latest_prices(tickers).items():
            found[ticker] = SecurityInfo(self.client.name(to_provider_symbol(ticker, "yahoo")), quote.currency)
        return found

    def fx_rates(self, currencies: Sequence[str], base: str) -> dict[str, float]:
        rates = {}
        for currency in dict.fromkeys(c.upper() for c in currencies):
            if currency == base.upper():
                continue
            result = self.client.last_price(f"{currency}{base.upper()}=X")
            if result is not None:
                rates[currency] = result[0]
        return rates

    def history(self, tickers: Sequence[str], start: date, end: date) -> pd.DataFrame:
        unique = list(dict.fromkeys(tickers))
        symbols = {to_provider_symbol(t, "yahoo"): t for t in unique}
        renamed = self.client.closes(list(symbols), start, end).rename(columns=symbols)
        return renamed[[t for t in unique if t in renamed.columns]]

    def fx_history(self, currencies: Sequence[str], base: str, start: date, end: date) -> pd.DataFrame:
        wanted = [c.upper() for c in dict.fromkeys(currencies) if c.upper() != base.upper()]
        symbols = {f"{c}{base.upper()}=X": c for c in wanted}
        if not symbols:
            return pd.DataFrame()
        renamed = self.client.closes(list(symbols), start, end).rename(columns=symbols)
        return renamed[[c for c in wanted if c in renamed.columns]]
