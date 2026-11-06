"""Today's managed holdings valued at past prices, and band breaches in the latest review period.

This is not the investor's actual past wealth: past trades and contributions are not known.
It answers "how has what I hold today moved?" and, for the latest review period only,
"when were my sleeves outside their IPS bands?", which is when the IPS asks for rebalancing.

Holdings without a price series (or before their first price) are held flat at today's value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from ips_soundboard.schemas.ips import IPS

REVIEW_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class ValueHistory:
    values: pd.Series  # managed portfolio value per day, in the base currency
    sleeve_weights: pd.DataFrame  # columns: sleeve ids; rows sum to 1


@dataclass(frozen=True)
class BreachPeriod:
    start: date
    end: date
    sleeves: list[str]  # sleeve ids outside their band at some point in the period


def value_history(base_prices: pd.DataFrame, positions: Sequence[tuple[str, str, float]]) -> ValueHistory:
    """positions: (series id, sleeve id, value today). Each is scaled so its last value is today's."""
    if base_prices.empty:
        raise ValueError("no price history")
    calendar = base_prices.index
    values: dict[str, pd.Series] = {}
    sleeve_of: dict[str, str] = {}
    for key, sleeve, value_today in positions:
        series = base_prices[key].ffill() if key in base_prices else None
        if series is None or series.notna().sum() == 0:
            values[key] = pd.Series(value_today, index=calendar)  # no price series: held flat
        else:
            values[key] = series.bfill() * (value_today / series.dropna().iloc[-1])
        sleeve_of[key] = sleeve
    frame = pd.DataFrame(values, index=calendar)
    total = frame.sum(axis=1)
    by_sleeve = frame.T.groupby(sleeve_of).sum().T
    return ValueHistory(total, by_sleeve.div(total, axis=0))


def review_start(ips: IPS, as_of: date) -> date:
    """Start of the latest review period, from the IPS monitoring frequency."""
    months = REVIEW_MONTHS[ips.rebalancing.monitoring_frequency]
    return (pd.Timestamp(as_of) - pd.DateOffset(months=months)).date()


def breach_periods(history: ValueHistory, ips: IPS, as_of: date) -> list[BreachPeriod]:
    """Runs of consecutive days, within the latest review period, when any SAA sleeve was outside its band."""
    start = pd.Timestamp(review_start(ips, as_of))
    weights = history.sleeve_weights.loc[history.sleeve_weights.index >= start]
    outside = pd.DataFrame({sleeve: _outside(weights.get(sleeve), band.min, band.max, weights.index)
                            for sleeve, band in ips.saa.items()}, index=weights.index)
    any_outside = outside.any(axis=1)
    periods: list[BreachPeriod] = []
    run_id = (any_outside != any_outside.shift()).cumsum()
    for _, run in outside[any_outside].groupby(run_id[any_outside]):
        sleeves = [s for s in ips.saa if run[s].any()]
        periods.append(BreachPeriod(run.index[0].date(), run.index[-1].date(), sleeves))
    return periods


def _outside(weights: pd.Series | None, low: float, high: float, index: pd.Index) -> pd.Series:
    if weights is None:  # nothing held in this sleeve: weight 0
        weights = pd.Series(0.0, index=index)
    return (weights < low - _TOLERANCE) | (weights > high + _TOLERANCE)
