# -*- coding: utf-8 -*-
"""Ponte entre PersistentConfig (PostgreSQL) e robot_config do bot D-1."""
from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.replicacao_d1.models import ReplicacaoD1ConfigGeral
from apps.replicacao_d1.services.config_audit import registrar_historico
from apps.replicacao_d1.services.config_dto import PersistentConfig
from apps.replicacao_d1.services.config_snapshot import bump_config_version, load_persistent_config

# Campos efêmeros — não persistidos em ConfigGeral.
RUN_ONLY_KEYS = frozenset(
    {
        "apenas_planejamento",
        "run_id",
        "gerar_novo_plano",
        "forcar_reexecucao",
        "apenas_pendentes",
        "replicacao_aud_data_ref",
        "auditores_ativos",
        "headless",
        "max_workers",
        "output_dir",
    }
)

# Mapeamento ConfigGeral -> chaves REPLICACAO_AUD_D1_CONFIG_DEFAULT.
_GERAL_FIELD_MAP: dict[str, str] = {
    "excluir_historico": "excluir_historico",
    "dias_historico": "dias_historico",
    "sobrescrever": "sobrescrever",
    "fallback_ultimo_parquet": "fallback_ultimo_parquet",
    "fallback_parquet_dias_ausentes": "fallback_parquet_dias_ausentes",
    "seed": "replicacao_aud_seed",
    "usar_escala_auditores": "usar_escala_auditores",
    "meta_produ_diaria": "meta_produ",
    "meta_produ_diaria_case": "meta_produ_case",
    "meta_produ_diaria_bio": "meta_produ_bio",
    "meta_produ_diaria_redoc": "meta_produ_redoc",
    "path_escala_csv": "escala_auditores_csv",
    "path_config_base": "replicacao_config_base",
    "path_default_xlsx": "replicacao_config_default",
    "path_categoria_xlsx": "replicacao_categoria_xlsx",
    "sincronizar_workflow_d1": "replicacao_sincronizar_workflow_d1",
    "apenas_ativos": "replicacao_apenas_ativos",
    "replicacao_cliente_destino": "replicacao_cliente_destino",
    "replicacao_cliente_cod": "replicacao_cliente_cod",
    "replicacao_workflow_destino": "replicacao_workflow_destino",
    "replicacao_workflow_cod": "replicacao_workflow_cod",
    "replicacao_workflow_destino_31": "replicacao_workflow_destino_31",
    "replicacao_workflow_cod_31": "replicacao_workflow_cod_31",
    "replicacao_workflow_destino_bio": "replicacao_workflow_destino_bio",
    "replicacao_workflow_cod_bio": "replicacao_workflow_cod_bio",
    "replicacao_workflow_destino_redoc": "replicacao_workflow_destino_redoc",
    "replicacao_workflow_cod_redoc": "replicacao_workflow_cod_redoc",
    "workflows_amostra_100": "replicacao_workflows_amostra_100",
    "workflows_amostra_pct": "replicacao_workflows_amostra_pct",
    "usar_amostra_mix_manual_automatico": "usar_amostra_mix_manual_automatico",
    "amostra_pct_manual": "amostra_pct_manual",
    "amostra_pct_automatico": "amostra_pct_automatico",
    "limpar_planos_automatico": "limpar_planos_automatico",
    "limpar_planos_ao_gerar": "limpar_planos_ao_gerar",
    "limpar_planos_apos_conclusao": "limpar_planos_apos_conclusao",
    "manter_planos_ultimos_n": "manter_planos_ultimos_n",
    "dias_retencao_planos": "dias_retencao_planos",
    "agendamento_ativo": "agendamento_ativo",
    "agendamento_hora_planejamento": "agendamento_hora_planejamento",
    "agendamento_hora_execucao": "agendamento_hora_execucao",
    "replicacao_destino_brflow_ativo": "replicacao_destino_brflow_ativo",
    "replicacao_destino_case31_ativo": "replicacao_destino_case31_ativo",
    "replicacao_destino_bio_ativo": "replicacao_destino_bio_ativo",
    "replicacao_destino_redoc_ativo": "replicacao_destino_redoc_ativo",
    "meta_cliente_ano_mes_ref": "meta_cliente_ano_mes_ref",
}


def _config_source_to_robot_config_dict(cfg: PersistentConfig | ReplicacaoD1ConfigGeral) -> dict[str, Any]:
    out: dict[str, Any] = {
        "fonte_banco_ativa": cfg.fonte_banco_ativa,
        "config_version": cfg.config_version,
        "config_hash": cfg.config_hash,
    }
    for model_field, robot_key in _GERAL_FIELD_MAP.items():
        val = getattr(cfg, model_field, None)
        if robot_key in ("meta_produ", "meta_produ_case", "meta_produ_bio", "meta_produ_redoc"):
            out[robot_key] = str(int(val) if float(val).is_integer() else val)
        else:
            out[robot_key] = val
    return out


