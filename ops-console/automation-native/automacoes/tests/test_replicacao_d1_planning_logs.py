# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from app.bots.replicacao_aud_d1_planning import _run_id_resolution_reason


def test_run_id_resolution_reason():
    data_exec = datetime(2026, 8, 13, 20, 52, 39)
    assert _run_id_resolution_reason(data_exec, {"run_id": "custom"}, "custom") == "informado"
    assert (
        _run_id_resolution_reason(data_exec, {"sobrescrever": True}, "20260813")
        == "sobrescrever_dia"
    )
    assert (
        _run_id_resolution_reason(data_exec, {}, "20260813_205239")
        == "timestamp"
    )


def test_load_retro_source_hybrid_logs_aggregate(caplog):
    from app.bots.replicacao_d1_db_bridge import load_retro_source_hybrid_db_parquet

    plano = SimpleNamespace(warnings=[], run_id="run_hybrid")
    retro_config = {
        "data_inicio": "2026-08-10",
        "data_fim": "2026-08-11",
        "workflow_d1_keys": {"wf-a"},
    }
    day_df = pd.DataFrame(
        [{"Protocolo": "P1", "Workflow": "WF A", "_selection_reason": "retroativo:2026-08-10:parquet"}]
    )
    db_df = pd.DataFrame(
        [{"Protocolo": "P2", "Workflow": "WF A", "_selection_reason": "retroativo:2026-08-11"}]
    )
    mock_source_batch = MagicMock()
    mock_source_batch.source_rows_by_day_range_from_rotina = MagicMock(
        return_value={datetime(2026, 8, 11).date(): [{"protocolo": "P2"}]}
    )

    with caplog.at_level(logging.INFO, logger="robots.replicacao_d1_db_bridge"):
        with patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=True), patch.dict(
            sys.modules,
            {"apps.replicacao_d1.services.source_batch": mock_source_batch},
        ), patch(
            "app.bots.replicacao_d1_db_bridge._retro_rows_to_dataframe",
            return_value=db_df,
        ), patch(
            "app.bots.replicacao_d1_db_bridge.fallback_parquet_dias_ausentes_ativo",
            return_value=True,
        ), patch(
            "app.bots.replicacao_aud_d1_planning._carregar_dia_parquet_tratado",
            return_value=day_df,
        ):
            result = load_retro_source_hybrid_db_parquet(
                {"run_id": "run_hybrid", "fallback_parquet_dias_ausentes": True},
                datetime(2026, 8, 12),
                retro_config,
                plano,
            )

    assert len(result) == 2
    assert "phase=planning_retro" in caplog.text
    assert "days_parquet=1" in caplog.text


def test_plan_log_emits_planlog_to_stdout(capsys):
    from app.bots.replicacao_d1.planning_phases import PLANNING_SOURCE, plan_log

    logger = logging.getLogger("test.plan_log_stdout")
    plan_log(
        logger,
        logging.INFO,
        "Fonte D-1 carregada",
        run_id="20260813_231947",
        phase=PLANNING_SOURCE,
        rows=1200,
        source="db",
    )
    captured = capsys.readouterr().out
    assert captured.startswith("PLANLOG|Fonte D-1 carregada")
    assert "phase=planning_source" in captured
    assert "rows=1200" in captured
