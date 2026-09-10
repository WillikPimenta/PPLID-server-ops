# -*- coding: utf-8 -*-
from report_falhas.legacy_main import _display_falhas_path, _scope_output_entry


def test_display_falhas_path_idf_bots():
    raw = (
        r"C:\Users\user\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bots\report-falhas-criticas\brasilia\2026-07-03"
    )
    assert _display_falhas_path(raw).startswith("Planejamento - IDF - Bots")


def test_display_falhas_path_idf_bases():
    raw = (
        r"C:\Users\user\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bases\Bots\report-falhas-criticas\brasilia\2026-07-03"
    )
    assert _display_falhas_path(raw).startswith("Planejamento - IDF - Bases")


def test_scope_output_entry(tmp_path):
    scope_dir = tmp_path / "brasilia" / "2026-07-03"
    scope_dir.mkdir(parents=True)
    entry = _scope_output_entry("Brasília", scope_dir)
    assert entry["scope"] == "Brasília"
    assert entry["path"] == str(scope_dir.resolve())
