from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ips_soundboard.engine.allocation import allocate, band_status, managed_weights
from ips_soundboard.engine.valuation import value_holdings
from ips_soundboard.holdings import load_holdings
from ips_soundboard.schemas.holdings import Holding
from ips_soundboard.schemas.ips import IPS, load_ips

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_investor_eur"
PRIVATE_DIR = ROOT / "private"

SAMPLE_PRICES = {
    "IMEU.AS": 30.0, "SXR8.DE": 500.0, "EIMI.AS": 32.0, "IEGA.AS": 110.0,
    "4GLD.DE": 80.0, "SAP.DE": 200.0, "ASML.AS": 700.0,
}


@pytest.fixture
def sample_ips() -> IPS:
    return load_ips(SAMPLE_DIR / "ips.yaml")


@pytest.fixture
def sample_allocation(sample_ips: IPS):
    valued = value_holdings(load_holdings(SAMPLE_DIR / "holdings.csv"), SAMPLE_PRICES, {}, "EUR")
    return allocate(valued, sample_ips)


def ips_with_reserve(sample_ips: IPS, reserve_sleeve: str = "reserve") -> IPS:
    raw = sample_ips.model_dump()
    raw["liquidity"]["reserve_sleeve"] = reserve_sleeve
    return IPS.model_validate(raw)


# --- sample investor: hand-checked numbers ----------------------------------
# Managed = 201,500 total - 85,000 pension = 116,500
#   eu_equity  27,000 + 8,000 + 8,400 = 43,400 -> 37.25% (band 20-30%: above)
#   world      30,000                         -> 25.75% (30-40%: below)
#   em          9,600                         ->  8.24% (2-8%: above)
#   bonds      19,800                         -> 17.00% (20-30%: below)
#   gold        7,200                         ->  6.18% (2-8%: within)
#   cash        6,500                         ->  5.58% (0-10%: within)

def test_sample_totals(sample_allocation) -> None:
    a = sample_allocation
    assert a.managed_value == pytest.approx(116_500)
    assert a.reported_only_value == pytest.approx(85_000)
    assert a.total_wealth == pytest.approx(201_500)
    assert a.outside_saa == {} and a.unvalued == []


def test_sample_drift_and_status(sample_allocation) -> None:
    got = {s.sleeve: (round(s.weight, 4), s.status) for s in sample_allocation.sleeves}
    assert got == {
        "eu_equity": (0.3725, "above"),
        "world_ex_eu_equity": (0.2575, "below"),
        "em_equity": (0.0824, "above"),
        "eur_govt_bonds": (0.17, "below"),
        "gold": (0.0618, "within"),
        "cash": (0.0558, "within"),
    }
    eu = next(s for s in sample_allocation.sleeves if s.sleeve == "eu_equity")
    assert eu.drift == pytest.approx(0.3725 - 0.25, abs=1e-4)


def test_sleeves_follow_ips_order(sample_allocation, sample_ips) -> None:
    assert [s.sleeve for s in sample_allocation.sleeves] == list(sample_ips.saa)


def test_reserve_not_checked_without_reserve_sleeve(sample_allocation) -> None:
    assert sample_allocation.reserve_required is None
    assert sample_allocation.reserve_ok is None


# --- rules ------------------------------------------------------------------

@pytest.mark.parametrize(("weight", "expected"), [(0.19, "below"), (0.20, "within"), (0.30, "within"), (0.31, "above")])
def test_band_edges_count_as_within(weight: float, expected: str) -> None:
    assert band_status(weight, 0.20, 0.30) == expected


def test_reserve_excluded_from_weights_and_checked(sample_ips: IPS) -> None:
    ips = ips_with_reserve(sample_ips)  # requires 6 x 3,500 = 21,000
    holdings = [
        Holding(name="ETF", account="brokerage", currency="EUR", manual_value=90_000, sleeve="eu_equity"),
        Holding(name="Reserve", account="brokerage", currency="EUR", quantity=15_000, instrument_type="cash", sleeve="reserve"),
    ]
    a = allocate(value_holdings(holdings, {}, {}, "EUR"), ips)
    assert a.managed_value == 90_000 and a.reserve_value == 15_000
    assert a.sleeves[0].weight == 1.0
    assert a.reserve_required == 21_000 and a.reserve_ok is False


def test_outside_saa_and_unclassified_stay_in_denominator(sample_ips: IPS) -> None:
    holdings = [
        Holding(name="ETF", account="brokerage", currency="EUR", manual_value=80, sleeve="eu_equity"),
        Holding(name="Silver", account="brokerage", currency="EUR", manual_value=15, sleeve="commodities"),
        Holding(name="Mystery", account="brokerage", currency="EUR", manual_value=5),
    ]
    a = allocate(value_holdings(holdings, {}, {}, "EUR"), sample_ips)
    assert a.managed_value == 100
    assert a.outside_saa == {"commodities": 15, "unclassified": 5}
    assert a.sleeves[0].weight == pytest.approx(0.80)


def test_out_of_scope_accounts_count_in_total_wealth_only(sample_ips: IPS) -> None:
    holdings = [
        Holding(name="ETF", account="brokerage", currency="EUR", manual_value=100, sleeve="eu_equity"),
        Holding(name="Old app", account="savings_app", currency="EUR", manual_value=40),
    ]
    a = allocate(value_holdings(holdings, {}, {}, "EUR"), sample_ips)
    assert a.managed_value == 100 and a.out_of_scope_value == 40 and a.total_wealth == 140


def test_unvalued_holdings_are_reported_not_counted(sample_ips: IPS) -> None:
    holdings = [
        Holding(name="ETF", account="brokerage", currency="EUR", manual_value=100, sleeve="eu_equity"),
        Holding(name="Ghost", account="brokerage", currency="EUR", ticker="ZZZ", quantity=1, sleeve="eu_equity"),
    ]
    a = allocate(value_holdings(holdings, {}, {}, "EUR"), sample_ips)
    assert a.managed_value == 100
    assert [v.holding.name for v in a.unvalued] == ["Ghost"]


def test_empty_portfolio_gives_zero_weights(sample_ips: IPS) -> None:
    a = allocate([], sample_ips)
    assert a.managed_value == 0 and all(s.weight == 0 for s in a.sleeves)


def test_managed_weights_per_holding(sample_allocation, sample_ips: IPS) -> None:
    valued = value_holdings(load_holdings(SAMPLE_DIR / "holdings.csv"), SAMPLE_PRICES, {}, "EUR")
    weights = {v.holding.name: w for v, w in managed_weights(valued, sample_ips)}
    assert "Company pension" not in weights
    assert weights["ASML Holding"] == pytest.approx(8_400 / 116_500)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_reserve_sleeve_cannot_be_an_saa_sleeve() -> None:
    raw = yaml.safe_load((SAMPLE_DIR / "ips.yaml").read_text(encoding="utf-8"))
    raw["liquidity"]["reserve_sleeve"] = "cash"
    with pytest.raises(ValidationError, match="counted twice"):
        IPS.model_validate(raw)


@pytest.mark.skipif(not (PRIVATE_DIR / "holdings.csv").exists(), reason="no private holdings on this machine")
def test_private_allocation_runs() -> None:
    holdings = load_holdings(PRIVATE_DIR / "holdings.csv")
    fx = {h.currency: 1.0 for h in holdings}  # dummy rates: checks the pipeline, not amounts
    a = allocate(value_holdings(holdings, {}, fx, "CHF"), load_ips(PRIVATE_DIR / "ips.yaml"))
    assert a.managed_value > 0 and a.unvalued == []
