"""Risk analysis of the managed portfolio, at today's weights, over historical data.

Positions without enough price history (or none, e.g. a bond without a ticker)
are excluded and listed with their weight; the remaining weights are rescaled,
and `coverage` says what share of the managed portfolio the numbers describe.
Cash has no price risk, but non-base cash carries FX risk.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

from ips_soundboard.engine.allocation import managed_weights
from ips_soundboard.engine.valuation import ValuedHolding
from ips_soundboard.marketdata.base import MarketData
from ips_soundboard.risk import metrics
from ips_soundboard.risk.history import ValueHistory, value_history
from ips_soundboard.risk.returns import base_currency_prices, daily_returns
from ips_soundboard.schemas.ips import IPS

_SCENARIOS_FILE = Path(__file__).with_name("scenarios.yaml")
UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Excluded:
    name: str
    weight: float  # share of the managed portfolio
    reason: str


@dataclass(frozen=True)
class Contribution:
    subject: str
    weight: float  # weight within the covered portfolio
    risk_share: float  # share of portfolio volatility; sums to 1


@dataclass(frozen=True)
class StressResult:
    name: str
    start: date
    end: date
    portfolio_return: float
    coverage: float  # share of the managed portfolio with history for this period


@dataclass(frozen=True)
class RiskReport:
    base_currency: str
    window_start: date
    window_end: date
    observations: int
    coverage: float
    covered_value: float
    volatility: float
    volatility_without_fx: float
    max_drawdown: float
    drawdown_peak: date | None
    drawdown_trough: date | None
    confidence: float
    horizon_days: int
    var: float
    cvar: float
    by_holding: list[Contribution]
    by_sleeve: list[Contribution]
    currency_exposure: dict[str, float]
    stress: list[StressResult]
    excluded: list[Excluded] = field(default_factory=list)
    history: ValueHistory | None = None  # today's holdings at past prices (see risk/history.py)

    @property
    def var_value(self) -> float:
        return self.var * self.covered_value

    @property
    def cvar_value(self) -> float:
        return self.cvar * self.covered_value


@dataclass
class _Position:
    key: str  # series id: canonical ticker, or "cash:CCY"
    label: str
    currency: str
    sleeve: str
    weight: float = 0.0
    value: float = 0.0
    has_series: bool = True


def analyse_risk(valued: Sequence[ValuedHolding], ips: IPS, provider: MarketData,
                 as_of: date | None = None) -> RiskReport:
    settings = ips.risk
    base = ips.base_currency
    end = as_of or date.today()
    start = end - timedelta(days=round(settings.lookback_years * 365.25))

    positions = _positions(valued, ips)
    if not positions:
        raise ValueError("no valued holdings in the managed portfolio")
    excluded = [Excluded(p.label, p.weight, "no market price series") for p in positions if not p.has_series]
    candidates = [p for p in positions if p.has_series]

    tickers = [p.key for p in candidates if not p.key.startswith("cash:")]
    currencies = sorted({p.currency for p in candidates} - {base})
    closes = provider.history(tickers, start, end) if tickers else pd.DataFrame()
    fx = provider.fx_history(currencies, base, start, end) if currencies else pd.DataFrame()
    closes = _add_cash_series(closes, fx, candidates)

    currency_of = {p.key: p.currency for p in candidates}
    prices_base = base_currency_prices(closes, currency_of, fx, base)
    returns = daily_returns(prices_base)
    local_returns = daily_returns(base_currency_prices(closes, {k: base for k in currency_of}, pd.DataFrame(), base))

    included, too_short = _enough_history(candidates, returns, settings.min_history_share, settings.lookback_years)
    excluded += too_short
    if not included:
        raise ValueError("no holding has enough price history to measure risk")

    keys = [p.key for p in included]
    sample = returns[keys].dropna()
    local_sample = local_returns[keys].reindex(sample.index).fillna(0.0)
    covered = sum(p.weight for p in included)
    weights = pd.Series({p.key: p.weight / covered for p in included})

    r_p = metrics.portfolio_returns(sample, weights)
    drawdown, peak, trough = metrics.max_drawdown(r_p)
    var, cvar = metrics.historical_var_cvar(r_p, settings.var_confidence, settings.var_horizon_days)
    shares = metrics.risk_contributions(sample, weights)

    return RiskReport(
        base_currency=base,
        window_start=sample.index[0].date(),
        window_end=sample.index[-1].date(),
        observations=len(sample),
        coverage=covered,
        covered_value=sum(p.value for p in included),
        volatility=metrics.annualised_volatility(r_p),
        volatility_without_fx=metrics.annualised_volatility(metrics.portfolio_returns(local_sample, weights)),
        max_drawdown=drawdown,
        drawdown_peak=peak.date() if peak is not None else None,
        drawdown_trough=trough.date() if trough is not None else None,
        confidence=settings.var_confidence,
        horizon_days=settings.var_horizon_days,
        var=var,
        cvar=cvar,
        by_holding=_by_holding(included, weights, shares),
        by_sleeve=_by_sleeve(included, weights, shares),
        currency_exposure=_currency_exposure(positions),
        stress=_stress(returns, included),
        excluded=sorted(excluded, key=lambda e: -e.weight),
        history=value_history(prices_base, [(p.key, p.sleeve, p.value) for p in positions]),
    )


# --- building blocks ------------------------------------------------------------

def _positions(valued: Sequence[ValuedHolding], ips: IPS) -> list[_Position]:
    """Managed holdings aggregated per series: lots summed by ticker, cash by currency."""
    by_key: dict[str, _Position] = {}
    for v, weight in managed_weights(valued, ips):
        h = v.holding
        if h.instrument_type == "cash":
            key, label, has_series = f"cash:{h.currency}", f"Cash {h.currency}", True
        elif h.ticker:
            key, label, has_series = h.ticker, h.name, True
        else:
            key, label, has_series = f"untracked:{h.name}", h.name, False
        position = by_key.setdefault(key, _Position(key, label, h.currency, h.sleeve or UNCLASSIFIED,
                                                    has_series=has_series))
        position.weight += weight
        position.value += v.value_base or 0.0
    return list(by_key.values())


def _add_cash_series(closes: pd.DataFrame, fx: pd.DataFrame, positions: list[_Position]) -> pd.DataFrame:
    """Cash is a constant price of 1 in its own currency; FX does the rest."""
    calendar = closes.index.union(fx.index)
    out = closes.reindex(calendar)
    for p in positions:
        if p.key.startswith("cash:"):
            out[p.key] = 1.0
    return out


def _enough_history(positions: list[_Position], returns: pd.DataFrame, min_share: float,
                    lookback_years: float) -> tuple[list[_Position], list[Excluded]]:
    included, excluded = [], []
    total = len(returns)
    for p in positions:
        available = int(returns[p.key].notna().sum()) if p.key in returns and total else 0
        if total and available / total >= min_share:
            included.append(p)
        else:
            years = lookback_years * available / total if total else 0.0
            excluded.append(Excluded(p.label, p.weight,
                                     f"only {years:.1f} years of price history (needs {min_share:.0%} of "
                                     f"{lookback_years:g} years)"))
    return included, excluded


def _by_holding(positions: list[_Position], weights: pd.Series, shares: pd.Series) -> list[Contribution]:
    rows = [Contribution(p.label, float(weights[p.key]), float(shares[p.key])) for p in positions]
    return sorted(rows, key=lambda c: -c.risk_share)


def _by_sleeve(positions: list[_Position], weights: pd.Series, shares: pd.Series) -> list[Contribution]:
    weight: dict[str, float] = defaultdict(float)
    share: dict[str, float] = defaultdict(float)
    for p in positions:
        weight[p.sleeve] += float(weights[p.key])
        share[p.sleeve] += float(shares[p.key])
    return sorted((Contribution(s, weight[s], share[s]) for s in weight), key=lambda c: -c.risk_share)


def _currency_exposure(positions: list[_Position]) -> dict[str, float]:
    exposure: dict[str, float] = defaultdict(float)
    total = sum(p.weight for p in positions)
    for p in positions:
        exposure[p.currency] += p.weight / total if total else 0.0
    return dict(sorted(exposure.items(), key=lambda kv: -kv[1]))


def load_scenarios() -> list[dict]:
    with _SCENARIOS_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _stress(returns: pd.DataFrame, positions: list[_Position]) -> list[StressResult]:
    """Each scenario uses every position with full history over that period (weights rescaled).

    Computed separately from the main sample, whose start is set by the youngest
    series: a fund listed in late January 2022 must not hide the whole 2022 shock.
    """
    results = []
    for s in load_scenarios():
        start, end = pd.Timestamp(s["start"]), pd.Timestamp(s["end"])
        if returns.empty or returns.index[0] > start or returns.index[-1] < end:
            continue
        window = returns.loc[start:end, [p.key for p in positions]]
        complete = [p for p in positions if window[p.key].notna().all()]
        covered = sum(p.weight for p in complete)
        if not complete or covered <= 0:
            continue
        weights = pd.Series({p.key: p.weight / covered for p in complete})
        path = metrics.portfolio_returns(window[weights.index], weights)
        results.append(StressResult(s["name"], s["start"], s["end"], float((1.0 + path).prod() - 1.0), covered))
    return results
