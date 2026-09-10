"""Agendamento diário da replicação D-1: planejamento + execução BRFlow."""

from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

from app.bots.rotina.io import _get_tz_br
from app.config import (
    INTERVALO_ATUALIZACAO_COUNTDOWN_ROTINA,
    INTERVALO_ATUALIZACAO_FINAL_ROTINA,
    PASTA_REPLICACAO_AUD_D1_RESUMO,
    TEMPO_LIMITE_COUNTDOWN_ROTINA,
)
from app.bots.bot_replicacao_aud_d1 import (
    _executar_planejamento,
    _normalizar_settings_replicacao,
    _reset_progress_state,
    _set_status,
    executar_bot_replicacao,
    parar_event,
)
from app.bots.replicacao_d1_db_bridge import update_scheduler_state_db
from app.bots.replicacao_d1.planning_phases import PLANNING_DONE, PLANNING_START, plan_log

log = logging.getLogger("robots.replicacao_aud_d1_orchestration")

AGENDADOR_D1_STATE_ARQUIVO = "agendador_d1_state.json"
HORA_PLANEJAMENTO_DEFAULT = "07:00"
HORA_EXECUCAO_DEFAULT = "12:00"
# Janela do disparo: qualquer segundo dentro do minuto HH:MM (ex.: 10:41:00–10:41:59).
TOLERANCIA_SEGUNDOS_APOS_HORARIO = 60

_HORA_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")
_SCHEDULER_RUNTIME: dict[str, int | bool] = {}


def parse_hora(raw: str, *, default: Tuple[int, int] = (7, 0)) -> Tuple[int, int]:
    """Converte 'HH:MM' em (hora, minuto). Retorna default se inválido."""
    text = str(raw or "").strip()
    match = _HORA_RE.match(text)
    if not match:
        return default
    hora = int(match.group(1))
    minuto = int(match.group(2))
    if hora < 0 or hora > 23 or minuto < 0 or minuto > 59:
        return default
    return hora, minuto


def format_hora(hora: int, minuto: int) -> str:
    return f"{int(hora):02d}:{int(minuto):02d}"


def normalizar_hora_config(raw: str, *, default: str = HORA_PLANEJAMENTO_DEFAULT) -> str:
    hora, minuto = parse_hora(raw, default=parse_hora(default))
    return format_hora(hora, minuto)


def caminho_state_agendador(settings: Optional[dict] = None) -> Path:
    settings = settings or {}
    base = str(settings.get("replicacao_config_base", "") or "").strip()
    if base:
        return Path(base).parent / "resumo" / AGENDADOR_D1_STATE_ARQUIVO
    return PASTA_REPLICACAO_AUD_D1_RESUMO / AGENDADOR_D1_STATE_ARQUIVO


def estado_vazio(hoje: str) -> dict:
    return {
        "data": hoje,
        "run_id_planejamento": "",
        "planejamento_ok": False,
        "planejamento_tentado": False,
        "execucao_ok": False,
    }


def carregar_state_agendador(settings: Optional[dict] = None) -> dict:
    path = caminho_state_agendador(settings)
    hoje = _hoje_iso()
    if not path.exists():
        return estado_vazio(hoje)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return estado_vazio(hoje)
        if str(data.get("data", "")) != hoje:
            return estado_vazio(hoje)
        return {
            "data": hoje,
            "run_id_planejamento": str(data.get("run_id_planejamento", "") or "").strip(),
            "planejamento_ok": bool(data.get("planejamento_ok")),
            "planejamento_tentado": bool(data.get("planejamento_tentado")),
            "execucao_ok": bool(data.get("execucao_ok")),
        }
    except Exception as exc:
        log.warning("Falha ao ler state do agendador D-1: %s", exc)
        return estado_vazio(hoje)


def salvar_state_agendador(state: dict, settings: Optional[dict] = None) -> Path:
    from app.bots.replicacao_d1.atomic import write_json_atomic

    path = caminho_state_agendador(settings)
    write_json_atomic(path, state)
    return path


