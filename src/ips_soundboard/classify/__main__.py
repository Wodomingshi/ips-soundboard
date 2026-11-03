"""Suggest sleeve, type, role, issuer and industry for each holding with Claude.

    python -m ips_soundboard.classify private/holdings.csv private/ips.yaml

Writes holdings.suggested.csv next to the holdings file (git-ignored) unless -o is given:
blanks filled where the model is confident, your existing values untouched. Sends only names,
tickers and currencies to the API. Needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ips_soundboard.classify.merge import extra_sleeves, merge, read_holdings_file, rows_to_classify
from ips_soundboard.classify.report import report_lines
from ips_soundboard.classify.suggest import suggest
from ips_soundboard.llm import LLMError, anthropic_client, default_model
from ips_soundboard.schemas.ips import load_ips


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("holdings")
    parser.add_argument("ips")
    parser.add_argument("-o", "--output", help="default: holdings.suggested.csv next to the holdings file")
    parser.add_argument("--model", default=None, help=f"Claude model (default: {default_model()})")
    args = parser.parse_args()

    client = anthropic_client()
    ips = load_ips(args.ips)
    file = read_holdings_file(args.holdings)
    rows = rows_to_classify(file, ips)
    print(f"Classifying {len(rows)} holding(s) with {args.model or default_model()}...")
    try:
        suggestions = suggest(rows, ips, client, extra_sleeves(file, ips), args.model)
    except LLMError as error:
        sys.exit(f"Classification failed: {error}")

    result = merge(file, suggestions, ips)
    output = Path(args.output) if args.output else Path(args.holdings).with_name("holdings.suggested.csv")
    output.write_text(result.csv_text, encoding="utf-8")
    print("\n".join(report_lines(result)))
    print(f"\nSuggestions written to {output}. Review them, then replace your holdings file.")


if __name__ == "__main__":
    main()
