"""Load holdings from CSV and check them against an IPS."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from ips_soundboard.schemas.holdings import Holding
from ips_soundboard.schemas.ips import IPS

REQUIRED_COLUMNS = {"name", "account", "currency"}
ALLOWED_COLUMNS = set(Holding.model_fields)


class HoldingsFileError(ValueError):
    """Raised when a holdings file has problems. Lists every problem, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid holdings file:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class Issue:
    """A finding from checking holdings against an IPS. Not an error: the data is valid."""

    level: Literal["warning", "info"]
    holding: str
    message: str


def load_holdings(path: str | Path) -> list[Holding]:
    """Read a holdings CSV. Row numbers in errors match a spreadsheet (header = row 1)."""
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _check_columns(reader.fieldnames or [])
        holdings, problems = [], []
        for row_number, row in enumerate(reader, start=2):
            cleaned = {key: value for key, value in row.items() if value not in ("", None)}
            if not cleaned:
                continue  # blank line
            try:
                holdings.append(Holding.model_validate(cleaned))
            except ValidationError as error:
                problems.extend(_describe(row_number, error))
    if problems:
        raise HoldingsFileError(problems)
    return holdings


def _check_columns(columns: list[str]) -> None:
    present = set(columns)
    problems = [f"missing required column '{c}'" for c in sorted(REQUIRED_COLUMNS - present)]
    problems += [f"unknown column '{c}'" for c in sorted(present - ALLOWED_COLUMNS)]
    if problems:
        raise HoldingsFileError(problems)


def _describe(row_number: int, error: ValidationError) -> list[str]:
    messages = []
    for detail in error.errors():
        field = ".".join(str(part) for part in detail["loc"]) or "row"
        messages.append(f"row {row_number}, {field}: {detail['msg']}")
    return messages


def check_against_ips(holdings: list[Holding], ips: IPS) -> list[Issue]:
    """Flag holdings that don't fit the IPS structure. Does not value anything."""
    known_accounts = set(ips.scope.managed) | set(ips.scope.reported_only)
    managed = set(ips.scope.managed)
    issues: list[Issue] = []
    for holding in holdings:
        if holding.account not in known_accounts:
            issues.append(Issue("warning", holding.name, f"account '{holding.account}' is not in the IPS scope"))
        if holding.account not in managed:
            continue
        if holding.sleeve is None:
            issues.append(Issue("warning", holding.name, "no sleeve: unclassified"))
        elif holding.sleeve not in ips.saa:
            issues.append(Issue("info", holding.name, f"sleeve '{holding.sleeve}' is outside the SAA"))
        if holding.instrument_type is None:
            issues.append(Issue("info", holding.name, "no instrument type: unclassified"))
    return issues
