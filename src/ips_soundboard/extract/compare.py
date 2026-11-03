"""Compare an extracted IPS with a hand-written reference (the built-in benchmark).

SAA sleeves are matched by position, because the document's sleeve names ("European equities")
need not match the ids chosen in a hand-written config ("eu_equity").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ips_soundboard.extract.settings import extractable

IGNORED = ("version",)  # bookkeeping, not policy


@dataclass(frozen=True)
class Difference:
    path: str
    extracted: Any
    reference: Any


@dataclass(frozen=True)
class Comparison:
    matching: int
    differences: list[Difference]
    renamed_sleeves: list[tuple[str, str]]  # (extracted id, reference id)

    @property
    def identical(self) -> bool:
        return not self.differences


def compare(extracted: dict[str, Any], reference: dict[str, Any]) -> Comparison:
    ext_saa, ref_saa = extracted.get("saa", {}), reference.get("saa", {})
    renamed = [(e, r) for e, r in zip(ext_saa, ref_saa) if e != r]
    flat_ext = _flatten({k: v for k, v in extracted.items() if k != "saa"})
    flat_ref = _flatten({k: v for k, v in reference.items() if k != "saa"})
    for i, (name, band) in enumerate(ext_saa.items()):
        flat_ext |= {f"saa[{i + 1}].{k}": v for k, v in band.items()}
    for i, (name, band) in enumerate(ref_saa.items()):
        flat_ref |= {f"saa[{i + 1}].{k}": v for k, v in band.items()}

    differences, matching = [], 0
    for path in sorted(set(flat_ext) | set(flat_ref)):
        if path in IGNORED or not extractable(path):
            continue  # bookkeeping, or a tool setting that is never extracted
        a, b = flat_ext.get(path), flat_ref.get(path)
        if _same(a, b):
            matching += 1
        else:
            differences.append(Difference(path, a, b))
    return Comparison(matching, differences, renamed)


def _flatten(node: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat |= _flatten(value, f"{path}.")
        else:
            flat[path] = value
    return flat


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-9
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    return a == b
