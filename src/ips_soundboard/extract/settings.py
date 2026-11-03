"""The IPS settings an LLM may extract, with plain-language descriptions for the prompt.

Paths mirror `schemas.ips.IPS` (e.g. "limits.single_name_buy_limit"). A test checks that this
list and the schema stay in sync, so a new IPS field cannot be forgotten here.
The SAA is extracted separately (as a list of sleeves), and tool settings are never extracted.
"""

from __future__ import annotations

import types
import typing
from typing import Literal, Union

from pydantic import BaseModel

from ips_soundboard.schemas.ips import IPS

Kind = Literal["text", "number", "int", "bool", "choice", "list", "industry"]

# Not stated in an IPS document: tool configuration, or handled separately.
NOT_EXTRACTED = ("saa", "liquidity.reserve_sleeve", "risk")

SETTINGS: dict[str, tuple[Kind, str]] = {
    "version": ("text", "Version number or date of the IPS document, if stated"),
    "base_currency": ("text", "Currency the investor measures wealth and spends in, as a 3-letter code (e.g. EUR)"),
    "objectives.real_return_target": ("number", "Return per year targeted above inflation"),
    "objectives.design_max_drawdown": ("number", "Largest peak-to-trough loss the investor accepts, written as a "
                                                  "negative percentage (e.g. '-30%')"),
    "liquidity.monthly_expenses_max": ("number", "Maximum monthly expenses, in the base currency"),
    "liquidity.min_months_of_expenses": ("number", "Months of expenses to keep as a cash reserve"),
    "scope.managed": ("list", "An account the strategic allocation applies to (one rule per account)"),
    "scope.reported_only": ("list", "An account that is reported but not managed, e.g. a pension "
                                    "(one rule per account)"),
    "limits.single_name_buy_limit": ("number", "Weight of one single stock above which no more is bought"),
    "limits.single_name_hard_limit": ("number", "Weight of one single stock above which it must be trimmed"),
    "limits.picks_max_total": ("number", "Maximum total weight of single stocks or conviction picks"),
    "limits.leverage_allowed": ("bool", "Whether borrowing to invest is allowed"),
    "limits.short_selling_allowed": ("bool", "Whether short selling is allowed"),
    "limits.derivatives_allowed": ("bool", "Whether derivatives are allowed"),
    "limits.leveraged_or_inverse_etfs_allowed": ("bool", "Whether leveraged or inverse ETFs are allowed"),
    "exclusions.apply_to": ("choice", "'direct_holdings' if exclusions apply only to securities held directly, "
                                      "'look_through' if also to holdings inside funds"),
    "exclusions.issuers": ("list", "A company excluded by name (one rule per company)"),
    "exclusions.sub_industries": ("industry", "An excluded industry, as one of the industry ids listed below "
                                            "(one rule per industry)"),
    "taa.sleeve_tilts.max_per_sleeve": ("number", "Largest tactical deviation from target allowed for one sleeve"),
    "taa.sleeve_tilts.max_total_one_way": ("number", "Largest total of all tactical overweights, counted one way"),
    "taa.themes.max_total": ("number", "Maximum total weight of thematic positions"),
    "taa.themes.max_per_theme": ("number", "Weight of one theme above which no more is bought"),
    "taa.themes.max_count": ("int", "Maximum number of themes held at once"),
    "taa.themes.hard_limit_per_theme": ("number", "Weight of one theme above which it must be trimmed"),
    "taa.trend.lookback_months": ("int", "Length in months of the moving average used as a trend filter"),
    "taa.trend.max_extension_above": ("number", "How far above its moving average a price may be for a new "
                                                "tactical position"),
    "taa.max_age_months": ("int", "Months after which a tactical position must be reviewed or closed"),
    "rebalancing.monitoring_frequency": ("choice", "How often the portfolio is reviewed"),
    "rebalancing.calendar_rebalance": ("choice", "How often the portfolio is fully rebalanced"),
    "rebalancing.min_trade": ("number", "Smallest trade worth making, in the base currency"),
    "rebalancing.new_money_max_sleeves": ("int", "Maximum number of sleeves that receive new savings at each "
                                                 "review"),
}


def schema_paths(model: type[BaseModel] = IPS, prefix: str = "") -> list[str]:
    """Every leaf setting of the IPS schema as a dotted path."""
    paths = []
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        nested = _model_in(info.annotation)
        paths += schema_paths(nested, f"{path}.") if nested else [path]
    return paths


def choices(path: str) -> tuple[str, ...]:
    """Allowed values of a 'choice' setting, read from the schema's Literal type."""
    model: type[BaseModel] = IPS
    *parents, leaf = path.split(".")
    for part in parents:
        model = _model_in(model.model_fields[part].annotation)  # type: ignore[assignment]
    return typing.get_args(model.model_fields[leaf].annotation)


def extractable(path: str) -> bool:
    return not any(path == p or path.startswith(f"{p}.") for p in NOT_EXTRACTED)


def _model_in(annotation: object) -> type[BaseModel] | None:
    """The pydantic model inside `X` or `X | None`, if any."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if typing.get_origin(annotation) in (Union, types.UnionType):
        for arg in typing.get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
    return None
