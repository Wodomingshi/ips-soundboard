# CLAUDE.md — working rules for this repo

## What this project is
IPS Soundboard: reviews a portfolio against its Investment Policy Statement. Deterministic Python engine for all numbers; LLM (Claude API) only for extraction (IPS text → validated config, holdings → classification) and narrative written from computed results. See `docs/architecture.md`.

## How to work
- **One module at a time.** Don't build ahead of the current module.
- For every module: **state assumptions first, propose the design briefly, then write code with tests.**
- Small, typed functions. Python ≥ 3.11. Config in YAML. **pydantic schemas for every LLM output.**
- **No hardcoded tickers, weights or investor data** in code.
- Explain finance choices (e.g. covariance estimator, VaR method) in one or two lines in the code or PR description.
- Don't over-engineer: MVP first.

## Privacy (treat the repo as public)
- `private/` is git-ignored and holds the owner's real IPS, holdings and statements. **Never commit, copy, quote or print its contents into tracked files, test fixtures, docs or commit messages.**
- Tests and examples use only the synthetic investors in `examples/`.
- API keys live in `.env` (git-ignored). Never hardcode them.

## Finance conventions
- Weights are fractions (0.25, not 25).
- FX rates are quoted as base-currency units per 1 unit of the foreign currency.
- Returns are simple returns unless a function says otherwise.
- The IPS is the source of truth: if code or data contradicts it, flag it rather than silently "fixing" it.

## Commands
- Install: `pip install -e ".[dev]"`
- Tests: `pytest`
- Lint (when added): `ruff check .`
