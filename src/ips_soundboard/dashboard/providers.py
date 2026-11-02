"""Which market-data provider the dashboard uses. Tests swap in a FakeProvider."""

from __future__ import annotations

from ips_soundboard.marketdata.base import MarketData

_override: MarketData | None = None


def set_provider(provider: MarketData | None) -> None:
    """Use `provider` instead of Yahoo (None restores the default). For tests and offline demos."""
    global _override
    _override = provider


def get_provider() -> MarketData:
    if _override is not None:
        return _override
    from ips_soundboard.marketdata.yahoo import YahooProvider, quiet_yfinance_warnings

    quiet_yfinance_warnings()
    return YahooProvider()