def persistent_to_robot_config_dict(cfg: PersistentConfig | None = None) -> dict[str, Any]:
    """Converte o snapshot persistente completo para o formato do robot_manager."""
    if cfg is None:
        cfg = load_persistent_config()
    return _config_source_to_robot_config_dict(cfg)


def general_to_robot_config_dict(
    geral: ReplicacaoD1ConfigGeral | None = None,
) -> dict[str, Any]:
    """Lê somente ConfigGeral para telas que não precisam dos catálogos completos."""
    if geral is None:
        geral = ReplicacaoD1ConfigGeral.get_solo()
    return _config_source_to_robot_config_dict(geral)


def split_robot_config_patch(patch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    permanent: dict[str, Any] = {}
    run_only: dict[str, Any] = {}
    for key, val in (patch or {}).items():
        if key in RUN_ONLY_KEYS:
            run_only[key] = val
        else:
            permanent[key] = val
    return permanent, run_only


def _robot_key_to_model_field(robot_key: str) -> str | None:
    for model_field, rk in _GERAL_FIELD_MAP.items():
        if rk == robot_key:
            return model_field
    inverse = {
        "replicacao_config_base": "path_config_base",
        "replicacao_config_default": "path_default_xlsx",
        "replicacao_categoria_xlsx": "path_categoria_xlsx",
        "escala_auditores_csv": "path_escala_csv",
        "replicacao_aud_seed": "seed",
        "meta_produ": "meta_produ_diaria",
        "meta_produ_case": "meta_produ_diaria_case",
        "meta_produ_bio": "meta_produ_diaria_bio",
        "meta_produ_redoc": "meta_produ_diaria_redoc",
        "replicacao_sincronizar_workflow_d1": "sincronizar_workflow_d1",
        "replicacao_apenas_ativos": "apenas_ativos",
        "replicacao_workflows_amostra_100": "workflows_amostra_100",
        "replicacao_workflows_amostra_pct": "workflows_amostra_pct",
    }
    return inverse.get(robot_key)


@transaction.atomic
def apply_robot_config_patch_to_db(patch: dict[str, Any], user) -> ReplicacaoD1ConfigGeral:
    """Persiste apenas chaves permanentes em ConfigGeral."""
    geral = ReplicacaoD1ConfigGeral.objects.select_for_update().get(pk=ReplicacaoD1ConfigGeral.SINGLETON_PK)
    before = _geral_to_audit_dict(geral)
    permanent, _ = split_robot_config_patch(patch)

    for robot_key, val in permanent.items():
        if robot_key in ("fonte_banco_ativa", "config_version", "config_hash"):
            continue
        model_field = _robot_key_to_model_field(robot_key)
        if not model_field:
            continue
        if model_field in (
            "meta_produ_diaria",
            "meta_produ_diaria_case",
            "meta_produ_diaria_bio",
            "meta_produ_diaria_redoc",
        ):
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
        if model_field in ("amostra_pct_manual", "amostra_pct_automatico"):
            try:
                val = max(0, min(100, int(val)))
            except (TypeError, ValueError):
                continue
        if model_field == "usar_amostra_mix_manual_automatico":
            if isinstance(val, str):
                val = val.strip().lower() in ("1", "true", "yes", "on")
            else:
                val = bool(val)
        if model_field == "seed":
            try:
                val = int(val)
            except (TypeError, ValueError):
                continue
        setattr(geral, model_field, val)

    if user and getattr(user, "is_authenticated", False):
        geral.updated_by = user
    geral.save()
    bump_config_version(user=user)
    geral.refresh_from_db()
    registrar_historico(
        "ReplicacaoD1ConfigGeral",
        geral.pk,
        "update",
        user,
        before,
        _geral_to_audit_dict(geral),
    )
    return geral


def merge_db_config_for_robot_manager(
    robot_manager,
    mode: str = "replicacao_auditoria_d1",
    *,
    active_only: bool = False,
) -> dict[str, Any] | None:
    """Merge leve de ConfigGeral + opções efêmeras mantidas em memória."""
    geral = ReplicacaoD1ConfigGeral.get_solo()
    if active_only and not geral.fonte_banco_ativa:
        return None
    db_dict = general_to_robot_config_dict(geral)
    legacy_cfg = robot_manager.robot_configs(mode=mode).get(mode, {})
    for key in RUN_ONLY_KEYS:
        if key in legacy_cfg:
            db_dict[key] = legacy_cfg[key]
    return robot_manager._normalize_robot_config(db_dict, mode)


def _geral_to_audit_dict(geral: ReplicacaoD1ConfigGeral) -> dict[str, Any]:
    fields = [
        "fonte_banco_ativa",
        "agendamento_ativo",
        "path_config_base",
        "meta_produ_diaria",
        "meta_produ_diaria_case",
        "meta_produ_diaria_bio",
        "meta_produ_diaria_redoc",
        "usar_escala_auditores",
        "seed",
    ]
    return {f: getattr(geral, f) for f in fields}
