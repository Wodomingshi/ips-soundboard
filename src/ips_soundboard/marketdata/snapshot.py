"""Fetch everything a valuation needs for a set of holdings, in one call.

Produces the plain `prices` and `fx` tables that engine.valuation expects, plus
a list of problems. A quote whose currency differs from the holding's currency
is not used: valuing it would silently apply the wrong FX rate.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from ips_soundboard.marketdata.base import MarketData, Quote
from ips_soundboard.schemas.holdings import Holding


@dataclass(frozen=True)
class MarketSnapshot:
    as_of: date
    base_currency: str
    prices: dict[str, float]  # ticker -> price in the holding's currency
    fx: dict[str, float]  # currency -> base units per 1 unit
    problems: list[str] = field(default_factory=list)


def take_snapshot(holdings: Sequence[Holding], base_currency: str, provider: MarketData,
                  as_of: date | None = None) -> MarketSnapshot:
    base = base_currency.upper()
    ticker_currency = {h.ticker: h.currency for h in holdings if h.ticker}
    quotes = provider.latest_prices(list(ticker_currency))
    prices, problems = _check_currencies(quotes, ticker_currency)
    problems += [f"{t}: no quote from provider" for t in ticker_currency if t not in quotes]

    needed = sorted({h.currency for h in holdings} - {base})
    fx = provider.fx_rates(needed, base)
    problems += [f"{c}/{base}: no FX rate from provider" for c in needed if c not in fx]
    return MarketSnapshot(as_of or date.today(), base, prices, fx, problems)


def _check_currencies(quotes: dict[str, Quote], expected: dict[str, str]) -> tuple[dict[str, float], list[str]]:
    prices, problems = {}, []
    for ticker, quote in quotes.items():
        if quote.currency == expected[ticker]:
            prices[ticker] = quote.price
        else:
            problems.append(f"{ticker}: quoted in {quote.currency}, but the holding is in "
                            f"{expected[ticker]}; price not used")
    return prices, problems


# --- cache: one JSON file per day, so repeated runs don't re-download ----------

def save_snapshot(snapshot: MarketSnapshot, cache_dir: str | Path) -> Path:
    path = Path(cache_dir) / f"snapshot-{snapshot.base_currency}-{snapshot.as_of.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(snapshot) | {"as_of": snapshot.as_of.isoformat()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_snapshot(base_currency: str, as_of: date, cache_dir: str | Path) -> MarketSnapshot | None:
    path = Path(cache_dir) / f"snapshot-{base_currency.upper()}-{as_of.isoformat()}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return MarketSnapshot(date.fromisoformat(raw["as_of"]), raw["base_currency"], raw["prices"], raw["fx"],
                          raw["problems"])
