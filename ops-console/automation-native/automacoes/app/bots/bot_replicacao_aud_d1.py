"""Bot de replicação D-1 (volumetria diária do parquet) no BRFlow.

Fluxo básico:
1) Abre Chrome
2) Faz login no Okta
3) Pesquisa por BRFlow
4) Alterna para a janela BRFlow
5) Acessa o menu "Replicação de Protocolos"
"""

import os
import pandas as pd
import sys
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.bots.replicacao_d1.domain import ReplicationRunResult, RunStatus
from app.bots.replicacao_d1.execution import (
    build_replication_run_result,
    collect_workflow_results,
    run_status_allows_consumo,
    run_status_allows_retention,
    write_run_manifest,
)
from app.bots.replicacao_d1.settings import (
    get_credentials as _get_credentials,
    get_headless as _get_headless,
    normalize_replicacao_settings as _normalizar_settings_replicacao,
    parse_bool_setting as _parse_bool_setting,
    refiltrar_apos_salvar,
)
from app.bots.replicacao_d1.observability import log_event
from app.bots.replicacao_d1.planning_phases import (
    PLANNING_DONE,
    PLANNING_ERROR,
    PLANNING_START,
    plan_log,
    set_plan_event_callback,
)
from app.bots.replicacao_d1.selenium.navigation import (
    abrir_brflow,
    acessar_menu_replicacao,
    create_chrome_driver as _create_chrome_driver,
    fazer_login_okta,
)
from app.bots.replicacao_d1.selenium.text_utils import (
    sanitizar_sufixo_screenshot as _sanitizar_sufixo_screenshot,
    xpath_escape_texto as _xpath_escape_texto,
)
from app.bots.replicacao_d1.selenium.workflow_upload import (
    configurar_workflow_replicacao as _configurar_workflow_replicacao_impl,
    csv_protocolos_vazio as _csv_protocolos_vazio,
)
from app.bots.replicacao_d1.selenium.brflow_workflows import (
    BrflowWorkflowCallbacks,
    clicar_pesquisar_replicacao as _clicar_pesquisar_replicacao_impl,
)
from app.bots.replicacao_d1.selenium.filtros_pesquisa import (
    preencher_replicacao_filtros as _preencher_replicacao_filtros_impl,
    validar_filtros_replicacao_antes_pesquisar as _validar_filtros_impl,
    aguardar_formulario_filtros_replicacao as _aguardar_formulario_filtros_replicacao,
)
from app.bots.replicacao_d1.selenium.listagem import (
    refiltrar_listagem_ativos_apos_salvar as _refiltrar_listagem_ativos_impl,
)
from selenium.common.exceptions import TimeoutException, WebDriverException

from app.infrastructure.selenium_helpers import take_error_screenshot
from app.core.common import safe_close_driver, MetricsContext
from app.config import (
    PASTA_REPLICACAO_AUD_D1_BASE,
    REPLICACAO_AUD_D1_LIMPAR_PLANOS_APOS_CONCLUSAO_DEFAULT,
    REPLICACAO_LISTAGEM_ESPERA_SEG,
)
from app.bots.replicacao_aud_planning import eh_fila_documentoscopia_31
from app.bots.replicacao_aud_d1_planning import (
    PlanoReplicacao,
    _normalizar_workflow,
    aplicar_politica_retencao_planos_d1,
    atualizar_relatorio_excel,
    exportar_parquet_consolidado_d1,
    carregar_estado_execucao,
    carregar_plano_por_run_id,
    gerar_plano_replicacao_d1,
    notificar_replicacao_d1_db_sync,
    resolver_ultimo_run_id,
)
from app.bots.meta_cliente_mensal import registrar_consumo_meta_plano_concluido

from app.core.bot_runtime import BotRuntime

log = logging.getLogger("robots.bot_replicacao_aud_d1")

_runtime = BotRuntime(mode="replicacao_auditoria_d1")
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
parar_event = _runtime.parar_event
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress
_reset_progress_state = _runtime.reset_progress_state
_plan_progress_context = threading.local()


