"""Display tables built from an Analysis. Pure functions, testable without Streamlit."""

from __future__ import annotations

import pandas as pd

from ips_soundboard.dashboard.pipeline import Analysis
from ips_soundboard.engine.contributions import ContributionPlan
from ips_soundboard.display import label
from ips_soundboard.risk.history import breach_periods, review_start

PRICE_SOURCE_LABEL = {
    "live": "live price",
    "manual_value": "manual value",
    "manual_price": "manual price",
    "cash": "cash balance",
}


def wealth_table(a: Analysis) -> pd.DataFrame:
    al = a.allocation
    rows = [
        ("Managed portfolio", al.managed_value),
        ("Cash reserve", al.reserve_value),
        ("Reported only (e.g. pensions)", al.reported_only_value),
        ("Outside the IPS scope", al.out_of_scope_value),
    ]
    frame = pd.DataFrame([r for r in rows if r[1]], columns=["Bucket", "Value"])
    frame["Share of total"] = frame["Value"] / al.total_wealth if al.total_wealth else 0.0
    return frame


def sleeve_table(a: Analysis) -> pd.DataFrame:
    rows = [{"Sleeve": label(s.sleeve), "Weight": s.weight, "Target": s.target, "Min": s.min, "Max": s.max,
             "Drift": s.drift, "Status": s.status, "Value": s.value} for s in a.allocation.sleeves]
    rows += [{"Sleeve": f"{label(name)} (outside SAA)", "Weight": value / a.allocation.managed_value
              if a.allocation.managed_value else 0.0, "Target": 0.0, "Min": 0.0, "Max": 0.0,
              "Drift": None, "Status": "outside SAA", "Value": value}
             for name, value in a.allocation.outside_saa.items()]
    return pd.DataFrame(rows)


def findings_table(a: Analysis) -> pd.DataFrame:
    """Each message names the holding or sleeve and the IPS limit, so no separate rule column is needed."""
    return pd.DataFrame([{"Severity": f.severity, "Rule": f.rule, "Finding": f.message} for f in a.findings],
                        columns=["Severity", "Rule", "Finding"])


def holdings_table(a: Analysis) -> pd.DataFrame:
    rows = []
    for v, weight in zip(a.valued, a.weights, strict=True):
        h = v.holding
        cost = h.quantity * h.purchase_price if h.quantity is not None and h.purchase_price is not None else None
        local_return = (v.value_local / cost - 1.0) if cost and v.value_local is not None else None
        rows.append({
            "Name": h.name,
            "Ticker": h.ticker or "",
            "Account": label(h.account),
            "Sleeve": label(h.sleeve) if h.sleeve else "",
            "Role": label(h.role) if h.role else "",
            "Industry": label(h.industry) if h.industry else "",
            "Currency": h.currency,
            "Value (local)": v.value_local,
            f"Value ({a.ips.base_currency})": v.value_base,
            "Weight": weight,
            "Return (local)": local_return,
            "Priced from": PRICE_SOURCE_LABEL.get(v.price_source or "", "not valued"),
            "Problem": v.problem or "",
        })
    frame = pd.DataFrame(rows)
    return frame.sort_values(f"Value ({a.ips.base_currency})", ascending=False, na_position="last")


def risk_headline(a: Analysis) -> pd.DataFrame:
    r = a.risk
    if r is None:
        return pd.DataFrame()
    horizon = f"{r.confidence:.0%}, {r.horizon_days} trading days"
    return pd.DataFrame([
        ("Volatility (annualised)", r.volatility, None),
        ("Volatility without FX moves", r.volatility_without_fx, None),
        ("Maximum drawdown", r.max_drawdown, f"{r.drawdown_peak} to {r.drawdown_trough}"),
        (f"VaR ({horizon})", r.var, r.var_value),
        (f"CVaR ({horizon})", r.cvar, r.cvar_value),
    ], columns=["Measure", "Value", "Detail"])


