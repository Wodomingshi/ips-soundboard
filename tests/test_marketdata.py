from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ips_soundboard.engine.valuation import unvalued, value_holdings
from ips_soundboard.holdings import load_holdings
from ips_soundboard.marketdata import FakeProvider, Quote, YahooProvider, take_snapshot
from ips_soundboard.marketdata.base import normalise_quote
from ips_soundboard.marketdata.snapshot import load_snapshot, save_snapshot
from ips_soundboard.marketdata.tickers import split_ticker, to_provider_symbol
from ips_soundboard.schemas.holdings import Holding

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_investor_eur" / "holdings.csv"


# --- ticker translation (config-driven) ---------------------------------------

@pytest.mark.parametrize(("canonical", "yahoo"), [
    ("SWX:NESN", "NESN.SW"),
    ("ETR:SIE", "SIE.DE"),
    ("AMS:ASML", "ASML.AS"),
    ("NYSEARCA:VPU", "VPU"),
    ("NYSE:BRK.B", "BRK-B"),
    ("OTCMKTS:UNCRY", "UNCRY"),
    ("NESN.SW", "NESN.SW"),  # no prefix: passed through
])
def test_to_yahoo_symbol(canonical: str, yahoo: str) -> None:
    assert to_provider_symbol(canonical, "yahoo") == yahoo


def test_unknown_exchange_says_where_to_fix_it() -> None:
    with pytest.raises(KeyError, match="exchanges.yaml"):
        to_provider_symbol("XYZ:ABC")


def test_split_ticker() -> None:
    assert split_ticker(" swx:NESN ") == ("SWX", "NESN")


# --- quote normalisation ------------------------------------------------------

def test_pence_quotes_become_pounds() -> None:
    assert normalise_quote(1250.0, "GBp") == Quote(12.5, "GBP")
    assert normalise_quote(99.0, "usd") == Quote(99.0, "USD")


# --- Yahoo provider, with a recorded fake client ------------------------------

class RecordedClient:
    """Stands in for yfinance: returns what Yahoo returned, keyed by Yahoo symbol."""

    def __init__(self) -> None:
        self.last = {
            "NESN.SW": (84.87, "CHF"),
            "VPU": (187.58, "USD"),
            "VOD.L": (7250.0, "GBp"),
            "USDCHF=X": (0.8201, "CHF"),
            "EURCHF=X": (0.9192, "CHF"),
        }
        self.requested: list[str] = []

    def last_price(self, symbol: str):
        self.requested.append(symbol)
        return self.last.get(symbol)

    def closes(self, symbols, start, end):
        index = pd.to_datetime(["2026-10-29", "2026-10-30"])
        data = {"NESN.SW": [84.0, 84.87], "VPU": [186.0, 187.58]}
        return pd.DataFrame({s: data[s] for s in symbols if s in data}, index=index)


def test_yahoo_latest_prices_translate_and_skip_failures() -> None:
    client = RecordedClient()
    quotes = YahooProvider(client).latest_prices(["SWX:NESN", "NYSEARCA:VPU", "LON:VOD", "NYSE:GONE", "SWX:NESN"])
    assert quotes == {"SWX:NESN": Quote(84.87, "CHF"), "NYSEARCA:VPU": Quote(187.58, "USD"),
                      "LON:VOD": Quote(72.5, "GBP")}
    assert client.requested.count("NESN.SW") == 1  # duplicates fetched once


def test_yahoo_fx_skips_base_and_missing_pairs() -> None:
    rates = YahooProvider(RecordedClient()).fx_rates(["USD", "EUR", "CHF", "JPY"], "CHF")
    assert rates == {"USD": 0.8201, "EUR": 0.9192}


def test_yahoo_history_uses_canonical_columns() -> None:
    closes = YahooProvider(RecordedClient()).history(
        ["SWX:NESN", "NYSEARCA:VPU", "NYSE:GONE"], date(2026, 10, 29), date(2026, 10, 30))
    assert list(closes.columns) == ["SWX:NESN", "NYSEARCA:VPU"]
    assert closes.loc["2026-10-30", "SWX:NESN"] == 84.87