def _publish_plan_event_to_runtime(event: dict) -> None:
    """Converte o contrato estruturado em callbacks legados consumidos pelo runner."""
    message = str(event.get("message") or event.get("phase_label") or "Planejamento")
    progress = event.get("progress_pct")
    if progress is not None:
        raw_progress = int(progress)
        plan_only = bool(getattr(_plan_progress_context, "plan_only", False))
        overall_progress = raw_progress if plan_only else 5 + round(raw_progress * 0.10)
        _set_progress(overall_progress, message)
    _set_status(message)
    if event.get("state") not in {"started", "completed", "skipped", "warning", "failed"}:
        return
    if not str(event.get("run_id") or "").strip():
        return
    try:
        from app.bots.replicacao_d1_db_bridge import append_planning_event_db

        append_planning_event_db(event)
    except Exception:
        log.debug("Falha ao persistir evento de fase do planejamento", exc_info=True)


set_plan_event_callback(_publish_plan_event_to_runtime)



def _preencher_replicacao_filtros(driver, settings: Optional[dict] = None):
    _preencher_replicacao_filtros_impl(
        driver,
        settings,
        set_status=_set_status,
        take_screenshot=take_error_screenshot,
    )


def _validar_filtros_replicacao_antes_pesquisar(driver, settings: Optional[dict] = None):
    return _validar_filtros_impl(driver, settings)


def _refiltrar_listagem_ativos_apos_salvar(
    driver,
    settings: Optional[dict] = None,
    motivo: str = "após salvar",
) -> None:
    _refiltrar_listagem_ativos_impl(
        driver,
        settings,
        motivo=motivo,
        set_status=_set_status,
        listagem_espera_seg=REPLICACAO_LISTAGEM_ESPERA_SEG,
    )


def _preparar_listagem_pos_paginacao(driver, settings: Optional[dict] = None) -> None:
    from app.bots.replicacao_d1.selenium import listagem as _listagem_mod

    _listagem_mod.preparar_listagem_pos_paginacao(
        driver,
        settings,
        set_status=_set_status,
        listagem_espera_seg=REPLICACAO_LISTAGEM_ESPERA_SEG,
    )


def _clicar_editar_por_workflow(
    driver,
    workflow_alvo,
    apenas_ativo: bool = False,
    settings: Optional[dict] = None,
    indice_listagem=None,
    nome_regra=None,
):
    from app.bots.replicacao_d1.selenium import listagem as _listagem_mod

    _listagem_mod.clicar_editar_por_workflow(
        driver,
        workflow_alvo,
        apenas_ativo=apenas_ativo,
        settings=settings,
        refilter_ativos=_refiltrar_listagem_ativos_apos_salvar,
        xpath_escape=_xpath_escape_texto,
        indice_listagem=indice_listagem,
        nome_regra=nome_regra,
    )


def _configurar_workflow_replicacao(
    driver,
    workflow: str,
    caminho_csv: Path,
    settings: Optional[dict] = None,
    workflow_brflow: Optional[str] = None,
    fila: Optional[str] = None,
):
    return _configurar_workflow_replicacao_impl(
        driver,
        workflow,
        caminho_csv,
        settings=settings,
        workflow_brflow=workflow_brflow,
        fila=fila,
        set_status=_set_status,
        refilter_after_save=(
            _refiltrar_listagem_ativos_apos_salvar
            if refiltrar_apos_salvar(settings)
            else None
        ),
        eh_fila_doc_31=eh_fila_documentoscopia_31,
        take_screenshot=take_error_screenshot,
        sanitizar_sufixo=_sanitizar_sufixo_screenshot,
    )


def _configurar_workflow_qtd(
    driver,
    workflow: str,
    qtd_calculada: int,
    settings: Optional[dict] = None,
    workflow_brflow: Optional[str] = None,
    fila: Optional[str] = None,
) -> str:
    from app.bots.replicacao_d1.selenium.painel_qtd import configurar_workflow_qtd_replicada

    return configurar_workflow_qtd_replicada(
        driver,
        workflow,
        qtd_calculada,
        settings=settings,
        workflow_brflow=workflow_brflow,
        fila=fila,
        set_status=_set_status,
        take_screenshot=take_error_screenshot,
        sanitizar_sufixo=_sanitizar_sufixo_screenshot,
    )


