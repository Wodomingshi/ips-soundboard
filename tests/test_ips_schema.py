from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ips_soundboard.schemas.ips import IPS, load_ips

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_investor_eur" / "ips.yaml"
PRIVATE = ROOT / "private" / "ips.yaml"


@pytest.fixture
def raw() -> dict:
    with SAMPLE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_sample_ips_is_valid() -> None:
    ips = load_ips(SAMPLE)
    assert ips.base_currency == "EUR"
    assert set(ips.saa) == {
        "eu_equity", "world_ex_eu_equity", "em_equity", "eur_govt_bonds", "gold", "cash",
    }
    assert ips.saa["eur_govt_bonds"].target == pytest.approx(0.25)


def test_required_reserve_is_months_times_expenses() -> None:
    assert load_ips(SAMPLE).liquidity.required_reserve() == pytest.approx(21_000)


def test_required_reserve_is_none_when_not_set(raw: dict) -> None:
    raw["liquidity"] = {}
    assert IPS.model_validate(raw).liquidity.required_reserve() is None


def test_saa_must_sum_to_one(raw: dict) -> None:
    raw["saa"]["cash"]["target"] = 0.10
    raw["saa"]["cash"]["max"] = 0.15
    with pytest.raises(ValidationError, match="sum to 1.0"):
        IPS.model_validate(raw)


def test_band_must_contain_target(raw: dict) -> None:
    raw["saa"]["gold"]["min"] = 0.06
    with pytest.raises(ValidationError, match="min <= target <= max"):
        IPS.model_validate(raw)


def test_weights_are_fractions_not_percentages(raw: dict) -> None:
    raw["saa"]["gold"]["max"] = 8
    with pytest.raises(ValidationError):
        IPS.model_validate(raw)


def test_sleeve_names_must_be_snake_case(raw: dict) -> None:
    raw["saa"]["Gold Bullion"] = raw["saa"].pop("gold")
    with pytest.raises(ValidationError, match="snake_case"):
        IPS.model_validate(raw)


def test_buy_limit_cannot_exceed_hard_limit(raw: dict) -> None:
    raw["limits"]["single_name_buy_limit"] = 0.10
    with pytest.raises(ValidationError, match="exceeds single_name_hard_limit"):
        IPS.model_validate(raw)


def test_currency_must_be_iso_code(raw: dict) -> None:
    raw["base_currency"] = "euro"
    with pytest.raises(ValidationError):
        IPS.model_validate(raw)


def test_unknown_fields_are_rejected(raw: dict) -> None:
    """Guards against an LLM extraction inventing rules."""
    raw["limits"]["crypto_allowed"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        IPS.model_validate(raw)


def test_account_cannot_be_managed_and_reported_only(raw: dict) -> None:
    raw["scope"]["reported_only"] = ["brokerage"]
    with pytest.raises(ValidationError, match="both managed and reported-only"):
        IPS.model_validate(raw)


def test_theme_cap_cannot_exceed_theme_total(raw: dict) -> None:
    raw["taa"]["themes"] = {"max_total": 0.05, "max_per_theme": 0.06, "max_count": 2}
    with pytest.raises(ValidationError, match="max_per_theme"):
        IPS.model_validate(raw)


@pytest.mark.skipif(not PRIVATE.exists(), reason="no private IPS on this machine")
def test_private_ips_is_valid() -> None:
    """The owner's own IPS must also pass. Asserts nothing about its contents."""
    load_ips(PRIVATE)
