import pytest

from ips_soundboard.display import label, pct


@pytest.mark.parametrize(("identifier", "expected"), [
    ("eu_equity", "EU equity"),
    ("world_ex_eu_equity", "World ex EU equity"),
    ("em_equity", "EM equity"),
    ("eur_govt_bonds", "EUR govt bonds"),
    ("us_reits", "US REITs"),
    ("gold", "Gold"),
    ("company_pension", "Company pension"),
    ("satellite", "Satellite"),
])
def test_label(identifier: str, expected: str) -> None:
    assert label(identifier) == expected


def test_pct_always_one_decimal() -> None:
    assert [pct(x) for x in (0.25, 0.3731, -0.0931, 0.0)] == ["25.0%", "37.3%", "-9.3%", "0.0%"]
