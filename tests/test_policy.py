from pathlib import Path

import pytest

from ips_soundboard.engine.limits import Finding
from ips_soundboard.policy import _FINDING_TO_PATH, policy_rules, rule_for, saa_rows
from ips_soundboard.schemas.ips import IPS, load_ips

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "sample_investor_eur"
LIMITS_FILE = Path(__file__).resolve().parents[1] / "src" / "ips_soundboard" / "engine" / "limits.py"


@pytest.fixture
def ips() -> IPS:
    return load_ips(SAMPLE_DIR / "ips.yaml")


def finding(rule: str, subject: str = "x") -> Finding:
    return Finding(rule, "warning", subject, None, None, "message")


def rules_by_name(ips: IPS) -> dict[str, str]:
    return {r.rule: r.value for r in policy_rules(ips)}


def test_policy_reads_the_ips_in_plain_words(ips: IPS) -> None:
    rules = rules_by_name(ips)
    assert rules["Single stock: buy limit"] == "5.0%"
    assert rules["Cash reserve"] == "at least 6 months of maximum expenses (3,500 EUR a month) = 21,000 EUR"
    assert rules["Not allowed"] == "leverage, short selling, derivatives, leveraged or inverse ETFs"
    assert rules["Excluded industries"] == "Tobacco"
    assert rules["Themes"] == "not allowed"
    assert rules["New money goes to at most"] == "2 sleeve(s)"


def test_saa_rows(ips: IPS) -> None:
    assert saa_rows(ips)[0] == {"Sleeve": "EU equity", "Target": "25.0%", "Band": "20.0% to 30.0%"}


def test_every_ips_finding_points_to_a_setting() -> None:
    """Each rule id the limits engine emits is mapped, except data quality."""
    import re
    emitted = set(re.findall(r'Finding\("([a-z_]+)"', LIMITS_FILE.read_text(encoding="utf-8")))
    assert emitted - set(_FINDING_TO_PATH) == {"sleeve_band", "unvalued_holding"}


def test_rule_for_findings(ips: IPS) -> None:
    assert str(rule_for(finding("single_name_buy_limit"), ips)) == "Single stock: buy limit: 5.0%"
    assert str(rule_for(finding("sleeve_band", "eu_equity"), ips)) == \
        "EU equity band: 20.0% to 30.0% (target 25.0%)"
    assert rule_for(finding("unvalued_holding"), ips) is None


def test_rule_paths_match_the_extraction_settings(ips: IPS) -> None:
    """Paths line up with what extraction quotes, so the quotes can be shown next to findings later."""
    from ips_soundboard.extract.settings import SETTINGS
    for r in policy_rules(ips):
        assert r.path in SETTINGS or any(s.startswith(f"{r.path}.") for s in SETTINGS) or r.path == "risk", r.path
