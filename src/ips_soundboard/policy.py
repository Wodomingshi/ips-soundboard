"""The IPS in plain words: every setting the tool reads, and which setting each finding comes from.

Deterministic display text, shared by the dashboard (IPS tab, Rules tab) and later the report.
Each rule carries the settings path (e.g. "limits.single_name_buy_limit") so it can be linked to
the quote the extraction step found for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ips_soundboard.display import label, pct
from ips_soundboard.engine.limits import Finding
from ips_soundboard.schemas.ips import IPS


@dataclass(frozen=True)
class PolicyRule:
    area: str
    rule: str
    value: str
    path: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.value}"


def policy_rules(ips: IPS) -> list[PolicyRule]:
    """Every IPS setting except the SAA (see saa_rows), grouped by area, in reading order."""
    ccy = ips.base_currency
    o, lim, ex, taa, reb, risk = ips.objectives, ips.limits, ips.exclusions, ips.taa, ips.rebalancing, ips.risk

    def opt(value: float | None) -> str:
        return pct(value) if value is not None else "not set"

    rules = [
        PolicyRule("Objectives", "Real return target (above inflation)", opt(o.real_return_target),
                   "objectives.real_return_target"),
        PolicyRule("Objectives", "Design maximum drawdown", opt(o.design_max_drawdown),
                   "objectives.design_max_drawdown"),
        _reserve_rule(ips),
        PolicyRule("Scope", "Managed accounts", ", ".join(label(a) for a in ips.scope.managed), "scope.managed"),
        PolicyRule("Scope", "Reported only", ", ".join(label(a) for a in ips.scope.reported_only) or "none",
                   "scope.reported_only"),
        PolicyRule("Position limits", "Single stock: buy limit", opt(lim.single_name_buy_limit),
                   "limits.single_name_buy_limit"),
        PolicyRule("Position limits", "Single stock: hard limit (trim)", opt(lim.single_name_hard_limit),
                   "limits.single_name_hard_limit"),
        PolicyRule("Position limits", "Conviction picks: total cap", opt(lim.picks_max_total),
                   "limits.picks_max_total"),
        _banned_rule(ips),
        PolicyRule("Exclusions", "Applies to",
                   "direct holdings only" if ex.apply_to == "direct_holdings" else "also holdings inside funds",
                   "exclusions.apply_to"),
        PolicyRule("Exclusions", "Excluded issuers", ", ".join(ex.issuers) or "none", "exclusions.issuers"),
        PolicyRule("Exclusions", "Excluded industries", ", ".join(label(i) for i in ex.sub_industries) or "none",
                   "exclusions.sub_industries"),
    ]
    if taa.sleeve_tilts:
        rules.append(PolicyRule("Tactical allocation", "Sleeve tilts",
                                f"up to ±{pct(taa.sleeve_tilts.max_per_sleeve)} per sleeve, "
                                f"{pct(taa.sleeve_tilts.max_total_one_way)} in total (one way)", "taa.sleeve_tilts"))
    rules += _theme_rules(ips)
    if taa.trend:
        extension = (f", at most {pct(taa.trend.max_extension_above)} above it"
                     if taa.trend.max_extension_above is not None else "")
        rules.append(PolicyRule("Tactical allocation", "Trend filter",
                                f"price above its {taa.trend.lookback_months}-month average{extension}", "taa.trend"))
    if taa.max_age_months:
        rules.append(PolicyRule("Tactical allocation", "Maximum age of a tactical position",
                                f"{taa.max_age_months} months", "taa.max_age_months"))
    rules += [
        PolicyRule("Rebalancing", "Review", reb.monitoring_frequency, "rebalancing.monitoring_frequency"),
        PolicyRule("Rebalancing", "Full rebalance", reb.calendar_rebalance, "rebalancing.calendar_rebalance"),
        PolicyRule("Rebalancing", "Minimum trade", f"{reb.min_trade:,.0f} {ccy}", "rebalancing.min_trade"),
        PolicyRule("Rebalancing", "New money goes to at most",
                   f"{reb.new_money_max_sleeves} sleeve(s)" if reb.new_money_max_sleeves else "no limit",
                   "rebalancing.new_money_max_sleeves"),
        PolicyRule("Risk measurement (tool setting)", "History and VaR",
                   f"{risk.lookback_years:g} years; VaR {risk.var_confidence:.0%} over {risk.var_horizon_days} "
                   f"trading days; holdings need {risk.min_history_share:.0%} of the history", "risk"),
    ]
    return rules


def saa_rows(ips: IPS) -> list[dict[str, str]]:
    return [{"Sleeve": label(name), "Target": pct(band.target), "Band": f"{pct(band.min)} to {pct(band.max)}"}
            for name, band in ips.saa.items()]


def rule_for(finding: Finding, ips: IPS) -> PolicyRule | None:
    """The IPS setting a finding comes from. None for data-quality findings, which are not IPS rules."""
    by_path = {r.path: r for r in policy_rules(ips)}
    if finding.rule == "sleeve_band":
        band = ips.saa.get(finding.subject)
        if band is None:
            return None
        return PolicyRule("Strategic allocation", f"{label(finding.subject)} band",
                          f"{pct(band.min)} to {pct(band.max)} (target {pct(band.target)})", f"saa.{finding.subject}")
    path = _FINDING_TO_PATH.get(finding.rule)
    return by_path.get(path) if path else None


_FINDING_TO_PATH = {
    "single_name_buy_limit": "limits.single_name_buy_limit",
    "single_name_hard_limit": "limits.single_name_hard_limit",
    "picks_max_total": "limits.picks_max_total",
    "banned_instrument": "limits",
    "excluded_industry": "exclusions.sub_industries",
    "exclusions_unchecked": "exclusions.sub_industries",
    "excluded_issuer": "exclusions.issuers",
    "themes_not_allowed": "taa.themes",
    "theme_buy_limit": "taa.themes.max_per_theme",
    "theme_hard_limit": "taa.themes.hard_limit_per_theme",
    "themes_max_total": "taa.themes.max_total",
    "themes_max_count": "taa.themes.max_count",
    "liquidity_reserve": "liquidity",
}


def _reserve_rule(ips: IPS) -> PolicyRule:
    liq, ccy = ips.liquidity, ips.base_currency
    required = liq.required_reserve()
    if required is None:
        value = "not set"
    else:
        value = (f"at least {liq.min_months_of_expenses:g} months of maximum expenses "
                 f"({liq.monthly_expenses_max:,.0f} {ccy} a month) = {required:,.0f} {ccy}")
    return PolicyRule("Liquidity", "Cash reserve", value, "liquidity")


def _banned_rule(ips: IPS) -> PolicyRule:
    lim = ips.limits
    banned = [name for name, allowed in (("leverage", lim.leverage_allowed), ("short selling", lim.short_selling_allowed),
                                          ("derivatives", lim.derivatives_allowed),
                                          ("leveraged or inverse ETFs", lim.leveraged_or_inverse_etfs_allowed))
              if not allowed]
    return PolicyRule("Position limits", "Not allowed", ", ".join(banned) or "nothing banned", "limits")


def _theme_rules(ips: IPS) -> list[PolicyRule]:
    themes = ips.taa.themes
    if themes is None:
        return [PolicyRule("Tactical allocation", "Themes", "not allowed", "taa.themes")]
    rules = [PolicyRule("Tactical allocation", "Themes: total budget", pct(themes.max_total), "taa.themes.max_total"),
             PolicyRule("Tactical allocation", "Theme: buy limit", pct(themes.max_per_theme),
                        "taa.themes.max_per_theme"),
             PolicyRule("Tactical allocation", "Themes: maximum number", str(themes.max_count), "taa.themes.max_count")]
    if themes.hard_limit_per_theme is not None:
        rules.append(PolicyRule("Tactical allocation", "Theme: hard limit (trim)", pct(themes.hard_limit_per_theme),
                                "taa.themes.hard_limit_per_theme"))
    return rules
