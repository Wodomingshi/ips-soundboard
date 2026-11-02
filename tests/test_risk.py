from datetime import date

import numpy as np
import pandas as pd
import pytest

from ips_soundboard.engine.valuation import value_holdings
from ips_soundboard.marketdata import FakeProvider
from ips_soundboard.risk import analyse_risk, metrics
from ips_soundboard.risk.returns import base_currency_prices, daily_returns
from ips_soundboard.schemas.holdings import Holding
from ips_soundboard.schemas.ips import IPS

# --- metrics: hand-checkable cases ----------------------------------------------


def test_max_drawdown_finds_peak_and_trough() -> None:
    # wealth path: 100 -> 120 -> 90 -> 110 ; worst fall 120 -> 90 = -25%
    r = pd.Series([0.20, -0.25, 110 / 90 - 1], index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    dd, peak, trough = metrics.max_drawdown(r)
    assert dd == pytest.approx(-0.25)
    assert (peak, trough) == (pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03"))


def test_max_drawdown_is_zero_when_prices_only_rise() -> None:
    assert metrics.max_drawdown(pd.Series([0.01, 0.02]))[0] == 0.0


def test_volatility_is_annualised_with_252_days() -> None:
    r = pd.Series([0.01, -0.01] * 50)
    assert metrics.annualised_volatility(r) == pytest.approx(r.std() * np.sqrt(252))


def test_one_day_historical_var_and_cvar() -> None:
    # 100 daily returns: -10%, -9%, ..., +89%  -> 5th percentile is about -5.05%
    r = pd.Series(np.arange(-10, 90) / 100)
    var, cvar = metrics.historical_var_cvar(r, confidence=0.95, horizon_days=1)
    assert var == pytest.approx(-r.quantile(0.05))
    assert cvar == pytest.approx(-r[r <= r.quantile(0.05)].mean())
    assert cvar > var  # the average tail loss is worse than the threshold


def test_horizon_returns_compound() -> None:
    r = pd.Series([0.10, 0.10, 0.10])
    assert list(metrics.horizon_returns(r, 2).round(6)) == [0.21, 0.21]


def test_var_needs_enough_history_for_its_horizon() -> None:
    with pytest.raises(ValueError, match="not enough history"):
        metrics.historical_var_cvar(pd.Series([0.01] * 5), 0.95, horizon_days=21)


def test_risk_contributions_sum_to_one_and_split_evenly_for_twins() -> None:
    rng = np.random.default_rng(0)
    returns = pd.DataFrame(rng.normal(0, 0.01, size=(2000, 2)), columns=["A", "B"])
    shares = metrics.risk_contributions(returns, pd.Series({"A": 0.5, "B": 0.5}))
    assert shares.sum() == pytest.approx(1.0)
    assert shares["A"] == pytest.approx(0.5, abs=0.05)


def test_a_riskier_asset_contributes_more_than_its_weight() -> None:
    rng = np.random.default_rng(1)
    returns = pd.DataFrame({"calm": rng.normal(0, 0.005, 2000), "wild": rng.normal(0, 0.03, 2000)})
    shares = metrics.risk_contributions(returns, pd.Series({"calm": 0.5, "wild": 0.5}))
    assert shares["wild"] > 0.9


def test_period_return_needs_full_coverage() -> None:
    r = pd.Series([0.1, 0.1], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    assert metrics.period_return(r, pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")) == pytest.approx(0.21)
    assert metrics.period_return(r, pd.Timestamp("2023-12-01"), pd.Timestamp("2024-01-03")) is None


# --- FX conversion ------------------------------------------------------------------


def test_fx_moves_change_base_currency_returns() -> None:
    days = pd.to_datetime(["2024-01-02", "2024-01-03"])
    closes = pd.DataFrame({"US": [100.0, 100.0]}, index=days)   # flat in USD
    fx = pd.DataFrame({"USD": [0.80, 0.808]}, index=days)        # USD +1% vs CHF
    r = daily_returns(base_currency_prices(closes, {"US": "USD"}, fx, "CHF"))
    assert r["US"].iloc[0] == pytest.approx(0.01)


def test_missing_fx_history_gives_no_returns() -> None:
    days = pd.to_datetime(["2024-01-02", "2024-01-03"])
    prices = base_currency_prices(pd.DataFrame({"JP": [1.0, 1.1]}, index=days), {"JP": "JPY"}, pd.DataFrame(), "CHF")
    assert prices["JP"].isna().all()


# --- full analysis with a fake market -------------------------------------------------

DAYS = pd.bdate_range("2021-11-01", "2026-10-30")


def fake_market() -> FakeProvider:
    rng = np.random.default_rng(42)
    n = len(DAYS)
    closes = pd.DataFrame({
        "SWX:CALM": 100 * np.cumprod(1 + rng.normal(0.0002, 0.006, n)),
        "NYSE:WILD": 50 * np.cumprod(1 + rng.normal(0.0004, 0.025, n)),
        "NYSE:YOUNG": 20 * np.cumprod(1 + rng.normal(0.0, 0.01, n)),
    }, index=DAYS)
    closes.loc[: DAYS[-200], "NYSE:YOUNG"] = np.nan  # listed less than a year ago
    fx = pd.DataFrame({"USD": 0.85 * np.cumprod(1 + rng.normal(0, 0.004, n))}, index=DAYS)
    return FakeProvider(closes=closes, fx_closes=fx)


def holding(name: str, value: float, currency: str, **kw) -> Holding:
    return Holding(name=name, account="brokerage", currency=currency, manual_value=value, **kw)


@pytest.fixture
def ips() -> IPS:
    return IPS.model_validate({
        "version": "t", "base_currency": "CHF", "scope": {"managed": ["brokerage"]},
        "saa": {"ch_equity": {"target": 0.5, "min": 0, "max": 1}, "us_equity": {"target": 0.5, "min": 0, "max": 1}},
    })


@pytest.fixture
def report(ips: IPS):
    holdings = [
        holding("Calm CH", 40_000, "CHF", ticker="SWX:CALM", sleeve="ch_equity"),
        holding("Wild US", 30_000, "USD", ticker="NYSE:WILD", sleeve="us_equity"),
        holding("Young US", 10_000, "USD", ticker="NYSE:YOUNG", sleeve="us_equity"),
        holding("Perp bond", 10_000, "USD"),                                           # no ticker
        holding("Cash CHF", 5_000, "CHF", instrument_type="cash", sleeve="ch_equity"),
        holding("Cash USD", 5_000, "USD", instrument_type="cash", sleeve="us_equity"),
    ]
    valued = value_holdings(holdings, prices={}, fx={"USD": 0.82}, base_currency="CHF")
    return analyse_risk(valued, ips, fake_market(), as_of=date(2026, 10, 30))


def test_short_and_untracked_holdings_are_excluded_with_reasons(report) -> None:
    reasons = {e.name: e.reason for e in report.excluded}
    assert reasons["Perp bond"] == "no market price series"
    assert reasons["Young US"].startswith("only 0.") and "needs 80% of 5 years" in reasons["Young US"]


def test_coverage_is_the_included_share(report) -> None:
    # managed = 40,000 + 24,600 + 8,200 + 8,200 + 5,000 + 4,100 = 90,100 CHF
    # included = Calm 40,000 + Wild 24,600 + cash 5,000 + 4,100 = 73,700
    assert report.covered_value == pytest.approx(73_700)
    assert report.coverage == pytest.approx(73_700 / 90_100)


def test_risk_shares_sum_to_one_and_wild_dominates(report) -> None:
    assert sum(c.risk_share for c in report.by_holding) == pytest.approx(1.0)
    assert report.by_holding[0].subject == "Wild US"
    assert {c.subject for c in report.by_sleeve} == {"ch_equity", "us_equity"}


def test_chf_cash_has_no_risk_but_usd_cash_does(report) -> None:
    share = {c.subject: c.risk_share for c in report.by_holding}
    assert share["Cash CHF"] == pytest.approx(0.0, abs=1e-12)
    assert share["Cash USD"] != pytest.approx(0.0, abs=1e-6)


def test_headline_numbers_are_consistent(report) -> None:
    assert 0 < report.volatility < 0.5
    assert report.cvar >= report.var > 0
    assert report.var_value == pytest.approx(report.var * report.covered_value)
    assert report.max_drawdown < 0
    assert report.volatility != report.volatility_without_fx  # FX matters for a USD-heavy book


def test_currency_exposure_covers_all_managed_holdings(report) -> None:
    assert sum(report.currency_exposure.values()) == pytest.approx(1.0)
    assert report.currency_exposure["USD"] == pytest.approx(45_100 / 90_100)


def test_only_covered_stress_scenarios_are_reported(report) -> None:
    names = [s.name for s in report.stress]
    assert any(n.startswith("2022") for n in names)
    assert any(n.startswith("2025") for n in names)
    assert not any(n.startswith("2020") for n in names)  # before the 5-year window


def test_nothing_to_measure_raises(ips: IPS) -> None:
    valued = value_holdings([holding("Perp bond", 10_000, "CHF")], {}, {}, "CHF")
    with pytest.raises(ValueError, match="enough price history"):
        analyse_risk(valued, ips, fake_market(), as_of=date(2026, 10, 30))


def test_risk_settings_come_from_the_ips(ips: IPS) -> None:
    assert (ips.risk.lookback_years, ips.risk.var_confidence, ips.risk.var_horizon_days) == (5.0, 0.95, 21)
