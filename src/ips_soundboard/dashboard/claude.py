"""The Claude client the dashboard uses to read IPS documents. Tests swap in a fake.

The key comes from the app's secrets or the environment (ANTHROPIC_API_KEY); without one the
dashboard offers no Claude features and asks for an ips.yaml instead.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from ips_soundboard.llm import Client

# Haiku: tested on the sample and a real IPS with every value verified, at a fraction of Sonnet's cost.
APP_MODEL = "claude-haiku-5-5"
KEY = "ANTHROPIC_API_KEY"

_override: Client | None = None


def set_client(client: Client | None) -> None:
    """Use `client` instead of the Anthropic API (None restores the default). For tests."""
    global _override
    _override = client


def api_key(secrets: Mapping[str, Any]) -> str | None:
    return str(secrets[KEY]) if KEY in secrets else os.environ.get(KEY)


def get_client(secrets: Mapping[str, Any]) -> Client | None:
    """A client, or None when no key is set."""
    if _override is not None:
        return _override
    key = api_key(secrets)
    if not key:
        return None
    import anthropic

    return anthropic.Anthropic(api_key=key)


def app_model(secrets: Mapping[str, Any]) -> str:
    return str(secrets.get("IPS_SOUNDBOARD_MODEL") or os.environ.get("IPS_SOUNDBOARD_MODEL") or APP_MODEL)
