"""Quarterly allocation of new savings: which SAA sleeves get new money, and how much.

Buys only, never sells. Order of use:
  1. Top up the cash reserve if it is below what the IPS requires.
  2. Invest the rest in the SAA sleeves furthest below target ("deepest gap first").

Deepest gap first: gaps are measured in money against each sleeve's target in the portfolio
*after* the contribution. Money goes to the deepest gap until it is level with the next one,
then to both, and so on. This uses as few sleeves as possible and minimises the largest
remaining shortfall, which suits an IPS that steers with new money rather than trades.

The money always fits: the positive gaps sum to at least the amount invested, because SAA
targets sum to 1 and every overweight sleeve or holding outside the SAA widens them.

Themes, conviction picks and holdings outside the SAA get no new money: they are diluted by it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ips_soundboard.display import label
from ips_soundboard.engine.allocation import Allocation, bucket_of
from ips_soundboard.engine.valuation import ValuedHolding
from ips_soundboard.schemas.ips import IPS


@dataclass(frozen=True)
class SleeveBuy:
    sleeve: str
    amount: float
    candidates: list[str]  # existing core holdings in the sleeve, largest first


@dataclass(frozen=True)
class SleeveEffect:
    sleeve: str
    target: float
    weight_before: float
    buy: float
    weight_after: float


@dataclass(frozen=True)
class ContributionPlan:
    base_currency: str
    amount: float
    reserve_top_up: float
    buys: list[SleeveBuy]
    held_back: float  # kept as cash (below the minimum trade)
    effects: list[SleeveEffect]  # every SAA sleeve, in IPS order
    notes: list[str] = field(default_factory=list)

    @property
    def invested(self) -> float:
        return sum(b.amount for b in self.buys)


def plan_contribution(allocation: Allocation, valued: Sequence[ValuedHolding], ips: IPS,
                      amount: float) -> ContributionPlan:
    """Split `amount` (in the base currency) between the reserve and the most underweight SAA sleeves."""
    if amount < 0:
        raise ValueError("the amount to invest cannot be negative")
    ccy = allocation.base_currency
    notes: list[str] = []

    top_up = _reserve_top_up(allocation, amount)
    if top_up:
        notes.append(f"Cash reserve is below the required {allocation.reserve_required:,.0f} {ccy}: "
                     f"{top_up:,.0f} {ccy} stays in cash first")
    investable = amount - top_up
    total_after = allocation.managed_value + investable
    gaps = {s.sleeve: s.target * total_after - s.value for s in allocation.sleeves}

    amounts, held_back = _allocate(investable, gaps, ips.rebalancing.new_money_max_sleeves,
                                   ips.rebalancing.min_trade, notes, ccy)
    if allocation.unvalued:
        notes.append(f"{len(allocation.unvalued)} holding(s) could not be valued and are left out of the gaps")

    buys = [SleeveBuy(sleeve, value, _candidates(sleeve, valued, ips))
            for sleeve, value in sorted(amounts.items(), key=lambda item: -item[1])]
    effects = [SleeveEffect(s.sleeve, s.target, s.weight, amounts.get(s.sleeve, 0.0),
                            (s.value + amounts.get(s.sleeve, 0.0)) / total_after if total_after else 0.0)
               for s in allocation.sleeves]
    return ContributionPlan(ccy, amount, top_up, buys, held_back, effects, notes)


# --- steps ---------------------------------------------------------------------

def _reserve_top_up(allocation: Allocation, amount: float) -> float:
    if allocation.reserve_required is None:
        return 0.0
    return min(amount, max(0.0, allocation.reserve_required - allocation.reserve_value))


def _allocate(investable: float, gaps: dict[str, float], max_sleeves: int | None, min_trade: float,
              notes: list[str], ccy: str) -> tuple[dict[str, float], float]:
    """Deepest gap first, within the sleeve cap and the minimum trade. Returns (amounts, held back)."""
    if investable <= 0:
        return {}, 0.0
    ranked = sorted(((s, g) for s, g in gaps.items() if g > 0), key=lambda item: -item[1])
    if not ranked:  # only possible without SAA sleeves to fill
        notes.append("No SAA sleeve is below target: the amount is kept as cash")
        return {}, investable

    count = min(max_sleeves or len(ranked), len(ranked))
    while count < len(ranked) and sum(g for _, g in ranked[:count]) < investable:
        count += 1  # the cap gives way only when the money does not fit in fewer sleeves
    if max_sleeves and count > max_sleeves:
        notes.append(f"The amount does not fit in {max_sleeves} sleeve(s) without overshooting their "
                     f"targets: {count} sleeves are used")

    while count > 0:
        amounts = water_fill(investable, [g for _, g in ranked[:count]])
        if min(amounts) >= min_trade or count == 1:
            break
        count -= 1  # the smallest buy is below the minimum trade: give its money to deeper gaps
    if amounts[0] < min_trade:
        notes.append(f"{investable:,.0f} {ccy} is below the minimum trade of {min_trade:,.0f} {ccy}: "
                     f"keep it as cash until the next review")
        return {}, investable
    chosen = {sleeve: value for (sleeve, _), value in zip(ranked[:count], amounts, strict=True) if value > 0}
    held_back = investable - sum(chosen.values())
    if held_back > 0.5:
        notes.append(f"{held_back:,.0f} {ccy} does not fit without overshooting a target: keep it as cash")
    return chosen, held_back


def water_fill(amount: float, gaps: Sequence[float]) -> list[float]:
    """Fill gaps (sorted, deepest first) from the top down to a common level. Never exceeds a gap.

    Example: 9,000 into gaps of 13,925 and 11,575 -> 5,675 and 3,325 (both left 8,250 short).
    """
    level = 0.0
    for j in range(1, len(gaps) + 1):
        candidate = (sum(gaps[:j]) - amount) / j
        next_gap = gaps[j] if j < len(gaps) else 0.0
        if candidate >= next_gap:
            level = candidate
            break
    return [max(0.0, g - max(level, 0.0)) for g in gaps]


def _candidates(sleeve: str, valued: Sequence[ValuedHolding], ips: IPS) -> list[str]:
    """Existing core holdings in the sleeve. Picks and themes are thesis decisions, never suggested."""
    def is_core(v: ValuedHolding) -> bool:
        h = v.holding
        return h.role == "core" or (h.role is None and h.instrument_type not in ("stock", "cash"))

    held = [v for v in valued if v.is_valued and v.holding.sleeve == sleeve and is_core(v)
            and bucket_of(v.holding.account, v.holding.sleeve, ips) == "managed"]
    names = [v.holding.name for v in sorted(held, key=lambda v: -(v.value_base or 0.0))]
    return list(dict.fromkeys(names))


def describe(plan: ContributionPlan) -> list[str]:
    """Plain-text plan, one line per action (for the CLI)."""
    ccy = plan.base_currency
    lines = []
    if plan.reserve_top_up:
        lines.append(f"Keep {plan.reserve_top_up:,.0f} {ccy} in cash (reserve top-up)")
    for b in plan.buys:
        where = f": e.g. {', '.join(b.candidates[:3])}" if b.candidates else ": no current holding, choose an instrument"
        lines.append(f"Buy {b.amount:,.0f} {ccy} of {label(b.sleeve)}{where}")
    if plan.held_back:
        lines.append(f"Hold {plan.held_back:,.0f} {ccy} as cash until the next review")
    return lines
