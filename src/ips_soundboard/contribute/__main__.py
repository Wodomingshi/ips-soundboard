"""Where to invest new savings this quarter.

    python -m ips_soundboard.contribute private/holdings.csv private/ips.yaml --amount 9000
"""

from __future__ import annotations

import argparse
import logging

from ips_soundboard.display import label, pct
from ips_soundboard.engine.allocation import allocate
from ips_soundboard.engine.contributions import ContributionPlan, describe, plan_contribution
from ips_soundboard.engine.valuation import value_holdings
from ips_soundboard.holdings import load_holdings
from ips_soundboard.marketdata.snapshot import take_snapshot
from ips_soundboard.marketdata.yahoo import YahooProvider, quiet_yfinance_warnings
from ips_soundboard.schemas.ips import load_ips


def main() -> None:
    quiet_yfinance_warnings()
    logging.basicConfig(level=logging.WARNING, format="  ! %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("holdings")
    parser.add_argument("ips")
    parser.add_argument("--amount", type=float, required=True, help="amount to invest, in the base currency")
    args = parser.parse_args()

    ips = load_ips(args.ips)
    holdings = load_holdings(args.holdings)
    snapshot = take_snapshot(holdings, ips.base_currency, YahooProvider())
    valued = value_holdings(holdings, snapshot.prices, snapshot.fx, ips.base_currency)
    print_plan(plan_contribution(allocate(valued, ips), valued, ips, args.amount))


def print_plan(plan: ContributionPlan) -> None:
    print(f"\nNew money: {plan.amount:,.0f} {plan.base_currency}\n")
    for line in describe(plan) or ["Nothing to do"]:
        print(f"  {line}")
    for note in plan.notes:
        print(f"  ! {note}")
    print("\n  Sleeve                       before     after    target")
    for e in plan.effects:
        mark = "  <- buy" if e.buy else ""
        print(f"    {label(e.sleeve)[:26]:26} {pct(e.weight_before):>7}   {pct(e.weight_after):>7}   "
              f"{pct(e.target):>7}{mark}")


if __name__ == "__main__":
    main()