# --- snapshot: from holdings to valuation inputs -------------------------------

def test_snapshot_feeds_valuation_end_to_end() -> None:
    holdings = load_holdings(SAMPLE)
    provider = FakeProvider(quotes={
        "AMS:IMEU": Quote(30.0, "EUR"), "ETR:SXR8": Quote(500.0, "EUR"), "AMS:EIMI": Quote(32.0, "EUR"),
        "AMS:IEGA": Quote(110.0, "EUR"), "ETR:4GLD": Quote(80.0, "EUR"), "ETR:SAP": Quote(200.0, "EUR"),
        "AMS:ASML": Quote(700.0, "EUR"),
    })
    snap = take_snapshot(holdings, "EUR", provider, as_of=date(2026, 11, 2))
    assert snap.problems == []
    valued = value_holdings(holdings, snap.prices, snap.fx, snap.base_currency)
    assert unvalued(valued) == []
    assert all(v.price_source == "live" for v in valued if v.holding.ticker)


def test_snapshot_reports_missing_quotes_fx_and_currency_mismatch() -> None:
    holdings = [
        Holding(name="A", account="brokerage", currency="USD", ticker="NYSE:AAA", quantity=1),
        Holding(name="B", account="brokerage", currency="GBP", ticker="LON:BBB", quantity=1),
        Holding(name="C", account="brokerage", currency="JPY", ticker="TYO:CCC", quantity=1),
    ]
    provider = FakeProvider(quotes={"NYSE:AAA": Quote(10.0, "USD"), "LON:BBB": Quote(5.0, "USD")},
                            fx={"USD": 0.82, "GBP": 1.10})
    snap = take_snapshot(holdings, "CHF", provider)
    assert snap.prices == {"NYSE:AAA": 10.0}
    assert "LON:BBB: quoted in USD, but the holding is in GBP; price not used" in snap.problems
    assert "TYO:CCC: no quote from provider" in snap.problems
    assert "JPY/CHF: no FX rate from provider" in snap.problems


def test_snapshot_cache_round_trip(tmp_path: Path) -> None:
    provider = FakeProvider(quotes={"NYSE:AAA": Quote(10.0, "USD")}, fx={"USD": 0.82})
    holdings = [Holding(name="A", account="brokerage", currency="USD", ticker="NYSE:AAA", quantity=1)]
    snap = take_snapshot(holdings, "CHF", provider, as_of=date(2026, 11, 2))
    save_snapshot(snap, tmp_path)
    assert load_snapshot("CHF", date(2026, 11, 2), tmp_path) == snap
    assert load_snapshot("CHF", date(2026, 11, 3), tmp_path) is None


def test_fake_history_window() -> None:
    closes = pd.DataFrame({"X": [1.0, 2.0, 3.0]}, index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]))
    window = FakeProvider(closes=closes).history(["X", "Y"], date(2026, 1, 2), date(2026, 1, 3))
    assert list(window["X"]) == [2.0, 3.0] and list(window.columns) == ["X"]


def test_yfinance_pandas_warnings_are_silenced_after_yfinance_import() -> None:
    """yfinance re-enables DeprecationWarnings for its modules on import; ours must win."""
    import warnings

    from ips_soundboard.marketdata.yahoo import quiet_yfinance_warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        quiet_yfinance_warnings()
        warnings.warn_explicit("The inplace keyword in DataFrame.rename is deprecated", DeprecationWarning,
                               filename="yfinance/multi.py", lineno=220, module="yfinance.multi")
        warnings.warn_explicit("Something else is deprecated", DeprecationWarning,
                               filename="yfinance/multi.py", lineno=221, module="yfinance.multi")
    messages = [str(w.message) for w in caught]
    assert messages == ["Something else is deprecated"]  # only the pandas 'inplace' noise is hidden
