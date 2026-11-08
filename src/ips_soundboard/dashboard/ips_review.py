"""Tables for reviewing an IPS extracted from a document, and a check of the edited YAML.

Display only: the extraction and its checks live in `extract`; this module lays them out.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
import yaml
from pydantic import ValidationError

from ips_soundboard.display import label
from ips_soundboard.extract.convert import ExtractionResult, holdings_mismatches
from ips_soundboard.extract.settings import SETTINGS
from ips_soundboard.schemas.holdings import Holding
from ips_soundboard.schemas.ips import IPS

CHECK_WORDS = {"verified": "✓", "quote not found": "⚠ quote not in your document",
               "number not in quote": "⚠ number not in its quote"}


# Descriptions written for the model that read badly as labels.
_LABELS = {"exclusions.apply_to": "What the exclusions apply to (direct holdings only, or inside funds too)"}


def setting_label(path: str) -> str:
    """'saa.eu_equity' -> 'Allocation: EU equity'; other settings by their plain-language description,
    without the instructions meant for the model ('(one rule per account)', ', as a 3-letter code')."""
    if path.startswith("saa."):
        return f"Allocation: {label(path.split('.', 1)[1])}"
    if path in _LABELS:
        return _LABELS[path]
    if path not in SETTINGS:
        return path
    text = re.sub(r"\s*\([^)]*\)", "", SETTINGS[path][1])
    return re.sub(r",\s*(?:as|written as) .*$", "", text)


def found_table(result: ExtractionResult) -> pd.DataFrame:
    """Every value read from the document, as written, with the quote it came from."""
    rows = [{"Setting": setting_label(e.setting), "As written": _written(e.setting, e.value, result),
             "From your document": e.quote, "Check": CHECK_WORDS[e.status]} for e in result.evidence]
    return pd.DataFrame(rows, columns=["Setting", "As written", "From your document", "Check"])


def not_stated_table(result: ExtractionResult) -> pd.DataFrame:
    """Settings the document does not state, with the value the tool will use (the schema default)."""
    used = result.ips.model_dump(mode="json") if result.ips is not None else None
    rows = [{"Setting": setting_label(path), "Value used": _shown(_at(used, path)) if used else "—"}
            for path in result.not_found if path != "version"]
    return pd.DataFrame(rows, columns=["Setting", "Value used"])


def check_yaml(text: str) -> tuple[IPS | None, list[str]]:
    """The IPS, or readable errors. Used live while the user edits the draft."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f"line {mark.line + 1}: " if mark is not None else ""
        return None, [f"{where}{getattr(error, 'problem', None) or 'not valid YAML'}"]
    if not isinstance(data, dict):
        return None, ["the file must be a set of settings (key: value lines)"]
    try:
        return IPS.model_validate(data), []
    except ValidationError as error:
        return None, [f"{'.'.join(str(p) for p in e['loc']) or 'IPS'}: {e['msg']}" for e in error.errors()]


def name_check(ips: IPS, holdings: list[Holding]) -> tuple[list[str], list[str]]:
    """IPS sleeve and account names the holdings never use, and the sleeve names the holdings do use.

    A sleeve name that differs from the holdings' (european_equities vs eu_equity) would put those
    holdings outside the allocation, so this is checked live while the user edits.
    """
    sleeves = sorted({h.sleeve for h in holdings if h.sleeve})
    accounts = sorted({h.account for h in holdings})
    unused = holdings_mismatches(ips.model_dump(mode="json"), sleeves, accounts)
    return unused, [s for s in sleeves if s not in ips.saa]


def _written(path: str, value: str, result: ExtractionResult) -> str:
    """Sleeves: 'target 25% (20%–30%)' from the converted numbers; other settings as written."""
    if path.startswith("saa."):
        sleeve = result.data.get("saa", {}).get(path.split(".", 1)[1], {})
        if {"target", "min", "max"} <= set(sleeve):
            return f"{_percent(sleeve['target'])} (range {_percent(sleeve['min'])}–{_percent(sleeve['max'])})"
    return value


def _percent(fraction: float) -> str:
    return f"{round(fraction * 100, 2):g}%"  # 0.25 -> 25%, 0.025 -> 2.5%


def _at(data: dict[str, Any], path: str) -> Any:
    for part in path.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


def _shown(value: Any) -> str:
    if value is None:
        return "not set (no check)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "none"
    return str(value)
