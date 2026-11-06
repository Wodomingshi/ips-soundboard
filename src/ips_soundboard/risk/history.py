"""Value of the managed holdings over time, and band breaches in the latest review period.

Each lot counts from its purchase date, so the value steps up when money was invested. Limits:
positions sold and no longer in the holdings file are not known; lots without a purchase date
count from the start of the price history; cash is shown at today's balance throughout.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ips_soundboard.schemas.ips import IPS

REVIEW_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Lot:
    key: str  # price series id (ticker, "cash:CHF", or "untracked:..." without a series)
    sleeve: str
    value_today: float  # base currency
    bought: date | None  # purchase date; None = held throughout


@dataclass(frozen=True)
class ValueHistory:
    values: pd.Series  # managed portfolio value per day, in the base currency
    sleeve_weights: pd.DataFrame  # columns: sleeve ids; rows sum to 1
    notes: list[str] = field(default_factory=list)
    first_purchase: date | None = None  # earliest purchase date in the holdings file


@dataclass(frozen=True)
class BreachPeriod:
    start: date
    end: date
    sleeves: list[str]  # sleeve ids outside their band at some point in the period


def value_history(base_prices: pd.DataFrame, lots: Sequence[Lot]) -> ValueHistory:
    """Each lot's value follows its price series (scaled to today's value) from its purchase date."""
    if base_prices.empty:
        raise ValueError("no price history")
    calendar = base_prices.index
    first = calendar[0]
    values: dict[int, pd.Series] = {}
    sleeve_of: dict[int, str] = {}
    undated = early = 0
    for i, lot in enumerate(lots):
        series = base_prices[lot.key].ffill() if lot.key in base_prices else None
        if series is None or series.notna().sum() == 0:
            value = pd.Series(lot.value_today, index=calendar)  # no price series: flat at today's value
        else:
            value = series.bfill() * (lot.value_today / series.dropna().iloc[-1])
        start = pd.Timestamp(lot.bought) if lot.bought else first
        undated += lot.bought is None and not lot.key.startswith("cash:")
        if start < first:
            early += 1
            start = first
        values[i] = value.where(calendar >= start, 0.0)
        sleeve_of[i] = lot.sleeve

    frame = pd.DataFrame(values, index=calendar)
    total = frame.sum(axis=1)
    by_sleeve = frame.T.groupby(sleeve_of).sum().T
    weights = by_sleeve.div(total.where(total > 0), axis=0)
    notes = []
    if undated:
        notes.append(f"{undated} holding(s) without a purchase date are shown as held from the start.")
    if early:
        notes.append(f"{early} holding(s) bought before {first.date()} start at their value on that day.")
    dated = [lot.bought for lot in lots if lot.bought]
    return ValueHistory(total, weights, notes, min(dated) if dated else None)


def review_start(ips: IPS, as_of: date) -> date:
    """Start of the latest review period, from the IPS monitoring frequency."""
    months = REVIEW_MONTHS[ips.rebalancing.monitoring_frequency]
    return (pd.Timestamp(as_of) - pd.DateOffset(months=months)).date()


def breach_periods(history: ValueHistory, ips: IPS, as_of: date) -> list[BreachPeriod]:
    """Runs of consecutive days, within the latest review period, when any SAA sleeve was outside its band."""
    start = pd.Timestamp(review_start(ips, as_of))
    weights = history.sleeve_weights.loc[history.sleeve_weights.index >= start].dropna(how="all")
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
    return (weights.fillna(0.0) < low - _TOLERANCE) | (weights.fillna(0.0) > high + _TOLERANCE)