def _hoje_iso() -> str:
    return datetime.now(_get_tz_br()).date().isoformat()


def _horario_hoje(hora: int, minuto: int) -> datetime:
    agora = datetime.now(_get_tz_br())
    return agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)


def _proxima_ocorrencia(hora: int, minuto: int) -> datetime:
    agora = datetime.now(_get_tz_br())
    alvo = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if alvo <= agora:
        alvo += timedelta(days=1)
    return alvo


def _publicar_heartbeat(*, status: str = "running", run_id: str = "", force: bool = False) -> None:
    if not _SCHEDULER_RUNTIME or not _SCHEDULER_RUNTIME.get("publish"):
        return
    update_scheduler_state_db(
        enabled=status not in {"disabled", "stopped"},
        status=status,
        next_planning_at=_proxima_ocorrencia(
            _SCHEDULER_RUNTIME["h_plan"], _SCHEDULER_RUNTIME["m_plan"]
        ),
        next_execution_at=_proxima_ocorrencia(
            _SCHEDULER_RUNTIME["h_exec"], _SCHEDULER_RUNTIME["m_exec"]
        ),
        run_id=run_id,
        force=force,
    )


def esta_no_minuto_alvo(agora: datetime, hora: int, minuto: int) -> bool:
    """True em qualquer segundo do minuto agendado (ex.: 10:41:00 até 10:41:59)."""
    return int(agora.hour) == int(hora) and int(agora.minute) == int(minuto)


def deve_executar_imediato(hora: int, minuto: int) -> bool:
    """True se estamos no minuto alvo ou se o horário de hoje já passou (catch-up no dia)."""
    agora = datetime.now(_get_tz_br())
    if esta_no_minuto_alvo(agora, hora, minuto):
        return True
    alvo = _horario_hoje(hora, minuto)
    if agora >= alvo:
        # Catch-up: ainda pode executar no mesmo dia após o minuto alvo.
        return (agora - alvo).total_seconds() < 86400
    return False


def _iniciar_agendador_com_ciclo_limpo(
    settings: Optional[dict],
    *,
    forcar_novo_ciclo: bool = True,
) -> None:
    """Sempre começa um ciclo novo: execucao_ok/planejamento_ok não bloqueiam reexecução."""
    hoje = _hoje_iso()
    anterior = carregar_state_agendador(settings)
    if forcar_novo_ciclo and (
        anterior.get("planejamento_ok")
        or anterior.get("planejamento_tentado")
        or anterior.get("execucao_ok")
        or anterior.get("run_id_planejamento")
    ):
        log.info(
            "Agendador D-1 | state anterior ignorado para nova execução | "
            "planejamento_ok=%s execucao_ok=%s run_id=%s",
            anterior.get("planejamento_ok"),
            anterior.get("execucao_ok"),
            anterior.get("run_id_planejamento") or "",
        )
    if forcar_novo_ciclo or str(anterior.get("data", "")) != hoje:
        salvar_state_agendador(estado_vazio(hoje), settings)


def _settings_base(settings: Optional[dict]) -> dict:
    base = deepcopy(settings or {})
    base.pop("apenas_planejamento", None)
    base.pop("gerar_novo_plano", None)
    base.pop("run_id", None)
    return _normalizar_settings_replicacao(base)


def _settings_planejamento(settings: Optional[dict]) -> dict:
    cfg = _settings_base(settings)
    cfg["apenas_planejamento"] = True
    cfg["gerar_novo_plano"] = True
    return cfg


def _settings_execucao(settings: Optional[dict], run_id: str) -> dict:
    cfg = _settings_base(settings)
    cfg["apenas_planejamento"] = False
    cfg["gerar_novo_plano"] = False
    cfg["run_id"] = str(run_id or "").strip()
    cfg["execucao_agendada"] = True
    return cfg


