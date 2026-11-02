from pathlib import Path

import pytest

from ips_soundboard.engine.valuation import (
    total_value,
    unvalued,
    value_holdings,
)
from ips_soundboard.holdings import load_holdings
from ips_soundboard.schemas.holdings import Holding

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_investor_eur" / "holdings.csv"
PRIVATE = ROOT / "private" / "holdings.csv"


def make(**kwargs) -> Holding:
    defaults = {"name": "X", "account": "brokerage", "currency": "USD"}
    return Holding(**(defaults | kwargs))


def value_one(holding: Holding, prices=None, fx=None, base="CHF"):
    return value_holdings([holding], prices or {}, fx or {}, base)[0]


# --- precedence -------------------------------------------------------------

def test_live_price_times_quantity() -> None:
    v = value_one(make(ticker="AAA", quantity=10), prices={"AAA": 5.0}, fx={"USD": 0.8})
    assert (v.value_local, v.value_base, v.price_source) == (50.0, 40.0, "live")


def test_live_price_wins_over_manual_value() -> None:
    v = value_one(make(ticker="AAA", quantity=10, manual_value=999), prices={"AAA": 5.0}, fx={"USD": 1})
    assert v.price_source == "live" and v.value_local == 50.0


def test_manual_value_when_no_live_price() -> None:
    v = value_one(make(ticker="AAA", quantity=10, manual_value=70), fx={"USD": 1})
    assert (v.value_local, v.price_source) == (70, "manual_value")


def test_manual_price_is_last_priced_fallback() -> None:
    v = value_one(make(ticker="BOND", quantity=20, manual_price=99.5), fx={"USD": 1})
    assert (v.value_local, v.price_source) == (1990.0, "manual_price")


def test_cash_quantity_is_the_balance() -> None:
    v = value_one(make(name="USD cash", instrument_type="cash", quantity=593.38), fx={"USD": 0.82})
    assert v.price_source == "cash"
    assert v.value_base == pytest.approx(486.57, abs=0.01)


def test_base_currency_needs_no_fx_entry() -> None:
    v = value_one(make(currency="CHF", manual_value=100))
    assert v.value_base == 100


# --- unvalued holdings are flagged, not zeroed ------------------------------

def test_missing_price_and_fallback_is_flagged() -> None:
    v = value_one(make(ticker="ZZZ", quantity=3), fx={"USD": 1})
    assert not v.is_valued
    assert v.problem == "no live price and no manual fallback"


def test_missing_fx_is_flagged_but_local_value_kept() -> None:
    v = value_one(make(currency="JPY", ticker="T", quantity=100), prices={"T": 10})
    assert v.value_local == 1000 and v.value_base is None
    assert v.problem == "no FX rate for JPY->CHF"


def test_totals_exclude_unvalued_holdings() -> None:
    valued = value_holdings(
        [make(currency="CHF", manual_value=100), make(name="Ghost", ticker="ZZZ", quantity=1)],
        prices={}, fx={}, base_currency="CHF",
    )
    assert total_value(valued) == 100
    assert [v.holding.name for v in unvalued(valued)] == ["Ghost"]


def test_non_positive_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="FX rate must be positive"):
        value_holdings([], prices={}, fx={"USD": 0}, base_currency="CHF")
    with pytest.raises(ValueError, match="price must be positive"):
        value_holdings([], prices={"AAA": -1}, fx={}, base_currency="CHF")


# --- sample investor --------------------------------------------------------

SAMPLE_PRICES = {
    "IMEU.AS": 30.0, "SXR8.DE": 500.0, "EIMI.AS": 32.0, "IEGA.AS": 110.0,
    "4GLD.DE": 80.0, "SAP.DE": 200.0, "ASML.AS": 700.0,
}


def test_sample_portfolio_values() -> None:
    valued = value_holdings(load_holdings(SAMPLE), SAMPLE_PRICES, fx={}, base_currency="EUR")
    assert unvalued(valued) == []
    # 27,000 + 30,000 + 9,600 + 19,800 + 7,200 + 8,000 + 8,400 + 6,500 cash + 85,000 pension
    assert total_value(valued) == pytest.approx(201_500)
    sources = {v.holding.name: v.price_source for v in valued}
    assert sources["Cash EUR"] == "cash"
    assert sources["Company pension"] == "manual_value"


@pytest.mark.skipif(not PRIVATE.exists(), reason="no private holdings on this machine")
def test_private_holdings_value_with_fallbacks_only() -> None:
    """With no live prices, every private holding should still value from its fallbacks."""
    holdings = load_holdings(PRIVATE)
    fx = {h.currency: 1.0 for h in holdings}  # dummy rates: this checks coverage, not amounts
    valued = value_holdings(holdings, prices={}, fx=fx, base_currency="CHF")
    assert unvalued(valued) == []
