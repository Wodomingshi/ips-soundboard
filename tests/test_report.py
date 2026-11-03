import json
from types import SimpleNamespace

import pytest

from ips_soundboard.dashboard.pipeline import new_money_plan, run_analysis
from ips_soundboard.holdings import load_holdings
from ips_soundboard.report.facts import build_facts
from ips_soundboard.report.narrative import Narrative, write_narrative
from ips_soundboard.report.numbers import allowed_numbers, check_paragraphs, unsupported_numbers
from ips_soundboard.report.render import render
from ips_soundboard.schemas.ips import load_ips
from test_dashboard import AS_OF, SAMPLE_DIR, sample_market


@pytest.fixture(scope="module")
def analysis():
    return run_analysis(load_holdings(SAMPLE_DIR / "holdings.csv"), load_ips(SAMPLE_DIR / "ips.yaml"),
                        sample_market(), as_of=AS_OF)


@pytest.fixture(scope="module")
def facts(analysis):
    return build_facts(analysis, new_money_plan(analysis, 9_000))


def good_narrative(f: dict) -> Narrative:
    """What a correct narrative looks like: every number copied from the facts."""
    eu, world = f["allocation_vs_saa"][0], f["allocation_vs_saa"][1]
    risk, buy = f["risk"], f["new_money"]["buys"][0]
    return Narrative(
        summary=[f"There are {f['rule_findings']['breaches']} breaches and {f['rule_findings']['warnings']} warnings.",
                 f"{eu['sleeve']} is {eu['weight']}, above its {eu['band']} band."],
        allocation=[f"{world['sleeve']} is {world['weight']} against a target of {world['target']}. "
                    f"The managed portfolio is worth {f['wealth']['managed_portfolio']}."],
        rules=["Two single stocks are above the buy limit, so the IPS allows no further buying of them."],
        risk=[f"Annualised volatility is {risk['volatility_annualised']} and the maximum drawdown was "
              f"{risk['max_drawdown']} ({risk['max_drawdown_period']})."],
        new_money=[f"Of the {f['new_money']['amount']}, {buy['amount']} goes to {buy['sleeve']}."],
    )


class FakeClient:
    def __init__(self, *replies) -> None:
        self.replies, self.calls, self.messages = list(replies), [], self

    def create(self, **request):
        self.calls.append(request)
        text = SimpleNamespace(type="text", text=self.replies.pop(0).model_dump_json())
        return SimpleNamespace(content=[text], stop_reason="end_turn")


# --- facts ------------------------------------------------------------------------

def test_facts_are_json_with_numbers_preformatted(facts) -> None:
    json.dumps(facts)
    assert facts["allocation_vs_saa"][0]["weight"] == "37.3%"
    assert facts["wealth"]["managed_portfolio"] == "116,500 EUR"
    assert facts["rule_findings"]["breaches"] == 4


def test_facts_have_no_new_money_without_an_amount(analysis) -> None:
    assert build_facts(analysis)["new_money"] is None


# --- the number check -----------------------------------------------------------------

def test_numbers_from_the_facts_pass(facts) -> None:
    allowed = allowed_numbers(facts)
    assert unsupported_numbers("EU equity is 37.3%, 12.3 percentage points above target.", allowed) == []
    assert unsupported_numbers("The managed portfolio is 116,500 EUR with 4 breaches.", allowed) == []


def test_rounded_or_derived_numbers_fail(facts) -> None:
    allowed = allowed_numbers(facts)
    assert unsupported_numbers("EU equity is about 37% of the portfolio.", allowed) == ["37"]
    assert unsupported_numbers("Bonds are 8.1% below target.", allowed) == ["8.1"]


def test_a_number_from_the_facts_used_as_a_percentage_fails(facts) -> None:
    # 116,500 is a known amount, but never a percentage
    assert unsupported_numbers("A gain of 116,500% is possible.", allowed_numbers(facts)) == ["116,500"]


def test_check_reports_whole_sentences(facts) -> None:
    problems = check_paragraphs(["Gold is 6.2%. Cash could rise to 12.5% next year."], allowed_numbers(facts))
    assert [(p.sentence, p.numbers) for p in problems] == [("Cash could rise to 12.5% next year.", ["12.5"])]


