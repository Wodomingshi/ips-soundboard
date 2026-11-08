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



@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_app_reads_a_portfolio_from_secrets() -> None:
    """A private hosted deployment stores the portfolio as app secrets; it becomes the first data option."""
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    set_provider(sample_market())
    try:
        at = streamlit_testing.AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=60)
        at.secrets["holdings_csv"] = (SAMPLE_DIR / "holdings.csv").read_text(encoding="utf-8")
        at.secrets["ips_yaml"] = (SAMPLE_DIR / "ips.yaml").read_text(encoding="utf-8")
        at.run()
        assert not at.exception, at.exception
        assert at.sidebar.radio[0].options[0] == "My portfolio"
        assert at.sidebar.radio[0].value == "My portfolio"
        assert any("116,500 EUR" in m.value for m in at.markdown)
    finally:
        set_provider(None)


def test_secrets_text_reads_back_identically(tmp_path) -> None:
    import sys
    import tomllib
    sys.path.insert(0, str(ROOT / "tools"))
    from make_secrets import secrets_toml

    for name in ("holdings.csv", "ips.yaml", "IPS.md"):
        (tmp_path / name).write_text((SAMPLE_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    text = secrets_toml(tmp_path)
    assert tomllib.loads(text)["holdings_csv"] == (SAMPLE_DIR / "holdings.csv").read_text(encoding="utf-8")
    odd = "Odd ''' quotes and \\ backslashes"
    (tmp_path / "IPS.md").write_text(odd, encoding="utf-8")
    assert tomllib.loads(secrets_toml(tmp_path))["ips_md"] == odd


def test_value_history_and_ranges(analysis) -> None:
    ranges = tables.history_ranges(analysis)
    assert list(ranges) == ["Last year", "Since first trade", "Max (5 years)"]
    assert ranges["Last year"] == date(2025, 10, 30)
    assert ranges["Since first trade"] == date(2022, 11, 2)  # earliest purchase in the sample holdings
    history = tables.history_frame(analysis, ranges["Since first trade"])
    assert history["Date"].iloc[0] >= pd.Timestamp("2022-11-02")
    assert history["Value"].iloc[-1] == pytest.approx(analysis.allocation.managed_value)
    assert len(tables.history_frame(analysis)) > len(history)  # max shows more
    breaches = tables.breach_frame(analysis)
    assert not breaches.empty and (breaches["Start"] >= pd.Timestamp("2026-07-30")).all()


def test_allocation_chart_adds_an_outside_saa_row() -> None:
    text = (SAMPLE_DIR / "holdings.csv").read_text(encoding="utf-8")
    text += "Clean Energy ETF,,brokerage,EUR,,,10000,etf,clean_energy,theme,,,,\n"
    from ips_soundboard.holdings import parse_holdings
    a = run_analysis(parse_holdings(text), load_ips(SAMPLE_DIR / "ips.yaml"), sample_market(), as_of=AS_OF,
                     with_risk=False)
    frame = tables.sleeve_chart_frame(a)
    outside = frame[frame["Sleeve"] == "Outside SAA"].iloc[0]
    assert outside["Weight"] == pytest.approx(10_000 / a.allocation.managed_value)
    assert (outside["Target"], outside["Max"], outside["Status"]) == (0.0, 0.0, "above")  # sample IPS allows no themes
    assert "Clean energy" in outside["Details"]
    assert list(tables.sleeve_chart_frame(run_analysis(load_holdings(SAMPLE_DIR / "holdings.csv"),
                load_ips(SAMPLE_DIR / "ips.yaml"), sample_market(), as_of=AS_OF, with_risk=False))["Sleeve"])[-1] == "Cash"
