"""The facts a report may use: everything already computed, formatted exactly as it may be written.

Numbers are pre-formatted strings ("37.3%", "116,500 EUR"), so the narrative copies them rather
than computing or rounding its own. The number check then accepts only numbers found here.
"""

from __future__ import annotations

from typing import Any

from ips_soundboard.dashboard.pipeline import Analysis
from ips_soundboard.display import label, pct
from ips_soundboard.engine.contributions import ContributionPlan

TOP_CONTRIBUTORS = 5


def build_facts(a: Analysis, plan: ContributionPlan | None = None) -> dict[str, Any]:
    ccy = a.ips.base_currency
    al = a.allocation

    def money(value: float) -> str:
        return f"{value:,.0f} {ccy}"

    facts: dict[str, Any] = {
        "as_of": str(a.snapshot.as_of),
        "base_currency": ccy,
        "wealth": {
            "total": money(al.total_wealth),
            "managed_portfolio": money(al.managed_value),
            "cash_reserve": money(al.reserve_value),
            "reported_only": money(al.reported_only_value),
            "outside_ips_scope": money(al.out_of_scope_value),
        },
        "allocation_vs_saa": [
            {"sleeve": label(s.sleeve), "weight": pct(s.weight), "target": pct(s.target),
             "band": f"{pct(s.min)} to {pct(s.max)}", "drift": f"{s.drift * 100:+.1f} percentage points",
             "status": s.status, "value": money(s.value)}
            for s in al.sleeves],
        "outside_saa": [{"sleeve": label(name), "weight": pct(value / al.managed_value) if al.managed_value else "n/a",
                         "value": money(value)} for name, value in al.outside_saa.items()],
        "liquidity_reserve": None if al.reserve_required is None else {
            "value": money(al.reserve_value), "required": money(al.reserve_required),
            "meets_requirement": bool(al.reserve_ok)},
        "rule_findings": {
            "breaches": sum(f.severity == "breach" for f in a.findings),
            "warnings": sum(f.severity == "warning" for f in a.findings),
            "items": [{"severity": f.severity, "finding": f.message} for f in a.findings],
        },
        "ips_rebalancing_policy": {
            "review": a.ips.rebalancing.monitoring_frequency,
            "full_rebalance": a.ips.rebalancing.calendar_rebalance,
            "minimum_trade": money(a.ips.rebalancing.min_trade),
            "new_money_max_sleeves": a.ips.rebalancing.new_money_max_sleeves,
        },
        "data_quality": {
            "unvalued_holdings": [v.holding.name for v in al.unvalued],
            "market_data_problems": len(a.snapshot.problems),
        },
        "risk": _risk(a, money),
        "new_money": _new_money(plan, money) if plan else None,
    }
    return facts


def _risk(a: Analysis, money: Any) -> dict[str, Any] | None:
    r = a.risk
    if r is None:
        return {"not_available": a.risk_error}
    return {
        "history_window": f"{r.window_start} to {r.window_end}",
        "coverage_of_managed_portfolio": pct(r.coverage),
        "volatility_annualised": pct(r.volatility),
        "volatility_without_fx_moves": pct(r.volatility_without_fx),
        "max_drawdown": pct(r.max_drawdown),
        "max_drawdown_period": f"{r.drawdown_peak} to {r.drawdown_trough}",
        "value_at_risk": {"confidence": f"{r.confidence:.0%}", "horizon": f"{r.horizon_days} trading days",
                          "loss": f"{pct(r.var)} ({money(r.var_value)})"},
        "conditional_value_at_risk": {"loss": f"{pct(r.cvar)} ({money(r.cvar_value)})",
                                      "meaning": f"average loss in the worst {1 - r.confidence:.0%} of periods"},
        "largest_risk_contributors": [{"holding": c.subject, "weight": pct(c.weight), "share_of_risk": pct(c.risk_share)}
                                      for c in r.by_holding[:TOP_CONTRIBUTORS]],
        "risk_by_sleeve": [{"sleeve": label(c.subject), "weight": pct(c.weight), "share_of_risk": pct(c.risk_share)}
                           for c in r.by_sleeve],
        "currency_exposure": {currency: pct(share) for currency, share in r.currency_exposure.items()},
        "stress_tests": [{"scenario": s.name, "period": f"{s.start} to {s.end}",
                          "portfolio_return": pct(s.portfolio_return), "coverage": pct(s.coverage)} for s in r.stress],
        "stress_test_note": "Scenario names are labels; the tool does not know why markets moved.",
        "not_in_risk_numbers": [{"holding": e.name, "weight": pct(e.weight), "reason": e.reason} for e in r.excluded],
    }


def _new_money(plan: ContributionPlan, money: Any) -> dict[str, Any]:
    return {
        "amount": money(plan.amount),
        "reserve_top_up": money(plan.reserve_top_up),
        "buys": [{"sleeve": label(b.sleeve), "amount": money(b.amount),
                  "existing_instruments": b.candidates[:3]} for b in plan.buys],
        "kept_as_cash": money(plan.held_back),
        "notes": plan.notes,
    }
