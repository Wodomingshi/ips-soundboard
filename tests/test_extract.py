import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from ips_soundboard.extract.compare import compare
from ips_soundboard.extract.convert import convert, normalise, parse_number, quote_in_document
from ips_soundboard.extract.draft import IPSDraft, RuleDraft, SleeveDraft
from ips_soundboard.extract.llm import ExtractionError, extract_ips
from ips_soundboard.extract.output import draft_yaml, report_lines
from ips_soundboard.extract.settings import SETTINGS, extractable, schema_paths
from ips_soundboard.schemas.ips import parse_ips

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "sample_investor_eur"
DOCUMENT = (SAMPLE_DIR / "IPS.md").read_text(encoding="utf-8")
REFERENCE = yaml.safe_load((SAMPLE_DIR / "ips.yaml").read_text(encoding="utf-8"))


def rule(setting: str, value: str, quote: str) -> RuleDraft:
    return RuleDraft(setting=setting, value=value, quote=quote)


def sleeve(name: str, target: str, low: str, high: str) -> SleeveDraft:
    return SleeveDraft(name=name, target=target, min=low, max=high, quote=f"| {name} | {target} | {low[:-1]}–{high} |")


NO_LEVERAGE = "No leverage, short selling, derivatives or leveraged/inverse ETFs."

# What a correct extraction of the sample IPS looks like: values as written, quotes copied verbatim.
GOLDEN = IPSDraft(
    sleeves=[sleeve("European equities", "25%", "20%", "30%"), sleeve("World equities ex-Europe", "35%", "30%", "40%"),
             sleeve("Emerging-market equities", "5%", "2%", "8%"), sleeve("Euro government bonds", "25%", "20%", "30%"),
             sleeve("Gold", "5%", "2%", "8%"), sleeve("Cash", "5%", "0%", "10%")],
    rules=[
        rule("base_currency", "EUR", "Monthly expenses are at most EUR 3,500."),
        rule("objectives.real_return_target", "3%", "Earn 3% a year above euro-area inflation"),
        rule("objectives.design_max_drawdown", "-30%", "Accept a peak-to-trough loss of up to 30% in a severe crisis"),
        rule("liquidity.monthly_expenses_max", "3,500", "Monthly expenses are at most EUR 3,500."),
        rule("liquidity.min_months_of_expenses", "6", "Keep at least 6 months of expenses in cash"),
        rule("scope.managed", "brokerage", "The SAA applies to her brokerage account."),
        rule("scope.reported_only", "company pension", "Her company pension is reported but not managed."),
        rule("limits.single_name_buy_limit", "5%", "No single stock above 5% of the portfolio when buying"),
        rule("limits.single_name_hard_limit", "8%", "anything above 8% is trimmed"),
        rule("limits.picks_max_total", "20%", "Single stocks in total no more than 20%."),
        rule("limits.leverage_allowed", "false", NO_LEVERAGE),
        rule("limits.short_selling_allowed", "false", NO_LEVERAGE),
        rule("limits.derivatives_allowed", "false", NO_LEVERAGE),
        rule("limits.leveraged_or_inverse_etfs_allowed", "false", NO_LEVERAGE),
        rule("exclusions.apply_to", "direct_holdings", "No direct holdings in tobacco companies."),
        rule("exclusions.sub_industries", "tobacco", "No direct holdings in tobacco companies."),
        rule("taa.sleeve_tilts.max_per_sleeve", "3 percentage points", "Sleeve tilts of up to ±3 percentage points"),
        rule("taa.sleeve_tilts.max_total_one_way", "6 points", "a total one-way deviation of at most 6 points"),
        rule("taa.trend.lookback_months", "10", "when the price is above its 10-month average"),
        rule("rebalancing.monitoring_frequency", "quarterly", "Review quarterly, rebalance annually"),
        rule("rebalancing.calendar_rebalance", "annually", "Review quarterly, rebalance annually"),
        rule("rebalancing.min_trade", "1,000", "don't trade amounts below EUR 1,000"),
        rule("rebalancing.new_money_max_sleeves", "two", "in the one or two sleeves furthest below target"),
    ],
    ambiguities=[],
)


def edited(draft: IPSDraft, **changes) -> IPSDraft:
    return draft.model_copy(update=changes)


class FakeClient:
    """Stands in for anthropic.Anthropic(): returns prepared drafts and records the requests."""

    def __init__(self, *replies, stop_reason: str = "end_turn") -> None:
        self.replies, self.calls, self.stop_reason = list(replies), [], stop_reason
        self.messages = self

    def parse(self, **request):
        self.calls.append(request)
        return SimpleNamespace(parsed_output=self.replies.pop(0), stop_reason=self.stop_reason)


# --- settings and schema --------------------------------------------------------

def test_settings_cover_every_extractable_ips_field() -> None:
    assert set(SETTINGS) == {p for p in schema_paths() if extractable(p)}


def test_draft_rejects_unknown_settings() -> None:
    with pytest.raises(ValidationError):
        RuleDraft(setting="limits.max_crypto", value="5%", quote="x")


# --- numbers and quotes ------------------------------------------------------------

@pytest.mark.parametrize(("written", "expected"), [
    ("25%", 0.25), ("-30%", -0.30), ("3 percentage points", 0.03), ("6 points", 0.06), ("1,000", 1000),
    ("12'000", 12000), ("5k", 5000), ("10", 10), ("two", 2), ("0.5%", 0.005),
])
def test_parse_number(written: str, expected: float) -> None:
    assert parse_number(written) == pytest.approx(expected)


