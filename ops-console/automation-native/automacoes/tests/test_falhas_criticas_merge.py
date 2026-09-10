# -*- coding: utf-8 -*-
"""Testes do merge Power BI → Base."""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.bots.falhas_criticas.powerbi import config
from app.bots.falhas_criticas.powerbi.merge_base import (
    MergeStats,
    _com_hresults,
    _dedupe_combined,
    _is_excel_save_error,
    _normalize_column_names,
    should_persist_merge,
    validate_export_row_count,
)
from app.config.paths import DEFAULT_FALHAS_CRITICAS_EXCEL
from app.services.robot_manager import RobotProcessManager


def test_normalize_column_names_responsavel_to_lider():
    df = pd.DataFrame({"Responsável": ["A"], "Protocolo": [1]})
    out = _normalize_column_names(df)
    assert "Líder" in out.columns
    assert out.loc[0, "Líder"] == "A"


def test_dedupe_combined_keeps_export_row():
    df_base = pd.DataFrame(
        {
            "Protocolo": [100, 200],
            "Data de Análise": pd.to_datetime(["2026-07-01", "2026-07-01"]),
            "Cliente": ["A", "B"],
        }
    )
    df_new = pd.DataFrame(
        {
            "Protocolo": [100],
            "Data de Análise": pd.to_datetime(["2026-07-01"]),
            "Cliente": ["A-atualizado"],
        }
    )
    combined, dupes = _dedupe_combined(df_base, df_new)
    assert dupes == 1
    assert len(combined) == 2
    row = combined[combined["Protocolo"] == 100].iloc[0]
    assert row["Cliente"] == "A-atualizado"


def test_validate_export_row_count_single_day_warning():
    df = pd.DataFrame({"Protocolo": list(range(60)), "Data de Análise": pd.to_datetime(["2026-07-01"] * 60)})
    warnings = validate_export_row_count(df, date(2026, 7, 1), date(2026, 7, 1))
    assert len(warnings) == 1
    assert "109" not in warnings[0]
    assert "60" in warnings[0]


def test_validate_export_row_count_dates_outside_period():
    df = pd.DataFrame(
        {
            "Protocolo": [1, 2],
            "Data de Análise": pd.to_datetime(["2026-06-15", "2026-06-20"]),
        }
    )
    warnings = validate_export_row_count(df, date(2026, 7, 1), date(2026, 7, 1))
    assert any("fora do período" in w for w in warnings)


def test_merge_stats_status_message():
    stats = MergeStats(rows_before=2162, rows_after=2163, rows_added=1)
    assert stats.status_message() == "Merge: +1 linha(s) na Base (2162→2163)"


def test_merge_stats_status_message_updates_only():
    stats = MergeStats(rows_before=2162, rows_after=2162, rows_added=0, duplicates_removed=5)
    assert "5 linha(s) atualizada(s)" in stats.status_message()


def test_should_persist_merge_new_or_updated_rows():
    assert should_persist_merge(1, 0) is True
    assert should_persist_merge(0, 3) is True
    assert should_persist_merge(0, 0) is False


def test_dedupe_triggers_persist_when_export_overwrites_base():
    df_base = pd.DataFrame(
        {
            "Protocolo": [100],
            "Data de Análise": pd.to_datetime(["2026-07-01"]),
            "Módulo": ["Demais falhas"],
        }
    )
    df_new = pd.DataFrame(
        {
            "Protocolo": [100],
            "Data de Análise": pd.to_datetime(["2026-07-01"]),
            "Módulo": ["G AUDITORIA"],
        }
    )
    combined, dupes = _dedupe_combined(df_base, df_new)
    assert dupes == 1
    assert len(combined) == 1
    assert combined.iloc[0]["Módulo"] == "G AUDITORIA"
    assert should_persist_merge(len(combined) - len(df_base), dupes) is True


def test_apply_settings_downloads_dir_next_to_master(monkeypatch, tmp_path: Path):
    master = tmp_path / "FALHAS_CRITICAS_MANUAL.xlsx"
    master.write_bytes(b"")
    config.apply_settings({"falhas_excel_path": str(master)})
    assert config.DOWNLOADS_DIR == master.parent / "bi"
    assert config.ERRORS_DIR == master.parent / "bi" / "errors"


def test_resolve_falhas_excel_path_migrates_legacy():
    legacy = r"C:\x\Planejamento - IDF - Bases\Bots\report-falhas-criticas\FALHAS_CRITICAS_MANUAL.xlsx"
    resolved = RobotProcessManager._resolve_falhas_excel_path(legacy, str(DEFAULT_FALHAS_CRITICAS_EXCEL))
    assert "Planejamento - IDF - Bots" in resolved
    assert "Bases" not in resolved


def test_resolve_falhas_excel_path_directory():
    folder = r"C:\x\Planejamento - IDF - Bots\report-falhas-criticas"
    resolved = RobotProcessManager._resolve_falhas_excel_path(folder, str(DEFAULT_FALHAS_CRITICAS_EXCEL))
    assert resolved.endswith("FALHAS_CRITICAS_MANUAL.xlsx")


def test_is_excel_save_error_documento_nao_salvo():
    class FakeComError(Exception):
        args = (-2146827284, "Exceção.", (0, "Microsoft Excel", "Documento não salvo.", "xlmain11.chm", 0, -2146827284))

    assert _is_excel_save_error(FakeComError()) is True
    assert -2146827284 in _com_hresults(FakeComError())


def test_is_excel_save_error_other():
    assert _is_excel_save_error(RuntimeError("outro")) is False

