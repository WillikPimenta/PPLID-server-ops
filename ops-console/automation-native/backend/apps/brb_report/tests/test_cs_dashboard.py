# -*- coding: utf-8 -*-
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from apps.brb_report.services.cs_dashboard import prepare_cs_source_snapshot


@patch("apps.brb_report.services.cs_dashboard._load_report_bundle")
@patch("apps.brb_report.services.cs_dashboard.resolve_source_workbook")
def test_prepare_cs_source_snapshot_returns_counts(resolve_mock, load_mock, tmp_path):
    source_path = tmp_path / "source.xlsx"
    source_path.write_bytes(b"xlsx")
    resolve_mock.return_value = source_path

    bundle = MagicMock()
    bundle.na_demandas = pd.DataFrame()
    bundle.na_falhas = pd.DataFrame()
    bundle.falhas_gerais = pd.DataFrame()
    bundle.auditados = pd.DataFrame()
    bundle.treinamentos_horas = pd.DataFrame()
    bundle.contestacao = pd.DataFrame(
        [{"Protocolo": "1", "Data": None, "Data de Análise": None, "classificacao_conforme": "falha"}]
    )
    load_mock.return_value = bundle

    result = prepare_cs_source_snapshot("cs-key", "brb")

    assert result["contestations"] == 1
    assert result["days"] == 0
    assert result["storage_key"] == "cs-key"
    assert (source_path.parent / "cs_snapshot.json").is_file()
