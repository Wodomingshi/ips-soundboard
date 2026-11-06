"""IPS Soundboard dashboard.

    streamlit run app/dashboard.py

Displays what the engine and risk modules compute; no financial logic lives here.
"""

from __future__ import annotations

import html
import os
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from pydantic import ValidationError

from ips_soundboard.dashboard import tables
from ips_soundboard.dashboard.pipeline import Analysis, new_money_plan, run_analysis
from ips_soundboard.engine.contributions import describe
from ips_soundboard.risk.history import review_start
from ips_soundboard.dashboard.providers import get_provider
from ips_soundboard.holdings import HoldingsFileError, parse_holdings
from ips_soundboard.schemas.ips import parse_ips

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "examples" / "sample_investor_eur"
PRIVATE_DIR = ROOT / "private"

DEMO = "Demo: sample investor (fictional)"
PRIVATE = "My files (private/ folder)"
UPLOAD = "Upload files"
# Hide the private/ option (layout checks and screenshots must never show real data).
DEMO_ONLY = bool(os.environ.get("IPS_SOUNDBOARD_DEMO_ONLY"))


# Risk chart: the neutral measure in grey, the finding (share of risk) in one strong colour.
NEUTRAL = "#c4c7cc"
ACCENT = "#1f3a68"

st.set_page_config(page_title="IPS Soundboard", layout="wide")


# --- layout --------------------------------------------------------------------------

# Key figures as a grid: four across on a desktop, two by two on a phone (Streamlit's own
# columns would stack them one under another on a narrow screen).
KPI_CSS = """
<style>
.kpis {display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.75rem 1.5rem; margin: 0.25rem 0 0.5rem;}
@media (max-width: 640px) {.kpis {grid-template-columns: repeat(2, minmax(0, 1fr));}}
.kpi .label {font-size: 0.875rem; opacity: 0.7;}
.kpi .value {font-size: 1.75rem; line-height: 1.3; font-variant-numeric: tabular-nums; white-space: nowrap;}
.kpi .value.alert {color: #d62728;}
.kpi .note {font-size: 0.8rem; opacity: 0.65; line-height: 1.3;}
@media (max-width: 640px) {.kpi .value {font-size: 1.35rem;} h1 {font-size: 2rem !important;}}
.finding {margin: 0 0 0.5rem; line-height: 1.45;}
</style>
"""


def kpis(items: list[tuple[str, str, bool]] | list[tuple[str, str, bool, str]]) -> None:
    """(label, value, highlight in red[, small note under the value])."""
    cells = ""
    for label_, value, alert, *note in items:
        note_html = f'<div class="note">{html.escape(note[0])}</div>' if note else ""
        cells += (f'<div class="kpi"><div class="label">{html.escape(label_)}</div>'
                  f'<div class="value{" alert" if alert else ""}">{html.escape(value)}</div>{note_html}</div>')
    st.markdown(f'{KPI_CSS}<div class="kpis">{cells}</div>', unsafe_allow_html=True)


# --- inputs ------------------------------------------------------------------------

def choose_inputs() -> tuple[str, str, str | None] | None:
    """(holdings CSV, IPS YAML, IPS document or None)."""
    st.sidebar.header("Portfolio")
    has_private = (PRIVATE_DIR / "holdings.csv").exists() and (PRIVATE_DIR / "ips.yaml").exists()
    sources = [DEMO] + ([PRIVATE] if has_private and not DEMO_ONLY else []) + [UPLOAD]
    source = st.sidebar.radio("Data", sources, index=1 if PRIVATE in sources else 0)
    if source in (DEMO, PRIVATE):
        folder = DEMO_DIR if source == DEMO else PRIVATE_DIR
        document = folder / "IPS.md"
        return ((folder / "holdings.csv").read_text(encoding="utf-8"), (folder / "ips.yaml").read_text(encoding="utf-8"),
                document.read_text(encoding="utf-8") if document.exists() else None)
    holdings_file = st.sidebar.file_uploader("holdings.csv", type="csv")
    ips_file = st.sidebar.file_uploader("ips.yaml", type=["yaml", "yml"])
    document_file = st.sidebar.file_uploader("IPS document (optional)", type=["md", "txt"])
    if holdings_file is None or ips_file is None:
        st.info("Upload a holdings CSV and an IPS YAML in the sidebar, or pick the demo.")
        return None
    document = document_file.getvalue().decode("utf-8") if document_file else None
    return holdings_file.getvalue().decode("utf-8"), ips_file.getvalue().decode("utf-8"), document


