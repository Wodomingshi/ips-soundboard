"""Shared plumbing for the modules that call Claude (extract, classify, report).

The client is anything with `messages.create(...)`, such as `anthropic.Anthropic()`, so tests
pass a fake. Replies are structured outputs validated against a pydantic model.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "claude-sonnet-5-5"
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """The model did not return a usable reply (refusal, truncation, empty output)."""


class Client(Protocol):
    messages: Any


def default_model() -> str:
    return os.environ.get("IPS_SOUNDBOARD_MODEL", DEFAULT_MODEL)


def ask(client: Client, model: str, system: str, messages: list[dict[str, str]], output: type[T],
        max_tokens: int = 16_000) -> T:
    """One structured-output call, validated here. Raises LLMError with the reason instead of crashing.

    Uses messages.create (not the SDK's parse helper) so the stop reason is known before the JSON
    is read: a refusal or a cut-off reply is reported as such. A reply that ends normally but is
    not valid JSON is asked for once more.
    """
    request = dict(model=model, max_tokens=max_tokens, system=system, messages=messages,
                   output_config={"format": {"type": "json_schema", "schema": _schema(output)}})
    for attempt in (1, 2):
        response = client.messages.create(**request)
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        if response.stop_reason == "refusal":
            raise LLMError("the model declined to process this input")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"the reply was cut off at the {max_tokens}-token output limit "
                           f"({len(text):,} characters received)")
        try:
            return output.model_validate_json(text)
        except ValidationError as error:
            if attempt == 2:
                problem = error.errors()[0]["msg"]
                raise LLMError(f"the reply was not valid twice (stop reason '{response.stop_reason}', "
                               f"{len(text):,} characters): {problem}") from error
    raise AssertionError("unreachable")


def _schema(output: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema in the form structured outputs accept (constraints moved into descriptions)."""
    try:
        from anthropic import transform_schema
    except ImportError:  # the SDK is optional; tests with a fake client do not need it
        return output.model_json_schema()
    return transform_schema(output)


def anthropic_client() -> Any:
    """A real client, after loading .env and checking the key. Exits with a clear message otherwise."""
    load_dotenv(Path(".env"))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Add it as a Codespaces secret or to a .env file (see README).")
    try:
        import anthropic
    except ImportError:
        sys.exit('The Anthropic SDK is not installed: pip install -e ".[llm]"')
    return anthropic.Anthropic()


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
