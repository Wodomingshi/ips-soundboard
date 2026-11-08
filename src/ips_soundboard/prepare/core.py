"""Turn a minimal holdings list into a complete holdings file.

Minimum per row: a ticker and a quantity, or a name and a manual_value. Blanks are filled where
the answer is known: name and currency from the price feed, account from one default. Values the
user wrote are never changed; differences from the price feed are reported instead. The strict
loader (holdings.py) is unchanged: the prepared file is checked by loading it.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from ips_soundboard.holdings import parse_holdings
from ips_soundboard.marketdata.base import MarketData
from ips_soundboard.schemas.holdings import Holding, to_snake

COLUMNS = ["name", "ticker"] + [c for c in Holding.model_fields if c not in ("name", "ticker")]  # output order
REQUIRED = ["name", "ticker", "account", "currency", "quantity"]  # always written, even if blank
NUMERIC = ("quantity", "manual_price", "manual_value", "purchase_price")

# Common header names from broker exports and hand-made lists. Generic vocabulary only.
ALIASES = {
    "symbol": "ticker", "security": "name", "description": "name", "holding": "name",
    "qty": "quantity", "shares": "quantity", "units": "quantity", "nominal": "quantity",
    "ccy": "currency", "type": "instrument_type", "asset_type": "instrument_type",
    "date": "purchase_date", "buy_date": "purchase_date", "trade_date": "purchase_date",
    "bought": "purchase_date", "buy_price": "purchase_price", "cost_price": "purchase_price",
}


@dataclass(frozen=True)
class Note:
    row: int  # spreadsheet row number (header = 1)
    holding: str  # name, ticker or "row n"
    message: str


@dataclass
class PrepareResult:
    columns: list[str]
    rows: dict[int, dict[str, str]]
    filled: list[Note] = field(default_factory=list)  # blanks filled automatically
    suggestions: list[Note] = field(default_factory=list)  # official names that differ from yours
    needs_input: list[Note] = field(default_factory=list)  # rows the file cannot be used without
    notes: list[str] = field(default_factory=list)  # file-level remarks

    @property
    def ready(self) -> bool:
        return not self.needs_input

    @property
    def csv_text(self) -> str:
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=self.columns, lineterminator="\n")
        writer.writeheader()
        for n in sorted(self.rows):
            writer.writerow({c: self.rows[n].get(c, "") for c in self.columns})
        return out.getvalue()


def read_rows(text: str) -> tuple[dict[int, dict[str, str]], list[str]]:
    """Rows keyed by spreadsheet row number, headers normalised. Second value: file-level notes."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿"), newline=""))
    header = next(reader, [])
    keep: dict[int, str] = {}
    notes: list[str] = []
    for i, raw in enumerate(header):
        column = ALIASES.get(to_snake(raw), to_snake(raw))
        if not column:
            continue
        if column not in COLUMNS:
            notes.append(f"Column '{raw.strip()}' is not used and was left out.")
        elif column in keep.values():
            notes.append(f"Column '{raw.strip()}' repeats '{column}' and was left out.")
        else:
            keep[i] = column
    rows = {}
    for n, values in enumerate(reader, start=2):
        row = {column: values[i].strip() for i, column in keep.items() if i < len(values)}
        if any(row.values()):
            rows[n] = row
    return rows, notes


def prepare_rows(rows: dict[int, dict[str, str]], provider: MarketData, account: str = "main",
                 currency: str | None = None, official_names: bool = False) -> PrepareResult:
    """Fill blanks, report what is missing. Never changes a value the user wrote (except with official_names)."""
    rows = {n: {c: _clean(c, v) for c, v in row.items()} for n, row in rows.items()}
    tickers = sorted({r["ticker"] for r in rows.values() if r.get("ticker")})
    info = provider.describe(tickers) if tickers else {}
    blank_account = sum(1 for r in rows.values() if not r.get("account"))
    result = PrepareResult(_columns(rows), rows)
    for n, row in rows.items():
        _prepare_row(n, row, info, account, currency, official_names, result)
    _file_notes(rows, info, account, blank_account, result)
    return result


