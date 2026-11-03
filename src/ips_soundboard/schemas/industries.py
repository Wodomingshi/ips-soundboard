"""The shared industry vocabulary (see industries.yaml)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

VOCABULARY_FILE = Path(__file__).with_name("industries.yaml")


@cache
def industries() -> dict[str, str]:
    """{industry id: description}, in file order."""
    with VOCABULARY_FILE.open(encoding="utf-8") as handle:
        return dict(yaml.safe_load(handle))
