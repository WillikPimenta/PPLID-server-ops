# -*- coding: utf-8 -*-
import json
import logging
from unittest.mock import patch

from app.bots.replicacao_d1.observability import format_log_context, log_event
from app.bots.replicacao_d1.selenium.text_utils import (
    classificar_situacao_texto,
    linha_deve_ocultar_por_status_titulo,
    sanitizar_sufixo_screenshot,
    xpath_escape_texto,
)


def test_classificar_situacao():
    assert classificar_situacao_texto("Ativo") == "ATIVO"
    assert classificar_situacao_texto("Inativo") == "INATIVO"
    assert classificar_situacao_texto("") == "DESCONHECIDO"


def test_linha_oculta_inativo():
    assert linha_deve_ocultar_por_status_titulo("Inativo") is True
    assert linha_deve_ocultar_por_status_titulo("Ativo") is False


def test_xpath_escape():
    assert xpath_escape_texto("G Auditoria") == '"G Auditoria"'
    assert "concat" in xpath_escape_texto('a"b\'c')


def test_screenshot_suffix():
    assert sanitizar_sufixo_screenshot('WF:Case*') == "WF_Case_"


def test_log_event_formats_context(caplog):
    logger = logging.getLogger("test.replicacao_d1.obs")
    with caplog.at_level(logging.INFO):
        log_event(logger, logging.INFO, "upload ok", run_id="r1", workflow="WF G", phase="upload")
    assert "run_id=r1" in caplog.text
    assert "workflow=WF G" in caplog.text
    assert format_log_context(run_id="r1", phase="close") == "run_id=r1 | phase=close"


def test_planning_phases_and_plan_log(caplog):
    from app.bots.replicacao_d1.planning_phases import (
        PLANNING_DONE,
        PLANNING_SOURCE,
        PLANNING_START,
        plan_log,
        truncate_hash,
    )

    assert PLANNING_START == "planning_start"
    assert PLANNING_SOURCE == "planning_source"
    assert PLANNING_DONE == "planning_done"
    assert truncate_hash("abcdef0123456789") == "abcdef01"

    logger = logging.getLogger("test.replicacao_d1.planning")
    with caplog.at_level(logging.INFO):
        plan_log(
            logger,
            logging.INFO,
            "Planejamento iniciado",
            run_id="run_test",
            phase=PLANNING_START,
            database_only=True,
        )
    assert "phase=planning_start" in caplog.text
    assert "run_id=run_test" in caplog.text
    assert "database_only=True" in caplog.text


def test_plan_log_emits_structured_event_and_callback(capsys):
    from app.bots.replicacao_d1.planning_phases import (
        PLANNING_ALLOCATE,
        PLAN_EVENT_PREFIX,
        plan_log,
        set_plan_event_callback,
    )

    events = []
    set_plan_event_callback(events.append)
    try:
        plan_log(
            logging.getLogger("test.replicacao_d1.plan_event"),
            logging.INFO,
            "Alocando workflow",
            run_id="run-1",
            phase=PLANNING_ALLOCATE,
            current=2,
            total=5,
        )
    finally:
        set_plan_event_callback(None)

    lines = capsys.readouterr().out.splitlines()
    structured = next(line for line in lines if line.startswith(PLAN_EVENT_PREFIX))
    payload = json.loads(structured[len(PLAN_EVENT_PREFIX) :])
    assert payload["version"] == 1
    assert payload["phase"] == PLANNING_ALLOCATE
    assert payload["state"] == "progress"
    assert payload["current"] == 2
    assert payload["total"] == 5
    assert payload["progress_pct"] == 58
    assert events[0]["event_id"] == payload["event_id"]


def test_plan_event_runtime_progress_uses_safe_ranges():
    from app.bots import bot_replicacao_aud_d1 as bot

    event = {
        "state": "progress",
        "message": "Alocando",
        "progress_pct": 40,
        "run_id": "run-1",
    }
    with patch.object(bot, "_set_progress") as progress, patch.object(bot, "_set_status"):
        bot._plan_progress_context.plan_only = False
        bot._publish_plan_event_to_runtime(event)
        progress.assert_called_once_with(9, "Alocando")

        progress.reset_mock()
        bot._plan_progress_context.plan_only = True
        bot._publish_plan_event_to_runtime(event)
        progress.assert_called_once_with(40, "Alocando")
    bot._plan_progress_context.plan_only = False
