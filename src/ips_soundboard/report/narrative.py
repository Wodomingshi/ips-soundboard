"""Claude writes the narrative from the facts; the number check decides what survives.

One retry: sentences with numbers not in the facts are sent back to be rewritten. Any that are
still unsupported after the retry are removed, and the report lists them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ips_soundboard.llm import Client, ask, default_model
from ips_soundboard.report.numbers import AllowedNumbers, Unsupported, allowed_numbers, check_paragraphs, sentences, \
    tokens


class Narrative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: list[str] = Field(description="3 to 5 bullet points, most important first")
    allocation: list[str] = Field(description="Paragraphs on allocation vs the SAA")
    rules: list[str] = Field(description="Paragraphs on the IPS rule findings and what they require")
    risk: list[str] = Field(description="Paragraphs on risk")
    new_money: list[str] = Field(description="Paragraphs on the new-money plan; empty if there is none")


SECTIONS = ("summary", "allocation", "rules", "risk", "new_money")


@dataclass(frozen=True)
class NarrativeResult:
    narrative: Narrative
    removed: list[Unsupported]
    attempts: int
    numbers_checked: int


def write_narrative(facts: dict[str, Any], client: Client, model: str | None = None) -> NarrativeResult:
    model = model or default_model()
    allowed = allowed_numbers(facts)
    messages = [{"role": "user", "content": user_prompt(facts)}]
    narrative = ask(client, model, SYSTEM_PROMPT, messages, Narrative)
    problems, attempts = check(narrative, allowed), 1
    if problems:
        messages += [{"role": "assistant", "content": narrative.model_dump_json()},
                     {"role": "user", "content": retry_prompt(problems)}]
        narrative = ask(client, model, SYSTEM_PROMPT, messages, Narrative)
        problems, attempts = check(narrative, allowed), 2
    if problems:
        narrative = remove(narrative, problems)
    counted = sum(len(tokens(p)) for section in SECTIONS for p in getattr(narrative, section))
    return NarrativeResult(narrative, problems, attempts, counted)


def check(narrative: Narrative, allowed: AllowedNumbers) -> list[Unsupported]:
    return [problem for section in SECTIONS for problem in check_paragraphs(getattr(narrative, section), allowed)]


def remove(narrative: Narrative, problems: list[Unsupported]) -> Narrative:
    bad = {p.sentence for p in problems}
    cleaned = {}
    for section in SECTIONS:
        paragraphs = [" ".join(s for s in sentences(p) if s not in bad) for p in getattr(narrative, section)]
        cleaned[section] = [p for p in paragraphs if p.strip()]
    return Narrative(**cleaned)


SYSTEM_PROMPT = """\
You write the narrative of a portfolio review for the investor. You receive a JSON of facts
computed by a deterministic engine against the investor's own Investment Policy Statement (IPS).

Rules:
- Use only the facts given.
- Copy every number exactly as it appears in the facts, with the same rounding and format
  ("37.3%", "116,500 EUR"). Never calculate, round, add, subtract or compare numbers to produce
  a new one. Describe differences in words instead ("well above its band").
- Give no advice beyond what the IPS rules and findings in the facts already say. No market
  views, forecasts or explanations of why markets moved: stress-test names are labels only.
- Plain, calm, factual language. Short sentences. No exclamation marks, no hype.
- summary: 3 to 5 bullet points, breaches before warnings, the most important first.
- allocation: how the portfolio sits against its SAA targets and bands.
- rules: what each breach and warning means and what the finding itself says to do.
- risk: volatility, drawdown, VaR and CVaR, where the risk comes from, currency exposure,
  stress tests, and anything left out of the risk numbers.
- new_money: the plan, only if facts.new_money is not null; otherwise an empty list.
- The tool adds tables with every row, so highlight what matters rather than listing everything.
"""


def user_prompt(facts: dict[str, Any]) -> str:
    return f"Facts:\n```json\n{json.dumps(facts, indent=2, ensure_ascii=False)}\n```"


def retry_prompt(problems: list[Unsupported]) -> str:
    listed = "\n".join(f"- \"{p.sentence}\" (not in the facts: {', '.join(p.numbers)})" for p in problems)
    return ("These sentences contain numbers that are not in the facts:\n"
            f"{listed}\n\nReturn the full narrative again. Rewrite those sentences using only numbers copied "
            "from the facts, or describe the point in words without a number.")
