# -*- coding: utf-8 -*-
"""Orquestração Selenium: pesquisar, processar workflows e upload CSV no BRFlow."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from threading import Event
from typing import Any, Callable, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.bots.meta_cliente_mensal import sincronizar_consumo_meta_run
from app.bots.replicacao_aud_d1_planning import (
    PlanoReplicacao,
    salvar_estado_execucao,
    workflow_nome_brflow,
)
from app.bots.replicacao_aud_planning import (
    agrupar_workflows_por_fila,
    eh_fila_redoc,
    resolver_filtros_brflow_por_fila,
)
from app.bots.replicacao_d1.selenium.listagem import (
    IndiceListagemBrflow,
    aguardar_listagem_replicacao_carregada,
    contar_linhas_ativas_regra,
    eh_workflow_apenas_inativo_brflow,
    forcar_paginacao_js,
    garantir_paginacao_listagem_replicacao,
    indexar_listagem_replicacao,
    indice_para_resumo,
    invalidar_entrada_indice,
    ler_info_paginador,
    ler_linhas_replicacao,
    ler_linhas_todas_paginas_replicacao,
    logar_workflow_ausente_diagnostico,
    resumir_linhas_por_workflow,
    workflow_ausente_listagem_brflow,
)
from app.bots.replicacao_d1.settings import (
    estado_batch_size,
    filtrar_workflow_destino_habilitado,
    indexar_parar_quando_plano_completo,
    listagem_modo_indice,
    paginacao_tamanho,
    paginacao_varrer_todas,
    upload_csv_vazio_habilitado,
)
from app.bots.replicacao_d1.state import resolve_csv_upload_workflow, update_workflow_state
from app.bots.replicacao_d1.domain import WorkflowActionResult
from app.bots.replicacao_d1.selenium.workflow_upload import SaveNotConfirmedError
from app.config import REPLICACAO_CSV_PLACEHOLDER_LIMPEZA
from app.infrastructure.selenium_helpers import click_element

log = logging.getLogger("robots.bot_replicacao_aud_d1")

STATUS_WORKFLOW_SKIP_RETOMADA = frozenset({"UPLOAD_OK", "SALVO_OK", "SEM_ALTERACAO", "INATIVO"})

SetStatusFn = Callable[[str], None]
SetProgressFn = Callable[[int, str], None]
ConfigurarWorkflowFn = Callable[..., None]
ConfigurarWorkflowQtdFn = Callable[..., str]
ClicarEditarFn = Callable[..., None]
RefilterFn = Callable[..., None]
ValidarFiltrosFn = Callable[..., Any]
PreencherFiltrosFn = Callable[..., None]
PrepararListagemFn = Callable[..., None]
TakeScreenshotFn = Callable[..., None]


def _modo_replicacao_workflow(
    plano: PlanoReplicacao,
    workflow: str,
    fila: str,
    estado: Optional[dict] = None,
) -> str:
    """Lê o modo congelado; fila é somente fallback para planos legados."""
    mapa = getattr(plano, "workflow_modo_replicacao", {})
    modo = mapa.get(workflow) if isinstance(mapa, dict) else None
    if not modo and estado:
        modo = (estado.get("workflows", {}).get(workflow, {}) or {}).get("modo")
    modo_normalizado = str(modo or "").strip().casefold()
    if modo_normalizado in {"protocolos", "qtd"}:
        return modo_normalizado
    return "qtd" if str(fila or "").strip().casefold() in {"bio", "redoc"} else "protocolos"


@dataclass
class BrflowWorkflowCallbacks:
    set_status: SetStatusFn
    set_progress: SetProgressFn
    parar_event: Event
    validar_filtros: ValidarFiltrosFn
    preencher_filtros: PreencherFiltrosFn
    preparar_listagem: PrepararListagemFn
    refilter_listagem: RefilterFn
    clicar_editar: ClicarEditarFn
    configurar_workflow: ConfigurarWorkflowFn
    configurar_workflow_qtd: ConfigurarWorkflowQtdFn
    take_screenshot: TakeScreenshotFn
    csv_protocolos_vazio: Callable[[Any], bool]
    normalizar_workflow: Callable[[str], str]


class EstadoPersistenciaBatch:
    """Persiste estado e sync meta em lotes para reduzir I/O."""

    def __init__(
        self,
        estado: Optional[dict],
        estado_path,
        plano: PlanoReplicacao,
        settings: dict,
    ):
        self.estado = estado
        self.estado_path = estado_path
        self.plano = plano
        self.settings = settings
        self.batch_size = estado_batch_size(settings)
        self.pending = 0

    def flush(self, *, force: bool = False, sync_meta: bool = True) -> None:
        if not self.estado:
            return
        run_id = str(self.estado.get("run_id") or "").strip()
        from app.bots.replicacao_aud_d1_planning import _DATABASE_ONLY_RUN_IDS

        db_only = run_id in _DATABASE_ONLY_RUN_IDS
        pode_persistir = bool(self.estado_path) or db_only
        if not pode_persistir:
            return
        if not force and self.pending < self.batch_size:
            return
        salvar_estado_execucao(self.estado, self.estado_path)
        if sync_meta:
            try:
                sincronizar_consumo_meta_run(self.plano, self.estado, settings=self.settings)
            except Exception:
                log.exception("Falha ao sincronizar consumo meta (batch flush)")
        self.pending = 0

    def registrar_mudanca(self, *, sync_meta: bool = False) -> None:
        self.pending += 1
        self.flush(sync_meta=sync_meta)


def _log_timing_fase(fase: str, inicio: float, **extra) -> None:
    ms = int((time.time() - inicio) * 1000)
    extras = " ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
    log.info("[BRFLOW_TIMING] %s_ms=%d %s", fase, ms, extras.strip())


def _workflows_pendentes_keys(
    plano: PlanoReplicacao,
    workflows: list,
    estado: Optional[dict],
    settings: dict,
) -> set[str]:
    """Chaves normalizadas dos workflows BRFlow ainda pendentes de upload."""
    forcar = bool(settings.get("forcar_reexecucao", False))
    apenas_pendentes = bool(settings.get("apenas_pendentes", True))
    keys: set[str] = set()
    for workflow in workflows:
        if estado and apenas_pendentes and not forcar:
            info = estado.get("workflows", {}).get(workflow, {})
            if str(info.get("status", "PENDENTE")) in STATUS_WORKFLOW_SKIP_RETOMADA:
                continue
        wf_brflow = workflow_nome_brflow(plano, workflow)
        keys.add(_normalizar_workflow_brflow(wf_brflow))
    return keys


def _normalizar_workflow_brflow(nome: str) -> str:
    from app.bots.replicacao_aud_d1_planning import _normalizar_workflow

    return _normalizar_workflow(nome)


def _restaurar_listagem_pos_workflow(
    driver,
    settings: dict,
    callbacks: BrflowWorkflowCallbacks,
) -> None:
    """Reaplica paginação da listagem após salvar/erro (BRFlow pode resetar o rodapé)."""
    garantir_paginacao_listagem_replicacao(
        driver,
        settings,
        set_status=callbacks.set_status,
    )


def _construir_listagem_resumo(
    driver,
    plano: PlanoReplicacao,
    workflows: list,
    estado: Optional[dict],
    settings: dict,
    callbacks: BrflowWorkflowCallbacks,
) -> tuple[dict, list, Optional[IndiceListagemBrflow]]:
    """Indexa ou lê listagem conforme settings. Retorna (resumo, linhas, indice)."""
    apenas_ativos = bool(settings.get("replicacao_apenas_ativos", True))
    if not apenas_ativos:
        return {}, [], None

    inicio = time.time()
    use_index = listagem_modo_indice(settings) and paginacao_varrer_todas(settings)
    wf_keys_alvo: set[str] = set()
    if indexar_parar_quando_plano_completo(settings):
        wf_keys_alvo = _workflows_pendentes_keys(plano, workflows, estado, settings)

    indice: Optional[IndiceListagemBrflow] = None
    linhas_br: list = []

    if use_index:
        indice = indexar_listagem_replicacao(
            driver,
            settings,
            wf_keys_alvo=wf_keys_alvo or None,
            set_status=callbacks.set_status,
        )
        resumo_br = indice_para_resumo(indice)
        _log_timing_fase(
            "indexar",
            inicio,
            workflows=len(resumo_br),
            paginas=indice.paginas_lidas,
            parada_antecipada=indice.parada_antecipada,
        )
    elif paginacao_varrer_todas(settings):
        linhas_br = ler_linhas_todas_paginas_replicacao(
            driver, settings, set_status=callbacks.set_status
        )
        resumo_br = resumir_linhas_por_workflow(linhas_br)
        _log_timing_fase("varrer_linhas", inicio, linhas=len(linhas_br))
    else:
        linhas_br = ler_linhas_replicacao(driver)
        resumo_br = resumir_linhas_por_workflow(linhas_br)
        _log_timing_fase("ler_pagina", inicio, linhas=len(linhas_br))

    info_pag = ler_info_paginador(driver)
    duplicatas = sum(
        1 for item in resumo_br.values() if item["linhas_ativas"] + item["linhas_inativas"] > 1
    )
    ativos = sum(1 for item in resumo_br.values() if item["tem_ativo"])
    inativos = sum(1 for item in resumo_br.values() if item["tem_inativo"] and not item["tem_ativo"])
    log.info(
        "Listagem BRFlow | %d linhas/index | %d workflow(s) | ativos=%d | "
        "só inativo=%d | duplicatas=%d | paginação=%s/%s itens=%s | modo_indice=%s",
        indice.linhas_lidas if indice else len(linhas_br),
        len(resumo_br),
        ativos,
        inativos,
        duplicatas,
        info_pag.get("pagina_atual"),
        info_pag.get("total_paginas"),
        info_pag.get("total_itens"),
        use_index,
    )
    return resumo_br, linhas_br, indice


def pesquisar_e_preparar_listagem_replicacao(
    driver,
    settings: Optional[dict] = None,
    *,
    validar_filtros: ValidarFiltrosFn,
    preparar_listagem: PrepararListagemFn,
) -> None:
    """Clica Pesquisar, pagina e prepara listagem de replicação."""
    settings = settings or {}
    inicio = time.time()
    validar_filtros(driver, settings)
    pesquisar_xpath = (
        '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/fieldset/div[2]/button'
    )
    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, pesquisar_xpath)))
    click_element(driver, by=By.XPATH, value=pesquisar_xpath)
    log.info("[PESQUISAR] Botão Pesquisar clicado com sucesso")
    aguardar_listagem_replicacao_carregada(
        driver,
        timeout=30,
        usar_alvo_paginador=False,
    )
    forcar_paginacao_js(driver, settings=settings)
    preparar_listagem(driver, settings)
    _log_timing_fase("pesquisar", inicio)


def processar_workflows_grupo_fila(
    driver,
    plano: PlanoReplicacao,
    workflows_grupo: list,
    fila: str,
    estado: Optional[dict],
    settings: dict,
    callbacks: BrflowWorkflowCallbacks,
    *,
    indice_inicial: int = 1,
    total_geral: int = 0,
    resumo_br: Optional[dict] = None,
    linhas_br: Optional[list] = None,
    indice_listagem: Optional[IndiceListagemBrflow] = None,
    estado_batch: Optional[EstadoPersistenciaBatch] = None,
) -> None:
    """Processa upload CSV para workflows de uma fila após Pesquisar."""
    forcar = bool(settings.get("forcar_reexecucao", False))
    apenas_pendentes = bool(settings.get("apenas_pendentes", True))
    apenas_ativos = bool(settings.get("replicacao_apenas_ativos", True))
    use_index_mode = listagem_modo_indice(settings)

    if resumo_br is None and apenas_ativos:
        resumo_br, linhas_br, indice_listagem = _construir_listagem_resumo(
            driver, plano, workflows_grupo, estado, settings, callbacks
        )
    resumo_br = resumo_br or {}
    linhas_br = linhas_br or []

    batch = estado_batch or EstadoPersistenciaBatch(
        estado, plano.estado_execucao_path, plano, settings
    )

    total = total_geral or len(workflows_grupo)
    refilters_evitados = 0

    for offset, workflow in enumerate(workflows_grupo):
        indice = indice_inicial + offset
        if callbacks.parar_event.is_set():
            log.info("[PESQUISAR] Parada solicitada; interrompendo loop de workflows")
            for cancelado in workflows_grupo[offset:]:
                cancel_entry = (estado or {}).get("workflows", {}).get(cancelado, {})
                if str(cancel_entry.get("status") or "").upper() in STATUS_WORKFLOW_SKIP_RETOMADA:
                    continue
                update_workflow_state(
                    estado,
                    cancelado,
                    "CANCELADO",
                    resultado="cancelado",
                    motivo_codigo="EXECUCAO_CANCELADA",
                    motivo_resumo="Execução cancelada antes do processamento do workflow",
                    fase_execucao="fila_workflows",
                    plano=plano,
                )
                batch.registrar_mudanca()
            break

        # Contabiliza também workflows pulados, ausentes ou já processados.
        callbacks.set_progress(0, f"Workflow {indice}/{total}: {workflow}")

        if estado:
            info = estado.get("workflows", {}).get(workflow, {})
            status_atual = str(info.get("status", "PENDENTE"))
            if status_atual in STATUS_WORKFLOW_SKIP_RETOMADA and apenas_pendentes and not forcar:
                log.info("[PESQUISAR] Workflow %s já processado (%s); pulando", workflow, status_atual)
                callbacks.set_status(f"Pulado (já processado): {workflow}")
                continue

        modo_replicacao = _modo_replicacao_workflow(plano, workflow, fila, estado)
        modo_qtd = modo_replicacao == "qtd"
        csv_path = None if modo_qtd else resolve_csv_upload_workflow(plano, workflow, estado)
        qtd_calculada = int(getattr(plano, "qtd_por_workflow", {}).get(workflow, 0) or 0)
        if estado:
            entry = estado.setdefault("workflows", {}).setdefault(workflow, {})
            entry["modo"] = modo_replicacao
            if modo_qtd:
                entry["qtd_calculada"] = qtd_calculada
        log.info(
            "[PESQUISAR] Workflow preparado | fila=%s | workflow=%s | modo=%s | qtd=%s | csv=%s",
            fila,
            workflow,
            modo_replicacao,
            qtd_calculada if modo_qtd else 0,
            bool(csv_path),
        )

        if modo_qtd:
            if qtd_calculada <= 0:
                log.warning("[PESQUISAR] Qtd zero para %s (fila=%s); pulando", workflow, fila)
                callbacks.set_status(f"Pulado (qtd zero): {workflow}")
                update_workflow_state(
                    estado,
                    workflow,
                    "PULADO",
                    None,
                    workflow_brflow=workflow_nome_brflow(plano, workflow),
                    motivo="qtd zero",
                    resultado="pulado",
                    motivo_codigo="QTD_ZERO",
                    fase_execucao="validacao_entrada",
                    quantidade_alvo=qtd_calculada,
                    plano=plano,
                )
                batch.registrar_mudanca()
                continue
        elif not csv_path.exists():
            log.warning("[PESQUISAR] CSV ausente para %s; pulando", workflow)
            plano.warnings.append(f"{workflow}: CSV não encontrado para upload na UI")
            callbacks.set_status(f"Pulado (CSV ausente): {workflow}")
            update_workflow_state(
                estado, workflow, "PULADO", csv_path, motivo="csv ausente",
                resultado="pulado", motivo_codigo="CSV_AUSENTE",
                fase_execucao="validacao_entrada", plano=plano
            )
            batch.registrar_mudanca()
            continue
        else:
            plano.csv_paths[workflow] = csv_path
            csv_vazio = callbacks.csv_protocolos_vazio(csv_path)
            if csv_vazio:
                if upload_csv_vazio_habilitado(settings):
                    log.info(
                        "[PESQUISAR] CSV limpeza (placeholder %s) para %s — enviando no BRFlow",
                        REPLICACAO_CSV_PLACEHOLDER_LIMPEZA,
                        workflow,
                    )
                else:
                    log.warning(
                        "[PESQUISAR] CSV limpeza (placeholder %s) para %s; pulando upload",
                        REPLICACAO_CSV_PLACEHOLDER_LIMPEZA,
                        workflow,
                    )
                    callbacks.set_status(f"Pulado (sem protocolos): {workflow}")
                    update_workflow_state(
                        estado,
                        workflow,
                        "PULADO",
                        csv_path,
                        motivo="sem protocolos",
                        resultado="pulado",
                        motivo_codigo="SEM_PROTOCOLOS",
                        fase_execucao="validacao_entrada",
                        plano=plano,
                    )
                    batch.registrar_mudanca()
                    continue

        wf_brflow = workflow_nome_brflow(plano, workflow)
        nome_regra = str(
            getattr(plano, "workflow_regra_brflow", {}).get(workflow, "") or ""
        ).strip()
        if eh_fila_redoc(fila) and not nome_regra:
            msg = f"{workflow} (Redoc): nome_regra_brflow ausente no cadastro/plano"
            log.warning("[PESQUISAR] %s", msg)
            plano.warnings.append(msg)
            callbacks.set_status(f"Pulado (regra ausente): {workflow}")
            update_workflow_state(
                estado,
                workflow,
                "PULADO",
                csv_path if not modo_qtd else None,
                workflow_brflow=wf_brflow,
                motivo="regra ausente",
                resultado="pulado",
                motivo_codigo="REGRA_AUSENTE",
                fase_execucao="validacao_listagem",
                plano=plano,
            )
            batch.registrar_mudanca()
            continue
        if apenas_ativos:
            if workflow_ausente_listagem_brflow(resumo_br, wf_brflow):
                msg = f"{workflow} (BRFlow: {wf_brflow}): ausente na listagem após Pesquisar"
                log.warning("[PESQUISAR] %s", msg)
                logar_workflow_ausente_diagnostico(workflow, wf_brflow, linhas_br, resumo_br)
                plano.warnings.append(msg)
                callbacks.set_status(f"Pulado (ausente no BRFlow): {workflow}")
                update_workflow_state(
                    estado,
                    workflow,
                    "PULADO",
                    csv_path,
                    workflow_brflow=wf_brflow,
                    motivo="ausente listagem BRFlow",
                    resultado="pulado",
                    motivo_codigo="WORKFLOW_AUSENTE_BRFLOW",
                    fase_execucao="validacao_listagem",
                    plano=plano,
                )
                batch.registrar_mudanca()
                continue
            if eh_workflow_apenas_inativo_brflow(resumo_br, wf_brflow):
                msg = f"{workflow} (BRFlow: {wf_brflow}): apenas linha(s) inativa(s)"
                log.warning("[PESQUISAR] %s", msg)
                plano.warnings.append(msg)
                update_workflow_state(
                    estado,
                    workflow,
                    "INATIVO",
                    csv_path,
                    workflow_brflow=wf_brflow,
                    motivo="apenas inativo no BRFlow",
                    resultado="inativo",
                    motivo_codigo="WORKFLOW_INATIVO_BRFLOW",
                    fase_execucao="validacao_listagem",
                    plano=plano,
                )
                batch.registrar_mudanca()
                continue
            wf_key = callbacks.normalizar_workflow(wf_brflow)
            linhas_ativas = resumo_br.get(wf_key, {}).get("linhas_ativas", 0)
            if nome_regra:
                linhas_regra = contar_linhas_ativas_regra(
                    linhas_br,
                    wf_key,
                    nome_regra,
                    indice=indice_listagem,
                    resumo_br=resumo_br,
                )
                if linhas_regra == 0:
                    msg = (
                        f"{workflow} (BRFlow: {wf_brflow}, regra={nome_regra}): "
                        "regra não encontrada na listagem"
                    )
                    log.warning("[PESQUISAR] %s", msg)
                    plano.warnings.append(msg)
                    callbacks.set_status(f"Pulado (regra divergente): {workflow}")
                    update_workflow_state(
                        estado,
                        workflow,
                        "PULADO",
                        csv_path if not modo_qtd else None,
                        workflow_brflow=wf_brflow,
                        motivo="regra divergente",
                        resultado="pulado",
                        motivo_codigo="REGRA_DIVERGENTE",
                        fase_execucao="validacao_listagem",
                        plano=plano,
                    )
                    batch.registrar_mudanca()
                    continue
                if linhas_regra > 1:
                    log.warning(
                        "[PESQUISAR] %s (BRFlow: %s, regra=%s): %d linhas ATIVO; editando a primeira",
                        workflow,
                        wf_brflow,
                        nome_regra,
                        linhas_regra,
                    )
            elif linhas_ativas > 1:
                log.warning(
                    "[PESQUISAR] %s (BRFlow: %s): %d linhas ATIVO; editando a primeira "
                    "(cadastre Nome regra BRFlow no portal para desambiguar)",
                    workflow,
                    wf_brflow,
                    linhas_ativas,
                )

        callbacks.set_status(
            f"Editando workflow ({indice}/{total}, fila={fila}): {wf_brflow}"
            + (f" | regra={nome_regra[:80]}" if nome_regra else "")
        )
        log.info(
            "[PESQUISAR] Abrindo edição | fila=%s | config=%s | brflow=%s | regra=%s",
            fila,
            workflow,
            wf_brflow,
            nome_regra or "(nenhuma)",
        )
        inicio_editar = time.time()
        update_workflow_state(
            estado,
            workflow,
            "PROCESSANDO",
            csv_path if not modo_qtd else None,
            workflow_brflow=wf_brflow,
            resultado="processando",
            motivo_codigo="",
            motivo_resumo="",
            fase_execucao="abrir_edicao",
            quantidade_alvo=qtd_calculada if modo_qtd else None,
            iniciar_tentativa=True,
            plano=plano,
        )
        batch.registrar_mudanca()
        batch.flush(force=True, sync_meta=False)
        try:
            if apenas_ativos and not use_index_mode:
                callbacks.refilter_listagem(driver, settings, motivo="antes de abrir edição")
            else:
                refilters_evitados += 1
            callbacks.clicar_editar(
                driver,
                wf_brflow,
                apenas_ativo=apenas_ativos,
                settings=settings,
                indice_listagem=indice_listagem if use_index_mode else None,
                nome_regra=nome_regra or None,
            )
            _log_timing_fase("editar_abrir", inicio_editar, workflow=workflow)
            inicio_salvar = time.time()
            if modo_qtd:
                action_result = callbacks.configurar_workflow_qtd(
                    driver,
                    workflow,
                    qtd_calculada,
                    settings=settings,
                    workflow_brflow=wf_brflow,
                    fila=fila,
                )
                _log_timing_fase("salvar_qtd", inicio_salvar, workflow=workflow)
                if (
                    isinstance(action_result, WorkflowActionResult)
                    and action_result.status == "SALVO_OK"
                ):
                    from app.bots.replicacao_d1.selenium.painel_qtd import (
                        confirmar_qtd_replicada_pos_save,
                    )

                    def _reabrir_para_confirmacao():
                        if apenas_ativos and not use_index_mode:
                            callbacks.refilter_listagem(
                                driver, settings, motivo="confirmar quantidade salva"
                            )
                        callbacks.clicar_editar(
                            driver,
                            wf_brflow,
                            apenas_ativo=apenas_ativos,
                            settings=settings,
                            indice_listagem=indice_listagem if use_index_mode else None,
                            nome_regra=nome_regra or None,
                        )

                    qtd_confirmada = confirmar_qtd_replicada_pos_save(
                        driver,
                        qtd_calculada,
                        abrir_edicao=_reabrir_para_confirmacao,
                        set_status=callbacks.set_status,
                    )
                    action_result = replace(
                        action_result, quantidade_encontrada=qtd_confirmada
                    )
                update_workflow_state(
                    estado,
                    workflow,
                    (
                        action_result.status
                        if isinstance(action_result, WorkflowActionResult)
                        else str(action_result)
                    ),
                    None,
                    workflow_brflow=wf_brflow,
                    resultado=(
                        action_result.resultado
                        if isinstance(action_result, WorkflowActionResult)
                        else "salvo"
                    ),
                    motivo_codigo=(
                        action_result.motivo_codigo
                        if isinstance(action_result, WorkflowActionResult)
                        else ""
                    ),
                    motivo_resumo=(
                        action_result.motivo_resumo
                        if isinstance(action_result, WorkflowActionResult)
                        else ""
                    ),
                    fase_execucao=(
                        action_result.fase_execucao
                        if isinstance(action_result, WorkflowActionResult)
                        else "confirmacao_salvamento"
                    ),
                    quantidade_alvo=qtd_calculada,
                    quantidade_encontrada=(
                        action_result.quantidade_encontrada
                        if isinstance(action_result, WorkflowActionResult)
                        else None
                    ),
                    plano=plano,
                )
                if estado:
                    entry = estado.get("workflows", {}).get(workflow, {})
                    entry["modo"] = "qtd"
                    entry["qtd_calculada"] = qtd_calculada
                    if nome_regra:
                        entry["nome_regra_brflow"] = nome_regra
            else:
                action_result = callbacks.configurar_workflow(
                    driver,
                    workflow,
                    csv_path,
                    settings=settings,
                    workflow_brflow=wf_brflow,
                    fila=fila,
                )
                _log_timing_fase("salvar", inicio_salvar, workflow=workflow)
                update_workflow_state(
                    estado,
                    workflow,
                    "SALVO_OK",
                    csv_path,
                    workflow_brflow=wf_brflow,
                    resultado=(
                        action_result.resultado
                        if isinstance(action_result, WorkflowActionResult)
                        else "salvo"
                    ),
                    motivo_codigo="",
                    motivo_resumo="",
                    fase_execucao="confirmacao_salvamento",
                    plano=plano,
                )
            batch.registrar_mudanca(sync_meta=True)
            if use_index_mode:
                invalidar_entrada_indice(indice_listagem, callbacks.normalizar_workflow(wf_brflow))
                _restaurar_listagem_pos_workflow(driver, settings, callbacks)
        except Exception as exc:
            nao_salvo = isinstance(exc, SaveNotConfirmedError)
            update_workflow_state(
                estado,
                workflow,
                "NAO_SALVO" if nao_salvo else "ERRO",
                csv_path if not modo_qtd else None,
                workflow_brflow=wf_brflow,
                resultado="nao_salvo" if nao_salvo else "falhou",
                motivo_codigo=(
                    "SALVAMENTO_NAO_CONFIRMADO"
                    if nao_salvo
                    else type(exc).__name__.upper()[:64]
                ),
                motivo_resumo=str(exc)[:255]
                or ("Salvamento não confirmado" if nao_salvo else "Erro na automação"),
                fase_execucao="confirmacao_salvamento" if nao_salvo else "interacao_selenium",
                quantidade_alvo=qtd_calculada if modo_qtd else None,
                plano=plano,
            )
            batch.flush(force=True, sync_meta=False)
            try:
                callbacks.take_screenshot(driver, "log_screenshots", f"replicacao_wf_{indice}")
            except Exception:
                log.debug("Falha ao capturar screenshot do workflow", exc_info=True)
            callbacks.set_status(f"Erro no workflow {workflow}: {exc}")
            log.exception("[PESQUISAR] Falha no workflow %s; continuando", workflow)
            if use_index_mode:
                _restaurar_listagem_pos_workflow(driver, settings, callbacks)
        time.sleep(0.15)

    batch.flush(force=True, sync_meta=True)
    if refilters_evitados:
        log.info("[BRFLOW_TIMING] refilters_evitados=%d", refilters_evitados)


def clicar_pesquisar_replicacao(
    driver,
    plano: PlanoReplicacao,
    estado: Optional[dict],
    settings: Optional[dict],
    callbacks: BrflowWorkflowCallbacks,
) -> None:
    """Pesquisa por fila, abre edição e anexa CSV de protocolos para cada workflow do plano."""
    settings = settings or {}
    workflows = plano.workflows
    if not workflows:
        log.warning("[PESQUISAR] Nenhum workflow com protocolos no plano de replicação")
        return

    grupos = agrupar_workflows_por_fila(workflows, plano, settings=settings)
    total = len(workflows)
    indice_global = 1
    filtrar_destino = filtrar_workflow_destino_habilitado(settings)
    estado_batch = EstadoPersistenciaBatch(estado, plano.estado_execucao_path, plano, settings)

    try:
        if not filtrar_destino:
            _processar_busca_unica_cliente(
                driver,
                plano,
                estado,
                settings,
                callbacks,
                grupos,
                total,
                indice_global,
                estado_batch,
            )
        else:
            _processar_busca_por_fila(
                driver,
                plano,
                estado,
                settings,
                callbacks,
                grupos,
                total,
                indice_global,
                estado_batch,
            )
    finally:
        estado_batch.flush(force=True, sync_meta=True)


def _processar_busca_unica_cliente(
    driver,
    plano: PlanoReplicacao,
    estado: Optional[dict],
    settings: dict,
    callbacks: BrflowWorkflowCallbacks,
    grupos: dict,
    total: int,
    indice_global: int,
    estado_batch: EstadoPersistenciaBatch,
) -> None:
    """Uma Pesquisar (cliente GAQ) compartilhada entre filas quando busca ampla."""
    primeira_fila = next(iter(grupos))
    filtros = resolver_filtros_brflow_por_fila(primeira_fila, settings)
    settings_busca = {
        **settings,
        **filtros,
        "replicacao_filtrar_workflow_destino": False,
    }
    todos_workflows = [wf for wfs in grupos.values() for wf in wfs]
    log.info(
        "[PESQUISAR] Busca única cliente=%s (%s) | %d fila(s) | %d workflow(s) | paginação=%s/página",
        filtros["replicacao_cliente_cod"],
        filtros.get("replicacao_cliente_destino", "GAQ"),
        len(grupos),
        len(todos_workflows),
        paginacao_tamanho(settings_busca),
    )
    callbacks.preencher_filtros(driver, settings=settings_busca)
    pesquisar_e_preparar_listagem_replicacao(
        driver,
        settings=settings_busca,
        validar_filtros=callbacks.validar_filtros,
        preparar_listagem=callbacks.preparar_listagem,
    )

    resumo_br, linhas_br, indice_listagem = _construir_listagem_resumo(
        driver, plano, todos_workflows, estado, settings_busca, callbacks
    )

    for fila, workflows_grupo in grupos.items():
        if callbacks.parar_event.is_set():
            break
        filtros_fila = resolver_filtros_brflow_por_fila(fila, settings)
        settings_fila = {**settings, **filtros_fila, "replicacao_filtrar_workflow_destino": False}
        log.info("[PESQUISAR] Processando fila %s | %d workflow(s) (listagem compartilhada)", fila, len(workflows_grupo))
        processar_workflows_grupo_fila(
            driver,
            plano,
            workflows_grupo,
            fila,
            estado,
            settings_fila,
            callbacks,
            indice_inicial=indice_global,
            total_geral=total,
            resumo_br=resumo_br,
            linhas_br=linhas_br,
            indice_listagem=indice_listagem,
            estado_batch=estado_batch,
        )
        indice_global += len(workflows_grupo)


def _processar_busca_por_fila(
    driver,
    plano: PlanoReplicacao,
    estado: Optional[dict],
    settings: dict,
    callbacks: BrflowWorkflowCallbacks,
    grupos: dict,
    total: int,
    indice_global: int,
    estado_batch: EstadoPersistenciaBatch,
) -> None:
    """Loop legado: Pesquisar separado por fila quando filtro WorkFlow Destino ativo."""
    for fila, workflows_grupo in grupos.items():
        if callbacks.parar_event.is_set():
            break

        filtros = resolver_filtros_brflow_por_fila(fila, settings)
        settings_fila = {**settings, **filtros}
        log.info(
            "[PESQUISAR] Fila %s | %d workflow(s) | cliente=%s | workflow=%s | paginação=%s/página",
            fila,
            len(workflows_grupo),
            filtros["replicacao_cliente_cod"],
            filtros.get("replicacao_workflow_cod"),
            paginacao_tamanho(settings_fila),
        )
        callbacks.preencher_filtros(driver, settings=settings_fila)
        pesquisar_e_preparar_listagem_replicacao(
            driver,
            settings=settings_fila,
            validar_filtros=callbacks.validar_filtros,
            preparar_listagem=callbacks.preparar_listagem,
        )
        processar_workflows_grupo_fila(
            driver,
            plano,
            workflows_grupo,
            fila,
            estado,
            settings_fila,
            callbacks,
            indice_inicial=indice_global,
            total_geral=total,
            estado_batch=estado_batch,
        )
        indice_global += len(workflows_grupo)