def _prepare_row(n: int, row: dict[str, str], info: dict, account: str, currency: str | None,
                 official_names: bool, result: PrepareResult) -> None:
    ticker = row.get("ticker", "")
    found = info.get(ticker)
    shown = row.get("name") or ticker or f"row {n}"

    def fill(column: str, value: str, why: str) -> None:
        row[column] = value
        result.filled.append(Note(n, shown, f"{column} set to '{value}' ({why})"))

    def need(message: str) -> None:
        result.needs_input.append(Note(n, shown, message))

    if not ticker and not row.get("name"):
        need("needs a ticker or a name")
    if not row.get("quantity") and not row.get("manual_value"):
        need("needs a quantity (or a manual_value for assets without a price)")

    if found and found.name:
        if not row.get("name"):
            fill("name", found.name, "price feed")
        elif row["name"].casefold() != found.name.casefold():
            if official_names:
                result.filled.append(Note(n, shown, f"name replaced by the official name '{found.name}'"))
                row["name"] = found.name
            else:
                result.suggestions.append(Note(n, shown, f"official name: '{found.name}'"))
    elif ticker and not row.get("name"):
        need(f"no name found for ticker '{ticker}': add a name")

    if not row.get("currency"):
        if found:
            fill("currency", found.currency, "price feed")
        elif currency:
            fill("currency", currency.upper(), "default")
        else:
            need("needs a currency")
    elif found and row["currency"].upper() != found.currency:
        need(f"currency {row['currency'].upper()} differs from the price feed ({found.currency}): "
             f"check the ticker or the currency")

    if not row.get("account"):
        row["account"] = account  # reported once, in the file notes

    if not any(note.row == n for note in result.needs_input):
        try:
            Holding.model_validate({k: v for k, v in row.items() if v})
        except ValidationError as error:
            for detail in error.errors():
                where = ".".join(str(p) for p in detail["loc"])
                need(f"{where + ': ' if where else ''}{detail['msg']}")


def _file_notes(rows: dict[int, dict[str, str]], info: dict, account: str, blank_account: int,
                result: PrepareResult) -> None:
    unknown = sorted({r["ticker"] for r in rows.values() if r.get("ticker") and r["ticker"] not in info})
    if unknown:
        result.notes.append(f"No price found for {', '.join(unknown)}. Check the ticker (Yahoo style, "
                            f"e.g. ROG.SW), or leave it blank and give a manual_value.")
    if blank_account:
        result.notes.append(f"{blank_account} row(s) had no account and were put in '{account}'. "
                            f"It must match an account in your IPS scope.")
    undated = sum(1 for r in rows.values() if r.get("quantity") and not r.get("purchase_date"))
    if undated:
        result.notes.append(f"{undated} row(s) have no purchase_date: the value chart shows them as "
                            f"held from the start.")


def check_prepared(result: PrepareResult) -> list[str]:
    """Load the prepared file with the strict loader. Empty list = the file is usable as is."""
    if not result.ready:
        return [f"row {n.row} ({n.holding}): {n.message}" for n in result.needs_input]
    try:
        parse_holdings(result.csv_text)
    except ValueError as error:
        return getattr(error, "problems", [str(error)])
    return []


def _columns(rows: dict[int, dict[str, str]]) -> list[str]:
    present = {c for row in rows.values() for c, v in row.items() if v}
    return [c for c in COLUMNS if c in REQUIRED or c in present]


def _clean(column: str, value: str) -> str:
    """Thousands separators in numbers: 1'000 and 1 000 -> 1000. Decimal commas are left for the loader to flag."""
    if column in NUMERIC:
        return value.replace("'", "").replace("’", "").replace(" ", "")
    return value


def read_file(path: str | Path) -> tuple[dict[int, dict[str, str]], list[str]]:
    return read_rows(Path(path).read_text(encoding="utf-8-sig"))
