"""Live check: fetch prices and FX for a holdings file and print what was found.

    python -m ips_soundboard.marketdata private/holdings.csv --base CHF

Results are cached in data/cache/ (git-ignored). Use --refresh to re-download.
"""

from __future__ import annotations

import argparse
import logging
import warnings
from datetime import date

from ips_soundboard.holdings import load_holdings
from ips_soundboard.marketdata.snapshot import load_snapshot, save_snapshot, take_snapshot
from ips_soundboard.marketdata.yahoo import YahooProvider

CACHE_DIR = "data/cache"


def main() -> None:
    # yfinance (not this project) triggers pandas deprecation warnings; matched by message
    # because pandas attributes them to varying modules
    warnings.filterwarnings("ignore", message=r"The inplace keyword")
    # show why an individual symbol failed (logged by the Yahoo client)
    logging.basicConfig(level=logging.WARNING, format="  ! %(message)s")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("holdings")
    parser.add_argument("--base", default="CHF")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    holdings = load_holdings(args.holdings)
    snapshot = None if args.refresh else load_snapshot(args.base, date.today(), CACHE_DIR)
    if snapshot is None:
        snapshot = take_snapshot(holdings, args.base, YahooProvider())
        print(f"Fetched from Yahoo, cached at {save_snapshot(snapshot, CACHE_DIR)}")
    else:
        print("Using today's cached snapshot (use --refresh to re-download)")

    print(f"\n{len(snapshot.prices)} prices, {len(snapshot.fx)} FX rates, {len(snapshot.problems)} problems")
    for currency, rate in sorted(snapshot.fx.items()):
        print(f"  FX {currency}/{snapshot.base_currency}: {rate:.4f}")
    for ticker, price in sorted(snapshot.prices.items()):
        print(f"  {ticker:18} {price:>12,.2f}")
    for problem in snapshot.problems:
        print(f"  ! {problem}")


if __name__ == "__main__":
    main()
