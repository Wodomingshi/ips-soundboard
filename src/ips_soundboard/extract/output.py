"""Draft YAML and the plain-text review report."""

from __future__ import annotations

from datetime import date

import yaml

from ips_soundboard.extract.compare import Comparison
from ips_soundboard.extract.convert import ExtractionResult


def draft_yaml(result: ExtractionResult, source: str, model: str, today: date | None = None) -> str:
    header = (f"# DRAFT extracted by an LLM ({model}) from {source} on {today or date.today()}.\n"
              f"# Review it against the report before renaming it to ips.yaml.\n"
              f"# Only settings stated in the document are listed; the schema defaults apply to the rest.\n")
    if result.ips is None:
        header += "# NOT VALID YET: see the errors in the report.\n"
    return header + yaml.safe_dump(result.data, sort_keys=False, allow_unicode=True, default_flow_style=None)


def report_lines(result: ExtractionResult, comparison: Comparison | None = None) -> list[str]:
    lines = []
    tries = "1 attempt" if result.attempts == 1 else f"{result.attempts} attempts"
    if result.ips is not None:
        lines.append(f"Valid IPS ({tries}).")
    else:
        lines.append(f"NOT a valid IPS after {tries}: {len(result.errors)} error(s)")
        lines += [f"  x {e}" for e in result.errors]

    verified = len(result.evidence) - len(result.flagged)
    lines.append(f"\nEvidence: {verified} of {len(result.evidence)} values verified against the document")
    for e in result.flagged:
        lines.append(f"  ! {e.setting} = {e.value!r}: {e.status}")
        lines.append(f"      quote: \"{e.quote}\"")

    if result.not_found:
        lines.append("\nNot stated in the document (schema defaults apply):")
        lines += [f"  - {path}" for path in result.not_found]
    if result.ambiguities:
        lines.append("\nAmbiguities noted by the model (check the document):")
        lines += [f"  ? {a}" for a in result.ambiguities]
    if "saa" in result.data:
        ids = ", ".join(result.data["saa"])
        lines.append(f"\nSleeve ids: {ids}")
        lines.append("  These must match the 'sleeve' column of your holdings file; rename either if needed.")

    if comparison is not None:
        lines.append(f"\nCompared with the reference: {comparison.matching} settings match, "
                     f"{len(comparison.differences)} differ")
        for d in comparison.differences:
            lines.append(f"  ~ {d.path}: extracted {d.extracted!r}, reference {d.reference!r}")
        for extracted, reference in comparison.renamed_sleeves:
            lines.append(f"  (sleeve named '{extracted}' here, '{reference}' in the reference)")
    return lines
