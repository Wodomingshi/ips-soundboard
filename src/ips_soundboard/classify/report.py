"""Plain-text review report for classification suggestions."""

from __future__ import annotations

from ips_soundboard.classify.merge import Change, ClassificationResult

SECTIONS = (
    ("filled", "Filled in (blank before; check them)"),
    ("differs", "Different from your file (NOT changed: decide yourself)"),
    ("low confidence", "Low confidence (NOT filled: check by hand)"),
    ("needs input", "Needs your input"),
)


def report_lines(result: ClassificationResult) -> list[str]:
    lines = [f"Classified {result.classified} holding(s); skipped {result.skipped} "
             f"(cash, pensions, reported-only accounts)."]
    for action, title in SECTIONS:
        changes = [c for c in result.changes if c.action == action]
        if changes:
            lines.append(f"\n{title}: {len(changes)}")
            lines += [_line(c) for c in changes]
    lines.append(f"\nAgrees with your file: {result.agreed} value(s)")
    if result.missing_rows:
        lines.append(f"Not answered by the model (rows): {', '.join(map(str, result.missing_rows))}")

    lines.append("\nExclusion check with these classifications:")
    if result.exclusions:
        lines += [f"  {'x' if f.severity == 'breach' else '!'} {f.message}" for f in result.exclusions]
    else:
        lines.append("  no exclusion breach")
    return lines


def _line(c: Change) -> str:
    was = f"{c.current} -> " if c.current else ""
    return f"  row {c.row} {c.name}: {c.field} {was}{c.suggested} ({c.confidence}; {c.reason})"
