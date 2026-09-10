"""Testes do agendador diário da replicação D-1."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.bots.bot_replicacao_aud_d1 import parar_event
from app.bots.replicacao_aud_d1_orchestration import (
    _aguardar_proximo_ciclo,
    _iniciar_agendador_com_ciclo_limpo,
    _settings_execucao,
    _settings_planejamento,
    carregar_state_agendador,
    deve_executar_imediato,
    esta_no_minuto_alvo,
    estado_vazio,
    format_hora,
    normalizar_hora_config,
    parse_hora,
    resolver_horarios_agendamento,
    salvar_state_agendador,
)


def test_parse_hora_valido():
    assert parse_hora("07:00") == (7, 0)
    assert parse_hora("12:30") == (12, 30)
    assert parse_hora("7:05") == (7, 5)


def test_parse_hora_invalido_usa_default():
    assert parse_hora("", default=(8, 15)) == (8, 15)
    assert parse_hora("25:00", default=(8, 15)) == (8, 15)
    assert parse_hora("07:99", default=(8, 15)) == (8, 15)


def test_normalizar_hora_config():
    assert normalizar_hora_config("07:05") == "07:05"
    assert normalizar_hora_config("invalido", default="07:00") == "07:00"


def test_format_hora():
    assert format_hora(7, 0) == "07:00"


def test_esta_no_minuto_alvo_qualquer_segundo():
    base = datetime(2026, 7, 15, 10, 41, 0)
    assert esta_no_minuto_alvo(base, 10, 41) is True
    assert esta_no_minuto_alvo(base.replace(second=13), 10, 41) is True
    assert esta_no_minuto_alvo(base.replace(second=59), 10, 41) is True
    assert esta_no_minuto_alvo(base.replace(minute=42, second=0), 10, 41) is False
    assert esta_no_minuto_alvo(base.replace(hour=11), 10, 41) is False


@patch("app.bots.replicacao_aud_d1_orchestration._get_tz_br", return_value=None)
@patch("app.bots.replicacao_aud_d1_orchestration.datetime")
def test_deve_executar_imediato_dentro_do_minuto(mock_dt, _tz):
    agora = datetime(2026, 7, 15, 10, 41, 37)
    mock_dt.now.return_value = agora
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    assert deve_executar_imediato(10, 41) is True


@patch("app.bots.replicacao_aud_d1_orchestration._get_tz_br", return_value=None)
@patch("app.bots.replicacao_aud_d1_orchestration.datetime")
def test_deve_executar_imediato_antes_do_horario(mock_dt, _tz):
    agora = datetime(2026, 7, 15, 10, 40, 59)
    mock_dt.now.return_value = agora
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    assert deve_executar_imediato(10, 41) is False


def test_iniciar_agendador_limpa_ciclo_concluido(tmp_path: Path):
    from app.bots.replicacao_aud_d1_orchestration import _iniciar_agendador_com_ciclo_limpo

    settings = {"replicacao_config_base": str(tmp_path / "config")}
    resumo = tmp_path / "resumo"
    resumo.mkdir(parents=True)
    hoje = "2026-07-15"
    state = estado_vazio(hoje)
    state["planejamento_ok"] = True
    state["planejamento_tentado"] = True
    state["execucao_ok"] = True
    state["run_id_planejamento"] = "old"
    salvar_state_agendador(state, settings)

    with patch(
        "app.bots.replicacao_aud_d1_orchestration._hoje_iso",
        return_value=hoje,
    ):
        _iniciar_agendador_com_ciclo_limpo(settings)
        loaded = carregar_state_agendador(settings)

    assert loaded["planejamento_ok"] is False
    assert loaded["execucao_ok"] is False
    assert loaded["planejamento_tentado"] is False
    assert loaded["run_id_planejamento"] == ""


def test_iniciar_agendador_permite_reexecucao_mesmo_fora_do_minuto(tmp_path: Path):
    """execucao_ok=true não bloqueia novo Start em qualquer horário do dia."""
    from app.bots.replicacao_aud_d1_orchestration import _iniciar_agendador_com_ciclo_limpo

    settings = {"replicacao_config_base": str(tmp_path / "config")}
    resumo = tmp_path / "resumo"
    resumo.mkdir(parents=True)
    hoje = "2026-07-15"
    state = estado_vazio(hoje)
    state["planejamento_ok"] = True
    state["execucao_ok"] = True
    state["run_id_planejamento"] = "keep-me-not"
    salvar_state_agendador(state, settings)

    with patch(
        "app.bots.replicacao_aud_d1_orchestration._hoje_iso",
        return_value=hoje,
    ):
        _iniciar_agendador_com_ciclo_limpo(settings)
        loaded = carregar_state_agendador(settings)

    assert loaded["execucao_ok"] is False
    assert loaded["run_id_planejamento"] == ""


def test_resolver_horarios_agendamento_defaults():
    plan, exec_ = resolver_horarios_agendamento({})
    assert plan == (7, 0)
    assert exec_ == (12, 0)


def test_resolver_horarios_agendamento_custom():
    plan, exec_ = resolver_horarios_agendamento(
        {
            "agendamento_hora_planejamento": "06:30",
            "agendamento_hora_execucao": "13:45",
        }
    )
    assert plan == (6, 30)
    assert exec_ == (13, 45)


def test_state_persistencia(tmp_path: Path):
    settings = {"replicacao_config_base": str(tmp_path / "config")}
    resumo = tmp_path / "resumo"
    resumo.mkdir(parents=True)
    hoje = "2026-06-30"
    state = estado_vazio(hoje)
    state["planejamento_ok"] = True
    state["planejamento_tentado"] = True
    state["run_id_planejamento"] = "20260630_070001"
    path = salvar_state_agendador(state, settings)
    assert path.exists()
    with patch(
        "app.bots.replicacao_aud_d1_orchestration._hoje_iso",
        return_value=hoje,
    ):
        loaded = carregar_state_agendador(settings)
    assert loaded["run_id_planejamento"] == "20260630_070001"
    assert loaded["planejamento_ok"] is True


def test_state_reseta_em_novo_dia(tmp_path: Path):
    settings = {"replicacao_config_base": str(tmp_path / "config")}
    resumo = tmp_path / "resumo"
    resumo.mkdir(parents=True)
    path = resumo / "agendador_d1_state.json"
    path.write_text(
        json.dumps(
            {
                "data": "2026-06-29",
                "run_id_planejamento": "old",
                "planejamento_ok": True,
                "planejamento_tentado": True,
                "execucao_ok": True,
            }
        ),
        encoding="utf-8",
    )
    with patch(
        "app.bots.replicacao_aud_d1_orchestration._hoje_iso",
        return_value="2026-06-30",
    ):
        loaded = carregar_state_agendador(settings)
    assert loaded["data"] == "2026-06-30"
    assert loaded["planejamento_ok"] is False
    assert loaded["execucao_ok"] is False


def test_settings_planejamento_e_execucao():
    base = {"replicacao_aud_seed": 99, "run_id": "manual", "apenas_planejamento": False}
    plan = _settings_planejamento(base)
    assert plan["apenas_planejamento"] is True
    assert plan["gerar_novo_plano"] is True
    assert "run_id" not in plan or plan.get("run_id") in ("", None)

    exec_cfg = _settings_execucao(base, "20260630_120000")
    assert exec_cfg["apenas_planejamento"] is False
    assert exec_cfg["gerar_novo_plano"] is False
    assert exec_cfg["run_id"] == "20260630_120000"
    assert exec_cfg["replicacao_aud_seed"] == 99
    assert exec_cfg["execucao_agendada"] is True


@patch("app.bots.replicacao_aud_d1_orchestration._executar_planejamento")
def test_executar_fase_planejamento_retorna_run_id(mock_plan):
    from app.bots.replicacao_aud_d1_orchestration import _executar_fase_planejamento

    class Plano:
        run_id = "20260630_070500"

    mock_plan.return_value = Plano()
    ok, run_id = _executar_fase_planejamento({})
    assert ok is True
    assert run_id == "20260630_070500"
    mock_plan.assert_called_once()
    args, _ = mock_plan.call_args
    assert args[0]["apenas_planejamento"] is True
    assert args[0]["gerar_novo_plano"] is True


@patch("app.bots.replicacao_aud_d1_orchestration._aguardar_horario", return_value=True)
def test_aguardar_proximo_ciclo_forca_proximo_dia(mock_wait):
    parar_event.clear()
    assert _aguardar_proximo_ciclo(7, 0) is True
    mock_wait.assert_called_once_with(7, 0, rotulo="planejamento", forcar_proximo_dia=True)


@patch("app.bots.replicacao_aud_d1_orchestration.start_agendado")
def test_start_rota_para_agendador_quando_ativo(mock_start_agendado):
    from app.bots import bot_replicacao_aud_d1 as bot

    parar_event.clear()
    thread = bot.start({"agendamento_ativo": True})
    thread.join(timeout=2)
    mock_start_agendado.assert_called_once()
    args, _ = mock_start_agendado.call_args
    assert args[0]["agendamento_ativo"] is True


@patch("app.bots.replicacao_aud_d1_orchestration.start_agendado")
def test_start_rota_para_agendador_com_string_true(mock_start_agendado):
    from app.bots import bot_replicacao_aud_d1 as bot

    parar_event.clear()
    thread = bot.start({"agendamento_ativo": "true"})
    thread.join(timeout=2)
    mock_start_agendado.assert_called_once()


@patch("app.bots.replicacao_aud_d1_orchestration.start_agendado")
def test_start_rota_para_agendador_com_persistent_config(mock_start_agendado):
    from app.bots import bot_replicacao_aud_d1 as bot

    parar_event.clear()
    thread = bot.start(
        {
            "fonte_banco_ativa": True,
            "_execution_snapshot": {
                "persistent": {
                    "agendamento_ativo": True,
                    "agendamento_hora_planejamento": "15:00",
                    "agendamento_hora_execucao": "20:00",
                }
            },
        }
    )
    thread.join(timeout=2)
    mock_start_agendado.assert_called_once()
    args, _ = mock_start_agendado.call_args
    assert args[0]["agendamento_ativo"] is True


@patch("app.bots.bot_replicacao_aud_d1.executar_bot_replicacao")
def test_start_sem_agendamento_executa_bot_direto(mock_executar):
    from app.bots import bot_replicacao_aud_d1 as bot

    parar_event.clear()
    thread = bot.start({"agendamento_ativo": False})
    thread.join(timeout=2)
    mock_executar.assert_called_once()
    assert mock_executar.call_args[0][0]["agendamento_ativo"] is False


@patch("app.bots.replicacao_aud_d1_orchestration._aguardar_proximo_ciclo")
@patch("app.bots.replicacao_aud_d1_orchestration._executar_fase_execucao", return_value=True)
@patch(
    "app.bots.replicacao_aud_d1_orchestration._executar_fase_planejamento",
    return_value=(True, "20260706_070001"),
)
@patch("app.bots.replicacao_aud_d1_orchestration.deve_executar_imediato", return_value=True)
def test_start_agendado_executa_planejamento_e_brflow_no_ciclo(
    mock_deve_exec,
    mock_plan,
    mock_exec,
    mock_proximo,
    tmp_path: Path,
):
    """Simula um dia completo: planejamento -> execução BRFlow -> aguarda próximo dia."""
    from app.bots.replicacao_aud_d1_orchestration import start_agendado

    parar_event.clear()

    def encerrar_apos_proximo_ciclo(*_args, **_kwargs):
        parar_event.set()
        return True

    mock_proximo.side_effect = encerrar_apos_proximo_ciclo

    settings = {
        "agendamento_ativo": True,
        "agendamento_hora_planejamento": "07:00",
        "agendamento_hora_execucao": "12:00",
        "replicacao_config_base": str(tmp_path / "config"),
    }

    start_agendado(settings)

    mock_plan.assert_called_once()
    mock_exec.assert_called_once()
    exec_args, _ = mock_exec.call_args
    assert exec_args[1] == "20260706_070001"
    mock_proximo.assert_called_once_with(7, 0)

    state_path = tmp_path / "resumo" / "agendador_d1_state.json"
    assert state_path.exists()
    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)
    # Após ciclo OK o state é limpo para permitir nova execução (Start / próximo horário).
    assert state["planejamento_ok"] is False
    assert state["execucao_ok"] is False
    assert state["run_id_planejamento"] == ""


@patch("app.bots.replicacao_aud_d1_orchestration._executar_fase_planejamento")
@patch("app.bots.replicacao_aud_d1_orchestration._aguardar_horario", return_value=True)
def test_start_agendado_aguarda_horario_antes_do_planejamento(
    mock_aguardar,
    mock_plan,
    tmp_path: Path,
):
    """Antes do horário: aguarda planejamento, executa fase e encerra."""
    from app.bots.replicacao_aud_d1_orchestration import start_agendado

    parar_event.clear()
    mock_plan.return_value = (True, "20260706_100001")

    call_count = {"n": 0}

    def deve_exec(hora, minuto):
        call_count["n"] += 1
        # Primeira chamada (planejamento): ainda não passou do horário
        if call_count["n"] == 1:
            return False
        # Execução BRFlow: já passou
        return True

    def encerrar_apos_exec(*_args, **_kwargs):
        parar_event.set()
        return True

    with patch(
        "app.bots.replicacao_aud_d1_orchestration.deve_executar_imediato",
        side_effect=deve_exec,
    ), patch(
        "app.bots.replicacao_aud_d1_orchestration._executar_fase_execucao",
        return_value=True,
    ) as mock_exec, patch(
        "app.bots.replicacao_aud_d1_orchestration._aguardar_proximo_ciclo",
        side_effect=encerrar_apos_exec,
    ):
        settings = {
            "agendamento_ativo": True,
            "agendamento_hora_planejamento": "10:00",
            "agendamento_hora_execucao": "12:00",
            "replicacao_config_base": str(tmp_path / "config"),
        }
        start_agendado(settings)

    mock_aguardar.assert_any_call(10, 0, rotulo="planejamento")
    mock_plan.assert_called_once()
    mock_exec.assert_called_once()


@patch("app.bots.bot_replicacao_aud_d1.write_run_manifest")
@patch("app.bots.bot_replicacao_aud_d1.build_replication_run_result")
@patch("app.bots.bot_replicacao_aud_d1.collect_workflow_results", return_value=[])
@patch("app.bots.bot_replicacao_aud_d1.carregar_estado_execucao", return_value={})
@patch("app.bots.bot_replicacao_aud_d1._executar_planejamento")
def test_apenas_planejamento_sets_parar_event(mock_plan, _estado, _collect, mock_build, _manifest):
    from app.bots.bot_replicacao_aud_d1 import executar_bot_replicacao, parar_event
    from app.bots.replicacao_d1.domain import ReplicationRunResult, RunStatus

    class Plano:
        run_id = "20260813_120000"
        pasta_protocolos = Path()

    mock_plan.return_value = Plano()
    mock_build.return_value = ReplicationRunResult(
        run_id="20260813_120000",
        status=RunStatus.PLANNED,
        workflows_success=0,
        workflows_failed=0,
        workflows_skipped=0,
    )
    parar_event.clear()
    executar_bot_replicacao({"apenas_planejamento": True})
    assert parar_event.is_set()


@patch("app.bots.bot_replicacao_aud_d1._executar_planejamento", side_effect=RuntimeError("falhou"))
def test_apenas_planejamento_sets_parar_event_on_error(mock_plan):
    from app.bots.bot_replicacao_aud_d1 import executar_bot_replicacao, parar_event

    parar_event.clear()
    try:
        executar_bot_replicacao({"apenas_planejamento": True})
    except RuntimeError:
        pass
    assert parar_event.is_set()


def test_start_expoe_erro_da_thread_para_o_runner(monkeypatch):
    from app.bots import bot_replicacao_aud_d1 as bot

    erro = RuntimeError("planejamento vazio")
    monkeypatch.setattr(
        "app.bots.replicacao_d1_db_bridge.resolve_agendamento_settings",
        lambda settings: settings,
    )
    monkeypatch.setattr(bot, "executar_bot_replicacao", lambda _settings: (_ for _ in ()).throw(erro))

    thread = bot.start(settings={"agendamento_ativo": False})
    thread.join(timeout=2)

    assert thread.is_alive() is False
    assert thread.execution_error is erro