# --- writing the narrative --------------------------------------------------------------

def test_good_narrative_passes_on_the_first_attempt(facts) -> None:
    client = FakeClient(good_narrative(facts))
    result = write_narrative(facts, client, model="test-model")
    assert result.removed == [] and result.attempts == 1 and result.numbers_checked > 10
    assert '"breaches": 4' in client.calls[0]["messages"][0]["content"]


def test_unsupported_sentence_is_sent_back_once(facts) -> None:
    good = good_narrative(facts)
    bad = good.model_copy(update={"risk": good.risk + ["Volatility should fall to about 6% soon."]})
    client = FakeClient(bad, good)
    result = write_narrative(facts, client, model="test-model")
    assert result.attempts == 2 and result.removed == []
    retry = client.calls[1]["messages"][-1]["content"]
    assert "Volatility should fall to about 6% soon." in retry and "not in the facts: 6" in retry


def test_still_unsupported_after_the_retry_is_removed_and_listed(facts) -> None:
    good = good_narrative(facts)
    bad = good.model_copy(update={"risk": [good.risk[0] + " A crash would cost about 31,000 EUR."]})
    result = write_narrative(facts, FakeClient(bad, bad), model="test-model")
    assert [p.numbers for p in result.removed] == [["31,000"]]
    assert result.narrative.risk == good.risk  # the rest of the paragraph stays


# --- the Markdown report ---------------------------------------------------------------

def test_report_has_narrative_tables_and_checks(facts) -> None:
    result = write_narrative(facts, FakeClient(good_narrative(facts)), model="test-model")
    text = render(facts, result, "test-model", "review.facts.json")
    for heading in ("# Portfolio review, 2026-10-30", "## Summary", "## Allocation vs SAA", "## IPS rules",
                    "## Risk", "## New money", "## Checks"):
        assert heading in text
    assert "| EU equity | 37.3% | 25.0% | 20.0% to 30.0% | above |" in text
    assert "numbers in the narrative were found in the computed facts" in text


def test_report_lists_removed_sentences(facts) -> None:
    good = good_narrative(facts)
    bad = good.model_copy(update={"summary": good.summary + ["Returns will be 9.9% next year."]})
    result = write_narrative(facts, FakeClient(bad, bad), model="test-model")
    text = render(facts, result, "test-model", "review.facts.json")
    assert '"Returns will be 9.9% next year." (unsupported: 9.9)' in text
    assert "Returns will be" not in text.split("## Checks")[0]


# --- accuracy fixes after the first live report ----------------------------------------

def test_date_parts_do_not_vouch_for_numbers(facts) -> None:
    allowed = allowed_numbers(facts)
    assert unsupported_numbers("The 2022 rate shock ran from 2022-01-03 to 2022-10-12.", allowed) == []
    assert unsupported_numbers("There are 3 single names over the limit.", allowed) == ["3"]  # 3 only in dates
    assert unsupported_numbers("It started on 2022-01-04.", allowed) == ["2022-01-04"]


def test_counts_written_as_words_are_checked(facts) -> None:
    allowed = allowed_numbers(facts)
    assert unsupported_numbers("Four breaches and two warnings were found.", allowed) == []
    assert unsupported_numbers("Seven breaches were found.", allowed) == ["Seven"]


def test_facts_give_counts_and_label_risk_weights(facts) -> None:
    assert facts["rule_findings"]["counts_by_rule"]["single_name_buy_limit (warning)"] == 2
    assert "weight_in_covered_portfolio" in facts["risk"]["largest_risk_contributors"][0]


def test_new_money_without_buys_has_no_empty_table(analysis) -> None:
    f = build_facts(analysis, new_money_plan(analysis, 500))  # below the 1,000 minimum trade
    narrative = good_narrative(build_facts(analysis, new_money_plan(analysis, 9_000)))
    result = write_narrative(f, FakeClient(narrative.model_copy(update={"new_money": [], "summary": []})),
                             model="m")
    text = render(f, result, "m", "x.json")
    assert "No buys this time; kept as cash: 500 EUR." in text
    assert "| Sleeve | Buy |" not in text
