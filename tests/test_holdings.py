from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from ips_soundboard.holdings import HoldingsFileError, check_against_ips, load_holdings
from ips_soundboard.schemas.holdings import Holding
from ips_soundboard.schemas.ips import load_ips

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_investor_eur"
PRIVATE_DIR = ROOT / "private"

HEADER = "name,ticker,account,currency,quantity,manual_value,sleeve,purchase_date,purchase_price\n"


def write_csv(tmp_path: Path, body: str, header: str = HEADER) -> Path:
    path = tmp_path / "holdings.csv"
    path.write_text(header + body, encoding="utf-8")
    return path


# --- schema -----------------------------------------------------------------

def test_holding_normalises_ids_and_currency() -> None:
    h = Holding(name=" Nestlé ", account="Pillar 3a", currency=" chf", quantity=10, sleeve="CH Equity")
    assert (h.name, h.account, h.currency, h.sleeve) == ("Nestlé", "pillar_3a", "CHF", "ch_equity")


def test_holding_needs_quantity_or_manual_value() -> None:
    with pytest.raises(ValidationError, match="quantity or a manual_value"):
        Holding(name="X", account="brokerage", currency="USD")


def test_manual_value_alone_is_enough() -> None:
    assert Holding(name="Pension", account="pillar_2", currency="CHF", manual_value=46_600).quantity is None


def test_purchase_price_needs_quantity() -> None:
    with pytest.raises(ValidationError, match="purchase_price needs a quantity"):
        Holding(name="X", account="brokerage", currency="USD", manual_value=100, purchase_price=10)


def test_unknown_instrument_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Holding(name="X", account="brokerage", currency="USD", quantity=1, instrument_type="crypto")


# --- loader -----------------------------------------------------------------

def test_sample_holdings_load() -> None:
    holdings = load_holdings(SAMPLE_DIR / "holdings.csv")
    assert len(holdings) == 9
    sap = next(h for h in holdings if h.ticker == "ETR:SAP")
    assert sap.role == "satellite"
    assert sap.purchase_date == date(2023, 9, 12)


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "A,AAA,brokerage,USD,1,,,,\n,,,,,,,,\n")
    assert len(load_holdings(path)) == 1


def test_errors_report_every_bad_row_with_spreadsheet_numbering(tmp_path: Path) -> None:
    body = (
        "Good,GD,brokerage,USD,5,,,,\n"          # row 2
        "Bad currency,BC,brokerage,dollars,5,,,,\n"  # row 3
        "No size,NS,brokerage,USD,,,,,\n"        # row 4
    )
    with pytest.raises(HoldingsFileError) as caught:
        load_holdings(write_csv(tmp_path, body))
    problems = caught.value.problems
    assert any(p.startswith("row 3, currency") for p in problems)
    assert any(p.startswith("row 4") and "quantity or a manual_value" in p for p in problems)
    assert not any(p.startswith("row 2") for p in problems)


def test_unknown_and_missing_columns_are_rejected(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "A,brokerage,1\n", header="name,account,shares\n")
    with pytest.raises(HoldingsFileError) as caught:
        load_holdings(path)
    assert "missing required column 'currency'" in caught.value.problems
    assert "unknown column 'shares'" in caught.value.problems


# --- check against IPS ------------------------------------------------------

def test_sample_holdings_fit_the_sample_ips() -> None:
    issues = check_against_ips(load_holdings(SAMPLE_DIR / "holdings.csv"), load_ips(SAMPLE_DIR / "ips.yaml"))
    assert issues == []


def test_check_flags_scope_and_classification_gaps() -> None:
    ips = load_ips(SAMPLE_DIR / "ips.yaml")
    holdings = [
        Holding(name="Mystery", account="brokerage", currency="EUR", quantity=1),
        Holding(name="Crypto fund", account="brokerage", currency="EUR", quantity=1,
                instrument_type="fund", sleeve="digital_assets"),
        Holding(name="Old account", account="savings_app", currency="EUR", manual_value=500),
    ]
    found = {(i.holding, i.level, i.message) for i in check_against_ips(holdings, ips)}
    assert ("Mystery", "warning", "no sleeve: unclassified") in found
    assert ("Mystery", "info", "no instrument type: unclassified") in found
    assert ("Crypto fund", "info", "sleeve 'digital_assets' is outside the SAA") in found
    assert ("Old account", "warning", "account 'savings_app' is not in the IPS scope") in found


def test_reported_only_accounts_are_not_classification_checked() -> None:
    ips = load_ips(SAMPLE_DIR / "ips.yaml")
    pension = Holding(name="Pension", account="company_pension", currency="EUR", manual_value=1)
    assert check_against_ips([pension], ips) == []


@pytest.mark.skipif(not (PRIVATE_DIR / "holdings.csv").exists(), reason="no private holdings on this machine")
def test_private_holdings_load() -> None:
    """The owner's real holdings must load. Asserts nothing about their contents."""
    holdings = load_holdings(PRIVATE_DIR / "holdings.csv")
    if (PRIVATE_DIR / "ips.yaml").exists():
        check_against_ips(holdings, load_ips(PRIVATE_DIR / "ips.yaml"))
    assert holdings
