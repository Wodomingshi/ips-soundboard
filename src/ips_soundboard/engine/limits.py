"""Check a valued portfolio against the IPS limits.

Two severities:
  breach  - a rule is violated and the IPS requires action (hard limit, banned
            instrument, sleeve outside its band, reserve below requirement)
  warning - a soft limit is reached: no further buying (buy limits), or the
            data is incomplete (unvalued holdings)

Exclusions use the holdings' issuer and industry columns (filled by hand or with the
classifier). Issuers match on whole words from the start of the name, so an excluded
"Meta" matches "Meta Platforms Inc" but not "Metals ETF". Only direct holdings (stocks
and bonds) are checked: what sits inside a fund is not known to the tool.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from ips_soundboard.display import label, pct
from ips_soundboard.engine.allocation import Allocation, allocate, managed_weights
from ips_soundboard.engine.valuation import ValuedHolding
from ips_soundboard.schemas.holdings import Holding, to_snake
from ips_soundboard.schemas.ips import IPS, PositionLimits

Severity = Literal["breach", "warning"]


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    subject: str  # holding, theme or sleeve concerned ("portfolio" for totals)
    value: float | None
    limit: float | None
    message: str


def check_limits(valued: Sequence[ValuedHolding], ips: IPS) -> list[Finding]:
    """All IPS limit findings, breaches first."""
    allocation = allocate(valued, ips)
    weights = managed_weights(valued, ips)
    findings = (
        check_single_names(weights, ips.limits)
        + check_picks_total(weights, ips.limits)
        + check_themes(weights, ips)
        + check_banned_instruments([v.holding for v in valued], ips.limits)
        + check_exclusions([v.holding for v in valued], ips)
        + check_sleeve_bands(allocation)
        + check_reserve(allocation)
        + check_unvalued(allocation)
    )
    return sorted(findings, key=lambda f: f.severity != "breach")


# --- single names -------------------------------------------------------------

def check_single_names(weights: Sequence[tuple[ValuedHolding, float]], limits: PositionLimits) -> list[Finding]:
    """Per-stock weight against the buy limit (no adds) and hard limit (trim). Lots are summed."""
    stocks = _sum_by_position(weights, lambda h: h.instrument_type == "stock")
    findings = []
    for key, (name, weight) in stocks.items():
        if limits.single_name_hard_limit is not None and weight > limits.single_name_hard_limit:
            findings.append(Finding("single_name_hard_limit", "breach", key, weight, limits.single_name_hard_limit,
                                    f"{name} is {pct(weight)}, above the {pct(limits.single_name_hard_limit)} hard "
                                    f"limit: trim back to the buy limit"))
        elif limits.single_name_buy_limit is not None and weight > limits.single_name_buy_limit:
            findings.append(Finding("single_name_buy_limit", "warning", key, weight, limits.single_name_buy_limit,
                                    f"{name} is {pct(weight)}, above the {pct(limits.single_name_buy_limit)} buy "
                                    f"limit: no adds"))
    return findings


def check_picks_total(weights: Sequence[tuple[ValuedHolding, float]], limits: PositionLimits) -> list[Finding]:
    """Conviction picks = stocks not marked as core. An unlabelled stock counts as a pick."""
    if limits.picks_max_total is None:
        return []
    total = sum(w for v, w in weights if v.holding.instrument_type == "stock" and v.holding.role != "core")
    if total <= limits.picks_max_total:
        return []
    return [Finding("picks_max_total", "breach", "portfolio", total, limits.picks_max_total,
                    f"Conviction picks total {pct(total)}, above the {pct(limits.picks_max_total)} cap")]


# --- themes -------------------------------------------------------------------

def check_themes(weights: Sequence[tuple[ValuedHolding, float]], ips: IPS) -> list[Finding]:
    budget = ips.taa.themes
    themes = _sum_by_position(weights, lambda h: h.role == "theme")
    if not themes:
        return []
    if budget is None:
        return [Finding("themes_not_allowed", "breach", key, weight, 0.0,
                        f"{name} is held as a theme, but the IPS allows no themes")
                for key, (name, weight) in themes.items()]

    findings = []
    for key, (name, weight) in themes.items():
        if budget.hard_limit_per_theme is not None and weight > budget.hard_limit_per_theme:
            findings.append(Finding("theme_hard_limit", "breach", key, weight, budget.hard_limit_per_theme,
                                    f"Theme {name} is {pct(weight)}, above the {pct(budget.hard_limit_per_theme)} "
                                    f"hard limit: trim"))
        elif weight > budget.max_per_theme:
            findings.append(Finding("theme_buy_limit", "warning", key, weight, budget.max_per_theme,
                                    f"Theme {name} is {pct(weight)}, above the {pct(budget.max_per_theme)} "
                                    f"per-theme limit: no adds"))
    total = sum(weight for _, weight in themes.values())
    if total > budget.max_total:
        findings.append(Finding("themes_max_total", "breach", "portfolio", total, budget.max_total,
                                f"Themes total {pct(total)}, above the {pct(budget.max_total)} budget"))
    if len(themes) > budget.max_count:
        findings.append(Finding("themes_max_count", "breach", "portfolio", float(len(themes)),
                                float(budget.max_count),
                                f"{len(themes)} themes held, above the maximum of {budget.max_count}"))
    return findings


# --- banned instruments --------------------------------------------------------

def check_banned_instruments(holdings: Sequence[Holding], limits: PositionLimits) -> list[Finding]:
    """Flags on a holding vs what the IPS allows. Applies to every account."""
    allowed = {
        "leveraged": limits.leveraged_or_inverse_etfs_allowed,
        "inverse": limits.leveraged_or_inverse_etfs_allowed,
        "derivative": limits.derivatives_allowed,
        "short": limits.short_selling_allowed,
    }
    findings = []
    for holding in holdings:
        banned = sorted(flag for flag in holding.flags if not allowed[flag])
        if banned:
            findings.append(Finding("banned_instrument", "breach", holding.name, None, None,
                                    f"{holding.name} is {' and '.join(banned)}, which the IPS does not allow"))
    return findings


# --- exclusions ------------------------------------------------------------------

DIRECT = ("stock", "bond")
_LEGAL_SUFFIXES = {"inc", "corp", "corporation", "co", "company", "plc", "ag", "sa", "nv", "se", "ltd", "limited",
                   "llc", "holding", "holdings", "group", "the", "class", "a", "b", "c", "adr", "reg", "shs"}


def check_exclusions(holdings: Sequence[Holding], ips: IPS) -> list[Finding]:
    """Direct holdings whose issuer or industry the IPS excludes (breach). Applies to every account.

    Also warns once when industry exclusions exist but some direct holdings have no industry,
    because those cannot be checked.
    """
    rules = ips.exclusions
    excluded_industries = {to_snake(i) for i in rules.sub_industries}
    excluded_issuers = [(name, name_words(name)) for name in rules.issuers]
    if not excluded_industries and not excluded_issuers:
        return []

    findings, unchecked = [], []
    for h in holdings:
        if h.instrument_type not in DIRECT:
            continue
        if h.industry and h.industry in excluded_industries:
            findings.append(Finding("excluded_industry", "breach", h.ticker or h.name, None, None,
                                    f"{h.name} is in {label(h.industry)}, which the IPS excludes"))
            continue
        issuer = next((name for name, words in excluded_issuers if _issuer_matches(words, h)), None)
        if issuer:
            findings.append(Finding("excluded_issuer", "breach", h.ticker or h.name, None, None,
                                    f"{h.name} is issued by {issuer}, which the IPS excludes"))
        elif excluded_industries and not h.industry:
            unchecked.append(h.name)
    if unchecked:
        findings.append(Finding("exclusions_unchecked", "warning", "portfolio", float(len(unchecked)), None,
                                f"{len(unchecked)} direct holding(s) have no industry, so industry exclusions "
                                f"cannot be checked for them: {', '.join(unchecked)}"))
    return findings


def _issuer_matches(excluded: list[str], h: Holding) -> bool:
    """Excluded name's words start the issuer (or holding) name, or equal the ticker symbol."""
    if not excluded:
        return False
    symbol = (h.ticker or "").split(":")[-1].lower()
    if len(excluded) == 1 and excluded[0] == symbol:
        return True
    return any(name_words(text)[:len(excluded)] == excluded for text in (h.issuer, h.name) if text)