def _executar_fase_planejamento(settings: Optional[dict]) -> Tuple[bool, str]:
    try:
        _reset_progress_state()
        cfg = _settings_planejamento(settings)
        plan_log(
            log,
            logging.INFO,
            "Agendador: fase planejamento",
            run_id=str(cfg.get("run_id") or ""),
            phase=PLANNING_START,
            trigger="scheduler",
        )
        plano = _executar_planejamento(cfg)
        run_id = str(plano.run_id or "").strip()
        if not run_id:
            _set_status("Erro no planejamento agendado: run_id vazio")
            return False, ""
        plan_log(
            log,
            logging.INFO,
            "Agendador: planejamento OK",
            run_id=run_id,
            phase=PLANNING_DONE,
            trigger="scheduler",
        )
        log.info("Agendador D-1 | planejamento OK | run_id=%s", run_id)
        return True, run_id
    except Exception as exc:
        log.exception("Agendador D-1 | falha no planejamento")
        _set_status(f"Erro no planejamento agendado: {exc}")
        return False, ""


def _executar_fase_execucao(settings: Optional[dict], run_id: str) -> bool:
    if not run_id:
        _set_status("Erro na execução agendada: run_id do planejamento ausente")
        return False
    try:
        _reset_progress_state()
        from app.bots.replicacao_d1.domain import RunStatus

        result = executar_bot_replicacao(_settings_execucao(settings, run_id))
        if result is None:
            log.warning("Agendador D-1 | execução sem resultado estruturado | run_id=%s", run_id)
            return False
        ok = result.status == RunStatus.COMPLETED
        log.info(
            "Agendador D-1 | execução BRFlow | run_id=%s | status=%s | ok=%s",
            run_id,
            result.status.value,
            ok,
        )
        if not ok:
            _set_status(
                f"Execução D-1 {result.status.value} | run_id={run_id} | "
                f"ok={result.workflows_success} falha={result.workflows_failed}"
            )
        return ok
    except Exception as exc:
        log.exception("Agendador D-1 | falha na execução BRFlow")
        _set_status(f"Erro na execução agendada: {exc}")
        return False


