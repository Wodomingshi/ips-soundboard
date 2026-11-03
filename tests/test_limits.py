from pathlib import Path

import pytest
from pydantic import ValidationError

from ips_soundboard.engine.limits import check_limits
from ips_soundboard.engine.valuation import value_holdings
from ips_soundboard.holdings import load_holdings
from ips_soundboard.schemas.holdings import Holding
from ips_soundboard.schemas.ips import IPS, load_ips

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_investor_eur"
PRIVATE_DIR = ROOT / "private"


@pytest.fixture
def ips() -> IPS:
    """Sample IPS (buy 5%, hard 8%, picks 20%) with a theme budget and a reserve sleeve added."""
    raw = load_ips(SAMPLE_DIR / "ips.yaml").model_dump()
    raw["taa"]["themes"] = {"max_total": 0.10, "max_per_theme": 0.03, "max_count": 2, "hard_limit_per_theme": 0.05}
    raw["liquidity"]["reserve_sleeve"] = "reserve"
    return IPS.model_validate(raw)


def h(name: str, value: float, sleeve: str = "eu_equity", **kwargs) -> Holding:
    return Holding(name=name, account="brokerage", currency="EUR", manual_value=value, sleeve=sleeve, **kwargs)


def balanced(extra: list[Holding], total: float = 100.0) -> list[Holding]:
    """Pad `extra` with core ETFs at SAA target weights so sleeve bands stay within."""
    targets = {"eu_equity": 0.25, "world_ex_eu_equity": 0.35, "em_equity": 0.05,
               "eur_govt_bonds": 0.25, "gold": 0.05, "cash": 0.05}
    used: dict[str, float] = {}
    for x in extra:
        used[x.sleeve] = used.get(x.sleeve, 0.0) + (x.manual_value or 0.0)
    pads = [h(f"{s} ETF", total * t - used.get(s, 0.0), s, instrument_type="etf", role="core")
            for s, t in targets.items() if total * t - used.get(s, 0.0) > 0]
    reserve = h("Reserve", 21_000, "reserve", instrument_type="cash")
    return extra + pads + [reserve]


def run(holdings: list[Holding], ips: IPS):
    return check_limits(value_holdings(holdings, {}, {}, "EUR"), ips)


def rules(findings) -> dict[tuple[str, str], str]:
    return {(f.rule, f.subject): f.severity for f in findings}


def test_clean_portfolio_has_no_findings(ips: IPS) -> None:
    assert run(balanced([]), ips) == []


# --- single names -------------------------------------------------------------

def test_stock_above_buy_limit_is_a_warning(ips: IPS) -> None:
    found = rules(run(balanced([h("SAP", 6, instrument_type="stock", role="satellite")]), ips))
    assert found == {("single_name_buy_limit", "SAP"): "warning"}


def test_stock_above_hard_limit_is_a_breach(ips: IPS) -> None:
    found = run(balanced([h("SAP", 9, instrument_type="stock", role="satellite")]), ips)
    assert rules(found) == {("single_name_hard_limit", "SAP"): "breach"}
    assert "trim back to the buy limit" in found[0].message


def test_lots_of_one_ticker_are_summed(ips: IPS) -> None:
    lots = [h("SAP lot 1", 3, ticker="SAP.DE", instrument_type="stock", role="satellite"),
            h("SAP lot 2", 3, ticker="SAP.DE", instrument_type="stock", role="satellite")]
    found = run(balanced(lots), ips)
    assert rules(found) == {("single_name_buy_limit", "SAP.DE"): "warning"}
    # subject keeps the ticker (machine id); the message names the holding for people
    assert found[0].message.startswith("SAP lot 1 is 6.0%, above the 5.0% buy limit")


def test_sleeve_messages_use_readable_names(ips: IPS) -> None:
    found = run(balanced([h("Extra EU", 40, instrument_type="etf", role="core")]), ips)
    band = [f for f in found if f.rule == "sleeve_band"]
    assert band and band[0].subject == "eu_equity"
    assert band[0].message.startswith("EU equity is ")
    assert "band (target 25.0%)" in band[0].message


def test_core_stocks_still_have_per_name_limits(ips: IPS) -> None:
    found = rules(run(balanced([h("Nestle", 6, instrument_type="stock", role="core")]), ips))
    assert found == {("single_name_buy_limit", "Nestle"): "warning"}


def test_etfs_are_not_single_names(ips: IPS) -> None:
    assert run(balanced([h("Big ETF", 25, instrument_type="etf", role="core")]), ips) == []


# --- picks cap ----------------------------------------------------------------

