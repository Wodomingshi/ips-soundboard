"""Human-readable text for identifiers and numbers, shared by every view so they match.

Identifiers (sleeve ids, account ids) stay snake_case in data and config; this module only
decides how they are shown.
"""

from __future__ import annotations

# Tokens shown in capitals (regions, currencies, instrument acronyms). Generic vocabulary, not investor data.
_SPECIAL = {
    "eu": "EU", "em": "EM", "dm": "DM", "us": "US", "uk": "UK", "ch": "CH", "row": "RoW",
    "usd": "USD", "eur": "EUR", "chf": "CHF", "gbp": "GBP", "jpy": "JPY",
    "etf": "ETF", "etfs": "ETFs", "reit": "REIT", "reits": "REITs", "esg": "ESG", "tips": "TIPS",
    "msci": "MSCI", "taa": "TAA", "saa": "SAA",
}


def label(identifier: str) -> str:
    """'world_ex_eu_equity' -> 'World ex EU equity'. Sentence case, known abbreviations in capitals."""
    words = [_SPECIAL.get(w, w) for w in identifier.strip().split("_") if w]
    if not words:
        return identifier
    first = words[0]
    words[0] = first if first in _SPECIAL.values() else first[:1].upper() + first[1:]
    return " ".join(words)


def pct(value: float) -> str:
    """One percentage format everywhere: one decimal ('25.0%', '-9.3%')."""
    return f"{value:.1%}"
