"""Turn an LLM draft into a validated IPS, checking every value against the document.

Nothing here trusts the model:
  - every quote must appear in the document (after normalising spaces, dashes, quotes, markdown);
  - every number must appear in its own quote, as written ("25%" -> 25);
  - code converts units ("25%" -> 0.25, "3 percentage points" -> 0.03, "1,000" -> 1000);
  - the result must pass the IPS schema (targets sum to 100%, min <= target <= max, ...).
Settings the document does not state are listed, never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from ips_soundboard.extract.draft import IPSDraft
from ips_soundboard.extract.settings import SETTINGS, choices
from ips_soundboard.schemas.holdings import to_snake
from ips_soundboard.schemas.industries import industries
from ips_soundboard.schemas.ips import IPS

Status = Literal["verified", "quote not found", "number not in quote"]
DRAFT_VERSION = "draft"

_NUMBER = re.compile(r"\d{1,3}(?:[,'’]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_PERCENT = re.compile(r"%|percent|percentage points?|\bpp\b|\bpoints?\b")
_ALIASES = {"annually": "annual", "yearly": "annual", "semi-annual": "semiannual", "semiannually": "semiannual",
            "semi-annually": "semiannual", "monthly": "monthly", "quarterly": "quarterly"}
_TRUE, _FALSE = {"true", "yes", "allowed"}, {"false", "no", "not allowed", "none"}


@dataclass(frozen=True)
class Evidence:
    setting: str
    value: str
    quote: str
    status: Status


@dataclass
class ExtractionResult:
    data: dict[str, Any]  # what goes into the draft YAML (only what the document states)
    ips: IPS | None  # None when the draft does not validate
    errors: list[str]
    evidence: list[Evidence]
    not_found: list[str]  # settings the document does not state: schema defaults apply
    ambiguities: list[str]
    attempts: int = 1
    notes: list[str] = field(default_factory=list)
    first_attempt_errors: list[str] = field(default_factory=list)
    holdings_check: list[str] | None = None  # names that don't match the holdings file (None: not checked)

    @property
    def flagged(self) -> list[Evidence]:
        return [e for e in self.evidence if e.status != "verified"]


def convert(draft: IPSDraft, document: str, known_sleeves: Sequence[str] = (),
            known_accounts: Sequence[str] = ()) -> ExtractionResult:
    """known_*: ids used in the user's holdings file, if given, so the IPS can reuse them."""
    errors: list[str] = []
    notes: list[str] = []
    evidence: list[Evidence] = []
    data: dict[str, Any] = {}
    normalised_doc = normalise(document)

    for sleeve in draft.sleeves:
        sleeve_id = sleeve.id if sleeve.id in known_sleeves else to_sleeve_id(sleeve.name)
        if sleeve.id and sleeve.id not in known_sleeves:
            notes.append(f"sleeve '{sleeve.name}': id '{sleeve.id}' is not in the holdings file, so "
                         f"'{sleeve_id}' was used")
        elif sleeve.id:
            notes.append(f"sleeve '{sleeve.name}' uses the holdings id '{sleeve_id}'")
        values = {}
        for part in ("target", "min", "max"):
            written = getattr(sleeve, part)
            try:
                values[part] = parse_number(written)
            except ValueError as error:
                errors.append(f"saa.{sleeve_id}.{part}: {error}")
        evidence.append(_check(f"saa.{sleeve_id}", [sleeve.target, sleeve.min, sleeve.max],
                               sleeve.quote, normalised_doc))
        if sleeve_id in data.setdefault("saa", {}):
            errors.append(f"saa: sleeve '{sleeve_id}' is listed twice")
        data["saa"][sleeve_id] = values

    for rule in draft.rules:
        kind = SETTINGS[rule.setting][0]
        numeric = [rule.value] if kind in ("number", "int") else []
        evidence.append(_check(rule.setting, numeric, rule.quote, normalised_doc, rule.value))
        try:
            _place(data, rule.setting, kind, _parse(rule.setting, kind, rule.value))
        except ValueError as error:
            errors.append(f"{rule.setting}: {error}")

    stated = {rule.setting for rule in draft.rules}
    not_found = [path for path in SETTINGS if path not in stated]
    if "version" not in stated:
        data["version"] = DRAFT_VERSION
    data = {key: data[key] for key in IPS.model_fields if key in data}  # schema order
    ips, validation_errors = _validate(data)
    result = ExtractionResult(data, ips, errors + validation_errors, evidence, not_found, list(draft.ambiguities),
                              notes=notes)
    if known_sleeves or known_accounts:
        result.holdings_check = holdings_mismatches(data, known_sleeves, known_accounts)
    return result


