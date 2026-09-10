"""Testes do encadeamento Case Manager dentro do ciclo Produção (H/H)."""
from unittest.mock import patch

from app.bots import bot_production


def test_maybe_run_produtividade_case_noop_when_disabled():
    with patch("app.bots.produtividade_case.bot.executar_pipeline") as mock_pipeline:
        bot_production._maybe_run_produtividade_case({"executar_produtividade_case": False})
    mock_pipeline.assert_not_called()


def test_maybe_run_produtividade_case_calls_pipeline():
    settings = {
        "executar_produtividade_case": True,
        "produtividade_case_settings": {"tarefas": ["prod_hora"], "dir_hora": "X"},
    }
    with (
        patch("app.bots.produtividade_case.bot.executar_pipeline") as mock_pipeline,
        patch.object(bot_production, "_set_status"),
        patch.object(bot_production, "_set_progress"),
        patch.object(bot_production.parar_event, "is_set", return_value=False),
    ):
        bot_production._maybe_run_produtividade_case(settings)
    mock_pipeline.assert_called_once_with(settings["produtividade_case_settings"])


def test_maybe_run_produtividade_case_skips_without_tarefas():
    with (
        patch("app.bots.produtividade_case.bot.executar_pipeline") as mock_pipeline,
        patch.object(bot_production, "_set_status") as mock_status,
        patch.object(bot_production.parar_event, "is_set", return_value=False),
        patch.dict("os.environ", {"PRODUTIVIDADE_CASE_SETTINGS": "{}"}, clear=False),
    ):
        bot_production._maybe_run_produtividade_case(
            {"executar_produtividade_case": True, "produtividade_case_settings": {}}
        )
    mock_pipeline.assert_not_called()
    assert any("sem tarefas" in str(c) for c in mock_status.call_args_list)


def test_maybe_run_produtividade_case_swallows_errors():
    settings = {
        "executar_produtividade_case": True,
        "produtividade_case_settings": {"tarefas": ["prod_hora"]},
    }
    with (
        patch(
            "app.bots.produtividade_case.bot.executar_pipeline",
            side_effect=RuntimeError("Authentication failed."),
        ),
        patch.object(bot_production, "_set_status"),
        patch.object(bot_production, "_set_progress"),
        patch.object(bot_production, "_registrar_erro_producao") as mock_err,
        patch.object(bot_production.parar_event, "is_set", return_value=False),
    ):
        bot_production._maybe_run_produtividade_case(settings)
    mock_err.assert_called_once()
