"""Check a valued portfolio against the IPS limits.

Two severities:
  breach  - a rule is violated and the IPS requires action (hard limit, banned
            instrument, sleeve outside its band, reserve below requirement)
  warning - a soft limit is reached: no further buying (buy limits), or the
            data is incomplete (unvalued holdings)

Not checked here: issuer and sub-industry exclusions. They need reliable
issuer/sector data, which the classifier module will provide. Matching names
by text gives false positives ("Meta" in "Metals").
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from ips_soundboard.engine.allocation import Allocation, allocate, managed_weights
from ips_soundboard.engine.valuation import ValuedHolding
from ips_soundboard.schemas.holdings import Holding
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
    for name, weight in stocks.items():
        if limits.single_name_hard_limit is not None and weight > limits.single_name_hard_limit:
            findings.append(Finding("single_name_hard_limit", "breach", name, weight, limits.single_name_hard_limit,
                                    f"{name} is {weight:.1%}, above the {limits.single_name_hard_limit:.0%} hard "
                                    f"limit: trim back to the buy limit"))
        elif limits.single_name_buy_limit is not None and weight > limits.single_name_buy_limit:
            findings.append(Finding("single_name_buy_limit", "warning", name, weight, limits.single_name_buy_limit,
                                    f"{name} is {weight:.1%}, above the {limits.single_name_buy_limit:.0%} buy "
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
                    f"Conviction picks total {total:.1%}, above the {limits.picks_max_total:.0%} cap")]


# --- themes -------------------------------------------------------------------

def check_themes(weights: Sequence[tuple[ValuedHolding, float]], ips: IPS) -> list[Finding]:
    budget = ips.taa.themes
    themes = _sum_by_position(weights, lambda h: h.role == "theme")
    if not themes:
        return []
    if budget is None:
        return [Finding("themes_not_allowed", "breach", name, weight, 0.0,
                        f"{name} is held as a theme, but the IPS allows no themes")
                for name, weight in themes.items()]

    findings = []
    for name, weight in themes.items():
        if budget.hard_limit_per_theme is not None and weight > budget.hard_limit_per_theme:
            findings.append(Finding("theme_hard_limit", "breach", name, weight, budget.hard_limit_per_theme,
                                    f"Theme {name} is {weight:.1%}, above the {budget.hard_limit_per_theme:.0%} "
                                    f"hard limit: trim"))
        elif weight > budget.max_per_theme:
            findings.append(Finding("theme_buy_limit", "warning", name, weight, budget.max_per_theme,
                                    f"Theme {name} is {weight:.1%}, above the {budget.max_per_theme:.0%} "
                                    f"per-theme limit: no adds"))
    total = sum(themes.values())
    if total > budget.max_total:
        findings.append(Finding("themes_max_total", "breach", "portfolio", total, budget.max_total,
                                f"Themes total {total:.1%}, above the {budget.max_total:.0%} budget"))
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


# --- sleeves, reserve, data quality -------------------------------------------

def check_sleeve_bands(allocation: Allocation) -> list[Finding]:
    findings = []
    for s in allocation.sleeves:
        if s.status == "within":
            continue
        limit = s.min if s.status == "below" else s.max
        findings.append(Finding("sleeve_band", "breach", s.sleeve, s.weight, limit,
                                f"{s.sleeve} is {s.weight:.1%}, {s.status} its {s.min:.0%}-{s.max:.0%} band "
                                f"(target {s.target:.0%})"))
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
                     include: Callable[[Holding], bool]) -> dict[str, float]:
    """Sum lot weights per position, keyed by ticker (or name when there is no ticker)."""
    totals: dict[str, float] = defaultdict(float)
    for v, weight in weights:
        if include(v.holding):
            totals[v.holding.ticker or v.holding.name] += weight
    return dict(totals)
