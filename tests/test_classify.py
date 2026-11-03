from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ips_soundboard.classify.merge import extra_sleeves, merge, read_holdings_file, rows_to_classify
from ips_soundboard.classify.report import report_lines
from ips_soundboard.classify.suggest import reply_model, suggest, user_prompt
from ips_soundboard.holdings import parse_holdings
from ips_soundboard.schemas.ips import IPS, load_ips

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "sample_investor_eur"

HOLDINGS = """\
name,ticker,account,currency,quantity,manual_value,instrument_type,sleeve,role
iShares Core MSCI Europe,AMS:IMEU,brokerage,EUR,900,,etf,eu_equity,core
Micron Technology,NASDAQ:MU,brokerage,USD,12,,,,
Alibaba Group,NYSE:BABA,brokerage,USD,30,,stock,world_ex_eu_equity,satellite
Shell plc,LON:SHEL,brokerage,GBP,100,,stock,eu_equity,satellite
Obscure Holdings,,brokerage,CHF,5,,,,
Utilities Select Sector SPDR,NYSEARCA:XLU,brokerage,USD,20,,etf,,
Cash EUR,,brokerage,EUR,6500,,cash,cash,core
Company pension,,company_pension,EUR,,85000,pension,,
"""


@pytest.fixture
def ips() -> IPS:
    raw = load_ips(SAMPLE_DIR / "ips.yaml").model_dump()
    raw["exclusions"]["sub_industries"] = ["tobacco", "integrated_oil_gas"]
    return IPS.model_validate(raw)


@pytest.fixture
def file(tmp_path: Path):
    path = tmp_path / "holdings.csv"
    path.write_text(HOLDINGS, encoding="utf-8")
    return read_holdings_file(path)


def s(row: int, type_: str, sleeve: str, role: str, issuer: str = "", industry: str = "not_applicable",
      confidence: str = "high", reason: str = "x", flags: tuple = ()) -> dict:
    return {"row": row, "instrument_type": type_, "sleeve": sleeve, "role": role, "flags": list(flags),
            "issuer": issuer, "industry": industry, "confidence": confidence, "reason": reason}


REPLY = [
    s(2, "etf", "eu_equity", "core"),
    s(3, "stock", "world_ex_eu_equity", "satellite", "Micron Technology, Inc.", "semiconductors",
      reason="US memory-chip maker."),
    s(4, "stock", "em_equity", "satellite", "Alibaba Group Holding Ltd", "consumer_retail",
      reason="Chinese e-commerce group."),
    s(5, "stock", "eu_equity", "satellite", "Shell plc", "integrated_oil_gas", reason="Integrated oil major."),
    s(6, "stock", "eu_equity", "satellite", "Obscure Holdings", "industrials", confidence="low",
      reason="Not recognised."),
    s(7, "etf", "outside_saa", "theme", reason="US utilities sector ETF."),
]


def classification(ips: IPS, file, rows: list[dict] = REPLY):
    model = reply_model([*ips.saa, *extra_sleeves(file, ips)])
    return list(model.model_validate({"rows": rows}).rows)


class FakeClient:
    def __init__(self, reply) -> None:
        self.reply, self.calls, self.messages = reply, [], self

    def parse(self, **request):
        self.calls.append(request)
        return SimpleNamespace(parsed_output=self.reply, stop_reason="end_turn")


# --- what is sent ------------------------------------------------------------------

def test_cash_pensions_and_reported_only_accounts_are_not_sent(file, ips: IPS) -> None:
    assert [r.row for r in rows_to_classify(file, ips)] == [2, 3, 4, 5, 6, 7]


def test_only_names_tickers_and_currencies_are_sent(file, ips: IPS) -> None:
    prompt = user_prompt(rows_to_classify(file, ips))
    assert "row 3: Micron Technology | ticker NASDAQ:MU | USD" in prompt
    assert "900" not in prompt and "satellite" not in prompt  # no quantities, no current classification


