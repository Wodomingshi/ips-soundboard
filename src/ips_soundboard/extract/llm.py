"""Ask Claude for an IPSDraft (structured output), then convert and check it.

One retry: if the draft does not validate, the errors are sent back once.
"""

from __future__ import annotations

from collections.abc import Sequence

from ips_soundboard.extract.convert import ExtractionResult, convert
from ips_soundboard.extract.draft import IPSDraft
from ips_soundboard.extract.settings import SETTINGS, choices
from ips_soundboard.schemas.industries import industries
from ips_soundboard.llm import Client, LLMError, ask, default_model

ExtractionError = LLMError


def extract_ips(document: str, client: Client, model: str | None = None, known_sleeves: Sequence[str] = (),
                known_accounts: Sequence[str] = ()) -> ExtractionResult:
    """known_*: sleeve and account ids from the holdings file, offered to the model for reuse."""
    model = model or default_model()
    messages: list[dict[str, str]] = [{"role": "user", "content": user_prompt(document, known_sleeves,
                                                                               known_accounts)}]
    draft = _ask(client, model, messages)
    result = convert(draft, document, known_sleeves, known_accounts)
    if result.errors:
        first_errors = result.errors
        messages += [{"role": "assistant", "content": draft.model_dump_json()},
                     {"role": "user", "content": retry_prompt(result.errors)}]
        result = convert(_ask(client, model, messages), document, known_sleeves, known_accounts)
        result.attempts, result.first_attempt_errors = 2, first_errors
    return result


def _ask(client: Client, model: str, messages: list[dict[str, str]]) -> IPSDraft:
    return ask(client, model, SYSTEM_PROMPT, messages, IPSDraft)


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
- Lists (accounts, excluded companies or industries): one rule per item. Account values: the
  account's name without the word "account" (e.g. "brokerage").
- Excluded industries: map each to the closest industry id in the allowed list; if none fits,
  leave it out and add an ambiguity.
- Yes/no settings: value "true" or "false". "No leverage" means leverage_allowed is "false".
- Choices: use one of the allowed values listed below.
- A range given for a setting that is itself a limit: use the bound the setting describes (the
  highest for a maximum, the lowest for a minimum, the deepest loss for a drawdown limit), with
  the range as the quote, and record it as an ambiguity.
- Record unclear or contradictory points in ambiguities; do not resolve them yourself.
"""


def settings_guide() -> str:
    lines = []
    for path, (kind, description) in SETTINGS.items():
        allowed = f" Allowed: {', '.join(choices(path))}." if kind == "choice" else \
            f" Allowed: {', '.join(industries())}." if kind == "industry" else ""
        lines.append(f"- {path} ({kind}): {description}.{allowed}")
    return "\n".join(lines)


def user_prompt(document: str, known_sleeves: Sequence[str] = (), known_accounts: Sequence[str] = ()) -> str:
    known = ""
    if known_sleeves or known_accounts:
        known = ("\n\nIds already used in the investor's holdings file. Where a sleeve or account in the "
                 "document clearly corresponds to one, use that id (sleeve: the id field; account: the value). "
                 "Otherwise leave the sleeve id empty and write the account as named in the document.\n"
                 f"- sleeves: {', '.join(known_sleeves) or 'none'}\n- accounts: {', '.join(known_accounts) or 'none'}")
    return (f"Settings you may extract:\n{settings_guide()}{known}\n\n"
            f"Extract the IPS below.\n\n<document>\n{document}\n</document>")


def retry_prompt(errors: list[str]) -> str:
    listed = "\n".join(f"- {e}" for e in errors)
    return (f"The extraction did not pass validation:\n{listed}\n\n"
            "Return the full extraction again with these problems fixed. Only use what the document "
            "states; if the document itself is inconsistent, keep its values and add an ambiguity.")
