"""What the LLM returns: values copied as written, each with a verbatim quote as evidence.

Deliberately not the IPS schema itself. Structured outputs cannot express a mapping with free
keys (the SAA) and limit how many optional fields a schema may have, so the model fills a flat
list of rules instead. Values stay as written ("25%"); code does every unit conversion.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ips_soundboard.extract.settings import SETTINGS

Setting = Literal[tuple(SETTINGS)]  # type: ignore[valid-type]


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SleeveDraft(_Draft):
    name: str = Field(description="Sleeve name as written in the document")
    id: str = Field(description="The matching sleeve id from the holdings file, if one is given and clearly "
                                "corresponds; otherwise an empty string")
    target: str = Field(description="Target weight exactly as written, e.g. '25%'")
    min: str = Field(description="Lower end of the allowed range as written, e.g. '20%'")
    max: str = Field(description="Upper end of the allowed range as written, e.g. '30%'")
    quote: str = Field(description="Verbatim excerpt (e.g. the table row) that contains these numbers")


class RuleDraft(_Draft):
    setting: Setting = Field(description="Which IPS setting this is")  # type: ignore[valid-type]
    value: str = Field(description="The value exactly as written in the quote")
    quote: str = Field(description="Verbatim excerpt from the document that states this value")


class IPSDraft(_Draft):
    sleeves: list[SleeveDraft] = Field(description="Every sleeve of the strategic asset allocation")
    rules: list[RuleDraft] = Field(description="Every other setting the document states")
    ambiguities: list[str] = Field(description="Points the document leaves unclear or contradictory, "
                                               "one short sentence each")
