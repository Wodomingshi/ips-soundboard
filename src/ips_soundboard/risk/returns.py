"""Turn price and FX history into daily returns in the base currency."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

# Exchanges close on different holidays. Carry a missing price forward for at
# most this many days before treating the gap as missing data.
MAX_FILL_DAYS = 5


def base_currency_prices(
    closes: pd.DataFrame,
    currencies: Mapping[str, str],
    fx_history: pd.DataFrame,
    base_currency: str,
) -> pd.DataFrame:
    """Price of each series expressed in the base currency, on a common calendar.

    closes: columns = series ids, values in each series' own currency.
    currencies: series id -> its currency.
    fx_history: columns = currencies, values = base units per 1 unit.
    A series whose currency has no FX history comes out empty (all NaN).
    """
    calendar = closes.index.union(fx_history.index).sort_values()
    prices = closes.reindex(calendar).ffill(limit=MAX_FILL_DAYS)
    fx = fx_history.reindex(calendar).ffill(limit=MAX_FILL_DAYS)
    out = {}
    for series_id in prices.columns:
        currency = currencies[series_id]
        if currency == base_currency:
            out[series_id] = prices[series_id]
        elif currency in fx.columns:
            out[series_id] = prices[series_id] * fx[currency]
        else:
            out[series_id] = pd.Series(float("nan"), index=calendar)
    return pd.DataFrame(out, index=calendar)


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Simple daily returns; the first row (no previous price) is dropped."""
    return prices.pct_change(fill_method=None).iloc[1:]