def _aguardar_horario(
    hora: int,
    minuto: int,
    *,
    rotulo: str,
    forcar_proximo_dia: bool = False,
) -> bool:
    """Aguarda até o horário de hoje ou do próximo dia. Retorna False se cancelado.

    Dispara em qualquer segundo do minuto alvo (ex.: 10:41:00–10:41:59), não só em :00.
    """
    tz_br = _get_tz_br()
    rotulo_fmt = format_hora(hora, minuto)

    while not parar_event.is_set():
        agora = datetime.now(tz_br)
        _publicar_heartbeat()

        # Sem forçar amanhã: qualquer segundo do minuto HH:MM dispara.
        if not forcar_proximo_dia and esta_no_minuto_alvo(agora, hora, minuto):
            _set_status(f"Horário de {rotulo} alcançado ({rotulo_fmt})")
            return True

        horario_execucao = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)

        if forcar_proximo_dia:
            # Próxima ocorrência: se já estamos no/após o minuto de hoje, vai para amanhã.
            if agora >= horario_execucao:
                horario_execucao += timedelta(days=1)
        elif agora > horario_execucao:
            # Após o minuto alvo (ex.: 10:42+): usa janela residual ou agenda amanhã.
            atraso = (agora - horario_execucao).total_seconds()
            if atraso < TOLERANCIA_SEGUNDOS_APOS_HORARIO:
                _set_status(f"Horário de {rotulo} alcançado ({rotulo_fmt})")
                return True
            horario_execucao += timedelta(days=1)

        diferenca = (horario_execucao - agora).total_seconds()
        if diferenca <= 1:
            _set_status(f"Horário de {rotulo} alcançado ({rotulo_fmt})")
            return True

        horas = int(diferenca // 3600)
        mins = int((diferenca % 3600) // 60)
        segs = int(diferenca % 60)
        _set_status(
            f"Aguardando {rotulo} às {rotulo_fmt} - Faltam {horas:02d}h:{mins:02d}m:{segs:02d}s"
        )

        # Sleep curto perto do alvo para não “pular” o minuto HH:MM.
        if diferenca <= TEMPO_LIMITE_COUNTDOWN_ROTINA:
            intervalo = min(INTERVALO_ATUALIZACAO_FINAL_ROTINA, max(1, int(diferenca)))
        else:
            intervalo = INTERVALO_ATUALIZACAO_COUNTDOWN_ROTINA
        parar_event.wait(intervalo)

    return False


def _aguardar_proximo_ciclo(hora_planejamento: int, minuto_planejamento: int) -> bool:
    """Aguarda até o horário de planejamento do próximo dia."""
    while not parar_event.is_set():
        if _aguardar_horario(
            hora_planejamento,
            minuto_planejamento,
            rotulo="planejamento",
            forcar_proximo_dia=True,
        ):
            return True
        parar_event.wait(30)
    return False


def resolver_horarios_agendamento(settings: Optional[dict]) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    from app.bots.replicacao_d1_db_bridge import resolve_agendamento_settings

    settings = resolve_agendamento_settings(settings or {})
    hora_plan = normalizar_hora_config(
        str(settings.get("agendamento_hora_planejamento", HORA_PLANEJAMENTO_DEFAULT)),
        default=HORA_PLANEJAMENTO_DEFAULT,
    )
    hora_exec = normalizar_hora_config(
        str(settings.get("agendamento_hora_execucao", HORA_EXECUCAO_DEFAULT)),
        default=HORA_EXECUCAO_DEFAULT,
    )
    return parse_hora(hora_plan), parse_hora(hora_exec)


def _scheduler_lock_path() -> Path:
    PASTA_REPLICACAO_AUD_D1_RESUMO.mkdir(parents=True, exist_ok=True)
    return PASTA_REPLICACAO_AUD_D1_RESUMO / "agendador_d1.lock"


def _acquire_scheduler_lock() -> tuple[int | None, Path]:
    path = _scheduler_lock_path()
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        return fd, path
    except FileExistsError:
        log.warning("Agendador D-1 já em execução (lock %s)", path)
        return None, path


def _release_scheduler_lock(fd: int | None, path: Path) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def start_agendado(settings=None):
    """Loop diário: planejamento no horário A, execução BRFlow no horário B."""
    settings = settings or {}
    lock_fd, lock_path = _acquire_scheduler_lock()
    if lock_fd is None:
        _set_status("Agendador D-1: outra instância já está ativa")
        return

    (h_plan, m_plan), (h_exec, m_exec) = resolver_horarios_agendamento(settings)
    _SCHEDULER_RUNTIME.update(
        {
            "h_plan": h_plan,
            "m_plan": m_plan,
            "h_exec": h_exec,
            "m_exec": m_exec,
            "publish": bool(settings.get("fonte_banco_ativa", False)),
        }
    )
    plan_fmt = format_hora(h_plan, m_plan)
    exec_fmt = format_hora(h_exec, m_exec)

    _set_status(
        f"Agendador D-1 ativo | planejamento {plan_fmt} | execução {exec_fmt}"
    )
    log.info(
        "Agendador D-1 iniciado | planejamento=%s | execução=%s",
        plan_fmt,
        exec_fmt,
    )
    # Reiniciar o processo não equivale a solicitar nova execução. A UI pode
    # usar reexecucao_manual=True para iniciar explicitamente outro ciclo.
    _iniciar_agendador_com_ciclo_limpo(
        settings,
        forcar_novo_ciclo=bool(settings.get("reexecucao_manual", False)),
    )
    _publicar_heartbeat(force=True)

    while not parar_event.is_set():
        try:
            state = carregar_state_agendador(settings)

            if not state.get("planejamento_ok") and not state.get("planejamento_tentado"):
                if deve_executar_imediato(h_plan, m_plan):
                    ok, run_id = _executar_fase_planejamento(settings)
                    state["planejamento_ok"] = ok
                    state["planejamento_tentado"] = True
                    state["run_id_planejamento"] = run_id if ok else ""
                    state["execucao_ok"] = False
                    salvar_state_agendador(state, settings)
                    if not ok:
                        _set_status(
                            f"Planejamento falhou - execução das {exec_fmt} cancelada neste ciclo"
                        )
                else:
                    if not _aguardar_horario(h_plan, m_plan, rotulo="planejamento"):
                        break
                    if parar_event.is_set():
                        break
                    ok, run_id = _executar_fase_planejamento(settings)
                    state = carregar_state_agendador(settings)
                    state["planejamento_ok"] = ok
                    state["planejamento_tentado"] = True
                    state["run_id_planejamento"] = run_id if ok else ""
                    state["execucao_ok"] = False
                    salvar_state_agendador(state, settings)
                    if not ok:
                        _set_status(
                            f"Planejamento falhou - execução das {exec_fmt} cancelada neste ciclo"
                        )
                    continue

            if (
                state.get("planejamento_tentado")
                and not state.get("planejamento_ok")
                and not state.get("execucao_ok")
            ):
                # Falha de planejamento: libera novo ciclo (não trava o dia).
                _set_status(
                    f"Planejamento falhou — aguardando próximo horário ({plan_fmt}) para tentar de novo"
                )
                salvar_state_agendador(estado_vazio(_hoje_iso()), settings)
                if not _aguardar_horario(h_plan, m_plan, rotulo="planejamento"):
                    break
                continue

            if state.get("planejamento_ok") and not state.get("execucao_ok"):
                run_id = str(state.get("run_id_planejamento", "") or "").strip()
                if deve_executar_imediato(h_exec, m_exec):
                    ok = _executar_fase_execucao(settings, run_id)
                    if ok:
                        state["execucao_ok"] = True
                        salvar_state_agendador(state, settings)
                        _set_status(f"Ciclo concluído (run_id={run_id})")
                    else:
                        # Não trava o dia: limpa state e aguarda próximo planejamento.
                        _set_status(
                            f"Execução BRFlow falhou (run_id={run_id}) — "
                            f"aguardando próximo ciclo ({plan_fmt}); Start reexecuta agora"
                        )
                        salvar_state_agendador(estado_vazio(_hoje_iso()), settings)
                        if not _aguardar_proximo_ciclo(h_plan, m_plan):
                            break
                    continue
                else:
                    if not _aguardar_horario(h_exec, m_exec, rotulo="execução BRFlow"):
                        break
                    if parar_event.is_set():
                        break
                    state = carregar_state_agendador(settings)
                    run_id = str(state.get("run_id_planejamento", "") or "").strip()
                    ok = _executar_fase_execucao(settings, run_id)
                    if ok:
                        state["execucao_ok"] = True
                        salvar_state_agendador(state, settings)
                        _set_status(f"Ciclo concluído (run_id={run_id})")
                    else:
                        _set_status(
                            f"Execução BRFlow falhou (run_id={run_id}) — "
                            f"aguardando próximo ciclo ({plan_fmt}); Start reexecuta agora"
                        )
                        salvar_state_agendador(estado_vazio(_hoje_iso()), settings)
                        if not _aguardar_proximo_ciclo(h_plan, m_plan):
                            break
                    continue

            if state.get("planejamento_ok") and state.get("execucao_ok"):
                # Ciclo OK: limpa state e agenda o próximo disparo (sem travar reexecução manual).
                _set_status(
                    f"Ciclo concluído — aguardando próximo planejamento ({plan_fmt})"
                )
                salvar_state_agendador(estado_vazio(_hoje_iso()), settings)
                if not _aguardar_proximo_ciclo(h_plan, m_plan):
                    break
                continue

            parar_event.wait(30)

        except Exception as exc:
            log.exception("Erro no agendador D-1: %s", exc)
            _set_status(f"Erro no agendador D-1: {exc}")
            _publicar_heartbeat(status="error", force=True)
            if not parar_event.is_set():
                parar_event.wait(300)

    log.info("Agendador D-1 finalizado")
    _set_status("Agendador D-1 finalizado")
    _publicar_heartbeat(status="stopped", force=True)
    _SCHEDULER_RUNTIME.clear()
    _release_scheduler_lock(lock_fd, lock_path)