def holdings_mismatches(data: dict[str, Any], known_sleeves: Sequence[str],
                        known_accounts: Sequence[str]) -> list[str]:
    """IPS names that the holdings file never uses: either a renaming is needed, or nothing is held there."""
    found = []
    for sleeve in data.get("saa", {}):
        if sleeve not in known_sleeves:
            found.append(f"sleeve '{sleeve}': no holding uses it (fine for a 0% target; otherwise rename)")
    scope = data.get("scope", {})
    for account in [*scope.get("managed", []), *scope.get("reported_only", [])]:
        if account not in known_accounts:
            found.append(f"account '{account}': no holding uses it")
    return found


# --- values --------------------------------------------------------------------

def parse_number(written: str) -> float:
    """'25%' -> 0.25, '3 percentage points' -> 0.03, '1,000' -> 1000, "12'000" -> 12000, '5k' -> 5000."""
    text = written.strip().lower()
    number = _first_number(text)
    if number is None:
        raise ValueError(f"no number in '{written}'")
    if re.search(r"\d\s*k\b", text):
        number *= 1_000
    elif re.search(r"\d\s*(m|mn|million)\b", text):
        number *= 1_000_000
    if text.startswith(("-", "−", "–")):
        number = -number
    return number / 100 if _PERCENT.search(text) else number


def _parse(path: str, kind: str, written: str) -> Any:
    text = written.strip()
    if kind == "number":
        number = parse_number(text)
        return int(number) if number.is_integer() and not _PERCENT.search(text.lower()) else number
    if kind == "int":
        number = parse_number(text)
        if number != int(number):
            raise ValueError(f"expected a whole number, got '{written}'")
        return int(number)
    if kind == "bool":
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"expected true or false, got '{written}'")
    if kind == "choice":
        value = _ALIASES.get(text.lower(), text.lower().replace(" ", "_"))
        if value not in choices(path):
            raise ValueError(f"'{written}' is not one of {', '.join(choices(path))}")
        return value
    if kind == "industry":
        industry = to_snake(text).replace("&", "")
        if industry not in industries():
            raise ValueError(f"'{written}' is not an industry id from the vocabulary")
        return industry
    if kind == "list":
        return account_id(text) if path.startswith("scope.") else text
    if path == "base_currency":
        return text.upper()
    return text


def _place(data: dict[str, Any], path: str, kind: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    node = data
    for part in parents:
        node = node.setdefault(part, {})
    if kind in ("list", "industry"):
        items = node.setdefault(leaf, [])
        if value not in items:
            items.append(value)
    elif leaf in node and node[leaf] != value:
        raise ValueError(f"stated twice with different values ({node[leaf]!r} and {value!r})")
    else:
        node[leaf] = value


def account_id(name: str) -> str:
    """'Brokerage account' -> 'brokerage': the id used in the holdings 'account' column."""
    slug = to_snake(name)
    return re.sub(r"_accounts?$", "", slug) or slug


def to_sleeve_id(name: str) -> str:
    """'Emerging-market equities' -> 'emerging_market_equities' (the id used in holdings)."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug if slug[:1].isalpha() else f"sleeve_{slug}"


def _validate(data: dict[str, Any]) -> tuple[IPS | None, list[str]]:
    try:
        return IPS.model_validate(data), []
    except ValidationError as error:
        return None, [f"{'.'.join(str(p) for p in e['loc']) or 'ips'}: {e['msg']}" for e in error.errors()]


# --- evidence ------------------------------------------------------------------

def normalise(text: str) -> str:
    """Lowercase, plain dashes and quotes, no markdown emphasis or table pipes, single spaces."""
    text = text.lower()
    for fancy, plain in (("–", "-"), ("—", "-"), ("−", "-"), ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                         ("±", "+/-")):
        text = text.replace(fancy, plain)
    text = re.sub(r"\*\*|__|`|\|", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_document(quote: str, normalised_document: str) -> bool:
    """Each part of the quote (split at '...') appears in the document, in order."""
    position = 0
    for part in re.split(r"\.\.\.|…", normalise(quote)):
        part = part.strip(" .,;:")
        if not part:
            continue
        found = normalised_document.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    return True


def numbers_in(text: str) -> set[float]:
    found = {float(re.sub(r"[,'’]", "", n)) for n in _NUMBER.findall(text)}
    found |= {float(v) for word, v in _WORDS.items() if re.search(rf"\b{word}\b", text.lower())}
    return found


def _check(setting: str, numeric_values: list[str], quote: str, normalised_doc: str,
           shown: str | None = None) -> Evidence:
    value = shown if shown is not None else " / ".join(numeric_values)
    if not quote_in_document(quote, normalised_doc):
        return Evidence(setting, value, quote, "quote not found")
    in_quote = numbers_in(quote)
    for written in numeric_values:
        number = _first_number(written.lower())
        if number is None or not any(abs(number - q) < 1e-9 for q in in_quote):
            return Evidence(setting, value, quote, "number not in quote")
    return Evidence(setting, value, quote, "verified")


def _first_number(text: str) -> float | None:
    match = _NUMBER.search(text)
    if match:
        return float(re.sub(r"[,'’]", "", match.group()))
    for word, value in _WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return float(value)
    return None
