from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ips_soundboard.display import label
from ips_soundboard.dashboard import run_analysis, tables
from ips_soundboard.dashboard.providers import get_provider, set_provider
from ips_soundboard.holdings import load_holdings
from ips_soundboard.marketdata import FakeProvider, Quote
from ips_soundboard.schemas.ips import load_ips

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_investor_eur"
AS_OF = date(2026, 10, 30)
PRICES = {"AMS:IMEU": 30.0, "ETR:SXR8": 500.0, "AMS:EIMI": 32.0, "AMS:IEGA": 110.0,
          "ETR:4GLD": 80.0, "ETR:SAP": 200.0, "AMS:ASML": 700.0}


def sample_market() -> FakeProvider:
    """Five years of synthetic EUR history for the sample investor's tickers, ending at today's prices."""
    days = pd.bdate_range("2021-10-01", AS_OF)
    rng = np.random.default_rng(7)
    closes = {}
    for ticker, last in PRICES.items():
        path = np.cumprod(1 + rng.normal(0.0003, 0.012, len(days)))
        closes[ticker] = last * path / path[-1]
    return FakeProvider(quotes={t: Quote(p, "EUR") for t, p in PRICES.items()},
                        closes=pd.DataFrame(closes, index=days))


@pytest.fixture
def analysis():
    holdings = load_holdings(SAMPLE_DIR / "holdings.csv")
    return run_analysis(holdings, load_ips(SAMPLE_DIR / "ips.yaml"), sample_market(), as_of=AS_OF)


def test_pipeline_runs_every_stage(analysis) -> None:
    assert analysis.risk is not None and analysis.risk_error is None
    assert analysis.allocation.managed_value == pytest.approx(116_500)
    assert {f.rule for f in analysis.findings} >= {"sleeve_band"}  # sample is deliberately off-target


def test_wealth_table_shares_sum_to_one(analysis) -> None:
    frame = tables.wealth_table(analysis)
    assert frame["Share of total"].sum() == pytest.approx(1.0)
    assert "Reported only (e.g. pensions)" in set(frame["Bucket"])


def test_sleeve_table_lists_saa_in_ips_order(analysis) -> None:
    frame = tables.sleeve_table(analysis)
    assert list(frame["Sleeve"]) == [label(s) for s in analysis.ips.saa]
    assert "World ex EU equity" in set(frame["Sleeve"])


def test_holdings_table_has_weights_only_for_managed_holdings(analysis) -> None:
    frame = tables.holdings_table(analysis).set_index("Name")
    assert pd.isna(frame.loc["Company pension", "Weight"])
    assert frame["Weight"].sum() == pytest.approx(1.0)
    assert frame.loc["SAP SE", "Return (local)"] == pytest.approx(200 / 124 - 1)
    assert frame.loc["SAP SE", "Priced from"] == "live price"


def test_risk_tables(analysis) -> None:
    assert tables.contribution_table(analysis)["Share of risk"].sum() == pytest.approx(1.0)
    assert set(tables.risk_headline(analysis)["Measure"]) >= {"Maximum drawdown", "Volatility (annualised)"}
    assert not tables.stress_table(analysis).empty


def test_new_money_table_covers_every_saa_sleeve(analysis) -> None:
    from ips_soundboard.dashboard.pipeline import new_money_plan

    plan = new_money_plan(analysis, 9_000)
    frame = tables.new_money_table(plan)
    assert list(frame["Sleeve"]) == [label(s) for s in analysis.ips.saa]
    assert frame["Buy"].sum() == pytest.approx(plan.invested)
    assert (frame["Buy"] > 0).sum() <= 2  # the sample IPS caps new money at 2 sleeves


def test_risk_failure_is_reported_not_raised() -> None:
    holdings = load_holdings(SAMPLE_DIR / "holdings.csv")
    a = run_analysis(holdings, load_ips(SAMPLE_DIR / "ips.yaml"), FakeProvider(), as_of=AS_OF)  # no history
    assert a.risk is None and "price history" in a.risk_error
    assert tables.risk_headline(a).empty


def test_provider_override() -> None:
    fake = FakeProvider()
    set_provider(fake)
    try:
        assert get_provider() is fake
    finally:
        set_provider(None)


# --- the real Streamlit app, run headless --------------------------------------------

@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_app_renders_demo_without_errors() -> None:
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    set_provider(sample_market())
    try:
        at = streamlit_testing.AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=60)
        at.run()
        at.sidebar.radio[0].set_value("Demo: sample investor (fictional)").run()
        assert not at.exception, at.exception
        assert [t.label for t in at.tabs] == ["Overview", "Rules", "Holdings", "Risk", "New money"]
        assert any('class="kpis"' in m.value and "Total wealth" in m.value for m in at.markdown)
        assert any("Volatility (annualised)" in m.value for m in at.markdown)
        assert any('class="finding"' in m.value and "Breach" in m.value for m in at.markdown)
        at.number_input[0].set_value(9_000).run()
        assert not at.exception, at.exception
        assert any("Buy " in m.value for m in at.markdown)
    finally:
        set_provider(None)



def test_value_history_and_rebalancing_summary(analysis) -> None:
    history = tables.history_frame(analysis)
    assert history["Value"].iloc[-1] == pytest.approx(analysis.allocation.managed_value)
    breaches = tables.breach_frame(analysis)
    assert not breaches.empty and (breaches["Start"] >= pd.Timestamp("2026-07-30")).all()
    summary = tables.rebalancing_summary(analysis)
    assert summary.startswith("Rebalancing needed: in the last quarter (since 2026-07-30)")
    assert "EU equity" in summary