def _brflow_workflow_callbacks() -> BrflowWorkflowCallbacks:
    return BrflowWorkflowCallbacks(
        set_status=_set_status,
        set_progress=_set_progress,
        parar_event=parar_event,
        validar_filtros=_validar_filtros_replicacao_antes_pesquisar,
        preencher_filtros=_preencher_replicacao_filtros,
        preparar_listagem=_preparar_listagem_pos_paginacao,
        refilter_listagem=_refiltrar_listagem_ativos_apos_salvar,
        clicar_editar=_clicar_editar_por_workflow,
        configurar_workflow=_configurar_workflow_replicacao,
        configurar_workflow_qtd=_configurar_workflow_qtd,
        take_screenshot=take_error_screenshot,
        csv_protocolos_vazio=_csv_protocolos_vazio,
        normalizar_workflow=_normalizar_workflow,
    )


def _clicar_pesquisar_replicacao(
    driver,
    plano: PlanoReplicacao,
    estado: Optional[dict] = None,
    settings: Optional[dict] = None,
):
    _clicar_pesquisar_replicacao_impl(
        driver, plano, estado, settings, _brflow_workflow_callbacks()
    )


def _resolver_plano(settings: dict) -> tuple[PlanoReplicacao, Optional[dict]]:
    """Gera plano novo ou carrega execução existente pelo run_id."""
    run_id = str(settings.get("run_id", "") or "").strip()
    gerar_novo = bool(settings.get("gerar_novo_plano", False))

    from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa, latest_plan_run_id_db

    if is_fonte_banco_ativa(settings) and not gerar_novo:
        run_id = run_id or str(latest_plan_run_id_db() or "")
        if not run_id:
            raise RuntimeError("Nenhum plano D-1 foi encontrado no banco.")
        settings = {**settings, "run_id": run_id}
        plano = carregar_plano_por_run_id(run_id, settings)
        return plano, carregar_estado_execucao(run_id)

    if not run_id:
        run_id = resolver_ultimo_run_id() or ""

    if run_id and not gerar_novo and carregar_estado_execucao(run_id):
        log.info("Carregando plano existente run_id=%s", run_id)
        plano = carregar_plano_por_run_id(run_id, settings)
        estado = carregar_estado_execucao(run_id)
        return plano, estado

    if run_id and not gerar_novo:
        settings = {**settings, "run_id": run_id}

    plano = gerar_plano_replicacao_d1(settings=settings)
    estado = carregar_estado_execucao(plano.run_id)
    return plano, estado