@st.cache_resource(ttl=3600, show_spinner="Fetching prices, FX and 5 years of history...")
def analyse(holdings_text: str, ips_text: str, as_of: date) -> Analysis:
    return run_analysis(parse_holdings(holdings_text), parse_ips(ips_text), get_provider(), as_of=as_of)


# --- tables ------------------------------------------------------------------------

def show(frame: pd.DataFrame, *, pct: tuple[str, ...] = (), amounts: tuple[str, ...] = (), **kwargs) -> None:
    """A table in the one number format: percentages to one decimal, amounts in whole units.

    Values are rounded here rather than by Streamlit's presets, which truncate (37.25% -> 37.2%)
    and would disagree with the rounded figures in the findings text.
    """
    frame = frame.copy()
    config = dict(kwargs.pop("column_config", {}))
    for column in pct:
        frame[column] = (pd.to_numeric(frame[column]) * 100).round(1)
        config.setdefault(column, st.column_config.NumberColumn(format="%.1f%%"))
    for column in amounts:
        frame[column] = pd.to_numeric(frame[column]).round(0)
        config.setdefault(column, st.column_config.NumberColumn(format="localized"))
    st.dataframe(frame, hide_index=True, width="stretch", column_config=config, **kwargs)


# --- charts ------------------------------------------------------------------------

def sleeve_chart(frame: pd.DataFrame) -> alt.Chart:
    saa = frame[frame["Status"] != "outside SAA"]
    base = alt.Chart(saa).encode(y=alt.Y("Sleeve:N", sort=None, title=None, axis=alt.Axis(labelLimit=140)))
    band = base.mark_bar(color="#d9d9d9", height=22).encode(
        x=alt.X("Min:Q", axis=alt.Axis(format="%"), title="Weight in the managed portfolio"), x2="Max:Q")
    actual = base.mark_bar(height=10).encode(
        x="Weight:Q",
        color=alt.Color("Status:N", scale=alt.Scale(domain=["below", "within", "above"],
                                                    range=["#d62728", "#2ca02c", "#d62728"]), legend=None),
        tooltip=["Sleeve", alt.Tooltip("Weight:Q", format=".1%"), alt.Tooltip("Target:Q", format=".1%"),
                 alt.Tooltip("Min:Q", format=".1%"), alt.Tooltip("Max:Q", format=".1%")])
    target = base.mark_tick(color="black", thickness=2, size=26).encode(x="Target:Q")
    return (band + actual + target).properties(height=40 * len(saa))


