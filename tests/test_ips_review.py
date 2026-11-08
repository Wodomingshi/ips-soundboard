from pathlib import Path

import pytest
from test_extract import DOCUMENT, GOLDEN, rule

from ips_soundboard.dashboard.ips_review import (
    check_yaml,
    found_table,
    name_check,
    not_stated_table,
    setting_label,
)
from ips_soundboard.extract.convert import convert
from ips_soundboard.extract.documents import DocumentError, document_text
from ips_soundboard.extract.output import draft_yaml

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "sample_investor_eur"


def test_found_values_show_as_written_with_their_quote() -> None:
    table = found_table(convert(GOLDEN, DOCUMENT))
    first = table.iloc[0]
    assert first["Setting"] == "Allocation: European equities"
    assert first["As written"] == "25% (range 20%–30%)"
    assert first["Check"] == "✓"
    buy = table[table["Setting"].str.startswith("Weight of one single stock above which no more")].iloc[0]
    assert (buy["As written"], buy["From your document"]) == (
        "5%", "No single stock above 5% of the portfolio when buying")


def test_a_value_not_in_the_document_is_flagged() -> None:
    draft = GOLDEN.model_copy(update={"rules": [*GOLDEN.rules[:-1],
                                                rule("rebalancing.new_money_max_sleeves", "three", "up to three sleeves")]})
    table = found_table(convert(draft, DOCUMENT))
    assert table["Check"].tolist().count("⚠ quote not in your document") == 1


def test_settings_not_stated_show_the_value_used() -> None:
    table = not_stated_table(convert(GOLDEN, DOCUMENT))
    themes = table[table["Setting"] == "Maximum number of themes held at once"]
    assert len(themes) == 1 and themes.iloc[0]["Value used"] != "—"
    assert "Version number or date of the IPS document, if stated" not in table["Setting"].tolist()


def test_draft_yaml_checks_valid_and_edits_are_checked() -> None:
    text = draft_yaml(convert(GOLDEN, DOCUMENT), "IPS.md", "test-model")
    ips, errors = check_yaml(text)
    assert errors == [] and ips is not None and ips.base_currency == "EUR"
    _, errors = check_yaml(text.replace("target: 0.25", "target: 0.95", 1))
    assert errors == [("saa.european_equities: Value error, band must satisfy min <= target <= max, "
                       "got 0.2 / 0.95 / 0.3")]
    _, errors = check_yaml("saa: [unclosed")
    assert errors and errors[0].startswith("line ")
    assert check_yaml("just text")[1] == ["the file must be a set of settings (key: value lines)"]


def test_setting_labels() -> None:
    assert setting_label("saa.world_ex_eu_equity") == "Allocation: World ex EU equity"
    assert setting_label("unknown.path") == "unknown.path"
    assert setting_label("scope.managed") == "An account the strategic allocation applies to"
    assert setting_label("base_currency") == "Currency the investor measures wealth and spends in"
    assert setting_label("objectives.design_max_drawdown") == "Largest peak-to-trough loss the investor accepts"
    assert setting_label("exclusions.sub_industries") == "An excluded industry"


def test_documents_are_read_from_bytes() -> None:
    assert document_text("IPS.md", (SAMPLE_DIR / "IPS.md").read_bytes()) == DOCUMENT
    assert document_text("ips.txt", "﻿hello".encode()) == "hello"
    with pytest.raises(DocumentError):
        document_text("ips.txt", b"\xff\xfe\x00bad")


def test_a_pdf_without_text_is_refused() -> None:
    pypdf = pytest.importorskip("pypdf")
    import io

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(DocumentError, match="no text layer"):
        document_text("scan.pdf", out.getvalue())


def test_names_are_checked_against_the_holdings() -> None:
    from ips_soundboard.holdings import load_holdings

    holdings = load_holdings(SAMPLE_DIR / "holdings.csv")
    ips = convert(GOLDEN, DOCUMENT).ips
    unused, holdings_only = name_check(ips, holdings)
    assert "sleeve 'european_equities': no holding uses it (fine for a 0% target; otherwise rename)" in unused
    assert "eu_equity" in holdings_only
    from ips_soundboard.schemas.ips import load_ips

    assert name_check(load_ips(SAMPLE_DIR / "ips.yaml"), holdings) == ([], [])


def test_command_line_extractor_reads_documents(tmp_path, monkeypatch, capsys) -> None:
    import sys

    from test_extract import FakeClient

    import ips_soundboard.extract.__main__ as cli

    monkeypatch.setattr(cli, "anthropic_client", lambda: FakeClient(GOLDEN))
    monkeypatch.setattr(sys, "argv", ["extract", str(SAMPLE_DIR / "IPS.md"), "-o", str(tmp_path / "ips.draft.yaml")])
    cli.main()
    assert "Valid IPS" in capsys.readouterr().out
    assert check_yaml((tmp_path / "ips.draft.yaml").read_text(encoding="utf-8"))[1] == []
