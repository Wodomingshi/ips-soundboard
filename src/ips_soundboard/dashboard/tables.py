"""Display tables built from an Analysis. Pure functions, testable without Streamlit."""

from __future__ import annotations

import pandas as pd

from ips_soundboard.dashboard.pipeline import Analysis

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
    rows = [{"Sleeve": s.sleeve, "Weight": s.weight, "Target": s.target, "Min": s.min, "Max": s.max,
             "Drift": s.drift, "Status": s.status, "Value": s.value} for s in a.allocation.sleeves]
    rows += [{"Sleeve": f"{name} (outside SAA)", "Weight": value / a.allocation.managed_value
              if a.allocation.managed_value else 0.0, "Target": 0.0, "Min": 0.0, "Max": 0.0,
              "Drift": None, "Status": "outside SAA", "Value": value}
             for name, value in a.allocation.outside_saa.items()]
    return pd.DataFrame(rows)


def findings_table(a: Analysis) -> pd.DataFrame:
    return pd.DataFrame([{"Severity": f.severity, "Rule": f.rule, "Subject": f.subject, "Finding": f.message}
                         for f in a.findings], columns=["Severity", "Rule", "Subject", "Finding"])


def holdings_table(a: Analysis) -> pd.DataFrame:
    rows = []
    for v, weight in zip(a.valued, a.weights, strict=True):
        h = v.holding
        cost = h.quantity * h.purchase_price if h.quantity is not None and h.purchase_price is not None else None
        local_return = (v.value_local / cost - 1.0) if cost and v.value_local is not None else None
        rows.append({
            "Name": h.name,
            "Ticker": h.ticker or "",
            "Account": h.account,
            "Sleeve": h.sleeve or "",
            "Role": h.role or "",
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
    rows = a.risk.by_holding if by == "holding" else a.risk.by_sleeve
    return pd.DataFrame([{"Name": c.subject, "Weight": c.weight, "Share of risk": c.risk_share} for c in rows])


def stress_table(a: Analysis) -> pd.DataFrame:
    if a.risk is None:
        return pd.DataFrame()
    return pd.DataFrame([{"Scenario": s.name, "From": s.start, "To": s.end, "Portfolio return": s.portfolio_return,
                          "Coverage": s.coverage} for s in a.risk.stress])


def excluded_table(a: Analysis) -> pd.DataFrame:
    if a.risk is None:
        return pd.DataFrame()
    return pd.DataFrame([{"Holding": e.name, "Weight": e.weight, "Reason": e.reason} for e in a.risk.excluded])
