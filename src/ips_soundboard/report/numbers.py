"""The number check: every number in the narrative must come from the computed facts.

Percentages are checked against percentages, other numbers against any number in the facts
(amounts, counts, dates). So a sentence cannot slip in a percentage the engine never computed,
or a rounded or derived figure ("about 40%", "12 points more than...").
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_NUMBER = re.compile(r"\d{1,3}(?:[,'’]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_PERCENT_AFTER = re.compile(r"\s*(%|percent\b|percentage points?\b|pp\b)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# Counts written as words are checked too ("four holdings"). "one" is left out: it is mostly idiomatic.
_WORDS = {w: float(i) for i, w in enumerate(
    "two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty".split(), start=2)}
_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)


@dataclass(frozen=True)
class AllowedNumbers:
    percentages: frozenset[float]
    numbers: frozenset[float]
    dates: frozenset[str] = frozenset()  # matched whole, so "04" in a date vouches for nothing


@dataclass(frozen=True)
class Unsupported:
    sentence: str
    numbers: list[str]  # as written in the sentence


def allowed_numbers(facts: Any) -> AllowedNumbers:
    percentages, numbers, dates = set(), set(), set()
    for text in _strings(facts):
        dates.update(_DATE.findall(text))
        for value, is_percent in tokens(_DATE.sub(" ", text)):
            numbers.add(value)
            if is_percent:
                percentages.add(value)
    return AllowedNumbers(frozenset(percentages), frozenset(numbers), frozenset(dates))


def tokens(text: str) -> list[tuple[float, bool]]:
    """(absolute value, is a percentage) for every number in the text."""
    found = []
    for match in _NUMBER.finditer(text):
        value = float(re.sub(r"[,'’]", "", match.group()))
        found.append((value, bool(_PERCENT_AFTER.match(text, match.end()))))
    return found


def unsupported_numbers(text: str, allowed: AllowedNumbers) -> list[str]:
    bad = [d for d in _DATE.findall(text) if d not in allowed.dates]
    text = _DATE.sub(" ", text)
    for match in _WORD.finditer(text):
        if _WORDS[match.group().lower()] not in allowed.numbers:
            bad.append(match.group())
    for match in _NUMBER.finditer(text):
        value = float(re.sub(r"[,'’]", "", match.group()))
        pool = allowed.percentages if _PERCENT_AFTER.match(text, match.end()) else allowed.numbers
        if not any(abs(value - v) < 1e-9 for v in pool):
            bad.append(match.group())
    return bad


def sentences(paragraph: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(paragraph.strip()) if s]


def check_paragraphs(paragraphs: Iterable[str], allowed: AllowedNumbers) -> list[Unsupported]:
    problems = []
    for paragraph in paragraphs:
        for sentence in sentences(paragraph):
            bad = unsupported_numbers(sentence, allowed)
            if bad:
                problems.append(Unsupported(sentence, bad))
    return problems


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, bool) or node is None:
        return
    else:
        yield str(node)
