"""Shared plumbing for the modules that call Claude (extract, classify, report).

The client is anything with `messages.parse(...)`, such as `anthropic.Anthropic()`, so tests
pass a fake. Replies are structured outputs validated against a pydantic model.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

DEFAULT_MODEL = "claude-sonnet-5-5"
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """The model did not return a usable reply (refusal, truncation, empty output)."""


class Client(Protocol):
    messages: Any


def default_model() -> str:
    return os.environ.get("IPS_SOUNDBOARD_MODEL", DEFAULT_MODEL)


def ask(client: Client, model: str, system: str, messages: list[dict[str, str]], output: type[T],
        max_tokens: int = 8_000) -> T:
    """One structured-output call. Raises LLMError instead of returning something unusable."""
    response = client.messages.parse(model=model, max_tokens=max_tokens, system=system,
                                     messages=messages, output_format=output)
    if response.stop_reason == "refusal":
        raise LLMError("the model declined to process this input")
    if response.stop_reason == "max_tokens":
        raise LLMError("the reply was cut off at the output limit")
    if response.parsed_output is None:
        raise LLMError("the model returned no structured output")
    return response.parsed_output


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