def contribution_table(a: Analysis, by: str = "holding") -> pd.DataFrame:
    if a.risk is None:
        return pd.DataFrame()
    if by == "holding":
        rows = [(c.subject, c) for c in a.risk.by_holding]
    else:
        rows = [(label(c.subject), c) for c in a.risk.by_sleeve]
    return pd.DataFrame([{"Name": name, "Weight": c.weight, "Share of risk": c.risk_share} for name, c in rows],
                        columns=["Name", "Weight", "Share of risk"])


def stress_table(a: Analysis) -> pd.DataFrame:
    if a.risk is None:
        return pd.DataFrame()
    return pd.DataFrame([{"Scenario": s.name, "From": s.start, "To": s.end, "Portfolio return": s.portfolio_return,
                          "Coverage": s.coverage} for s in a.risk.stress])


def excluded_table(a: Analysis) -> pd.DataFrame:
    if a.risk is None:
        return pd.DataFrame()
    return pd.DataFrame([{"Holding": e.name, "Weight": e.weight, "Reason": e.reason} for e in a.risk.excluded])


def new_money_table(plan: ContributionPlan) -> pd.DataFrame:
    """Every SAA sleeve: what it receives and its weight before and after, against the target."""
    candidates = {b.sleeve: ", ".join(b.candidates) or "no current holding" for b in plan.buys}
    return pd.DataFrame([{"Sleeve": label(e.sleeve), "Buy": e.buy, "Before": e.weight_before,
                          "After": e.weight_after, "Target": e.target, "Instruments": candidates.get(e.sleeve, "")}
                         for e in plan.effects])


# --- value history and the latest review period ------------------------------------------

PERIOD_WORD = {"monthly": "month", "quarterly": "quarter", "semiannual": "half-year", "annual": "year"}


def history_frame(a: Analysis) -> pd.DataFrame:
    """Managed portfolio value per day: today's holdings at past prices."""
    if a.risk is None or a.risk.history is None:
        return pd.DataFrame(columns=["Date", "Value"])
    values = a.risk.history.values.dropna()
    return pd.DataFrame({"Date": values.index, "Value": values.to_numpy()})


def breach_frame(a: Analysis) -> pd.DataFrame:
    """Periods in the latest review period when a sleeve was outside its band (for shading)."""
    columns = ["Start", "End", "Sleeves"]
    if a.risk is None or a.risk.history is None:
        return pd.DataFrame(columns=columns)
    periods = breach_periods(a.risk.history, a.ips, a.snapshot.as_of)
    return pd.DataFrame([{"Start": pd.Timestamp(p.start), "End": pd.Timestamp(p.end) + pd.Timedelta(days=1),
                          "Sleeves": ", ".join(label(s) for s in p.sleeves)} for p in periods], columns=columns)


def rebalancing_summary(a: Analysis) -> str:
    """One sentence on band breaches in the latest review period."""
    if a.risk is None or a.risk.history is None:
        return ""
    period = PERIOD_WORD[a.ips.rebalancing.monitoring_frequency]
    start = review_start(a.ips, a.snapshot.as_of)
    periods = breach_periods(a.risk.history, a.ips, a.snapshot.as_of)
    if not periods:
        return f"No sleeve left its band in the last {period} (since {start}): no rebalancing needed."
    sleeves = list(dict.fromkeys(label(s) for p in periods for s in p.sleeves))
    days = a.risk.history.sleeve_weights.index
    window = days[days >= pd.Timestamp(start)]
    throughout = len(periods) == 1 and periods[0].start == window[0].date() and periods[0].end == window[-1].date()
    when = f"throughout the {period}" if throughout else "on " + \
        "; ".join(f"{p.start} to {p.end}" if p.start != p.end else str(p.start) for p in periods)
    return (f"Rebalancing needed: in the last {period} (since {start}), {', '.join(sleeves)} "
            f"{'was outside its band' if len(sleeves) == 1 else 'were outside their bands'} {when}.")
