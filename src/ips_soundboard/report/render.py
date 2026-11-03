"""Markdown report: the narrative, then tables written by code from the same facts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from ips_soundboard.report.narrative import NarrativeResult


def render(facts: dict[str, Any], result: NarrativeResult, model: str, facts_file: str,
           today: date | None = None) -> str:
    n = result.narrative
    out = [f"# Portfolio review, {facts['as_of']}", "",
           f"*Narrative written by {model} on {today or date.today()} from computed results; tables and numbers "
           f"from the deterministic engine. Analysis against the investor's own IPS only: not investment, tax or "
           f"legal advice.*", "", "## Summary", ""]
    out += [f"- {line}" for line in n.summary] + [""]

    out += ["## Allocation vs SAA", ""] + _paragraphs(n.allocation)
    out += _table(["Sleeve", "Weight", "Target", "Band", "Status"],
                  [[s["sleeve"], s["weight"], s["target"], s["band"], s["status"]] for s in facts["allocation_vs_saa"]])
    if facts["outside_saa"]:
        out += ["Outside the SAA:", ""] + _table(["Sleeve", "Weight", "Value"],
                                                [[s["sleeve"], s["weight"], s["value"]] for s in facts["outside_saa"]])
    wealth = facts["wealth"]
    out += [f"Total wealth {wealth['total']}, of which managed portfolio {wealth['managed_portfolio']}.", ""]

    out += ["## IPS rules", ""] + _paragraphs(n.rules)
    items = facts["rule_findings"]["items"]
    out += _table(["Severity", "Finding"], [[f["severity"], f["finding"]] for f in items]) if items else \
        ["No breaches or warnings.", ""]

    out += ["## Risk", ""] + _paragraphs(n.risk) + _risk_tables(facts.get("risk"))

    if facts.get("new_money"):
        plan = facts["new_money"]
        out += ["## New money", ""] + _paragraphs(n.new_money)
        if plan["buys"]:
            out += _table(["Sleeve", "Buy", "Existing instruments"],
                          [[b["sleeve"], b["amount"], ", ".join(b["existing_instruments"]) or "none"]
                           for b in plan["buys"]])
        else:
            out += [f"No buys this time; kept as cash: {plan['kept_as_cash']}.", ""]

    out += ["## Checks", ""] + _checks(result, facts_file)
    return "\n".join(out).rstrip() + "\n"


def _risk_tables(risk: dict[str, Any] | None) -> list[str]:
    if not risk or "not_available" in risk:
        return [f"Risk could not be computed: {risk.get('not_available') if risk else 'no data'}", ""]
    var, cvar = risk["value_at_risk"], risk["conditional_value_at_risk"]
    rows = [["Volatility (annualised)", risk["volatility_annualised"]],
            ["Volatility without FX moves", risk["volatility_without_fx_moves"]],
            ["Max drawdown", f"{risk['max_drawdown']} ({risk['max_drawdown_period']})"],
            [f"VaR {var['confidence']}, {var['horizon']}", var["loss"]],
            [f"CVaR {var['confidence']}, {var['horizon']}", cvar["loss"]],
            ["Coverage of the managed portfolio", risk["coverage_of_managed_portfolio"]]]
    out = _table(["Measure", "Value"], rows)
    out += _table(["Largest risk contributors", "Weight (covered portfolio)", "Share of risk"],
                  [[c["holding"], c["weight_in_covered_portfolio"], c["share_of_risk"]]
                   for c in risk["largest_risk_contributors"]])
    if risk["stress_tests"]:
        out += _table(["Stress scenario", "Period", "Portfolio return", "Coverage"],
                      [[s["scenario"], s["period"], s["portfolio_return"], s["coverage"]] for s in risk["stress_tests"]])
    if risk["not_in_risk_numbers"]:
        out += _table(["Not in the risk numbers", "Weight", "Reason"],
                      [[e["holding"], e["weight"], e["reason"]] for e in risk["not_in_risk_numbers"]])
    return out


def _checks(result: NarrativeResult, facts_file: str) -> list[str]:
    lines = []
    if result.removed:
        lines.append(f"{len(result.removed)} sentence(s) were removed because they contained numbers that are "
                     f"not in the computed facts:")
        lines += [f"- \"{p.sentence}\" (unsupported: {', '.join(p.numbers)})" for p in result.removed]
    else:
        lines.append(f"All {result.numbers_checked} numbers in the narrative were found in the computed facts.")
    if result.attempts > 1:
        lines.append("The narrative was rewritten once to fix unsupported numbers.")
    lines += ["", f"Facts used: `{facts_file}`."]
    return lines


def _paragraphs(paragraphs: Sequence[str]) -> list[str]:
    out = []
    for p in paragraphs:
        out += [p, ""]
    return out


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|")

    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return lines + [""]
