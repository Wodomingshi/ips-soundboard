from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ips_soundboard.risk.history import Lot, ValueHistory, breach_periods, review_start, value_history
from ips_soundboard.schemas.ips import IPS, load_ips

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "sample_investor_eur"
AS_OF = date(2026, 10, 30)


@pytest.fixture
def ips() -> IPS:
    return load_ips(SAMPLE_DIR / "ips.yaml")  # quarterly review; EU equity band 20-30%


def test_values_end_at_todays_value_and_scale_with_price() -> None:
    days = pd.bdate_range("2026-01-01", periods=4)
    prices = pd.DataFrame({"A": [10.0, 11.0, None, 20.0], "B": [None, None, 5.0, 5.0]}, index=days)
    h = value_history(prices, [Lot("A", "eq", 1000.0, None), Lot("B", "bonds", 500.0, None),
                               Lot("untracked:X", "eq", 200.0, None)])
    assert h.values.iloc[-1] == pytest.approx(1700.0)
    assert h.values.iloc[0] == pytest.approx(500.0 + 500.0 + 200.0)  # A at half its last price; B flat before listing
    assert h.values.iloc[2] == pytest.approx(550.0 + 500.0 + 200.0)  # missing price carried forward
    assert h.sleeve_weights.sum(axis=1).tolist() == pytest.approx([1.0] * 4)


def test_lots_count_from_their_purchase_date() -> None:
    days = pd.bdate_range("2026-01-01", periods=5)  # Thu 1 Jan ... Wed 7 Jan
    prices = pd.DataFrame({"A": [10.0, 10.0, 12.0, 12.0, 15.0]}, index=days)
    lots = [Lot("A", "eq", 1500.0, date(2026, 1, 1)),    # 100 units, bought at 10
            Lot("A", "eq", 1500.0, date(2026, 1, 6))]    # 100 more, bought at 12
    h = value_history(prices, lots)
    assert h.values.tolist() == pytest.approx([1000, 1000, 1200, 2400, 3000])  # jumps on the second purchase
    assert h.notes == []


def test_missing_dates_and_prices_are_assumed_and_noted() -> None:
    days = pd.bdate_range("2026-01-01", periods=3)
    prices = pd.DataFrame({"A": [10.0, 10.0, 20.0]}, index=days)
    lots = [Lot("A", "eq", 200.0, None),                  # no date: held from the start
            Lot("A", "eq", 200.0, date(2025, 6, 1)),      # bought before the price history starts
            Lot("A", "eq", 200.0, date(2026, 1, 2))]
    h = value_history(prices, lots)
    assert h.values.tolist() == pytest.approx([200, 300, 600])
    assert len(h.notes) == 2


def test_review_period_follows_the_ips(ips: IPS) -> None:
    assert review_start(ips, AS_OF) == date(2026, 7, 30)
    raw = ips.model_dump()
    raw["rebalancing"]["monitoring_frequency"] = "annual"
    assert review_start(IPS.model_validate(raw), AS_OF) == date(2025, 10, 30)


def weights_history(ips: IPS, eu: list[float], days: pd.DatetimeIndex) -> ValueHistory:
    """Every sleeve near target except EU equity, which follows `eu`; two sleeves absorb the difference."""
    rows = []
    for w in eu:
        row = {s: band.target for s, band in ips.saa.items()}
        half = (row["eu_equity"] - w) / 2
        row["world_ex_eu_equity"] += half
        row["eur_govt_bonds"] += half
        row["eu_equity"] = w
        rows.append(row)
    return ValueHistory(pd.Series(100.0, index=days), pd.DataFrame(rows, index=days))


def test_breaches_are_only_reported_in_the_latest_review_period(ips: IPS) -> None:
    days = pd.DatetimeIndex(["2026-01-15", "2026-08-03", "2026-08-04", "2026-09-01", "2026-10-30"])
    h = weights_history(ips, [0.40, 0.31, 0.32, 0.25, 0.33], days)  # January breach is too old to show
    periods = breach_periods(h, ips, AS_OF)
    assert [(p.start, p.end, p.sleeves) for p in periods] == [
        (date(2026, 8, 3), date(2026, 8, 4), ["eu_equity"]),
        (date(2026, 10, 30), date(2026, 10, 30), ["eu_equity"]),
    ]


def test_no_breach_no_periods(ips: IPS) -> None:
    days = pd.bdate_range("2026-08-01", "2026-10-30")
    assert breach_periods(weights_history(ips, [0.25] * len(days), days), ips, AS_OF) == []


def test_demo_history_ends_at_the_managed_value() -> None:
    from test_dashboard import sample_market

    from ips_soundboard.dashboard.pipeline import run_analysis
    from ips_soundboard.holdings import load_holdings

    a = run_analysis(load_holdings(SAMPLE_DIR / "holdings.csv"), load_ips(SAMPLE_DIR / "ips.yaml"),
                     sample_market(), as_of=AS_OF)
    history = a.risk.history
    assert history.values.iloc[-1] == pytest.approx(a.allocation.managed_value)
    assert history.sleeve_weights.iloc[-1]["eu_equity"] == pytest.approx(
        next(s.weight for s in a.allocation.sleeves if s.sleeve == "eu_equity"))
    assert history.values.index[0] < pd.Timestamp("2022-01-01")  # the full price history, not just a quarter
