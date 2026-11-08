"""IPS Checker dashboard.

    streamlit run app/dashboard.py

Displays what the engine and risk modules compute; no financial logic lives here.
"""

from __future__ import annotations

import html
import os
import sys
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from pydantic import ValidationError

# Always import the package from this repository's src/, even if an older copy is installed:
# hosts such as Streamlit Community Cloud update the code on each push but reinstall packages
# only when requirements.txt changes, so an installed copy can lag behind this file.
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


def _reload_package_if_updated() -> None:
    """Streamlit reruns this script in the same process and reloads changed files from app/ only.

    After an update, the package under src/ would stay in memory in its old version while this
    file is new. So: if any package file changed since it was loaded, drop the loaded modules
    and the cached results, and import fresh.
    """
    version = max(path.stat().st_mtime for path in (SRC / "ips_soundboard").rglob("*.py"))
    loaded = sys.modules.get("ips_soundboard")
    if loaded is not None and getattr(loaded, "_code_version", version) != version:
        for name in [m for m in sys.modules if m == "ips_soundboard" or m.startswith("ips_soundboard.")]:
            del sys.modules[name]
        st.cache_resource.clear()
        st.cache_data.clear()
    import ips_soundboard

    ips_soundboard._code_version = version


_reload_package_if_updated()

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
SECRETS = "My portfolio"
# Hide the private/ option (layout checks and screenshots must never show real data).
DEMO_ONLY = bool(os.environ.get("IPS_SOUNDBOARD_DEMO_ONLY"))


# Risk chart: the neutral measure in grey, the finding (share of risk) in one strong colour.
NEUTRAL = "#c4c7cc"
ACCENT = "#1f3a68"

st.set_page_config(page_title="IPS Checker", layout="wide")


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
.status {display: flex; align-items: center; gap: 1rem; padding: 0.9rem 1rem; border-radius: 12px; margin: 0.25rem 0 1rem;}
.status .dot {flex: none; width: 2.6rem; height: 2.6rem; border-radius: 50%; color: #fff; font-size: 1.4rem;
  font-weight: 700; display: flex; align-items: center; justify-content: center;}
