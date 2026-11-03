"""Schema for portfolio holdings.

One row per purchase lot, so cost basis survives for later tax-lot work.
Classification fields (instrument_type, sleeve, role, issuer, industry) are optional: an
unclassified holding is valid input, and the classifier module fills the gaps.

Prices and FX are not part of a holding. They come from the market-data layer;
manual_price and manual_value are fallbacks for assets with no live quote.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ips_soundboard.schemas.industries import industries

InstrumentType = Literal["stock", "etf", "fund", "bond", "cash", "commodity", "pension", "other"]

# How a holding is governed by the IPS:
#   core      - implements an SAA sleeve (index funds, or direct holdings used as an index)
#   satellite - a conviction pick, subject to the single-stock caps
#   theme     - a TAA theme, subject to the theme budget
Role = Literal["core", "satellite", "theme"]

# Instrument properties the IPS can ban. In a CSV, write them separated by ';'.
Flag = Literal["leveraged", "inverse", "derivative", "short"]

_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")


def to_snake(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


class Holding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    account: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    ticker: str | None = None
    quantity: float | None = Field(default=None, ge=0.0)
    manual_price: float | None = Field(default=None, gt=0.0)
    manual_value: float | None = None
    instrument_type: InstrumentType | None = None
    sleeve: str | None = None
    role: Role | None = None
    purchase_date: date | None = None
    purchase_price: float | None = Field(default=None, ge=0.0)
    flags: frozenset[Flag] = frozenset()
    # For directly held stocks and bonds: the company behind it, and its industry (schemas/industries.yaml).
    # Used by the IPS exclusion check.
    issuer: str | None = None
    industry: str | None = None

    @field_validator("flags", mode="before")
    @classmethod
    def _split_flags(cls, value: object) -> object:
        """'leveraged; inverse' -> {'leveraged', 'inverse'}"""
        if isinstance(value, str):
            return {part.strip().lower() for part in value.replace(",", ";").split(";") if part.strip()}
        return value

    @field_validator("industry", mode="before")
    @classmethod
    def _check_industry(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        industry = to_snake(value)
        if industry not in industries():
            raise ValueError(f"unknown industry '{value}': use one of the ids in schemas/industries.yaml")
        return industry

    @field_validator("name", "ticker", "issuer", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("account", "sleeve", mode="before")
    @classmethod
    def _normalise_id(cls, value: object) -> object:
        """'Pillar 3a' -> 'pillar_3a', so account and sleeve names match the IPS."""
        return to_snake(value) if isinstance(value, str) else value

    @field_validator("account", "sleeve")
    @classmethod
    def _check_id(cls, value: str | None) -> str | None:
        if value is not None and not _SNAKE.match(value):
            raise ValueError(f"'{value}' must start with a letter and use only letters, digits and _")
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("instrument_type", "role", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _has_a_way_to_be_valued(self) -> Holding:
        if self.quantity is None and self.manual_value is None:
            raise ValueError("needs a quantity or a manual_value")
        if self.purchase_price is not None and self.quantity is None:
            raise ValueError("purchase_price needs a quantity")
        return self