def _executar_planejamento(settings=None) -> PlanoReplicacao:
    """Etapa 1: gera CSVs de protocolos por workflow (sem abrir navegador)."""
    settings = _normalizar_settings_replicacao(settings)
    _set_status("Planejamento D-1: parquet (volumetria) + Default.xlsx + Categoria")
    _set_progress(5, "Gerando plano de replicação")
    gerar_novo = bool(settings.get("gerar_novo_plano", False))
    run_id = str(settings.get("run_id", "") or "").strip()
    from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa

    plan_log(
        log,
        logging.INFO,
        "Wrapper planejamento iniciado",
        run_id=run_id,
        phase=PLANNING_START,
        gerar_novo_plano=gerar_novo,
        fonte_banco_ativa=is_fonte_banco_ativa(settings),
    )
    if run_id and not gerar_novo and carregar_estado_execucao(run_id):
        plan_log(
            log,
            logging.INFO,
            "Reutilizando plano existente",
            run_id=run_id,
            phase=PLANNING_START,
            action="reuse",
        )
        plano = carregar_plano_por_run_id(run_id, settings)
    else:
        plano = gerar_plano_replicacao_d1(settings=settings)
    for workflow, path in plano.csv_paths.items():
        qtd = len(plano.protocolos_por_workflow.get(workflow, []))
        log.info("Plano | %s | %d protocolos | CSV: %s", workflow, qtd, path)
    for aviso in plano.warnings:
        log.warning("Plano | %s", aviso)
    if plano.workflows_sem_registro:
        log.warning(
            "Plano | %d workflow(s) sem D-1 (CSV fallback vazio): %s",
            len(plano.workflows_sem_registro),
            ", ".join(plano.workflows_sem_registro),
        )
    if plano.relatorio_excel_path:
        log.info("Plano | relatório Excel: %s", plano.relatorio_excel_path)
    if plano.estado_execucao_path:
        log.info("Plano | estado: %s", plano.estado_execucao_path)
    log.info(
        "Plano | run_id=%s | base=%s | protocolos=%s | resumo=%s",
        plano.run_id,
        PASTA_REPLICACAO_AUD_D1_BASE,
        plano.pasta_protocolos,
        plano.pasta_resumo,
    )
    total_protocolos = sum(len(v) for v in plano.protocolos_por_workflow.values())
    plan_log(
        log,
        logging.INFO,
        "Wrapper planejamento concluído",
        run_id=plano.run_id,
        phase=PLANNING_DONE,
        workflows=len(plano.workflows),
        protocols=total_protocolos,
        warnings=len(plano.warnings),
    )
    _set_progress(15, f"Plano gerado: {len(plano.workflows)} workflow(s)")
    _set_status(
        f"run_id={plano.run_id} | protocolos: {plano.pasta_protocolos.name} | "
        f"resumo: {plano.pasta_resumo.name}"
    )
    return plano


