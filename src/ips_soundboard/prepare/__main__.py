"""Turn a minimal holdings list into a complete holdings file.

    python -m ips_soundboard.prepare my_list.csv --account brokerage

Each row needs a ticker and a quantity (or a name and a manual_value). Names and currencies are
looked up on Yahoo; your own values are never changed. Writes my_list.prepared.csv next to the
input (git-ignored) unless -o is given. With --classify ips.yaml, Claude then suggests sleeves,
roles and types for the prepared file (needs ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ips_soundboard.prepare.core import (
    PrepareResult,
    check_prepared,
    prepare_rows,
    read_file,
)


def report_lines(result: PrepareResult, notes: list[str]) -> list[str]:
    lines = [f"{len(result.rows)} row(s) read."]
    if result.filled:
        lines += ["", "Filled in:"] + [f"  row {n.row} {n.holding}: {n.message}" for n in result.filled]
    if result.suggestions:
        lines += ["", "Official names (yours kept; --official-names to use these):"]
        lines += [f"  row {n.row} {n.holding}: {n.message}" for n in result.suggestions]
    if result.needs_input:
        lines += ["", "Needs your input:"] + [f"  row {n.row} {n.holding}: {n.message}" for n in result.needs_input]
    if notes:
        lines += ["", "Notes:"] + [f"  {note}" for note in notes]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("holdings", help="CSV with at least ticker + quantity per row")
    parser.add_argument("-o", "--output", help="default: <input>.prepared.csv next to the input")
    parser.add_argument("--account", default="main", help="account for rows without one (default: main)")
    parser.add_argument("--currency", help="currency for rows without one that the price feed does not know")
    parser.add_argument("--official-names", action="store_true", help="replace your names with official names")
    parser.add_argument("--classify", metavar="IPS_YAML", help="then suggest sleeves, roles and types with Claude")
    parser.add_argument("--model", default=None, help="Claude model for --classify")
    args = parser.parse_args()

    from ips_soundboard.marketdata.yahoo import YahooProvider

    rows, notes = read_file(args.holdings)
    if not rows:
        sys.exit("No rows found.")
    print(f"Looking up {len({r['ticker'] for r in rows.values() if r.get('ticker')})} ticker(s) on Yahoo...")
    result = prepare_rows(rows, YahooProvider(), args.account, args.currency, args.official_names)
    output = Path(args.output) if args.output else _default_output(Path(args.holdings))
    output.write_text(result.csv_text, encoding="utf-8")
    print("\n".join(report_lines(result, notes + result.notes)))

    problems = check_prepared(result)
    if problems:
        print(f"\nWritten to {output}, but it is not ready yet: fix the rows above in that file, "
              f"then run this again on it.")
        sys.exit(1)
    print(f"\nWritten to {output}. It loads as a valid holdings file.")
    if args.classify:
        _classify(output, args.classify, args.model)


def _default_output(source: Path) -> Path:
    """my_list.csv -> my_list.prepared.csv; a prepared file run again is updated in place."""
    return source if source.name.endswith(".prepared.csv") else source.with_suffix(".prepared.csv")


def _classify(path: Path, ips_path: str, model: str | None) -> None:
    from ips_soundboard.classify.merge import (
        extra_sleeves,
        merge,
        read_holdings_file,
        rows_to_classify,
    )
    from ips_soundboard.classify.report import report_lines as classify_lines
    from ips_soundboard.classify.suggest import suggest
    from ips_soundboard.llm import LLMError, anthropic_client, default_model
    from ips_soundboard.schemas.ips import load_ips

    ips = load_ips(ips_path)
    file = read_holdings_file(path)
    rows = rows_to_classify(file, ips)
    print(f"\nClassifying {len(rows)} holding(s) with {model or default_model()}...")
    try:
        suggestions = suggest(rows, ips, anthropic_client(), extra_sleeves(file, ips), model)
    except LLMError as error:
        sys.exit(f"Classification failed: {error}")
    result = merge(file, suggestions, ips)
    path.write_text(result.csv_text, encoding="utf-8")
    print("\n".join(classify_lines(result)))
    print(f"\nClassifications added to {path}. Review them, then use it as your holdings file.")


if __name__ == "__main__":
    main()
