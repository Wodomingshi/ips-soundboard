# Architecture

## Layers

| Layer | Responsibility | LLM? |
|---|---|---|
| `schemas` | Typed contracts for the IPS, holdings and analysis results | No |
| `extract` | Free-text IPS (Markdown/PDF) → `IPS` object | Yes, with output validated against `schemas.ips.IPS` |
| `classify` | Raw holdings rows → ticker, sleeve, instrument type | Yes, as suggestions confirmed by the user |
| `engine` | Prices, FX, valuation, weights, drift, limit checks, TAA trend rules | No |
| `risk` | Volatility, drawdown, historical VaR/CVaR, risk contribution by sleeve | No |
| `report` | Narrative written from engine/risk output only | Yes; may only cite numbers it is given |

## Rule: no number originates in an LLM
Any figure in a report must be traceable to an engine or risk function. The narrative step receives a JSON of computed facts and is checked afterwards: every number in the text must appear in that JSON.

## Module order
1. **IPS schema** ✅
2. Holdings schema and loader (CSV)
3. Engine: valuation in base currency, weights, drift vs SAA, limit checks
4. Market data: prices and FX (swappable provider)
5. Risk analyser
6. IPS extraction (LLM)
7. Holdings classification (LLM)
8. Report (LLM narrative + tables)
9. Quarterly allocation of new savings ("where to invest this quarter")
