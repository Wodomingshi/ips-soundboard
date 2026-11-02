"""Run the whole analysis once: market snapshot -> valuation -> allocation -> limits -> risk.

The dashboard only displays what this returns; it computes nothing itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ips_soundboard.engine.allocation import Allocation, allocate, managed_weights
from ips_soundboard.engine.limits import Finding, check_limits
from ips_soundboard.engine.valuation import ValuedHolding, value_holdings
from ips_soundboard.holdings import Issue, check_against_ips
from ips_soundboard.marketdata.base import MarketData
from ips_soundboard.marketdata.snapshot import MarketSnapshot, take_snapshot
from ips_soundboard.risk.analysis import RiskReport, analyse_risk
from ips_soundboard.schemas.holdings import Holding
from ips_soundboard.schemas.ips import IPS


@dataclass(frozen=True)
class Analysis:
    ips: IPS
    snapshot: MarketSnapshot
    valued: list[ValuedHolding]
    weights: list[float | None]  # aligned with `valued`; None outside the managed portfolio
    allocation: Allocation
    findings: list[Finding]
    structure_issues: list[Issue]
    risk: RiskReport | None
    risk_error: str | None


def run_analysis(holdings: Sequence[Holding], ips: IPS, provider: MarketData,
                 as_of: date | None = None, with_risk: bool = True) -> Analysis:
    snapshot = take_snapshot(holdings, ips.base_currency, provider, as_of=as_of)
    valued = value_holdings(holdings, snapshot.prices, snapshot.fx, ips.base_currency)
    risk, risk_error = None, None
    if with_risk:
        try:
            risk = analyse_risk(valued, ips, provider, as_of=as_of)
        except ValueError as error:  # e.g. no holding with enough history
            risk_error = str(error)
    return Analysis(
        ips=ips,
        snapshot=snapshot,
        valued=valued,
        weights=_aligned_weights(valued, ips),
        allocation=allocate(valued, ips),
        findings=check_limits(valued, ips),
        structure_issues=check_against_ips(list(holdings), ips),
        risk=risk,
        risk_error=risk_error,
    )


def _aligned_weights(valued: list[ValuedHolding], ips: IPS) -> list[float | None]:
    by_identity = {id(v): w for v, w in managed_weights(valued, ips)}
    return [by_identity.get(id(v)) for v in valued]
