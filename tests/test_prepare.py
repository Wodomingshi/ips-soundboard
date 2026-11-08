from ips_soundboard.holdings import parse_holdings
from ips_soundboard.marketdata import FakeProvider, Quote
from ips_soundboard.prepare import check_prepared, prepare_rows, read_rows
from ips_soundboard.prepare.__main__ import report_lines


def feed() -> FakeProvider:
    """Synthetic securities, in both ticker styles the loader accepts."""
    return FakeProvider(
        quotes={"AMS:IMEU": Quote(30.0, "EUR"), "ROG.SW": Quote(250.0, "CHF"), "LON:VOD": Quote(0.7, "GBP"),
                "NONAME": Quote(10.0, "USD")},
        names={"AMS:IMEU": "iShares Core MSCI Europe UCITS ETF", "ROG.SW": "Roche Holding AG",
               "LON:VOD": "Vodafone Group Plc"},
    )


def prepare(text: str, **kwargs):
    rows, notes = read_rows(text)
    return prepare_rows(rows, feed(), **kwargs), notes


def test_two_columns_become_a_complete_valid_file() -> None:
    result, _ = prepare("ticker,quantity\nAMS:IMEU,900\nROG.SW,10\n")
    assert result.ready and check_prepared(result) == []
    holdings = parse_holdings(result.csv_text)
    assert [(h.name, h.currency, h.account) for h in holdings] == [
        ("iShares Core MSCI Europe UCITS ETF", "EUR", "main"), ("Roche Holding AG", "CHF", "main")]
    assert any("had no account" in n for n in result.notes)
    assert any("no purchase_date" in n for n in result.notes)


def test_your_values_are_never_changed() -> None:
    text = ("name,ticker,account,currency,quantity,sleeve,purchase_date\n"
            "My Europe fund,AMS:IMEU,brokerage,EUR,900,eu_equity,2023-03-15\n")
    result, _ = prepare(text, account="other")
    row = result.rows[2]
    assert (row["name"], row["account"], row["sleeve"]) == ("My Europe fund", "brokerage", "eu_equity")
    assert result.filled == []
    assert [n.message for n in result.suggestions] == ["official name: 'iShares Core MSCI Europe UCITS ETF'"]
    assert not any("purchase_date" in n or "account" in n for n in result.notes)


def test_official_names_only_with_the_flag() -> None:
    result, _ = prepare("name,ticker,quantity\nVodafone,LON:VOD,100\n", official_names=True)
    assert result.rows[2]["name"] == "Vodafone Group Plc"
    assert result.suggestions == []


def test_rows_that_need_input_are_reported_and_kept() -> None:
    text = ("ticker,quantity,currency\n"
            "ROG.SW,,\n"          # no quantity
            "UNKNOWN,5,\n"         # not on the feed: no name, no currency
            "ROG.SW,3,EUR\n"       # currency contradicts the feed
            "NONAME,4,\n")         # on the feed, but without a name
    result, _ = prepare(text)
    assert not result.ready and len(result.rows) == 4
    needs = {(n.row, n.message.split(":")[0].split(" (")[0]) for n in result.needs_input}
    assert (2, "needs a quantity") in needs
    assert (3, "no name found for ticker 'UNKNOWN'") in needs and (3, "needs a currency") in needs
    assert any(n.row == 4 and "differs from the price feed (CHF)" in n.message for n in result.needs_input)
    assert result.rows[4]["currency"] == "EUR"  # flagged, not "fixed"
    assert any(n.row == 5 and "no name found" in n.message for n in result.needs_input)
    assert any("No price found for UNKNOWN" in n for n in result.notes)
    assert check_prepared(result)  # not loadable yet, and says why


def test_assets_without_a_price_use_name_and_manual_value() -> None:
    result, _ = prepare("name,manual_value\nFlat in Lausanne,500000\n", currency="CHF", account="property")
    assert result.ready
    (h,) = parse_holdings(result.csv_text)
    assert (h.currency, h.account, h.manual_value) == ("CHF", "property", 500000.0)


def test_headers_are_normalised_and_unknown_columns_dropped() -> None:
    text = "Symbol,Shares,Buy Date,Broker notes\nROG.SW,1'000,2024-02-01,keep forever\n"
    result, notes = prepare(text)
    assert result.ready
    (h,) = parse_holdings(result.csv_text)
    assert (h.ticker, h.quantity, str(h.purchase_date)) == ("ROG.SW", 1000.0, "2024-02-01")
    assert notes == ["Column 'Broker notes' is not used and was left out."]


def test_report_lists_each_section() -> None:
    result, notes = prepare("name,ticker,quantity\nVodafone,LON:VOD,100\nAMS:IMEU,,\n")
    text = "\n".join(report_lines(result, notes + result.notes))
    assert "Official names" in text and "Needs your input" in text and "Filled in" in text


def test_a_prepared_file_run_again_is_updated_in_place() -> None:
    from pathlib import Path

    from ips_soundboard.prepare.__main__ import _default_output

    assert _default_output(Path("x/my_list.csv")) == Path("x/my_list.prepared.csv")
    assert _default_output(Path("x/my_list.prepared.csv")) == Path("x/my_list.prepared.csv")
