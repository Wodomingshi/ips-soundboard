# IPS Soundboard

Review a portfolio against its own Investment Policy Statement (IPS): allocation drift, rule breaches, concentration and risk, with a written analysis.

> **Status:** early development. IPS and holdings schemas, the valuation / allocation / limits engine and market data (Yahoo), the risk analyser, the quarterly new-money plan, a Streamlit dashboard and LLM extraction of an IPS document into a validated config are in place.

## What it does (target MVP)

```
IPS (Markdown / PDF) ──► [LLM extraction] ──► ips.yaml (validated)
                                                   │
holdings.csv ──► [LLM-assisted classification] ──► │
                                                   ▼
                              deterministic engine (Python, tested)
                       prices · FX · weights · drift vs SAA · limits ·
                       risk (volatility, drawdown, VaR/CVaR, risk contribution) ·
                       TAA trend rules · new-money plan (where to invest new savings)
                                                   │
                                                   ▼
                       [LLM narrative, from computed facts only] ──► report
```

## Design principles

1. **The LLM never does the maths.** Every number in a report comes from the deterministic engine. The LLM is used only to (a) extract structured rules from a free-text IPS, (b) suggest holding classifications for the user to confirm, and (c) write commentary from computed results.
2. **Everything an LLM produces is validated** against a pydantic schema before use.
3. **The IPS is the source of truth.** The tool reports against the investor's own rules; it does not supply its own.
4. **No hardcoded tickers or weights.** All investor-specific inputs live in config files.

## Repository layout

```
src/ips_soundboard/
  schemas/ips.py        # IPS schema: SAA, limits, TAA rules, liquidity, rebalancing
examples/
  sample_investor_eur/  # synthetic investor used in tests and demos
private/                # your own IPS and holdings (git-ignored, never committed)
tests/
docs/architecture.md
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
streamlit run app/dashboard.py     # dashboard; pick "Demo: sample investor" in the sidebar
```

Validate an IPS config:

```python
from ips_soundboard.schemas.ips import load_ips
ips = load_ips("examples/sample_investor_eur/ips.yaml")
print(ips.saa)
```

Fetch live prices and FX for a holdings file (needs internet; cached per day in `data/cache/`):

```bash
python -m ips_soundboard.marketdata examples/sample_investor_eur/holdings.csv --base EUR
```

Risk report (volatility, drawdown, historical VaR/CVaR, risk contribution, FX exposure, stress tests):

```bash
python -m ips_soundboard.risk examples/sample_investor_eur/holdings.csv examples/sample_investor_eur/ips.yaml
```

Where to invest new savings (cash reserve first, then the SAA sleeves furthest below target; buys only):

```bash
python -m ips_soundboard.contribute examples/sample_investor_eur/holdings.csv examples/sample_investor_eur/ips.yaml --amount 9000
```

### Extract an IPS document with Claude

Turns a free-text IPS (Markdown, text or PDF) into a **draft** `ips.yaml` plus a review report. Install with `pip install -e ".[llm]"` and set `ANTHROPIC_API_KEY` (a Codespaces secret, or a git-ignored `.env` file).

```bash
python -m ips_soundboard.extract examples/sample_investor_eur/IPS.md --compare examples/sample_investor_eur/ips.yaml
```

The model only reads; code checks everything it returns:
- values are copied as written ("25%") and converted by code, never by the model;
- every value carries a verbatim quote, and the quote must appear in the document;
- every number must appear in its own quote;
- the result must pass the IPS schema, with one automatic retry that sends the errors back;
- settings the document does not state are listed, never guessed.

`--compare` diffs the draft against a hand-written config: the sample investor doubles as a benchmark. The draft (`ips.draft.yaml`, git-ignored) is for you to review before renaming it to `ips.yaml`. Running it on your own IPS sends the document to the Anthropic API.

Tickers use the `EXCHANGE:SYMBOL` format (e.g. `SWX:NESN`, `NYSEARCA:VPU`). Exchange codes are mapped to provider symbols in `src/ips_soundboard/marketdata/exchanges.yaml`.

## Disclaimer

This is an educational and analytical tool. It compares a portfolio with rules the user has written themselves. It is **not investment, tax or legal advice**, and nothing it produces is a recommendation to buy or sell any security. The new-money plan only applies the user's own IPS rules (targets, minimum trade, sleeve cap) as arithmetic; it does not judge any instrument. All sample investors in this repository are fictional.