def name_words(name: str) -> list[str]:
    """'Tesla, Inc.' -> ['tesla']: lowercase words without legal suffixes."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return words


# --- sleeves, reserve, data quality -------------------------------------------

def check_sleeve_bands(allocation: Allocation) -> list[Finding]:
    findings = []
    for s in allocation.sleeves:
        if s.status == "within":
            continue
        limit = s.min if s.status == "below" else s.max
        findings.append(Finding("sleeve_band", "breach", s.sleeve, s.weight, limit,
                                f"{label(s.sleeve)} is {pct(s.weight)}, {s.status} its {pct(s.min)} to {pct(s.max)} "
                                f"band (target {pct(s.target)})"))
    return findings


def check_reserve(allocation: Allocation) -> list[Finding]:
    if allocation.reserve_ok is not False:
        return []
    required = allocation.reserve_required or 0.0
    return [Finding("liquidity_reserve", "breach", "reserve", allocation.reserve_value, required,
                    f"Cash reserve is {allocation.reserve_value:,.0f} {allocation.base_currency}, below the "
                    f"required {required:,.0f}: short by {required - allocation.reserve_value:,.0f}")]


def check_unvalued(allocation: Allocation) -> list[Finding]:
    return [Finding("unvalued_holding", "warning", v.holding.name, None, None,
                    f"{v.holding.name} could not be valued ({v.problem}); it is excluded from all checks")
            for v in allocation.unvalued]


def _sum_by_position(weights: Sequence[tuple[ValuedHolding, float]],
                     include: Callable[[Holding], bool]) -> dict[str, tuple[str, float]]:
    """Sum lot weights per position: {ticker (or name): (display name, weight)}. The first lot names it."""
    totals: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    for v, weight in weights:
        if include(v.holding):
            key = v.holding.ticker or v.holding.name
            totals[key] += weight
            names.setdefault(key, v.holding.name)
    return {key: (names[key], total) for key, total in totals.items()}
