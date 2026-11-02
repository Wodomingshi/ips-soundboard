"""Risk measures on daily return series. Pure functions: no I/O, no market data.

Conventions: returns are simple daily returns; weights sum to 1; volatility is
annualised with 252 trading days; losses (VaR, CVaR, drawdown) are reported as
positive or negative fractions as documented on each function.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def portfolio_returns(asset_returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Daily returns of a portfolio held at constant weights (rebalanced daily)."""
    return asset_returns[weights.index] @ weights


def annualised_volatility(returns: pd.Series) -> float:
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))


def max_drawdown(returns: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None]:
    """Worst peak-to-trough fall as a negative fraction, with peak and trough dates."""
    wealth = (1.0 + returns).cumprod()
    running_peak = wealth.cummax()
    drawdown = wealth / running_peak - 1.0
    if drawdown.empty or drawdown.min() >= 0:
        return 0.0, None, None
    trough = drawdown.idxmin()
    peak = wealth.loc[:trough].idxmax()
    return float(drawdown.min()), peak, trough


def horizon_returns(returns: pd.Series, horizon_days: int) -> pd.Series:
    """Overlapping compounded returns over `horizon_days` (e.g. 21 = about one month)."""
    if horizon_days == 1:
        return returns.dropna()
    log_growth = np.log1p(returns).rolling(horizon_days).sum()
    return np.expm1(log_growth).dropna()


def historical_var_cvar(returns: pd.Series, confidence: float, horizon_days: int) -> tuple[float, float]:
    """Historical-simulation VaR and CVaR over the horizon, as positive loss fractions.

    VaR: the loss exceeded in (1 - confidence) of historical periods.
    CVaR: the average loss in those worst periods (expected shortfall).
    Historical rather than normal VaR: real return tails are fatter than a bell curve.
    """
    sample = horizon_returns(returns, horizon_days)
    if sample.empty:
        raise ValueError("not enough history for the VaR horizon")
    cutoff = float(sample.quantile(1.0 - confidence))
    tail = sample[sample <= cutoff]
    return -cutoff, -float(tail.mean())


def risk_contributions(asset_returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Euler decomposition of portfolio volatility: each asset's share, summing to 1.

    share_i = w_i * (Sigma w)_i / (w' Sigma w), with Sigma the sample covariance.
    A negative share means the asset reduces total risk (a diversifier).
    """
    cov = asset_returns[weights.index].cov()
    marginal = cov @ weights
    variance = float(weights @ marginal)
    if variance <= 0:
        return pd.Series(0.0, index=weights.index)
    return weights * marginal / variance


def period_return(returns: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    """Compounded return over [start, end], or None if the history doesn't cover the period."""
    if returns.empty or returns.index[0] > start or returns.index[-1] < end:
        return None
    window = returns.loc[start:end]
    return float((1.0 + window).prod() - 1.0)
