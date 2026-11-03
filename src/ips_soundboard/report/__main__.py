"""Write a portfolio review: computed facts, a narrative by Claude, and the number check.

    python -m ips_soundboard.report private/holdings.csv private/ips.yaml [--amount 9000]

Writes reports/out/review-<date>.md and the facts it used (git-ignored). Needs internet for
prices and ANTHROPIC_API_KEY for the narrative. Only computed results are sent to the API.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from ips_soundboard.dashboard.pipeline import new_money_plan, run_analysis
from ips_soundboard.holdings import load_holdings
from ips_soundboard.llm import LLMError, anthropic_client, default_model
from ips_soundboard.marketdata.yahoo import YahooProvider, quiet_yfinance_warnings
from ips_soundboard.report.facts import build_facts
from ips_soundboard.report.narrative import write_narrative
from ips_soundboard.report.render import render
from ips_soundboard.schemas.ips import load_ips

OUT_DIR = Path("reports/out")


def main() -> None:
    quiet_yfinance_warnings()
    logging.basicConfig(level=logging.WARNING, format="  ! %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("holdings")
    parser.add_argument("ips")
    parser.add_argument("--amount", type=float, help="new savings to plan for, in the base currency")
    parser.add_argument("-o", "--output", help=f"default: {OUT_DIR}/review-<date>.md")
    parser.add_argument("--model", default=None, help=f"Claude model (default: {default_model()})")
    args = parser.parse_args()

    client = anthropic_client()
    model = args.model or default_model()
    print("Analysing the portfolio (prices, FX, 5 years of history)...")
    analysis = run_analysis(load_holdings(args.holdings), load_ips(args.ips), YahooProvider(), as_of=date.today())
    plan = new_money_plan(analysis, args.amount) if args.amount else None
    facts = build_facts(analysis, plan)

    output = Path(args.output) if args.output else OUT_DIR / f"review-{analysis.snapshot.as_of}.md"
    facts_file = output.with_suffix(".facts.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    facts_file.write_text(json.dumps(facts, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Writing the narrative with {model}...")
    try:
        result = write_narrative(facts, client, model)
    except LLMError as error:
        sys.exit(f"Report failed: {error}")
    output.write_text(render(facts, result, model, facts_file.name), encoding="utf-8")

    if result.removed:
        print(f"  ! {len(result.removed)} sentence(s) removed by the number check (listed in the report)")
    else:
        print(f"  All {result.numbers_checked} numbers in the narrative were found in the computed facts")
    print(f"Report written to {output}")


if __name__ == "__main__":
    main()
