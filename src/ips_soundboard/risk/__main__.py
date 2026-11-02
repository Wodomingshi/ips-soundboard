"""Risk report for a holdings file and IPS, using live Yahoo data.

    python -m ips_soundboard.risk private/holdings.csv private/ips.yaml
"""

from __future__ import annotations

import argparse
import logging
import warnings

from ips_soundboard.engine.valuation import value_holdings
from ips_soundboard.holdings import load_holdings
from ips_soundboard.marketdata.snapshot import take_snapshot
from ips_soundboard.marketdata.yahoo import YahooProvider
from ips_soundboard.risk.analysis import RiskReport, analyse_risk
from ips_soundboard.schemas.ips import load_ips


def main() -> None:
    warnings.filterwarnings("ignore", module=r"yfinance(\..*)?$")
    logging.basicConfig(level=logging.WARNING, format="  ! %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("holdings")
    parser.add_argument("ips")
    args = parser.parse_args()

    ips = load_ips(args.ips)
    holdings = load_holdings(args.holdings)
    provider = YahooProvider()
    snapshot = take_snapshot(holdings, ips.base_currency, provider)
    valued = value_holdings(holdings, snapshot.prices, snapshot.fx, ips.base_currency)
    print_report(analyse_risk(valued, ips, provider))


def print_report(r: RiskReport) -> None:
    ccy = r.base_currency
    print(f"\nRisk of the managed portfolio ({r.window_start} to {r.window_end}, {r.observations} days)")
    print(f"Coverage: {r.coverage:.1%} of the managed portfolio ({r.covered_value:,.0f} {ccy})\n")
    print(f"  Volatility (annualised)     {r.volatility:7.1%}   without FX moves: {r.volatility_without_fx:.1%}")
    print(f"  Max drawdown                {r.max_drawdown:7.1%}   ({r.drawdown_peak} to {r.drawdown_trough})")
    label = f"{r.confidence:.0%}, {r.horizon_days}-day"
    print(f"  VaR  ({label})         {r.var:7.1%}   = {r.var_value:,.0f} {ccy}")
    print(f"  CVaR ({label})         {r.cvar:7.1%}   = {r.cvar_value:,.0f} {ccy}")

    print("\n  Risk by sleeve                weight   share of risk")
    for c in r.by_sleeve:
        print(f"    {c.subject:26} {c.weight:7.1%}   {c.risk_share:7.1%}")
    print("\n  Top 10 risk contributors      weight   share of risk")
    for c in r.by_holding[:10]:
        print(f"    {c.subject[:26]:26} {c.weight:7.1%}   {c.risk_share:7.1%}")
    print("\n  Currency exposure")
    for currency, share in r.currency_exposure.items():
        print(f"    {currency}  {share:6.1%}")
    if r.stress:
        print("\n  Stress tests (today's portfolio through past crises)")
        for s in r.stress:
            print(f"    {s.portfolio_return:7.1%}   {s.name} ({s.start} to {s.end})")
    if r.excluded:
        print("\n  Not included in the risk numbers")
        for e in r.excluded:
            print(f"    ! {e.name} ({e.weight:.1%}): {e.reason}")


if __name__ == "__main__":
    main()
