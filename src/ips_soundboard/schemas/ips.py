"""Schema for a machine-readable Investment Policy Statement.

This is the contract between the IPS (written by the investor, or extracted from
free text by an LLM) and the deterministic engine. Every field is validated, and
unknown fields are rejected, so an extraction that invents rules fails loudly
instead of silently changing the analysis.

Conventions: weights and limits are fractions of the managed portfolio (0.25 = 25%).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
Frequency = Literal["monthly", "quarterly", "semiannual", "annual"]

_SAA_SUM_TOLERANCE = 1e-6
_SLEEVE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SleeveTarget(_Strict):
    """Strategic weight of one sleeve, with its tolerance band."""

    target: Fraction
    min: Fraction
    max: Fraction

    @model_validator(mode="after")
    def _band_contains_target(self) -> SleeveTarget:
        if not self.min <= self.target <= self.max:
            raise ValueError(
                f"band must satisfy min <= target <= max, got {self.min} / {self.target} / {self.max}"
            )
        return self


class Objectives(_Strict):
    real_return_target: float | None = Field(default=None, ge=-0.5, le=0.5)
    design_max_drawdown: float | None = Field(default=None, ge=-1.0, le=0.0)


class Liquidity(_Strict):
    """Cash reserve held outside the SAA, sized in months of expenses."""

    monthly_expenses_max: float | None = Field(default=None, ge=0.0)
    min_months_of_expenses: float | None = Field(default=None, ge=0.0)
    # Holdings tagged with this sleeve are the cash reserve: counted toward the
    # liquidity check and excluded from SAA weights. None = reserve held elsewhere.
    reserve_sleeve: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")

    def required_reserve(self) -> float | None:
        """Minimum reserve in base currency, or None if the IPS does not set one."""
        if self.monthly_expenses_max is None or self.min_months_of_expenses is None:
            return None
        return self.monthly_expenses_max * self.min_months_of_expenses


class Scope(_Strict):
    """Which accounts the SAA applies to, and which are only reported."""

    managed: list[str] = Field(min_length=1)
    reported_only: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_overlap(self) -> Scope:
        overlap = set(self.managed) & set(self.reported_only)
        if overlap:
            raise ValueError(f"accounts cannot be both managed and reported-only: {sorted(overlap)}")
        return self


class PositionLimits(_Strict):
    single_name_buy_limit: Fraction | None = None
    single_name_hard_limit: Fraction | None = None
    picks_max_total: Fraction | None = None
    leverage_allowed: bool = False
    short_selling_allowed: bool = False
    derivatives_allowed: bool = False
    leveraged_or_inverse_etfs_allowed: bool = False

    @model_validator(mode="after")
    def _buy_limit_below_hard_limit(self) -> PositionLimits:
        buy, hard = self.single_name_buy_limit, self.single_name_hard_limit
        if buy is not None and hard is not None and buy > hard:
            raise ValueError(f"single_name_buy_limit ({buy}) exceeds single_name_hard_limit ({hard})")
        return self


class Exclusions(_Strict):
    apply_to: Literal["direct_holdings", "look_through"] = "direct_holdings"
    issuers: list[str] = Field(default_factory=list)
    sub_industries: list[str] = Field(default_factory=list)


class SleeveTiltBudget(_Strict):
    max_per_sleeve: Fraction
    max_total_one_way: Fraction


class ThemeBudget(_Strict):
    max_total: Fraction
    max_per_theme: Fraction  # buy limit: above it, no adds
    max_count: int = Field(ge=0)
    hard_limit_per_theme: Fraction | None = None  # above it, trim

    @model_validator(mode="after")
    def _limits_are_ordered(self) -> ThemeBudget:
        if self.max_per_theme > self.max_total:
            raise ValueError("max_per_theme cannot exceed max_total")
        if self.hard_limit_per_theme is not None and self.hard_limit_per_theme < self.max_per_theme:
            raise ValueError("hard_limit_per_theme cannot be below max_per_theme")
        return self


class TrendRule(_Strict):
    """Price vs its moving average; used as an entry gate for tactical tilts."""

    lookback_months: int = Field(default=10, ge=1, le=60)
    max_extension_above: float | None = Field(default=None, ge=0.0)


class TAAPolicy(_Strict):
    sleeve_tilts: SleeveTiltBudget | None = None
    themes: ThemeBudget | None = None
    trend: TrendRule | None = None
    max_age_months: int | None = Field(default=None, ge=1)


class Rebalancing(_Strict):
    monitoring_frequency: Frequency = "quarterly"
    calendar_rebalance: Frequency = "annual"
    min_trade: float = Field(default=0.0, ge=0.0)


class RiskSettings(_Strict):
    """How risk is measured. Defaults: 5 years of daily data, 95% one-month historical VaR."""

    lookback_years: float = Field(default=5.0, gt=0.0, le=30.0)
    var_confidence: float = Field(default=0.95, gt=0.5, lt=1.0)
    var_horizon_days: int = Field(default=21, ge=1, le=260)  # trading days; 21 = about one month
    min_history_share: float = Field(default=0.8, gt=0.0, le=1.0)  # of the lookback window


class IPS(_Strict):
    """Top-level, machine-readable IPS."""

    version: str
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    objectives: Objectives = Objectives()
    liquidity: Liquidity = Liquidity()
    scope: Scope
    saa: dict[str, SleeveTarget] = Field(min_length=1)
    limits: PositionLimits = PositionLimits()
    exclusions: Exclusions = Exclusions()
    taa: TAAPolicy = TAAPolicy()
    rebalancing: Rebalancing = Rebalancing()
    risk: RiskSettings = RiskSettings()

    @field_validator("saa")
    @classmethod
    def _saa_is_complete(cls, saa: dict[str, SleeveTarget]) -> dict[str, SleeveTarget]:
        bad_names = [name for name in saa if not _SLEEVE_NAME.match(name)]
        if bad_names:
            raise ValueError(f"sleeve names must be snake_case: {bad_names}")
        total = sum(sleeve.target for sleeve in saa.values())
        if abs(total - 1.0) > _SAA_SUM_TOLERANCE:
            raise ValueError(f"SAA targets must sum to 1.0, got {total:.6f}")
        return saa

    @model_validator(mode="after")
    def _reserve_is_not_an_saa_sleeve(self) -> IPS:
        if self.liquidity.reserve_sleeve in self.saa:
            raise ValueError(
                f"reserve_sleeve '{self.liquidity.reserve_sleeve}' is also an SAA sleeve; it would be counted twice"
            )
        return self


def load_ips(path: str | Path) -> IPS:
    """Read and validate an IPS config from a YAML file."""
    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return IPS.model_validate(raw)