def test_reply_can_only_use_the_ips_sleeves_and_vocabulary(file, ips: IPS) -> None:
    with pytest.raises(ValidationError):
        classification(ips, file, [s(3, "stock", "us_tech", "satellite")])
    with pytest.raises(ValidationError):
        classification(ips, file, [s(3, "stock", "eu_equity", "satellite", "X", "oil")])


def test_suggest_sends_one_structured_request(file, ips: IPS) -> None:
    model = reply_model(list(ips.saa))
    client = FakeClient(model.model_validate({"rows": [REPLY[0]]}))
    suggest(rows_to_classify(file, ips), ips, client, model="test-model")
    request = client.calls[0]
    assert request["model"] == "test-model" and "eu_equity: EU equity (target 25%)" in request["system"]


# --- merging -------------------------------------------------------------------

def changes(result) -> dict[tuple[int, str], tuple[str, str]]:
    return {(c.row, c.field): (c.action, c.suggested) for c in result.changes}


def test_confident_suggestions_fill_blanks(file, ips: IPS) -> None:
    result = merge(file, classification(ips, file), ips)
    found = changes(result)
    assert found[(3, "sleeve")] == ("filled", "world_ex_eu_equity")
    assert found[(3, "industry")] == ("filled", "semiconductors")
    micron = next(h for h in parse_holdings(result.csv_text) if h.name == "Micron Technology")
    assert (micron.instrument_type, micron.role, micron.industry) == ("stock", "satellite", "semiconductors")


def test_existing_values_are_never_changed(file, ips: IPS) -> None:
    result = merge(file, classification(ips, file), ips)
    assert changes(result)[(4, "sleeve")] == ("differs", "em_equity")
    alibaba = next(h for h in parse_holdings(result.csv_text) if h.name == "Alibaba Group")
    assert alibaba.sleeve == "world_ex_eu_equity"


def test_low_confidence_is_reported_not_filled(file, ips: IPS) -> None:
    result = merge(file, classification(ips, file), ips)
    assert changes(result)[(6, "industry")][0] == "low confidence"
    obscure = next(h for h in parse_holdings(result.csv_text) if h.name == "Obscure Holdings")
    assert obscure.industry is None and obscure.sleeve is None


def test_outside_the_saa_needs_a_name_from_the_user(file, ips: IPS) -> None:
    result = merge(file, classification(ips, file), ips)
    assert changes(result)[(7, "sleeve")][0] == "needs input"
    assert changes(result)[(7, "role")] == ("filled", "theme")


def test_agreeing_values_are_counted_not_listed(file, ips: IPS) -> None:
    result = merge(file, classification(ips, file), ips)
    assert not any(c.row == 2 for c in result.changes)
    assert result.agreed >= 3  # row 2: type, sleeve, role


def test_new_columns_are_added_and_other_rows_kept(file, ips: IPS) -> None:
    result = merge(file, classification(ips, file), ips)
    header = result.csv_text.splitlines()[0].split(",")
    assert header[-3:] == ["flags", "issuer", "industry"]
    assert len(parse_holdings(result.csv_text)) == 8


def test_unanswered_rows_are_listed(file, ips: IPS) -> None:
    result = merge(file, classification(ips, file, REPLY[:-1]), ips)
    assert result.missing_rows == [7]


# --- exclusions and report ----------------------------------------------------------

def test_filled_industry_reveals_an_exclusion(file, ips: IPS) -> None:
    result = merge(file, classification(ips, file), ips)
    breaches = [f.message for f in result.exclusions if f.severity == "breach"]
    assert breaches == ["Shell plc is in Integrated oil gas, which the IPS excludes"]


def test_report_lists_every_kind_of_change(file, ips: IPS) -> None:
    text = "\n".join(report_lines(merge(file, classification(ips, file), ips)))
    for heading in ("Filled in", "Different from your file", "Low confidence", "Needs your input"):
        assert heading in text
    assert "row 4 Alibaba Group: sleeve world_ex_eu_equity -> em_equity (high; Chinese e-commerce group.)" in text
    assert "x Shell plc is in Integrated oil gas" in text
