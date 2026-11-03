"""Merge classification suggestions into a copy of the holdings file, and say what changed.

Rules (the file stays yours):
  - blanks are filled when the model is confident (high or medium);
  - low-confidence suggestions are reported, not filled;
  - existing values are never changed: a different suggestion is reported as a disagreement;
  - "outside the SAA" is reported: only you can name a sleeve that is not in the IPS.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ips_soundboard.classify.suggest import NOT_APPLICABLE, OUTSIDE_SAA, Row
from ips_soundboard.engine.limits import DIRECT, Finding, check_exclusions, name_words
from ips_soundboard.holdings import load_holdings, parse_holdings
from ips_soundboard.schemas.holdings import Holding
from ips_soundboard.schemas.ips import IPS

FIELDS = ("instrument_type", "sleeve", "role", "flags", "issuer", "industry")
SKIPPED_TYPES = ("cash", "pension")
Action = Literal["filled", "differs", "low confidence", "needs input"]


@dataclass(frozen=True)
class Change:
    row: int
    name: str
    field: str
    current: str
    suggested: str
    action: Action
    confidence: str
    reason: str


@dataclass
class HoldingsFile:
    columns: list[str]
    rows: dict[int, dict[str, str]]  # spreadsheet row number -> raw values
    holdings: dict[int, Holding]


@dataclass
class ClassificationResult:
    csv_text: str
    changes: list[Change]
    agreed: int
    classified: int
    skipped: int
    missing_rows: list[int]
    exclusions: list[Finding] = field(default_factory=list)


def read_holdings_file(path: str | Path) -> HoldingsFile:
    """Raw rows with their spreadsheet numbers. Validates the file first (clear errors)."""
    load_holdings(path)
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = {n: {k: v or "" for k, v in row.items()} for n, row in enumerate(reader, start=2)
                if any((v or "").strip() for v in row.values())}
        columns = list(reader.fieldnames or [])
    holdings = {n: Holding.model_validate({k: v for k, v in row.items() if v != ""}) for n, row in rows.items()}
    return HoldingsFile(columns, rows, holdings)


def rows_to_classify(file: HoldingsFile, ips: IPS) -> list[Row]:
    """Everything except cash, pensions and reported-only accounts."""
    return [Row(n, h.name, h.ticker, h.currency) for n, h in file.holdings.items() if _in_scope(h, ips)]


def extra_sleeves(file: HoldingsFile, ips: IPS) -> list[str]:
    """Sleeve names already used outside the SAA (e.g. themes), offered to the model as choices."""
    used = {h.sleeve for h in file.holdings.values() if h.sleeve and h.account in ips.scope.managed}
    return sorted(used - set(ips.saa) - {ips.liquidity.reserve_sleeve})


def merge(file: HoldingsFile, suggestions: Sequence[Any], ips: IPS) -> ClassificationResult:
    by_row: dict[int, Any] = {}
    for s in suggestions:
        if s.row in file.rows:
            by_row.setdefault(s.row, s)
    expected = [r.row for r in rows_to_classify(file, ips)]
    rows = {n: dict(values) for n, values in file.rows.items()}
    changes, agreed = [], 0
    for n in expected:
        if n not in by_row:
            continue
        for name, current, suggested in _fields(file.holdings[n], by_row[n], ips):
            change = _decide(n, file.holdings[n].name, name, current, suggested, by_row[n], set(ips.saa))
            if change is None:
                agreed += 1
                continue
            changes.append(change)
            if change.action == "filled":
                rows[n][name] = suggested
    columns = file.columns + [c for c in FIELDS if c not in file.columns]
    text = _to_csv(columns, rows)
    return ClassificationResult(text, changes, agreed, len(expected), len(file.rows) - len(expected),
                                [n for n in expected if n not in by_row],
                                check_exclusions(parse_holdings(text), ips))


# --- per field -------------------------------------------------------------------

def _fields(h: Holding, s: Any, ips: IPS) -> list[tuple[str, str, str]]:
    """(field, current value, suggested value) for the fields that apply to this holding."""
    out = [("instrument_type", h.instrument_type or "", s.instrument_type)]
    reserve = h.sleeve is not None and h.sleeve == ips.liquidity.reserve_sleeve
    if h.account in ips.scope.managed and not reserve:
        out += [("sleeve", h.sleeve or "", s.sleeve), ("role", h.role or "", s.role)]
    if s.flags or h.flags:
        out.append(("flags", ";".join(sorted(h.flags)), ";".join(sorted(set(s.flags)))))
    if (h.instrument_type or s.instrument_type) in DIRECT:
        if s.issuer.strip():
            out.append(("issuer", h.issuer or "", s.issuer.strip()))
        if s.industry != NOT_APPLICABLE:
            out.append(("industry", h.industry or "", s.industry))
    return out


def _decide(row: int, name: str, field_name: str, current: str, suggested: str, s: Any,
            saa: set[str]) -> Change | None:
    """None when the suggestion agrees with the file."""
    def change(action: Action, shown: str = suggested) -> Change:
        return Change(row, name, field_name, current, shown, action, s.confidence, s.reason)

    if current:
        if _same(field_name, current, suggested, saa):
            return None
        return change("differs", "outside the SAA" if suggested == OUTSIDE_SAA else suggested)
    if field_name == "sleeve" and suggested == OUTSIDE_SAA:
        return change("needs input", "outside the SAA: choose a sleeve name")
    if s.confidence == "low":
        return change("low confidence")
    return change("filled")


def _same(field_name: str, current: str, suggested: str, saa: set[str]) -> bool:
    if field_name == "sleeve" and suggested == OUTSIDE_SAA:
        return current not in saa  # the user's own name for a position outside the SAA
    if field_name == "issuer":
        a, b = name_words(current), name_words(suggested)
        return a[:len(b)] == b or b[:len(a)] == a
    return current.strip().lower() == suggested.strip().lower()


def _in_scope(h: Holding, ips: IPS) -> bool:
    return h.instrument_type not in SKIPPED_TYPES and h.account not in ips.scope.reported_only


def _to_csv(columns: list[str], rows: dict[int, dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for n in sorted(rows):
        writer.writerow({c: rows[n].get(c, "") for c in columns})
    return out.getvalue()