def test_quotes_are_matched_despite_markdown_dashes_and_spacing() -> None:
    doc = normalise(DOCUMENT)
    assert quote_in_document("| European equities | 25% | 20-30% |", doc)
    assert quote_in_document("Sleeve tilts of up to ±3 percentage points", doc)
    assert quote_in_document("Earn 3% a year ... over rolling 10-year periods", doc)
    assert not quote_in_document("Earn 4% a year above euro-area inflation", doc)


# --- the golden extraction ----------------------------------------------------------

def test_golden_extraction_is_valid_and_fully_verified() -> None:
    result = convert(GOLDEN, DOCUMENT)
    assert result.errors == [] and result.ips is not None
    assert result.flagged == []
    assert result.ips.saa["european_equities"].target == pytest.approx(0.25)
    assert result.ips.scope.reported_only == ["company_pension"]
    assert result.ips.rebalancing.calendar_rebalance == "annual"


def test_golden_extraction_matches_the_hand_written_config() -> None:
    comparison = compare(convert(GOLDEN, DOCUMENT).data, REFERENCE)
    assert comparison.differences == []
    assert ("european_equities", "eu_equity") in comparison.renamed_sleeves


def test_settings_not_stated_are_listed_not_guessed() -> None:
    result = convert(GOLDEN, DOCUMENT)
    assert "taa.themes.max_total" in result.not_found
    assert "themes" not in result.data.get("taa", {})
    assert result.data["version"] == "draft"  # the sample states no version


# --- what gets flagged ------------------------------------------------------------

def test_invented_quote_is_flagged() -> None:
    rules = [rule("limits.picks_max_total", "20%", "Conviction picks are capped at 20%.")]
    result = convert(edited(GOLDEN, rules=rules), DOCUMENT)
    assert [(e.setting, e.status) for e in result.flagged] == [("limits.picks_max_total", "quote not found")]


def test_number_missing_from_its_quote_is_flagged() -> None:
    rules = [rule("limits.picks_max_total", "25%", "Single stocks in total no more than 20%.")]
    result = convert(edited(GOLDEN, rules=rules), DOCUMENT)
    assert result.flagged[0].status == "number not in quote"


def test_bad_choice_and_bool_are_errors() -> None:
    rules = [rule("rebalancing.monitoring_frequency", "weekly", "Review quarterly"),
             rule("limits.leverage_allowed", "sometimes", NO_LEVERAGE)]
    errors = convert(edited(GOLDEN, rules=rules), DOCUMENT).errors
    assert any("monitoring_frequency" in e and "weekly" in e for e in errors)
    assert any("leverage_allowed" in e for e in errors)


def test_conflicting_values_for_one_setting_are_an_error() -> None:
    rules = GOLDEN.rules + [rule("limits.picks_max_total", "25%", "Single stocks in total no more than 20%.")]
    assert any("stated twice" in e for e in convert(edited(GOLDEN, rules=rules), DOCUMENT).errors)


def test_saa_that_does_not_sum_to_100_is_invalid() -> None:
    result = convert(edited(GOLDEN, sleeves=GOLDEN.sleeves[:-1]), DOCUMENT)
    assert result.ips is None and any("sum to 1.0" in e for e in result.errors)


# --- the LLM call ------------------------------------------------------------------

def test_valid_first_reply_needs_one_call() -> None:
    client = FakeClient(GOLDEN)
    result = extract_ips(DOCUMENT, client, model="test-model")
    assert result.ips is not None and result.attempts == 1 and len(client.calls) == 1
    request = client.calls[0]
    assert request["output_format"] is IPSDraft and request["model"] == "test-model"
    assert "<document>" in request["messages"][0]["content"]


def test_invalid_reply_is_retried_once_with_the_errors() -> None:
    client = FakeClient(edited(GOLDEN, sleeves=GOLDEN.sleeves[:-1]), GOLDEN)
    result = extract_ips(DOCUMENT, client, model="test-model")
    assert result.ips is not None and result.attempts == 2
    retry = client.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert "sum to 1.0" in retry[-1]["content"]


def test_still_invalid_after_retry_returns_the_errors() -> None:
    bad = edited(GOLDEN, sleeves=GOLDEN.sleeves[:-1])
    result = extract_ips(DOCUMENT, FakeClient(bad, bad), model="test-model")
    assert result.ips is None and result.attempts == 2 and result.errors


def test_refusal_is_an_error() -> None:
    with pytest.raises(ExtractionError):
        extract_ips(DOCUMENT, FakeClient(GOLDEN, stop_reason="refusal"), model="test-model")


# --- output ----------------------------------------------------------------------

def test_draft_yaml_loads_as_the_same_ips() -> None:
    result = convert(GOLDEN, DOCUMENT)
    text = draft_yaml(result, "IPS.md", "test-model")
    assert text.startswith("# DRAFT")
    assert parse_ips(text) == result.ips


def test_report_shows_flags_and_comparison() -> None:
    rules = GOLDEN.rules[1:] + [rule("base_currency", "EUR", "She spends in euro only.")]
    result = convert(edited(GOLDEN, rules=rules), DOCUMENT)
    text = "\n".join(report_lines(result, compare(result.data, REFERENCE)))
    assert "base_currency = 'EUR': quote not found" in text
    assert "settings match, 0 differ" in text


# --- live run (only with an API key) -------------------------------------------------

@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY: live LLM test skipped")
def test_live_extraction_of_the_sample_ips() -> None:
    anthropic = pytest.importorskip("anthropic")
    result = extract_ips(DOCUMENT, anthropic.Anthropic())
    assert result.ips is not None, result.errors
    comparison = compare(result.data, REFERENCE)
    assert comparison.differences == [], comparison.differences