def executar_bot_replicacao(settings=None) -> ReplicationRunResult | None:
    _reset_progress_state()
    _set_status("Replicação D-1 (volumetria diária): iniciando")
    _set_progress(0, "Inicializando")
    settings = _normalizar_settings_replicacao(settings)
    _plan_progress_context.plan_only = bool(settings.get("apenas_planejamento"))
    started_at = datetime.now()
    run_id_hint = str(settings.get("run_id") or "").strip()
    log_event(log, logging.INFO, "execução iniciada", run_id=run_id_hint, phase="start")

    if settings.get("apenas_planejamento"):
        try:
            plano = _executar_planejamento(settings)
            estado = carregar_estado_execucao(plano.run_id) or {}
            workflow_results = collect_workflow_results(plano, estado)
            result = build_replication_run_result(
                plano.run_id,
                workflow_results,
                started_at=started_at,
                finished_at=datetime.now(),
            )
            result.status = RunStatus.PLANNED
            write_run_manifest(plano, result, settings)
            log.info(
                "Planejamento concluído (sem BRFlow) | run_id=%s | status=%s | protocolos=%s",
                plano.run_id,
                result.status.value,
                plano.pasta_protocolos,
            )
            _set_progress(100, "Planejamento concluído (sem BRFlow)")
            _set_status(f"Planejamento OK | run_id={plano.run_id} | status={result.status.value}")
            return result
        except Exception as exc:
            log.exception("Falha no planejamento da replicação")
            plan_log(
                log,
                logging.ERROR,
                "Falha no planejamento",
                run_id=str(settings.get("run_id", "") or ""),
                phase=PLANNING_ERROR,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            _set_status(f"Erro no planejamento: {exc}")
            run_id = str(settings.get("run_id", "") or "").strip()
            try:
                from app.bots.replicacao_d1_db_bridge import mark_run_failed_db

                mark_run_failed_db(run_id, error_summary=str(exc))
            except Exception:
                log.debug("Falha ao registrar erro de planejamento no banco", exc_info=True)
            result = build_replication_run_result(
                run_id or "unknown",
                [],
                started_at=started_at,
                finished_at=datetime.now(),
                planning_error=str(exc),
            )
            raise RuntimeError(f"Planejamento D-1 falhou: {exc}") from exc
        finally:
            parar_event.set()
            _plan_progress_context.plan_only = False

    drv = None
    estado = None
    plano = None
    database_only = False
    run_result: ReplicationRunResult | None = None
    try:
        plano, estado = _resolver_plano(settings)
        from app.bots.replicacao_d1_db_bridge import ensure_plan_approved_db, is_fonte_banco_ativa

        database_only = is_fonte_banco_ativa(settings)
        if database_only and not bool(settings.get("execucao_agendada")):
            ensure_plan_approved_db(plano.run_id)
    except Exception as exc:
        log.exception("Falha no planejamento da replicação")
        plan_log(
            log,
            logging.ERROR,
            "Falha no planejamento",
            run_id=str(settings.get("run_id", "") or ""),
            phase=PLANNING_ERROR,
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        _set_status(f"Erro no planejamento: {exc}")
        run_id = str(settings.get("run_id", "") or "").strip()
        try:
            from app.bots.replicacao_d1_db_bridge import mark_run_failed_db

            mark_run_failed_db(run_id, error_summary=str(exc))
        except Exception:
            log.debug("Falha ao registrar erro de planejamento no banco", exc_info=True)
        run_result = build_replication_run_result(
            run_id or "unknown",
            [],
            started_at=started_at,
            finished_at=datetime.now(),
            planning_error=str(exc),
        )
        _plan_progress_context.plan_only = False
        raise RuntimeError(f"Planejamento D-1 falhou: {exc}") from exc

    _plan_progress_context.plan_only = False

    headless = _get_headless(settings)
    matricula, senha = _get_credentials(settings)
    drv = _create_chrome_driver(headless=headless)

    with MetricsContext("bot_replicacao_aud") as exec_ctx:
        falhou = False
        cancelled = parar_event.is_set()
        try:
            if database_only:
                from app.bots.replicacao_d1_db_bridge import mark_run_started_db

                mark_run_started_db(plano.run_id)
            if not matricula or not senha:
                _set_status("Credenciais Okta não fornecidas")
                log.warning("Credenciais Okta não encontradas em settings ou variáveis de ambiente")

            fazer_login_okta(drv, matricula, senha, set_status=_set_status, set_progress=_set_progress)
            abrir_brflow(drv, set_status=_set_status, set_progress=_set_progress)
            acessar_menu_replicacao(
                drv,
                set_status=_set_status,
                set_progress=_set_progress,
                wait_filters=_aguardar_formulario_filtros_replicacao,
            )
            _clicar_pesquisar_replicacao(drv, plano, estado=estado, settings=settings)
            _set_progress(100, "Fluxo concluído")
            _set_status("Replicação de Auditoria: execução concluída")
        except (TimeoutException, WebDriverException):
            falhou = True
            log.exception("Erro Selenium durante execução do bot de replicação")
            take_error_screenshot(drv, "log_screenshots", "bot_replicacao_aud_selenium_erro")
        except Exception:
            falhou = True
            log.exception("Erro inesperado no bot de replicação")
            take_error_screenshot(drv, "log_screenshots", "bot_replicacao_aud_erro")
        finally:
            if plano:
                try:
                    estado_atual = carregar_estado_execucao(plano.run_id) or estado or {}
                    workflow_results = collect_workflow_results(plano, estado_atual)
                    run_result = build_replication_run_result(
                        plano.run_id,
                        workflow_results,
                        started_at=started_at,
                        finished_at=datetime.now(),
                    )
                    cancelled = cancelled or parar_event.is_set()
                    if cancelled and run_result.status != RunStatus.FAILED:
                        run_result.status = RunStatus.CANCELLED
                    if falhou and run_result.status == RunStatus.COMPLETED:
                        run_result.status = RunStatus.FAILED

                    from app.bots.replicacao_d1_db_bridge import (
                        close_run_result_db,
                        is_fonte_banco_ativa,
                    )

                    try:
                        write_run_manifest(plano, run_result, settings)
                    except Exception:
                        log.exception("Falha ao persistir manifesto/evento D-1 | run_id=%s", plano.run_id)
                        if not falhou:
                            falhou = True
                        run_result.status = RunStatus.FAILED

                    if not database_only:
                        atualizar_relatorio_excel(plano, estado_atual)
                        try:
                            rel_path = plano.relatorio_excel_path
                            df_plano_bi = pd.DataFrame()
                            if rel_path and Path(rel_path).exists():
                                df_plano_bi = pd.read_excel(rel_path, sheet_name="Plano", engine="openpyxl")
                            exportar_parquet_consolidado_d1(
                                plano,
                                estado_atual,
                                df_plano=df_plano_bi,
                                settings=settings,
                            )
                        except Exception:
                            log.exception("Falha ao exportar parquet consolidado D-1")
                        log.info("Relatório Excel atualizado: %s", plano.relatorio_excel_path)
                        notificar_replicacao_d1_db_sync(plano.run_id, plano.relatorio_excel_path)

                    if run_status_allows_consumo(run_result.status):
                        registrar_consumo_meta_plano_concluido(plano, estado_atual, settings=settings)
                    else:
                        log.info(
                            "Consumo mensal não registrado | run_id=%s | status=%s",
                            plano.run_id,
                            run_result.status.value,
                        )

                    if not database_only and bool(
                        settings.get(
                            "limpar_planos_apos_conclusao",
                            REPLICACAO_AUD_D1_LIMPAR_PLANOS_APOS_CONCLUSAO_DEFAULT,
                        )
                    ) and run_status_allows_retention(run_result.status):
                        aplicar_politica_retencao_planos_d1(
                            settings,
                            excluir_run_id=plano.run_id,
                            forcar_politica=True,
                        )
                    elif not run_status_allows_retention(run_result.status):
                        log.info(
                            "Retenção de planos adiada | run_id=%s | status=%s",
                            plano.run_id,
                            run_result.status.value,
                        )

                    if database_only:
                        close_run_result_db(run_result)

                    log_event(
                        log,
                        logging.INFO,
                        "run finalizado",
                        run_id=plano.run_id,
                        phase="close",
                        status=run_result.status.value,
                        ok=run_result.workflows_success,
                        falha=run_result.workflows_failed,
                    )
                    log.info(
                        "Run D-1 finalizado | run_id=%s | status=%s | ok=%d falha=%d pulado=%d",
                        plano.run_id,
                        run_result.status.value,
                        run_result.workflows_success,
                        run_result.workflows_failed,
                        run_result.workflows_skipped,
                    )
                    _set_status(
                        f"run_id={plano.run_id} | status={run_result.status.value} | "
                        f"ok={run_result.workflows_success} falha={run_result.workflows_failed} "
                        f"pulado={run_result.workflows_skipped}"
                    )
                except Exception:
                    falhou = True
                    log.exception("Falha ao finalizar run D-1 após Selenium")
            safe_close_driver(drv)
            if plano:
                from app.bots.replicacao_d1_db_bridge import cleanup_temporary_plan

                cleanup_temporary_plan(plano)
        if falhou:
            raise RuntimeError("Replicação D-1 finalizou com erro (ver log/screenshots)")
        return run_result


def start(settings=None):
    from app.bots.replicacao_d1_db_bridge import resolve_agendamento_settings

    settings = resolve_agendamento_settings(settings or {})
    parar_event.clear()
    if _parse_bool_setting(settings.get("agendamento_ativo")):
        from app.bots.replicacao_aud_d1_orchestration import start_agendado

        target = start_agendado
    else:
        target = executar_bot_replicacao

    def _target_com_resultado():
        try:
            return target(settings)
        except BaseException as exc:
            # ``threading.Thread`` apenas imprime a excecao no stderr. O runner
            # precisa deste atributo para devolver exit code de falha ao portal.
            threading.current_thread().execution_error = exc
            return None

    thread = threading.Thread(target=_target_com_resultado, daemon=True)
    thread.execution_error = None
    thread.start()
    return thread


def stop():
    parar_event.set()


def main():
    executar_bot_replicacao()


if __name__ == "__main__":
    main()

