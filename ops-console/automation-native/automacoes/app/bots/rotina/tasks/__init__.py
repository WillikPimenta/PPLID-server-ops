"""Registry de tarefas da rotina."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from app.bots.rotina.constants import (
    TASK_AUDITORIA_ETAPAS,
    TASK_AUDITORIA_REPLICADOS_D1,
    TASK_CONFER_PRODUCAO,
    TASK_IRREGULARIDADE,
    TASK_LOG_EVENTOS,
    TASK_MONITOR_EVENTOS,
    TASK_MONITOR_UNIFICADO,
    TASK_PROD_UNIFICADO,
    TASK_PRODUTIVIDADE_D1,
    TASK_ROTINAS_1d,
    TASK_ROTINAS_2d,
    TASK_ROTINAS_3d,
    TASK_TO_ROTINA_DESCRICAO,
)
from app.bots.rotina.io import _limpar_downloads_temp_inicio, _obter_data_base_execucao
from app.bots.rotina.state import _registrar_erro, _registrar_status_tarefa
from . import auditoria, confer, detalhado, irregularidade, monitor, produtividade, unificados

log = logging.getLogger("robots.bot_rotina")


@dataclass
class TaskResult:
    ok: bool = True
    should_break: bool = False


def _data_d1_ref() -> str:
    return (_obter_data_base_execucao() - timedelta(days=1)).strftime("%Y%m%d")


def _run_rotinas(drv, _ctx, task_id: str) -> TaskResult:
    detalhado._baixar_e_combinar_rotinas(
        drv, descricao=TASK_TO_ROTINA_DESCRICAO[task_id]
    )
    return TaskResult()


def _run_produtividade(drv, _ctx, _task_id: str) -> TaskResult:
    produtividade._baixar_produtividade_d1(
        drv, descricao=TASK_TO_ROTINA_DESCRICAO[TASK_PRODUTIVIDADE_D1]
    )
    return TaskResult()


def _run_auditoria_replicados(drv, _ctx, _task_id: str) -> TaskResult:
    auditoria._baixar_auditoria_replicados_d1(
        drv, descricao=TASK_TO_ROTINA_DESCRICAO[TASK_AUDITORIA_REPLICADOS_D1]
    )
    return TaskResult()


def _run_auditoria_etapas(drv, _ctx, _task_id: str) -> TaskResult:
    auditoria._baixar_auditoria_etapas(
        drv, descricao=TASK_TO_ROTINA_DESCRICAO[TASK_AUDITORIA_ETAPAS]
    )
    return TaskResult()


def _run_irregularidade(drv, _ctx, _task_id: str) -> TaskResult:
    ok = irregularidade._baixar_irregularidade_ged(drv)
    return TaskResult(ok=ok)


def _run_monitor_eventos(drv, _ctx, _task_id: str) -> TaskResult:
    sucesso = monitor._baixar_monitor_eventos_d1(drv)
    if not sucesso:
        return TaskResult(ok=False)
    return TaskResult()


def _run_confer_producao(drv, _ctx, _task_id: str) -> TaskResult:
    sucesso = confer._baixar_relatorio_producao_confer(drv)
    if not sucesso:
        log.warning("[Confer] Falha na extração de produção; encerrando as tarefas restantes do dia.")
        return TaskResult(ok=False, should_break=True)
    return TaskResult()


def _run_log_eventos(drv, _ctx, _task_id: str) -> TaskResult:
    _limpar_downloads_temp_inicio("Extração Log Eventos D-1")
    confer.log_eventos(drv)
    unificados._juntar_confer_prod_tratados_dia(_data_d1_ref())
    return TaskResult()


def _run_prod_unificado(_drv, _ctx, _task_id: str) -> TaskResult:
    resultado = unificados._juntar_confer_prod_tratados_dia(_data_d1_ref())
    if resultado:
        _registrar_status_tarefa(TASK_PROD_UNIFICADO, True)
    else:
        _registrar_erro("⚠️ Prod unificado: falha na união confer/prod")
        _registrar_status_tarefa(TASK_PROD_UNIFICADO, False, "falha na união")
    return TaskResult(ok=bool(resultado))


def _run_monitor_unificado(_drv, _ctx, _task_id: str) -> TaskResult:
    resultado = unificados._juntar_monitor_tratado_com_monitor_confer_dia(_data_d1_ref())
    if resultado:
        _registrar_status_tarefa(TASK_MONITOR_UNIFICADO, True)
    else:
        _registrar_erro("⚠️ Monitor unificado: falha na união monitor/confer")
        _registrar_status_tarefa(TASK_MONITOR_UNIFICADO, False, "falha na união")
    return TaskResult(ok=bool(resultado))


TASK_REGISTRY = {
    TASK_ROTINAS_1d: _run_rotinas,
    TASK_ROTINAS_2d: _run_rotinas,
    TASK_ROTINAS_3d: _run_rotinas,
    TASK_PRODUTIVIDADE_D1: _run_produtividade,
    TASK_AUDITORIA_REPLICADOS_D1: _run_auditoria_replicados,
    TASK_AUDITORIA_ETAPAS: _run_auditoria_etapas,
    TASK_IRREGULARIDADE: _run_irregularidade,
    TASK_MONITOR_EVENTOS: _run_monitor_eventos,
    TASK_CONFER_PRODUCAO: _run_confer_producao,
    TASK_LOG_EVENTOS: _run_log_eventos,
    TASK_PROD_UNIFICADO: _run_prod_unificado,
    TASK_MONITOR_UNIFICADO: _run_monitor_unificado,
}


def execute_task(drv, task_id: str, settings=None) -> TaskResult:
    handler = TASK_REGISTRY.get(task_id)
    if handler is None:
        log.warning(f"Tarefa desconhecida: {task_id}")
        return TaskResult(ok=False)
    return handler(drv, settings, task_id)
