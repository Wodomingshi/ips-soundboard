from pathlib import Path

import pytest

from ips_soundboard.engine.allocation import allocate
from ips_soundboard.engine.contributions import ContributionPlan, describe, plan_contribution, water_fill
from ips_soundboard.engine.valuation import value_holdings
from ips_soundboard.schemas.holdings import Holding
from ips_soundboard.schemas.ips import IPS, load_ips

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "sample_investor_eur"


@pytest.fixture
def ips() -> IPS:
    """Sample IPS: min trade 1,000, new money to at most 2 sleeves, no reserve sleeve."""
    return load_ips(SAMPLE_DIR / "ips.yaml")


def with_rules(ips: IPS, **rebalancing) -> IPS:
    raw = ips.model_dump()
    raw["rebalancing"].update(rebalancing)
    return IPS.model_validate(raw)


def h(name: str, value: float, sleeve: str, **kwargs) -> Holding:
    kwargs.setdefault("instrument_type", "etf")
    kwargs.setdefault("role", "core")
    return Holding(name=name, account="brokerage", currency="EUR", manual_value=value, sleeve=sleeve, **kwargs)


# Same sleeve values as the demo investor (managed portfolio 116,500 EUR)
DEMO = [h("Europe ETF", 43_400, "eu_equity"), h("World ETF", 30_000, "world_ex_eu_equity"),
        h("EM ETF", 9_600, "em_equity"), h("Euro govt ETF", 19_800, "eur_govt_bonds"),
        h("Gold ETC", 7_200, "gold", instrument_type="commodity"),
        h("Cash", 6_500, "cash", instrument_type="cash")]


def plan(holdings: list[Holding], ips: IPS, amount: float) -> ContributionPlan:
    valued = value_holdings(holdings, {}, {}, "EUR")
    return plan_contribution(allocate(valued, ips), valued, ips, amount)


def buys(p: ContributionPlan) -> dict[str, float]:
    return {b.sleeve: round(b.amount, 2) for b in p.buys}


# --- deepest gap first ---------------------------------------------------------

def test_water_fill_levels_the_deepest_gaps() -> None:
    assert water_fill(9_000, [13_925, 11_575]) == pytest.approx([5_675, 3_325])


def test_water_fill_small_amount_goes_to_the_deepest_gap_only() -> None:
    assert water_fill(1_000, [5_000, 2_000]) == pytest.approx([1_000, 0])


def test_water_fill_never_exceeds_a_gap() -> None:
    assert water_fill(10_000, [3_000, 2_000]) == pytest.approx([3_000, 2_000])


def test_demo_example(ips: IPS) -> None:
    p = plan(DEMO, ips, 9_000)
    assert buys(p) == {"world_ex_eu_equity": 5_675, "eur_govt_bonds": 3_325}
    assert p.invested == pytest.approx(9_000) and p.held_back == 0 and p.reserve_top_up == 0
    after = {e.sleeve: e.weight_after for e in p.effects}
    assert after["world_ex_eu_equity"] == pytest.approx(35_675 / 125_500)
    assert after["eu_equity"] == pytest.approx(43_400 / 125_500)  # overweight: diluted, not sold


# --- minimum trade and sleeve cap ------------------------------------------------

def test_buy_below_minimum_trade_is_folded_into_the_deeper_gap(ips: IPS) -> None:
    # level fill would give 2,375 and 625; 625 is below the 1,000 minimum
    assert buys(plan(DEMO, ips, 3_000)) == {"world_ex_eu_equity": 3_000}


def test_amount_below_minimum_trade_is_kept_as_cash(ips: IPS) -> None:
    p = plan(DEMO, ips, 800)
    assert p.buys == [] and p.held_back == 800
    assert any("minimum trade" in n for n in p.notes)


def test_cap_gives_way_only_when_the_money_does_not_fit(ips: IPS) -> None:
    p = plan(DEMO, ips, 60_000)  # the two deepest gaps hold only 56,100
    assert len(p.buys) == 4 and p.invested == pytest.approx(60_000)
    assert all(b.amount >= 1_000 for b in p.buys)
    assert any("4 sleeves are used" in n for n in p.notes)


def test_without_a_cap_the_level_decides_how_many_sleeves(ips: IPS) -> None:
    p = plan(DEMO, with_rules(ips, new_money_max_sleeves=None), 9_000)
    assert buys(p) == {"world_ex_eu_equity": 5_675, "eur_govt_bonds": 3_325}


# --- reserve -------------------------------------------------------------------

@pytest.fixture
def ips_with_reserve(ips: IPS) -> IPS:
    raw = ips.model_dump()
    raw["liquidity"]["reserve_sleeve"] = "reserve"  # required: 6 x 3,500 = 21,000
    return IPS.model_validate(raw)


def test_reserve_is_topped_up_first(ips_with_reserve: IPS) -> None:
    holdings = DEMO + [h("Reserve", 15_000, "reserve", instrument_type="cash")]
    p = plan(holdings, ips_with_reserve, 9_000)
    assert p.reserve_top_up == 6_000
    assert p.invested == pytest.approx(3_000) and len(p.buys) == 1


def test_small_amount_all_goes_to_the_reserve(ips_with_reserve: IPS) -> None:
    holdings = DEMO + [h("Reserve", 15_000, "reserve", instrument_type="cash")]
    p = plan(holdings, ips_with_reserve, 4_000)
    assert p.reserve_top_up == 4_000 and p.buys == [] and p.held_back == 0


# --- what gets suggested ---------------------------------------------------------

def test_candidates_are_core_holdings_only(ips: IPS) -> None:
    holdings = [x for x in DEMO if x.sleeve != "world_ex_eu_equity"] + [
        h("World ETF", 20_000, "world_ex_eu_equity"),
        h("Small world ETF", 2_000, "world_ex_eu_equity"),
        h("Pick Inc", 8_000, "world_ex_eu_equity", instrument_type="stock", role="satellite"),
        h("Theme ETF", 3_000, "ai_power", role="theme"),  # outside the SAA: gets nothing
    ]
    p = plan(holdings, ips, 9_000)
    world = next(b for b in p.buys if b.sleeve == "world_ex_eu_equity")
    assert world.candidates == ["World ETF", "Small world ETF"]
    assert "ai_power" not in buys(p)


def test_sleeve_without_holdings_says_so(ips: IPS) -> None:
    holdings = [x for x in DEMO if x.sleeve != "eur_govt_bonds"]
    p = plan(holdings, ips, 9_000)
    bonds = next(b for b in p.buys if b.sleeve == "eur_govt_bonds")
    assert bonds.candidates == []
    assert any("EUR govt bonds: no current holding" in line for line in describe(p))


# --- input checks --------------------------------------------------------------

def test_zero_amount_is_an_empty_plan(ips: IPS) -> None:
    p = plan(DEMO, ips, 0)
    assert p.buys == [] and p.held_back == 0


def test_negative_amount_is_rejected(ips: IPS) -> None:
    with pytest.raises(ValueError):
        plan(DEMO, ips, -1)


def test_max_sleeves_must_be_positive(ips: IPS) -> None:
    with pytest.raises(ValueError):
        with_rules(ips, new_money_max_sleeves=0)
