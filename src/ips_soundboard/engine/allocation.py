"""Allocation: weights by sleeve, drift against the SAA, and the total-wealth view.

Buckets, in order of precedence:
  reported_only   account is reported-only in the IPS (e.g. a pension)
  out_of_scope    account not named in the IPS at all
  reserve         managed account, sleeve == IPS reserve_sleeve
  managed         everything else in managed accounts: the denominator for weights

Holdings outside the SAA sleeves (or unclassified) stay in the managed
denominator, so the SAA sleeves alone may sum to less than 100%.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ips_soundboard.engine.valuation import ValuedHolding, unvalued
from ips_soundboard.schemas.ips import IPS, SleeveTarget

UNCLASSIFIED = "unclassified"
BandStatus = Literal["below", "within", "above"]


@dataclass(frozen=True)
class SleeveDrift:
    sleeve: str
    target: float
    min: float
    max: float
    value: float
    weight: float
    drift: float  # weight - target
    status: BandStatus


@dataclass(frozen=True)
class Allocation:
    base_currency: str
    managed_value: float
    reserve_value: float
    reported_only_value: float
    out_of_scope_value: float
    sleeves: list[SleeveDrift]
    outside_saa: dict[str, float]  # sleeve name -> value in managed accounts
    reserve_required: float | None
    unvalued: list[ValuedHolding]

    @property
    def total_wealth(self) -> float:
        return self.managed_value + self.reserve_value + self.reported_only_value + self.out_of_scope_value

    @property
    def reserve_ok(self) -> bool | None:
        """None when the IPS sets no reserve rule or keeps the reserve outside these holdings."""
        if self.reserve_required is None:
            return None
        return self.reserve_value >= self.reserve_required


def bucket_of(holding_account: str, sleeve: str | None, ips: IPS) -> str:
    if holding_account in ips.scope.reported_only:
        return "reported_only"
    if holding_account not in ips.scope.managed:
        return "out_of_scope"
    if sleeve is not None and sleeve == ips.liquidity.reserve_sleeve:
        return "reserve"
    return "managed"


def band_status(weight: float, low: float, high: float) -> BandStatus:
    if weight < low:
        return "below"
    if weight > high:
        return "above"
    return "within"


def managed_weights(valued: Sequence[ValuedHolding], ips: IPS) -> list[tuple[ValuedHolding, float]]:
    """Each managed holding with its weight in the managed portfolio (for limit checks)."""
    managed = [v for v in valued if v.is_valued and _bucket(v, ips) == "managed"]
    denominator = sum(v.value_base for v in managed)  # type: ignore[misc]
    return [(v, (v.value_base / denominator) if denominator else 0.0) for v in managed]  # type: ignore[operator]


def allocate(valued: Sequence[ValuedHolding], ips: IPS) -> Allocation:
    totals: dict[str, float] = defaultdict(float)
    by_sleeve: dict[str, float] = defaultdict(float)
    for v in valued:
        if not v.is_valued:
            continue
        bucket = _bucket(v, ips)
        totals[bucket] += v.value_base  # type: ignore[operator]
        if bucket == "managed":
            by_sleeve[v.holding.sleeve or UNCLASSIFIED] += v.value_base  # type: ignore[operator]

    managed_value = totals["managed"]
    sleeves = [_drift(name, band, by_sleeve.get(name, 0.0), managed_value) for name, band in ips.saa.items()]
    outside = {name: value for name, value in sorted(by_sleeve.items()) if name not in ips.saa}
    reserve_rule = ips.liquidity.required_reserve() if ips.liquidity.reserve_sleeve else None

    return Allocation(
        base_currency=ips.base_currency,
        managed_value=managed_value,
        reserve_value=totals["reserve"],
        reported_only_value=totals["reported_only"],
        out_of_scope_value=totals["out_of_scope"],
        sleeves=sleeves,
        outside_saa=outside,
        reserve_required=reserve_rule,
        unvalued=unvalued(valued),
    )


def _bucket(v: ValuedHolding, ips: IPS) -> str:
    return bucket_of(v.holding.account, v.holding.sleeve, ips)


def _drift(name: str, band: SleeveTarget, value: float, managed_value: float) -> SleeveDrift:
    weight = value / managed_value if managed_value else 0.0
    return SleeveDrift(
        sleeve=name,
        target=band.target,
        min=band.min,
        max=band.max,
        value=value,
        weight=weight,
        drift=weight - band.target,
        status=band_status(weight, band.min, band.max),
    )