def contribution_chart(frame: pd.DataFrame) -> alt.Chart:
    top = frame.head(15).melt(id_vars="Name", value_vars=["Weight", "Share of risk"], var_name="Measure")
    return alt.Chart(top).mark_bar().encode(
        y=alt.Y("Name:N", sort=list(frame.head(15)["Name"]), title=None, axis=alt.Axis(labelLimit=140)),
        x=alt.X("value:Q", axis=alt.Axis(format="%"), title=None),
        yOffset="Measure:N",
        color=alt.Color("Measure:N", scale=alt.Scale(domain=["Weight", "Share of risk"],
                                                     range=[NEUTRAL, ACCENT]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["Name", "Measure", alt.Tooltip("value:Q", format=".1%")],
    ).properties(height=34 * len(top) // 2)


# --- tabs --------------------------------------------------------------------------

def value_chart(a: Analysis) -> alt.LayerChart | None:
    history = tables.history_frame(a)
    if history.empty:
        return None
    start = pd.Timestamp(review_start(a.ips, a.snapshot.as_of))
    period = tables.PERIOD_WORD[a.ips.rebalancing.monitoring_frequency]
    x = alt.X("Date:T", title=None, axis=alt.Axis(format="%Y", tickCount="year"))
    line = alt.Chart(history).mark_line(color=ACCENT, strokeWidth=1.5).encode(
        x=x, y=alt.Y("Value:Q", title=None, scale=alt.Scale(zero=False), axis=alt.Axis(format="~s")),
        tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip("Value:Q", format=",.0f")])
    marker = pd.DataFrame({"Date": [start], "Label": [f"Last {period}"]})
    rule = alt.Chart(marker).mark_rule(color="#888", strokeDash=[4, 3]).encode(x="Date:T")
    text = alt.Chart(marker).mark_text(align="right", dx=-4, y=8, color="#666", fontSize=11).encode(
        x="Date:T", text="Label")
    layers = [line, rule, text]
    breaches = tables.breach_frame(a)
    if not breaches.empty:
        layers.insert(0, alt.Chart(breaches).mark_rect(color="#d62728", opacity=0.18).encode(
            x="Start:T", x2="End:T",
            tooltip=[alt.Tooltip("Sleeves", title="Outside band"), alt.Tooltip("Start:T", format="%d %b %Y"),
                     alt.Tooltip("End:T", format="%d %b %Y")]))
    return alt.layer(*layers).properties(height=220)


def overview(a: Analysis) -> None:
    ccy = a.ips.base_currency
    chart = value_chart(a)
    if chart is not None:
        st.subheader("Portfolio value")
        st.altair_chart(chart, width="stretch")
        summary = tables.rebalancing_summary(a)
        (st.warning if summary.startswith("Rebalancing needed") else st.success)(summary)
        st.caption(f"Managed portfolio in {ccy}: today's holdings valued at past prices, so past trades and "
                   f"new money are not shown. Red shading: days in the latest review period when a sleeve was "
                   f"outside its IPS band.")
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Allocation vs SAA")
        st.caption("Grey bar = allowed band, black tick = target, coloured bar = actual (red = outside band).")
        sleeves = tables.sleeve_table(a)
        st.altair_chart(sleeve_chart(sleeves), width="stretch")
        show(sleeves.rename(columns={"Value": f"Value ({ccy})"}),
             pct=("Weight", "Target", "Min", "Max", "Drift"), amounts=(f"Value ({ccy})",))
    with right:
        st.subheader("Total wealth")
        wealth = tables.wealth_table(a).rename(columns={"Value": f"Value ({ccy})", "Share of total": "Share"})
        show(wealth, pct=("Share",), amounts=(f"Value ({ccy})",))
        reserve = a.allocation
        if reserve.reserve_ok is not None:
            text = (f"Cash reserve {reserve.reserve_value:,.0f} {ccy} vs required "
                    f"{reserve.reserve_required:,.0f} {ccy}")
            (st.success if reserve.reserve_ok else st.error)(text)
    if a.snapshot.problems:
        with st.expander(f"Market data notes ({len(a.snapshot.problems)})"):
            for p in a.snapshot.problems:
                st.write(f"- {p}")
    if a.structure_issues:
        with st.expander(f"Classification notes ({len(a.structure_issues)})"):
            for i in a.structure_issues:
                st.write(f"- **{i.holding}**: {i.message}")


def rules(a: Analysis, document: str | None) -> None:
    findings = tables.findings_table(a)
    if findings.empty:
        st.success("No breaches or warnings: the portfolio is within every IPS limit checked.")
    else:
        # Lines rather than a table: long findings wrap instead of being cut off on a phone.
        icons = {"breach": "🔴", "warning": "🟡"}
        for row in findings.itertuples():
            st.markdown(f'<p class="finding">{icons[row.Severity]} <b>{row.Severity.capitalize()}</b>: '
                        f'{html.escape(row.Finding)}</p>', unsafe_allow_html=True)
    st.caption("Breach = the IPS requires action. Warning = a soft limit is reached (no further buying). "
               "Exclusions are checked for directly held stocks and bonds, using the issuer and industry "
               "columns of the holdings file (fill them by hand or with the classifier).")
    if document:
        with st.expander("Read your IPS document"):
            st.markdown(document)


def holdings(a: Analysis) -> None:
    ccy = a.ips.base_currency
    show(tables.holdings_table(a), height=600, pct=("Weight", "Return (local)"),
         amounts=("Value (local)", f"Value ({ccy})"))
    st.caption("Weight = share of the managed portfolio. Return (local) = price return in the holding's own "
               "currency vs purchase price, excluding dividends and FX.")


def risk(a: Analysis) -> None:
    if a.risk is None:
        st.warning(f"Risk could not be computed: {a.risk_error}")
        return
    r, ccy = a.risk, a.ips.base_currency
    st.caption(f"Today's portfolio run over {r.window_start} to {r.window_end} ({r.observations} days), "
               f"in {ccy} including FX. Covers {r.coverage:.1%} of the managed portfolio.")
    kpis([("Volatility (annualised)", f"{r.volatility:.1%}", False, f"{r.volatility_without_fx:.1%} without FX moves"),
          ("Max drawdown", f"{r.max_drawdown:.1%}", False, f"{r.drawdown_peak} to {r.drawdown_trough}"),
          (f"VaR {r.confidence:.0%}, 1 month", f"{r.var_value:,.0f} {ccy}", False,
           f"{r.var:.1%} of the covered portfolio"),
          (f"CVaR {r.confidence:.0%}, 1 month", f"{r.cvar_value:,.0f} {ccy}", False,
           f"{r.cvar:.1%}: average loss in the worst {1 - r.confidence:.0%} of months")])

    st.subheader("Where the risk comes from")
    st.caption("Share of risk: each holding's contribution to portfolio volatility (sums to 100%). "
               "A share well above the weight means a concentrated risk.")
    by_holding = tables.contribution_table(a, "holding")
    st.altair_chart(contribution_chart(by_holding), width="stretch")
    left, right = st.columns(2)
    with left:
        show(tables.contribution_table(a, "sleeve").rename(columns={"Name": "Sleeve"}),
             pct=("Weight", "Share of risk"))
    with right:
        exposure = pd.DataFrame(list(r.currency_exposure.items()), columns=["Currency", "Share"])
        show(exposure, pct=("Share",))

    st.subheader("Stress tests")
    show(tables.stress_table(a), pct=("Portfolio return", "Coverage"))
    st.caption("Scenario names are labels defined in risk/scenarios.yaml. The tool only compounds today's "
               "portfolio between the dates; it does not know why markets moved.")
    if r.excluded:
        st.subheader("Not included in the risk numbers")
        show(tables.excluded_table(a), pct=("Weight",))


def new_money(a: Analysis) -> None:
    ccy, rules_ = a.ips.base_currency, a.ips.rebalancing
    step = float(rules_.min_trade) if rules_.min_trade else 1000.0
    amount = st.columns([1, 2])[0].number_input(f"Amount to invest ({ccy})", min_value=0.0, value=0.0, step=step,
                                                format="%.0f")
    n = rules_.new_money_max_sleeves
    cap = f"at most {n} sleeve{'s' if n > 1 else ''}" if n else "no sleeve cap"
    st.caption(f"Cash reserve first, then the SAA sleeves furthest below target ({cap}; minimum trade "
               f"{rules_.min_trade:,.0f} {ccy}). Buys only. Themes and conviction picks get no new money.")
    if amount <= 0:
        return
    plan = new_money_plan(a, amount)
    st.subheader("Plan")
    st.markdown("\n".join(f"- {line}" for line in describe(plan)) or "Nothing to do.")
    for note in plan.notes:
        st.info(note)
    st.subheader("Effect on the allocation")
    show(tables.new_money_table(plan).drop(columns="Instruments").rename(columns={"Buy": f"Buy ({ccy})"}),
         pct=("Before", "After", "Target"), amounts=(f"Buy ({ccy})",))
    st.caption("Instruments named in the plan are your existing core holdings in that sleeve, largest first. "
               "The choice is yours.")


# --- page --------------------------------------------------------------------------

def main() -> None:
    st.title("IPS Soundboard")
    st.caption("Reviews a portfolio against its own Investment Policy Statement. "
               "Analysis only: not investment, tax or legal advice.")
    inputs = choose_inputs()
    if inputs is None:
        return
    if st.sidebar.button("Refresh market data"):
        analyse.clear()
    try:
        a = analyse(inputs[0], inputs[1], as_of=date.today())
    except HoldingsFileError as error:
        st.error("The holdings file has problems:\n\n" + "\n".join(f"- {p}" for p in error.problems))
        return
    except ValidationError as error:
        st.error(f"The IPS file is not valid:\n\n{error}")
        return

    ccy = a.ips.base_currency
    breaches = sum(f.severity == "breach" for f in a.findings)
    warnings_ = sum(f.severity == "warning" for f in a.findings)
    kpis([("Total wealth", f"{a.allocation.total_wealth:,.0f} {ccy}", False),
          ("Managed portfolio", f"{a.allocation.managed_value:,.0f} {ccy}", False),
          ("IPS breaches", str(breaches), breaches > 0),
          ("Warnings", str(warnings_), False)])
    st.caption(f"Prices as of {a.snapshot.as_of}. Unvalued holdings: {len(a.allocation.unvalued)}.")

    document = inputs[2]
    tab_overview, tab_rules, tab_holdings, tab_risk, tab_new = st.tabs(
        ["Overview", "Rules", "Holdings", "Risk", "New money"])
    with tab_overview:
        overview(a)
    with tab_rules:
        rules(a, document)
    with tab_holdings:
        holdings(a)
    with tab_risk:
        risk(a)
    with tab_new:
        new_money(a)


main()
