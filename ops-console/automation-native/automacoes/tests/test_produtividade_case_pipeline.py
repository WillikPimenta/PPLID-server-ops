# -*- coding: utf-8 -*-
"""Smoke do orquestrador com collection mockada (sem DocumentDB)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.bots.produtividade_case.bot import executar_pipeline


def test_executar_pipeline_prod_hora_only(tmp_path: Path, capsys):
    collection = MagicMock()
    collection.aggregate.return_value = []
    client = MagicMock()

    settings = {
        "tarefas": ["prod_hora"],
        "dir_hora": str(tmp_path / "hora"),
        "dir_tempo_logado": str(tmp_path / "tempo"),
        "dir_consolidado": str(tmp_path / "cons"),
        "dir_temp": str(tmp_path / "temp"),
    }

    with (
        patch(
            "app.bots.produtividade_case.bot.validate_docdb_config",
            return_value=(True, ""),
        ),
        patch(
            "app.bots.produtividade_case.bot.get_collection",
            return_value=(client, collection),
        ),
    ):
        executar_pipeline(settings)

    assert collection.aggregate.called
    client.close.assert_called()
    hora_files = list((tmp_path / "hora").rglob("*.xlsx"))
    assert len(hora_files) == 1
    out = capsys.readouterr().out
    assert "PRODUTIVIDADE_CASE_SAVED|prod_hora|" in out