.status .headline {font-size: 1.15rem; font-weight: 600; line-height: 1.3;}
.status .detail {font-size: 0.9rem; opacity: 0.75;}
.status.ok {background: rgba(44, 160, 44, 0.10);} .status.ok .dot {background: #2ca02c;}
.status.warning {background: rgba(230, 160, 0, 0.12);} .status.warning .dot {background: #e6a000;}
.status.breach {background: rgba(214, 39, 40, 0.10);} .status.breach .dot {background: #d62728;}
.finding {border-left: 4px solid; border-radius: 6px; padding: 0.55rem 0.8rem; margin: 0 0 0.5rem; line-height: 1.45;
  background: rgba(128, 128, 128, 0.06);}
.finding.breach {border-color: #d62728;} .finding.warning {border-color: #e6a000;}
.finding .chip {font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; margin-right: 0.5rem;}
.finding.breach .chip {color: #d62728;} .finding.warning .chip {color: #b07a00;}
.kpi .tip {position: relative; display: inline-block; margin-left: 0.3rem; cursor: help; outline: none;}
.kpi .tip::after {content: attr(data-tip); position: absolute; left: -0.5rem; bottom: 1.5rem; z-index: 10; display: none;
  background: #262730; color: #fff; padding: 0.4rem 0.6rem; border-radius: 6px; font-size: 0.8rem; white-space: nowrap;}
.kpi .tip:hover::after, .kpi .tip:focus::after {display: block;}
.legend {display: flex; flex-wrap: wrap; gap: 0.35rem 1.1rem; font-size: 0.85rem; opacity: 0.85; margin: 0 0 0.25rem;}
.legend span {display: inline-flex; align-items: center; gap: 0.4rem;}
.legend i {display: inline-block; width: 16px; height: 10px; border-radius: 2px;}
.legend i.range {background: #d9d9d9; height: 14px;}
.legend i.within {background: #2ca02c;}
.legend i.outside {background: #d62728;}
.legend i.target {background: #111; width: 3px; height: 16px; border-radius: 0;}
</style>
"""


def kpis(items: list[tuple]) -> None:
    """(label, value, highlight in red[, note under the value[, text shown on hover/tap of an info icon]])."""
    cells = ""
    for label_, value, alert, *extra in items:
        note, tip = (extra + [None, None])[:2]
        note_html = f'<div class="note">{html.escape(note)}</div>' if note else ""
        tip_html = (f'<span class="tip" tabindex="0" data-tip="{html.escape(tip, quote=True)}">ⓘ</span>'
                    if tip else "")
        cells += (f'<div class="kpi"><div class="label">{html.escape(label_)}{tip_html}</div>'
                  f'<div class="value{" alert" if alert else ""}">{html.escape(value)}</div>{note_html}</div>')
    st.markdown(f'{KPI_CSS}<div class="kpis">{cells}</div>', unsafe_allow_html=True)


# --- inputs ------------------------------------------------------------------------

def secret_portfolio() -> tuple[str, str, str | None] | None:
    """Holdings and IPS stored as app secrets (a private hosted deployment), if present.

    Keys: holdings_csv, ips_yaml, and optionally ips_md. See docs/deploy.md.
    """
    try:
        secrets = st.secrets
        if "holdings_csv" in secrets and "ips_yaml" in secrets:
            return secrets["holdings_csv"], secrets["ips_yaml"], secrets.get("ips_md")
    except (FileNotFoundError, KeyError, st.errors.StreamlitSecretNotFoundError):
        pass  # no secrets configured: normal outside a deployment
    return None


def choose_inputs() -> tuple[str, str, str | None] | None:
    """(holdings CSV, IPS YAML, IPS document or None)."""
    st.sidebar.header("Portfolio")
    has_private = (PRIVATE_DIR / "holdings.csv").exists() and (PRIVATE_DIR / "ips.yaml").exists()
    from_secrets = None if DEMO_ONLY else secret_portfolio()
    own = ([SECRETS] if from_secrets else []) + ([PRIVATE] if has_private and not DEMO_ONLY else [])
    sources = own + [DEMO, UPLOAD]  # your own data first, when there is any
    source = st.sidebar.radio("Data", sources, index=0)
    if source == SECRETS:
        return from_secrets
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
    """One row per SAA sleeve, plus an 'Outside SAA' row when part of the portfolio sits outside it."""
    base = alt.Chart(frame).encode(y=alt.Y("Sleeve:N", sort=None, title=None, axis=alt.Axis(labelLimit=140)))
    band = base.mark_bar(color="#d9d9d9", height=22).encode(
        x=alt.X("Min:Q", axis=alt.Axis(format="%"), title="Weight in the managed portfolio"), x2="Max:Q")
    actual = base.mark_bar(height=10).encode(
        x="Weight:Q",
        color=alt.Color("Status:N", scale=alt.Scale(domain=["below", "within", "above"],
                                                    range=["#d62728", "#2ca02c", "#d62728"]), legend=None),
        tooltip=["Sleeve", alt.Tooltip("Weight:Q", format=".1%"), alt.Tooltip("Target:Q", format=".1%"),
                 alt.Tooltip("Min:Q", format=".1%"), alt.Tooltip("Max:Q", format=".1%"), "Details"])
    target = base.mark_tick(color="black", thickness=2, size=26).encode(x="Target:Q")
    return (band + actual + target).properties(height=40 * len(frame))


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

def value_chart(a: Analysis, start: date) -> alt.LayerChart | None:
    history = tables.history_frame(a, start)
    if history.empty:
        return None
    ccy = a.ips.base_currency
    review = pd.Timestamp(review_start(a.ips, a.snapshot.as_of))
    period = tables.PERIOD_WORD[a.ips.rebalancing.monitoring_frequency]
    x = alt.X("Date:T", title=None)
    y = alt.Y("Value:Q", title=None, scale=alt.Scale(zero=False), axis=alt.Axis(format="~s"))

    # Hover (or tap): the nearest date gets a vertical line, a dot on the curve and a tooltip with its value.
    hover = alt.selection_point(fields=["Date"], nearest=True, on="pointerover", clear="pointerout", empty=False)
    base = alt.Chart(history).encode(x=x, y=y)
    line = base.mark_line(color=ACCENT, strokeWidth=1.5)
    catcher = base.mark_point(opacity=0, size=200).encode(
        tooltip=[alt.Tooltip("Date:T", format="%d %b %Y", title="Date"),
                 alt.Tooltip("Value:Q", format=",.0f", title=f"Value ({ccy})")]).add_params(hover)
    cursor = alt.Chart(history).mark_rule(color="#888").encode(x=x).transform_filter(hover)
    dot = base.mark_point(filled=True, size=55, color=ACCENT).transform_filter(hover)

    marker = pd.DataFrame({"Date": [review], "Label": [f"Last {period}"]})
    period_rule = alt.Chart(marker).mark_rule(color="#888", strokeDash=[4, 3]).encode(x="Date:T")
    period_text = alt.Chart(marker).mark_text(align="right", dx=-4, y=alt.expr("height - 6"), color="#666",
                                              fontSize=11).encode(
        x="Date:T", text="Label")
    layers = [line, period_rule, period_text, catcher, cursor, dot]
    breaches = tables.breach_frame(a)
    if not breaches.empty:
        breaches["Shading"] = "Rebalancing required"
        layers.insert(0, alt.Chart(breaches).mark_rect(opacity=0.18).encode(
            x="Start:T", x2="End:T",
            color=alt.Color("Shading:N", scale=alt.Scale(range=["#d62728"]),
                            legend=alt.Legend(orient="top", title=None, symbolType="square", symbolOpacity=0.35)),
            tooltip=[alt.Tooltip("Sleeves", title="Outside band"), alt.Tooltip("Start:T", format="%d %b %Y"),
                     alt.Tooltip("End:T", format="%d %b %Y")]))
    return alt.layer(*layers).properties(height=220)


def overview(a: Analysis) -> None:
    ccy = a.ips.base_currency
    ranges = tables.history_ranges(a)
    if ranges:
        st.subheader("Portfolio value")
        default = "Since first trade"
        choice = st.segmented_control("Period", list(ranges), default=default, key="value_range",
                                      label_visibility="collapsed")
        st.altair_chart(value_chart(a, ranges[choice or default]), width="stretch")
        if a.risk.history.notes:  # only when some holdings had to be assumed (e.g. no purchase date)
            st.caption(" ".join(a.risk.history.notes))
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Allocation vs SAA")
        st.markdown('<div class="legend"><span><i class="range"></i>Allowed range</span>'
                    '<span><i class="target"></i>Target</span><span><i class="within"></i>Within range</span>'
                    '<span><i class="outside"></i>Outside range</span></div>', unsafe_allow_html=True)
        sleeves = tables.sleeve_table(a)
        st.altair_chart(sleeve_chart(tables.sleeve_chart_frame(a)), width="stretch")
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


def compliance(a: Analysis, document: str | None) -> None:
    level, headline, detail = tables.compliance_status(a)
    mark = {"ok": "✓", "warning": "!", "breach": "!"}[level]
    st.markdown(f'<div class="status {level}"><div class="dot">{mark}</div><div>'
                f'<div class="headline">{html.escape(headline)}</div><div class="detail">{html.escape(detail)}</div>'
                f'</div></div>', unsafe_allow_html=True)
    findings = tables.findings_table(a)
    if not findings.empty:
        order = {"breach": 0, "warning": 1}
        cards = "".join(
            f'<div class="finding {row.Severity}"><span class="chip">{row.Severity}</span>{html.escape(row.Finding)}</div>'
            for row in sorted(findings.itertuples(), key=lambda r: order[r.Severity]))
        st.markdown(cards, unsafe_allow_html=True)
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
    st.title("IPS Checker")
    st.caption("Checks your portfolio against your Investment Policy Statement.")
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
    unvalued = len(a.allocation.unvalued)
    kpis([("Total wealth", f"{a.allocation.total_wealth:,.0f} {ccy}", False, None,
           f"Prices as of {a.snapshot.as_of}. Unvalued holdings: {unvalued}."),
          ("Managed portfolio", f"{a.allocation.managed_value:,.0f} {ccy}", False),
          ("IPS breaches", str(breaches), breaches > 0),
          ("Warnings", str(warnings_), False)])
    if unvalued:  # worth seeing without hovering: these holdings are missing from every figure
        st.warning(f"{unvalued} holding(s) could not be valued and are left out of the figures (see Holdings).")

    document = inputs[2]
    tab_overview, tab_compliance, tab_holdings, tab_risk, tab_new = st.tabs(
        ["Overview", "Compliance", "Holdings", "Risk", "New money"])
    with tab_overview:
        overview(a)
    with tab_compliance:
        compliance(a, document)
    with tab_holdings:
        holdings(a)
    with tab_risk:
        risk(a)
    with tab_new:
        new_money(a)


main()
