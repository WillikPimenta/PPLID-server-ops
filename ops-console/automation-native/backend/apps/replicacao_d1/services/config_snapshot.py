# -*- coding: utf-8 -*-
"""Serviço central de configuração permanente e snapshot de execução D-1."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.replicacao_d1.exceptions import ConfigBancoIndisponivelError, ConfigIncompletaError
from apps.replicacao_d1.models import (
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1ConfigSnapshot,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1RetroativoConfig,
    ReplicacaoD1RetroativoWorkflow,
    ReplicacaoD1Run,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.services.config_dto import (
    ClienteDTO,
    EscalaDiaDTO,
    ExecutionSnapshot,
    PersistentConfig,
    RetroativoConfigDTO,
    RetroativoWorkflowDTO,
    RunOptions,
    WorkflowDTO,
    normalizar_calculadora_params,
)
from apps.replicacao_d1.services.ledger import carregar_consumo_meta_mensal


def is_fonte_banco_ativa() -> bool:
    try:
        return bool(ReplicacaoD1ConfigGeral.get_solo().fonte_banco_ativa)
    except Exception as exc:
        raise ConfigBancoIndisponivelError(f"Banco indisponível: {exc}") from exc


def load_persistent_config() -> PersistentConfig:
    """Leitura da configuração permanente sem bloqueio de escrita."""
    try:
        geral = ReplicacaoD1ConfigGeral.objects.get(pk=ReplicacaoD1ConfigGeral.SINGLETON_PK)
    except ReplicacaoD1ConfigGeral.DoesNotExist:
        geral = ReplicacaoD1ConfigGeral.get_solo()
    except Exception as exc:
        raise ConfigBancoIndisponivelError(f"Falha ao carregar configuração geral: {exc}") from exc

    clientes_qs = (
        ReplicacaoD1Cliente.objects.filter(ativo=True)
        .select_related("segmento", "categoria")
        .order_by("nome")
    )
    workflows_qs = ReplicacaoD1Workflow.objects.select_related("cliente").order_by("nome_canonico")
    escala_qs = ReplicacaoD1EscalaDia.objects.all().order_by("data")

    calc_params = normalizar_calculadora_params(geral.calculadora_params or {})

    clientes = tuple(
        ClienteDTO(
            id=c.pk,
            nome=c.nome,
            chave_normalizada=c.chave_normalizada,
            segmento_nome=(c.segmento.nome if c.segmento_id else "") or c.segmento_nome,
            categoria_nome=(c.categoria.nome if c.categoria_id else "") or c.categoria_nome,
            meta_mensal=c.meta_mensal,
            ativo=c.ativo,
        )
        for c in clientes_qs
    )

    workflows_ativos: list[WorkflowDTO] = []
    workflows_pendentes: list[WorkflowDTO] = []
    for wf in workflows_qs:
        dto = WorkflowDTO(
            id=wf.pk,
            nome_canonico=wf.nome_canonico,
            nome_d1=wf.nome_d1,
            nome_selenium=wf.nome_selenium,
            nome_regra_brflow=wf.nome_regra_brflow,
            fila=wf.fila,
            cliente_nome=wf.cliente.nome if wf.cliente_id else "",
            cliente_chave=wf.cliente.chave_normalizada if wf.cliente_id else "",
            chave_normalizada=wf.chave_normalizada,
            chave_d1_normalizada=wf.chave_d1_normalizada,
            workflow_origem_id=wf.workflow_origem_id,
            status=wf.status,
            amostra_pct_especial=wf.amostra_pct_especial,
            amostra_100=wf.amostra_100,
            usar_arquivo_csv=wf.usar_arquivo_csv,
            ativo=wf.ativo,
        )
        if wf.status == ReplicacaoD1Workflow.STATUS_PENDENTE:
            workflows_pendentes.append(dto)
        elif wf.ativo and wf.status == ReplicacaoD1Workflow.STATUS_ATIVO:
            workflows_ativos.append(dto)

    escala = tuple(
        EscalaDiaDTO(
            data=row.data.isoformat(),
            auditores_brflow=int(row.auditores_brflow),
            auditores_case=int(row.auditores_case),
            auditores_bio=int(row.auditores_bio),
            auditores_redoc=int(row.auditores_redoc),
        )
        for row in escala_qs
    )

    retro_cfg = ReplicacaoD1RetroativoConfig.get_solo()
    retro_workflows = tuple(
        RetroativoWorkflowDTO(
            id=link.workflow_id,
            nome_canonico=link.workflow.nome_canonico,
            nome_d1=link.workflow.nome_d1,
            chave_normalizada=link.workflow.chave_normalizada,
            chave_d1_normalizada=link.workflow.chave_d1_normalizada,
            cliente_nome=link.workflow.cliente.nome if link.workflow.cliente_id else "",
            ativo=True,
        )
        for link in ReplicacaoD1RetroativoWorkflow.objects.filter(
            config=retro_cfg,
            ativo=True,
            workflow__ativo=True,
            workflow__status=ReplicacaoD1Workflow.STATUS_ATIVO,
        ).select_related("workflow", "workflow__cliente").order_by("workflow__nome_canonico")
    )
    retroativo = RetroativoConfigDTO(
        retroativo_ativo=bool(retro_cfg.retroativo_ativo),
        retroativo_data_inicio=retro_cfg.retroativo_data_inicio.isoformat()
        if retro_cfg.retroativo_data_inicio
        else "",
        retroativo_data_fim=retro_cfg.retroativo_data_fim.isoformat() if retro_cfg.retroativo_data_fim else "",
        workflows=retro_workflows,
    )

    return PersistentConfig(
        fonte_banco_ativa=geral.fonte_banco_ativa,
        config_version=int(geral.config_version),
        config_hash=str(geral.config_hash or ""),
        agendamento_ativo=geral.agendamento_ativo,
        agendamento_hora_planejamento=geral.agendamento_hora_planejamento,
        agendamento_hora_execucao=geral.agendamento_hora_execucao,
        path_config_base=geral.path_config_base,
        path_default_xlsx=geral.path_default_xlsx,
        path_categoria_xlsx=geral.path_categoria_xlsx,
        path_escala_csv=geral.path_escala_csv,
        limpar_planos_automatico=geral.limpar_planos_automatico,
        limpar_planos_ao_gerar=geral.limpar_planos_ao_gerar,
        limpar_planos_apos_conclusao=geral.limpar_planos_apos_conclusao,
        manter_planos_ultimos_n=int(geral.manter_planos_ultimos_n),
        dias_retencao_planos=int(geral.dias_retencao_planos),
        replicacao_cliente_destino=geral.replicacao_cliente_destino,
        replicacao_cliente_cod=geral.replicacao_cliente_cod,
        replicacao_workflow_destino=geral.replicacao_workflow_destino,
        replicacao_workflow_cod=geral.replicacao_workflow_cod,
        replicacao_workflow_destino_31=geral.replicacao_workflow_destino_31,
        replicacao_workflow_cod_31=geral.replicacao_workflow_cod_31,
        replicacao_workflow_destino_bio=geral.replicacao_workflow_destino_bio,
        replicacao_workflow_cod_bio=geral.replicacao_workflow_cod_bio,
        replicacao_workflow_destino_redoc=geral.replicacao_workflow_destino_redoc,
        replicacao_workflow_cod_redoc=geral.replicacao_workflow_cod_redoc,
        replicacao_destino_brflow_ativo=bool(geral.replicacao_destino_brflow_ativo),
        replicacao_destino_case31_ativo=bool(geral.replicacao_destino_case31_ativo),
        replicacao_destino_bio_ativo=bool(geral.replicacao_destino_bio_ativo),
        replicacao_destino_redoc_ativo=bool(geral.replicacao_destino_redoc_ativo),
        meta_produ_diaria=float(geral.meta_produ_diaria),
        meta_produ_diaria_case=float(geral.meta_produ_diaria_case),
        meta_produ_diaria_bio=float(geral.meta_produ_diaria_bio),
        meta_produ_diaria_redoc=float(geral.meta_produ_diaria_redoc),
        usar_escala_auditores=geral.usar_escala_auditores,
        excluir_historico=geral.excluir_historico,
        dias_historico=int(geral.dias_historico),
        sobrescrever=geral.sobrescrever,
        fallback_ultimo_parquet=geral.fallback_ultimo_parquet,
        fallback_parquet_dias_ausentes=geral.fallback_parquet_dias_ausentes,
        seed=int(geral.seed),
        sincronizar_workflow_d1=geral.sincronizar_workflow_d1,
        apenas_ativos=geral.apenas_ativos,
        meta_cliente_ano_mes_ref=geral.meta_cliente_ano_mes_ref,
        calculadora_params=calc_params,
        workflows_amostra_100=list(geral.workflows_amostra_100 or []),
        workflows_amostra_pct=dict(geral.workflows_amostra_pct or {}),
        usar_amostra_mix_manual_automatico=bool(geral.usar_amostra_mix_manual_automatico),
        amostra_pct_manual=int(geral.amostra_pct_manual),
        amostra_pct_automatico=int(geral.amostra_pct_automatico),
        clientes=clientes,
        workflows=tuple(workflows_ativos),
        workflows_pendentes=tuple(workflows_pendentes),
        escala=escala,
        retroativo=retroativo,
    )


def validate_persistent_config(cfg: PersistentConfig, *, exigir_escala: bool | None = None) -> list[str]:
    """Retorna erros bloqueantes."""
    errors: list[str] = []

    if not cfg.clientes:
        errors.append("Nenhum cliente ativo cadastrado.")

    if not cfg.workflows:
        errors.append("Nenhum workflow ativo cadastrado para planejamento.")

    for wf in cfg.workflows:
        if not wf.nome_d1.strip():
            errors.append(f"Workflow '{wf.nome_canonico}': nome D-1/parquet ausente.")
        if not wf.nome_selenium.strip():
            errors.append(f"Workflow '{wf.nome_canonico}': nome Selenium ausente.")
        fila_norm = (wf.fila or "").strip().casefold()
        if fila_norm in ("redoc",) and not (wf.nome_regra_brflow or "").strip():
            errors.append(
                f"Workflow '{wf.nome_canonico}': nome regra BRFlow obrigatório para fila Redoc."
            )
        if not wf.cliente_nome.strip():
            errors.append(f"Workflow '{wf.nome_canonico}': cliente não vinculado.")

    usar_escala = cfg.usar_escala_auditores if exigir_escala is None else exigir_escala
    if usar_escala and not cfg.escala:
        errors.append("Escala de auditores ausente (usar_escala_auditores=True).")

    filas_ativas = {(wf.fila or "").strip().casefold() for wf in cfg.workflows}

    if cfg.replicacao_destino_brflow_ativo and float(cfg.meta_produ_diaria or 0) <= 0:
        errors.append("Meta Produ diária BRFlow (G auditoria) inválida ou ausente.")
    if cfg.replicacao_destino_case31_ativo and float(cfg.meta_produ_diaria_case or 0) <= 0:
        errors.append("Meta Produ diária Case (Documentoscopia 3.1) inválida ou ausente.")

    if cfg.replicacao_destino_bio_ativo and "bio" in filas_ativas:
        if not (cfg.replicacao_workflow_cod_bio or "").strip():
            errors.append("Código workflow destino Bio ausente (há workflows ativos na fila Bio).")
        if float(cfg.meta_produ_diaria_bio or 0) <= 0:
            errors.append("Meta Produ diária Bio inválida ou ausente.")
    if cfg.replicacao_destino_redoc_ativo and "redoc" in filas_ativas:
        if not (cfg.replicacao_workflow_cod_redoc or "").strip():
            errors.append("Código workflow destino Redoc ausente (há workflows ativos na fila Redoc).")
        if float(cfg.meta_produ_diaria_redoc or 0) <= 0:
            errors.append("Meta Produ diária Redoc inválida ou ausente.")

    return errors


def build_execution_snapshot(
    *,
    run_id: str | None = None,
    run_options: RunOptions | None = None,
    competencia_meta: str | None = None,
    persist: bool = True,
    require_fonte_ativa: bool = True,
) -> ExecutionSnapshot:
    """Monta snapshot determinístico; persiste por run_id quando informado."""
    if require_fonte_ativa and not is_fonte_banco_ativa():
        raise ConfigBancoIndisponivelError(
            "fonte_banco_ativa=False: snapshot pelo banco indisponível neste caminho."
        )

    cfg = load_persistent_config()
    errors = validate_persistent_config(cfg)
    if errors:
        raise ConfigIncompletaError(errors)

    opts = run_options or RunOptions()
    competencia = _resolver_competencia(cfg, opts, competencia_meta)
    consumo = carregar_consumo_meta_mensal(competencia) if competencia else {}

    persistent_dict = cfg.to_dict()
    run_dict = opts.to_dict()
    payload = {
        "persistent": persistent_dict,
        "run_options": run_dict,
        "consumo_mensal": consumo,
        "competencia_resolvida": competencia,
    }
    config_hash = compute_config_hash(payload)
    created_at = timezone.now().isoformat(timespec="seconds")

    snapshot = ExecutionSnapshot(
        config_version=cfg.config_version,
        config_hash=config_hash,
        persistent=copy.deepcopy(persistent_dict),
        run_options=copy.deepcopy(run_dict),
        consumo_mensal=dict(consumo),
        competencia_resolvida=competencia,
        created_at=created_at,
    )

    if persist and run_id:
        full = snapshot.to_dict()
        snap_obj, _ = ReplicacaoD1ConfigSnapshot.objects.update_or_create(
            run_id=str(run_id),
            defaults={
                "config_version": cfg.config_version,
                "config_hash": config_hash,
                "snapshot_json": full,
            },
        )
        from apps.replicacao_d1.services.run_lifecycle import ensure_planned_run

        data_ref = _parse_data_ref(opts.data_ref)
        ensure_planned_run(
            str(run_id),
            data_referencia_d1=data_ref,
            config_snapshot=snap_obj,
            config_version=cfg.config_version,
            config_hash=config_hash,
        )

    return snapshot


def compute_config_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@transaction.atomic
def bump_config_version(user=None) -> tuple[int, str]:
    """Incrementa versão monotônica e recalcula hash da config persistente."""
    ReplicacaoD1ConfigGeral.get_solo()
    geral = ReplicacaoD1ConfigGeral.objects.select_for_update().get(pk=ReplicacaoD1ConfigGeral.SINGLETON_PK)
    cfg = load_persistent_config()
    payload = {"persistent": cfg.to_dict()}
    new_hash = compute_config_hash(payload)
    geral.config_version = int(geral.config_version or 0) + 1
    geral.config_hash = new_hash
    if user and getattr(user, "is_authenticated", False):
        geral.updated_by = user
    geral.save(update_fields=["config_version", "config_hash", "updated_at", "updated_by"])
    return geral.config_version, new_hash


def preview_execution_snapshot(
    *,
    run_options: RunOptions | None = None,
    competencia_meta: str | None = None,
) -> ExecutionSnapshot:
    """Snapshot de preview sem persistir."""
    return build_execution_snapshot(
        run_id=None,
        run_options=run_options,
        competencia_meta=competencia_meta,
        persist=False,
        require_fonte_ativa=False,
    )


def _resolver_competencia(
    cfg: PersistentConfig,
    opts: RunOptions,
    competencia_override: str | None,
) -> str:
    if competencia_override:
        return str(competencia_override)
    if opts.competencia_meta:
        return str(opts.competencia_meta)
    ref = cfg.meta_cliente_ano_mes_ref
    data_ref = _parse_data_ref(opts.data_ref)
    if ref == ReplicacaoD1ConfigGeral.META_CLIENTE_DATA_REFERENCIA and data_ref:
        return data_ref.strftime("%Y-%m")
    return timezone.now().strftime("%Y-%m")


def _parse_data_ref(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
