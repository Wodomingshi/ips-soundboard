"""Canonical tickers ("EXCHANGE:SYMBOL") and their translation to provider symbols."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

_EXCHANGES_FILE = Path(__file__).with_name("exchanges.yaml")


@cache
def exchange_map(provider: str) -> dict[str, dict]:
    with _EXCHANGES_FILE.open(encoding="utf-8") as handle:
        table = yaml.safe_load(handle)
    if provider not in table:
        raise KeyError(f"no exchange table for provider '{provider}'")
    return table[provider]


def split_ticker(ticker: str) -> tuple[str | None, str]:
    """'SWX:NESN' -> ('SWX', 'NESN'); 'NESN.SW' -> (None, 'NESN.SW')."""
    exchange, sep, symbol = ticker.strip().partition(":")
    if not sep:
        return None, exchange
    return exchange.upper(), symbol


def to_provider_symbol(ticker: str, provider: str = "yahoo") -> str:
    """Translate a canonical ticker. A ticker without an exchange prefix is passed through unchanged."""
    exchange, symbol = split_ticker(ticker)
    if exchange is None:
        return symbol
    rules = exchange_map(provider).get(exchange)
    if rules is None:
        raise KeyError(f"unknown exchange '{exchange}' in ticker '{ticker}': add it to exchanges.yaml")
    if rules.get("dot_to_dash"):
        symbol = symbol.replace(".", "-")
    return symbol + rules.get("suffix", "")
