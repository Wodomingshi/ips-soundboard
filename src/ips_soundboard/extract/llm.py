"""Ask Claude for an IPSDraft (structured output), then convert and check it.

The client is anything with `messages.parse(...)` like `anthropic.Anthropic()`, so tests use a
fake. One retry: if the draft does not validate, the errors are sent back once.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from ips_soundboard.extract.convert import ExtractionResult, convert
from ips_soundboard.extract.draft import IPSDraft
from ips_soundboard.extract.settings import SETTINGS, choices

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 8_000


class ExtractionError(RuntimeError):
    """The model did not return a usable draft (refusal, truncation, empty output)."""


class Client(Protocol):
    messages: Any


def default_model() -> str:
    return os.environ.get("IPS_SOUNDBOARD_MODEL", DEFAULT_MODEL)


def extract_ips(document: str, client: Client, model: str | None = None) -> ExtractionResult:
    model = model or default_model()
    messages: list[dict[str, str]] = [{"role": "user", "content": user_prompt(document)}]
    draft = _ask(client, model, messages)
    result = convert(draft, document)
    if result.errors:
        messages += [{"role": "assistant", "content": draft.model_dump_json()},
                     {"role": "user", "content": retry_prompt(result.errors)}]
        result = convert(_ask(client, model, messages), document)
        result.attempts = 2
    return result


def _ask(client: Client, model: str, messages: list[dict[str, str]]) -> IPSDraft:
    response = client.messages.parse(model=model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                                     messages=messages, output_format=IPSDraft)
    if response.stop_reason == "refusal":
        raise ExtractionError("the model declined to process this document")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("the reply was cut off (document too long for the output limit)")
    if response.parsed_output is None:
        raise ExtractionError("the model returned no structured output")
    return response.parsed_output


# --- prompts -------------------------------------------------------------------

SYSTEM_PROMPT = """\
You turn an Investment Policy Statement (IPS) into structured rules for a portfolio-review tool.

Rules:
- Extract only what the document states. Never infer, estimate or compute a value. If a setting
  is not stated, leave it out: the tool lists it as not found.
- value: copy it exactly as written in the quote, including the % sign or unit words
  ("25%", "3 percentage points", "EUR 1,000" -> "1,000"). Do not convert units: never turn
  "25%" into 0.25 or "one month" into a number of days.
- quote: a short verbatim excerpt (one sentence or one table row) copied character for
  character from the document, containing the value. Use "..." only to skip words.
- Strategic allocation: one sleeve per row, including sleeves with a 0% target. A range written
  as "20-30%" gives min "20%" and max "30%".
- Lists (accounts, excluded companies or industries): one rule per item.
- Yes/no settings: value "true" or "false". "No leverage" means leverage_allowed is "false".
- Choices: use one of the allowed values listed below.
- Record unclear or contradictory points in ambiguities; do not resolve them yourself.
"""


def settings_guide() -> str:
    lines = []
    for path, (kind, description) in SETTINGS.items():
        allowed = f" Allowed: {', '.join(choices(path))}." if kind == "choice" else ""
        lines.append(f"- {path} ({kind}): {description}.{allowed}")
    return "\n".join(lines)


def user_prompt(document: str) -> str:
    return (f"Settings you may extract:\n{settings_guide()}\n\n"
            f"Extract the IPS below.\n\n<document>\n{document}\n</document>")


def retry_prompt(errors: list[str]) -> str:
    listed = "\n".join(f"- {e}" for e in errors)
    return (f"The extraction did not pass validation:\n{listed}\n\n"
            "Return the full extraction again with these problems fixed. Only use what the document "
            "states; if the document itself is inconsistent, keep its values and add an ambiguity.")