def test_picks_cap_counts_satellites_and_unlabelled_stocks_not_core(ips: IPS) -> None:
    world = "world_ex_eu_equity"
    stocks = [h(f"Pick {i}", 4, world, instrument_type="stock", role="satellite") for i in range(4)]  # 16%
    stocks += [h("Unlabelled", 4, world, instrument_type="stock")]                                    # 20%
    stocks += [h(f"Core {i}", 4, instrument_type="stock", role="core") for i in range(3)]  # not counted
    assert ("picks_max_total", "portfolio") not in rules(run(balanced(stocks), ips))
    stocks += [h("One more", 1, world, instrument_type="stock", role="satellite")]                     # 21%
    found = [f for f in run(balanced(stocks), ips) if f.rule == "picks_max_total"]
    assert found and found[0].value == pytest.approx(0.21)


# --- themes -------------------------------------------------------------------

def test_theme_limits(ips: IPS) -> None:
    themes = [h("Uranium", 4, ticker="URA", instrument_type="etf", role="theme")]          # > 3%: no adds
    themes += [h("Utilities", 6, ticker="VPU", instrument_type="etf", role="theme")]       # > 5%: trim
    found = rules(run(balanced(themes), ips))
    assert found[("theme_buy_limit", "URA")] == "warning"
    assert found[("theme_hard_limit", "VPU")] == "breach"
    assert ("themes_max_total", "portfolio") not in found          # 10% is not above 10%


def test_theme_count_and_total(ips: IPS) -> None:
    themes = [h(f"T{i}", 3.5, ticker=f"T{i}", instrument_type="etf", role="theme") for i in range(3)]
    found = rules(run(balanced(themes), ips))
    assert found[("themes_max_count", "portfolio")] == "breach"
    assert found[("themes_max_total", "portfolio")] == "breach"


def test_themes_without_a_budget_are_breaches() -> None:
    plain = load_ips(SAMPLE_DIR / "ips.yaml")  # sample IPS allows no themes
    found = rules(run([h("Uranium", 3, ticker="URA", instrument_type="etf", role="theme")], plain))
    assert found[("themes_not_allowed", "URA")] == "breach"


def test_theme_hard_limit_cannot_be_below_buy_limit() -> None:
    raw = load_ips(SAMPLE_DIR / "ips.yaml").model_dump()
    raw["taa"]["themes"] = {"max_total": 0.10, "max_per_theme": 0.03, "max_count": 3, "hard_limit_per_theme": 0.02}
    with pytest.raises(ValidationError, match="hard_limit_per_theme"):
        IPS.model_validate(raw)


# --- banned instruments, bands, reserve, data ---------------------------------

def test_flags_parse_from_csv_text() -> None:
    assert h("X", 1, flags="Leveraged; inverse").flags == {"leveraged", "inverse"}


def test_leveraged_inverse_etf_is_banned(ips: IPS) -> None:
    tbt = h("Inverse bond ETF", 2, "world_ex_eu_equity", instrument_type="etf", flags="leveraged;inverse")
    found = [f for f in run(balanced([tbt]), ips) if f.rule == "banned_instrument"]
    assert found and "is inverse and leveraged" in found[0].message


def test_sleeve_outside_band_is_a_breach(ips: IPS) -> None:
    holdings = [h("All equity", 100, instrument_type="etf"), h("Reserve", 21_000, "reserve", instrument_type="cash")]
    found = rules(run(holdings, ips))
    assert found[("sleeve_band", "eu_equity")] == "breach"
    assert found[("sleeve_band", "eur_govt_bonds")] == "breach"


def test_reserve_shortfall_is_a_breach(ips: IPS) -> None:
    holdings = balanced([])[:-1] + [h("Reserve", 15_000, "reserve", instrument_type="cash")]
    found = [f for f in run(holdings, ips) if f.rule == "liquidity_reserve"]
    assert found and "short by 6,000" in found[0].message


def test_unvalued_holding_is_a_warning(ips: IPS) -> None:
    ghost = Holding(name="Ghost", account="brokerage", currency="EUR", ticker="ZZZ", quantity=1, sleeve="eu_equity")
    assert rules(run(balanced([]) + [ghost], ips)) == {("unvalued_holding", "Ghost"): "warning"}


def test_breaches_are_listed_before_warnings(ips: IPS) -> None:
    holdings = balanced([h("SAP", 6, instrument_type="stock", role="satellite"),
                         h("ASML", 9, instrument_type="stock", role="satellite")])
    severities = [f.severity for f in run(holdings, ips)]
    assert severities == sorted(severities, key=lambda s: s != "breach")


@pytest.mark.skipif(not (PRIVATE_DIR / "holdings.csv").exists(), reason="no private holdings on this machine")
def test_private_limits_run() -> None:
    holdings = load_holdings(PRIVATE_DIR / "holdings.csv")
    fx = {x.currency: 1.0 for x in holdings}
    check_limits(value_holdings(holdings, {}, fx, "CHF"), load_ips(PRIVATE_DIR / "ips.yaml"))
