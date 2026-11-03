"""Ask Claude to classify holdings: one suggestion per row, chosen from fixed lists.

The reply schema is built per IPS at run time, so the sleeve field can only hold that IPS's
sleeves (plus existing non-SAA sleeve names, or "outside_saa"), and the industry field only the
shared vocabulary. Only name, ticker and currency are sent: no quantities or values, and not the
current classification, so the model's view is independent of it (an audit, not an echo).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from ips_soundboard.display import label
from ips_soundboard.llm import Client, ask, default_model
from ips_soundboard.schemas.holdings import Flag, InstrumentType, Role
from ips_soundboard.schemas.industries import industries
from ips_soundboard.schemas.ips import IPS

OUTSIDE_SAA = "outside_saa"
NOT_APPLICABLE = "not_applicable"
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class Row:
    """What the model sees about one holding."""

    row: int  # spreadsheet row number (header = 1)
    name: str
    ticker: str | None
    currency: str


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def reply_model(sleeves: Sequence[str]) -> type[BaseModel]:
    """Structured-output schema for this IPS: enums for every classification field."""
    sleeve_choice = Literal[tuple(dict.fromkeys([*sleeves, OUTSIDE_SAA]))]  # type: ignore[valid-type]
    industry_choice = Literal[(*industries(), NOT_APPLICABLE)]  # type: ignore[valid-type]
    suggestion = create_model(
        "Suggestion", __base__=_Strict,
        row=(int, Field(description="Row number, as given")),
        instrument_type=(InstrumentType, Field(description="What kind of instrument this is")),
        sleeve=(sleeve_choice, Field(description="SAA sleeve it belongs to, or outside_saa")),
        role=(Role, Field(description="core, satellite or theme")),
        flags=(list[Flag], Field(description="Banned-instrument properties it has; usually none")),
        issuer=(str, Field(description="Issuing company for stocks and bonds; empty for funds and commodities")),
        industry=(industry_choice, Field(description="Issuer's industry for stocks and bonds; not_applicable "
                                                     "otherwise")),
        confidence=(Confidence, Field(description="low if you do not recognise the security")),
        reason=(str, Field(description="One short sentence: what the security is")),
    )
    return create_model("Classification", __base__=_Strict, rows=(list[suggestion], ...))


def suggest(rows: Sequence[Row], ips: IPS, client: Client, extra_sleeves: Sequence[str] = (),
            model: str | None = None) -> list[BaseModel]:
    sleeves = [*ips.saa, *extra_sleeves]
    reply = ask(client, model or default_model(), system_prompt(ips, extra_sleeves),
                [{"role": "user", "content": user_prompt(rows)}], reply_model(sleeves), max_tokens=16_000)
    return list(reply.rows)  # type: ignore[attr-defined]


def system_prompt(ips: IPS, extra_sleeves: Sequence[str] = ()) -> str:
    saa = "\n".join(f"- {name}: {label(name)} (target {band.target:.0%})" for name, band in ips.saa.items())
    extra = "".join(f"\n- {name}: {label(name)} (outside the SAA, already used in this portfolio)"
                    for name in extra_sleeves)
    vocabulary = "\n".join(f"- {key}: {text}" for key, text in industries().items())
    return f"""\
You classify the holdings of a portfolio for an Investment Policy Statement (IPS) review tool.
Use your knowledge of each security from its name, ticker (EXCHANGE:SYMBOL) and currency.
If you do not recognise a security, say so with confidence "low" rather than guessing.

Sleeves of this IPS (strategic asset allocation):
{saa}{extra}
- {OUTSIDE_SAA}: none of the above (e.g. a sector, thematic or commodity fund outside the SAA)
A single stock belongs to the sleeve of its region or asset class.

Roles:
- core: implements an SAA sleeve (broad index funds; bonds; gold)
- satellite: a single stock picked by conviction
- theme: a sector or thematic fund held as a tactical bet
A single stock is usually satellite; say "medium" confidence for roles, which depend on intent.

Flags (banned-instrument properties): leveraged, inverse, derivative, short. Most holdings have none.

Issuer and industry apply to stocks and bonds only. Industries:
{vocabulary}
- {NOT_APPLICABLE}: funds, ETFs, commodities, cash

Return exactly one suggestion per row, with the row number as given.
Keep each reason to one short sentence describing the security."""


def user_prompt(rows: Sequence[Row]) -> str:
    lines = [f"row {r.row}: {r.name} | ticker {r.ticker or 'none'} | {r.currency}" for r in rows]
    return "Classify these holdings:\n" + "\n".join(lines)
