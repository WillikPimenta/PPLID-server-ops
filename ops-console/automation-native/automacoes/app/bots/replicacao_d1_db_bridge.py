# -*- coding: utf-8 -*-
"""Ponte PostgreSQL ↔ planejamento D-1 (Django process ou subprocess do bot)."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app.bots.replicacao_d1.planning_phases import (
    PLANNING_CONFIG,
    PLANNING_PERSIST,
    PLANNING_RETRO,
    PLANNING_SOURCE,
    PLANNING_START,
    plan_log,
    truncate_hash,
)

log = logging.getLogger("robots.replicacao_d1_db_bridge")

_DJANGO_READY = False
_BACKEND_DIR: Path | None = None
_LAST_SCHEDULER_HEARTBEAT = 0.0
_SOURCE_PROTOCOL_QUERY_CHUNK_SIZE = 500


def try_inject_planning_flags(settings: dict | None = None) -> dict:
    """Resolve flags do Django sem substituir uma escolha explícita da execução."""
    resolved = dict(settings or {})
    optimized_explicit = any(
        key in resolved
        for key in ("optimized_planning", "replicacao_d1_planejamento_otimizado")
    )
    shadow_explicit = "optimized_planning_shadow" in resolved
    if optimized_explicit and shadow_explicit:
        return resolved
    try:
        if not ensure_django_ready():
            return resolved
        from apps.replicacao_d1.feature_flags import (
            optimized_planning_enabled,
            optimized_planning_shadow_enabled,
        )

        if not optimized_explicit:
            resolved["optimized_planning"] = optimized_planning_enabled()
        if not shadow_explicit:
            resolved["optimized_planning_shadow"] = optimized_planning_shadow_enabled()
    except Exception as exc:
        log.debug("Flags do planejamento otimizado indisponíveis: %s", exc)
    return resolved


def append_planning_event_db(event: dict[str, Any]) -> bool:
    """Persiste transiÃ§Ãµes do planejamento em best-effort, sem afetar a execuÃ§Ã£o."""
    try:
        if not isinstance(event, dict) or str(event.get("state") or "") == "progress":
            return False
        if not ensure_django_ready():
            return False

        from apps.replicacao_d1.models import ReplicacaoD1ExecutionEvent, ReplicacaoD1Run

        run_id = str(event.get("run_id") or "").strip()[:64]
        phase = str(event.get("phase") or "").strip()[:32]
        state = str(event.get("state") or "").strip()[:32]
        if not run_id or not phase or state not in {
            "started", "completed", "skipped", "warning", "failed",
        }:
            return False
        run = ReplicacaoD1Run.objects.filter(run_id=run_id).first()
        if run is None:
            return False

        blocked = (
            "password", "senha", "token", "secret", "cookie", "session",
            "credential", "path", "file", "arquivo", "protocolo",
        )
        payload: dict[str, Any] = {}
        for raw_key in (
            "version", "event_id", "occurred_at", "progress_pct",
            "phase_progress_pct", "current", "total", "elapsed_ms",
        ):
            value = event.get(raw_key)
            if isinstance(value, (str, bool, int, float)) and value != "":
                payload[raw_key] = value[:240] if isinstance(value, str) else value
        metrics = event.get("metrics")
        if isinstance(metrics, dict):
            payload["metrics"] = {
                str(key)[:64]: value[:240] if isinstance(value, str) else value
                for key, value in metrics.items()
                if not any(fragment in str(key).lower() for fragment in blocked)
                and isinstance(value, (str, bool, int, float))
            }
        ReplicacaoD1ExecutionEvent.objects.create(
            run=run,
            phase=phase,
            status=state,
            message=str(event.get("message") or "")[:500],
            payload=payload,
        )
        return True
    except Exception as exc:
        log.debug("Falha best-effort ao persistir evento do planejamento: %s", exc)
        return False


def _resolve_backend_dir() -> Path | None:
    global _BACKEND_DIR
    if _BACKEND_DIR is not None:
        return _BACKEND_DIR

    env_dir = str(os.environ.get("PPLID_BACKEND_DIR", "") or "").strip()
    if env_dir:
        candidate = Path(env_dir)
        if (candidate / "config" / "settings.py").is_file():
            _BACKEND_DIR = candidate
            return _BACKEND_DIR

    anchors = [
        Path(__file__).resolve().parent.parent.parent.parent / "backend",
        Path(__file__).resolve().parent.parent.parent / "backend",
    ]
    for candidate in anchors:
        if (candidate / "config" / "settings.py").is_file():
            _BACKEND_DIR = candidate
            return _BACKEND_DIR
    return None


def ensure_django_ready() -> bool:
    """Try django.setup if apps not ready. Return False if unavailable."""
    global _DJANGO_READY
    if _DJANGO_READY:
        return True

    backend = _resolve_backend_dir()
    if backend is None:
        return False

    backend_str = str(backend)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    try:
        import django
        from django.apps import apps

        if not apps.ready:
            django.setup()
        _DJANGO_READY = True
        return True
    except Exception as exc:
        log.debug("Django indisponível para bridge D-1: %s", exc)
        return False


def is_fonte_banco_ativa(settings: dict | None = None) -> bool:
    """Prefer settings flag injected by robot_manager; else query Django ConfigGeral."""
    settings = settings or {}
    if "fonte_banco_ativa" in settings:
        return bool(settings.get("fonte_banco_ativa"))

    if not ensure_django_ready():
        return False

    try:
        from apps.replicacao_d1.services.config_snapshot import is_fonte_banco_ativa as _db_flag

        return bool(_db_flag())
    except Exception as exc:
        log.debug("Falha ao consultar fonte_banco_ativa: %s", exc)
        return False


def _run_options_from_settings(settings: dict) -> Any:
    from apps.replicacao_d1.services.config_dto import RunOptions

    return RunOptions(
        run_id=str(settings.get("run_id", "") or ""),
        data_ref=str(settings.get("replicacao_aud_data_ref", "") or ""),
        apenas_planejamento=bool(settings.get("apenas_planejamento")),
        gerar_novo_plano=bool(settings.get("gerar_novo_plano")),
        forcar_reexecucao=bool(settings.get("forcar_reexecucao")),
        apenas_pendentes=bool(settings.get("apenas_pendentes", True)),
        competencia_meta=str(settings.get("competencia_meta", "") or ""),
    )


def _execution_snapshot_from_dict(raw: dict) -> Any:
    from apps.replicacao_d1.services.config_dto import ExecutionSnapshot

    return ExecutionSnapshot(
        config_version=int(raw.get("config_version") or 0),
        config_hash=str(raw.get("config_hash") or ""),
        persistent=dict(raw.get("persistent") or {}),
        run_options=dict(raw.get("run_options") or {}),
        consumo_mensal=dict(raw.get("consumo_mensal") or {}),
        competencia_resolvida=str(raw.get("competencia_resolvida") or ""),
        created_at=str(raw.get("created_at") or ""),
    )


def _planning_calculadora_params(persistent: dict) -> dict:
    params = dict((persistent or {}).get("calculadora_params") or {})
    meta_diaria = (persistent or {}).get("meta_produ_diaria")
    if meta_diaria is not None:
        try:
            params["meta_produ"] = float(meta_diaria)
        except (TypeError, ValueError):
            pass
    meta_case = (persistent or {}).get("meta_produ_diaria_case")
    if meta_case is not None:
        try:
            params["meta_produ_case"] = float(meta_case)
        except (TypeError, ValueError):
            pass
    return params


_PERSISTENT_BRFLOW_SETTING_KEYS = (
    "replicacao_cliente_destino",
    "replicacao_cliente_cod",
    "replicacao_workflow_destino",
    "replicacao_workflow_cod",
    "replicacao_workflow_destino_31",
    "replicacao_workflow_cod_31",
    "replicacao_workflow_destino_bio",
    "replicacao_workflow_cod_bio",
    "replicacao_workflow_destino_redoc",
    "replicacao_workflow_cod_redoc",
    "meta_produ_diaria_bio",
    "meta_produ_diaria_redoc",
    "sincronizar_workflow_d1",
    "apenas_ativos",
)


def _apply_persistent_agendamento_settings(settings: dict, persistent: dict) -> None:
    """Propaga agendamento diário da config persistente para settings do bot."""
    if not isinstance(persistent, dict):
        return
    if "agendamento_ativo" in persistent:
        settings["agendamento_ativo"] = bool(persistent.get("agendamento_ativo"))
    for key in ("agendamento_hora_planejamento", "agendamento_hora_execucao"):
        raw = persistent.get(key)
        if raw is not None and str(raw).strip():
            settings[key] = str(raw).strip()


def resolve_agendamento_settings(settings: dict | None = None) -> dict:
    """
    Garante agendamento_ativo e horários a partir de settings, snapshot persistente ou DB.
    Usado ao iniciar o bot quando a config permanente está no PostgreSQL.
    """
    from app.bots.replicacao_d1.settings import parse_bool_setting
    from app.bots.replicacao_aud_d1_planning import _persistent_dict_from_settings

    settings = dict(settings or {})
    persistent = _persistent_dict_from_settings(settings)

    if not parse_bool_setting(settings.get("agendamento_ativo")):
        if persistent and parse_bool_setting(persistent.get("agendamento_ativo")):
            settings["agendamento_ativo"] = True

    for key in ("agendamento_hora_planejamento", "agendamento_hora_execucao"):
        if not str(settings.get(key) or "").strip():
            raw = (persistent or {}).get(key)
            if raw is not None and str(raw).strip():
                settings[key] = str(raw).strip()

    if not parse_bool_setting(settings.get("agendamento_ativo")):
        try:
            from apps.replicacao_d1.config_models import ReplicacaoD1ConfigGeral

            geral = ReplicacaoD1ConfigGeral.get_solo()
            if geral.agendamento_ativo:
                settings["agendamento_ativo"] = True
                if not str(settings.get("agendamento_hora_planejamento") or "").strip():
                    settings["agendamento_hora_planejamento"] = str(
                        geral.agendamento_hora_planejamento or ""
                    ).strip()
                if not str(settings.get("agendamento_hora_execucao") or "").strip():
                    settings["agendamento_hora_execucao"] = str(
                        geral.agendamento_hora_execucao or ""
                    ).strip()
        except Exception:
            pass

    return settings


def _apply_persistent_brflow_settings(settings: dict, persistent: dict) -> None:
    """Propaga campos de config persistente para settings usados na execução BRFlow."""
    if not isinstance(persistent, dict):
        return
    for key in _PERSISTENT_BRFLOW_SETTING_KEYS:
        if key not in persistent:
            continue
        raw = persistent.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            value = raw.strip()
        elif key.startswith("meta_produ_diaria_"):
            value = str(int(raw) if float(raw).is_integer() else raw)
        else:
            value = raw
        if value == "":
            continue
        settings[key] = value
    bio_meta = persistent.get("meta_produ_diaria_bio")
    if bio_meta is not None and str(bio_meta).strip() != "":
        settings["meta_produ_bio"] = str(int(bio_meta) if float(bio_meta).is_integer() else bio_meta)
    redoc_meta = persistent.get("meta_produ_diaria_redoc")
    if redoc_meta is not None and str(redoc_meta).strip() != "":
        settings["meta_produ_redoc"] = str(
            int(redoc_meta) if float(redoc_meta).is_integer() else redoc_meta
        )
    if "sincronizar_workflow_d1" in persistent:
        settings["replicacao_sincronizar_workflow_d1"] = bool(persistent.get("sincronizar_workflow_d1"))
    if "apenas_ativos" in persistent:
        settings["replicacao_apenas_ativos"] = bool(persistent.get("apenas_ativos"))
    for key in (
        "replicacao_destino_brflow_ativo",
        "replicacao_destino_case31_ativo",
        "replicacao_destino_bio_ativo",
        "replicacao_destino_redoc_ativo",
    ):
        if key in persistent:
            settings[key] = bool(persistent.get(key))


def _freeze_snapshot_on_settings(settings: dict, snapshot: Any, frames: dict[str, pd.DataFrame]) -> None:
    settings["_execution_snapshot"] = snapshot.to_dict()
    settings["config_version"] = snapshot.config_version
    settings["config_hash"] = snapshot.config_hash
    settings["fonte_banco_ativa"] = True
    settings["_mapa_workflow_d1_df"] = frames.get("mapa_workflow_d1")
    settings["_categoria_clientes_df"] = frames.get("categoria_clientes")
    settings["_escala_df"] = frames.get("escala_auditores")
    persistent = snapshot.persistent or {}
    _apply_persistent_agendamento_settings(settings, persistent)
    _apply_persistent_brflow_settings(settings, persistent)
    calc = _planning_calculadora_params(persistent)
    settings["_calculadora_params"] = calc
    if "meta_produ" in calc:
        settings["meta_produ"] = str(calc["meta_produ"])
    if "meta_produ_case" in calc:
        settings["meta_produ_case"] = str(calc["meta_produ_case"])
    settings["usar_amostra_mix_manual_automatico"] = bool(
        persistent.get("usar_amostra_mix_manual_automatico", False)
    )
    try:
        settings["amostra_pct_manual"] = int(persistent.get("amostra_pct_manual", 70))
    except (TypeError, ValueError):
        settings["amostra_pct_manual"] = 70
    try:
        settings["amostra_pct_automatico"] = int(persistent.get("amostra_pct_automatico", 30))
    except (TypeError, ValueError):
        settings["amostra_pct_automatico"] = 30
    settings["fallback_parquet_dias_ausentes"] = bool(
        persistent.get("fallback_parquet_dias_ausentes", False)
    )
    if snapshot.competencia_resolvida:
        settings["_competencia_meta_resolvida"] = snapshot.competencia_resolvida


def ensure_planned_run_db(
    run_id: str,
    *,
    data_referencia_d1: datetime | Any = None,
    parquet_referencia: str = "",
) -> None:
    """Garante registro planned no banco antes do planejamento."""
    if not ensure_django_ready():
        return
    from apps.replicacao_d1.services.run_lifecycle import ensure_planned_run

    data_ref = data_referencia_d1
    if hasattr(data_ref, "date"):
        data_ref = data_ref.date()
    ensure_planned_run(
        str(run_id),
        data_referencia_d1=data_ref,
        parquet_referencia=str(parquet_referencia or ""),
    )
    plan_log(
        log,
        logging.INFO,
        "Run planned registrado",
        run_id=str(run_id),
        phase=PLANNING_START,
        data_ref=str(data_ref),
    )


def mark_run_failed_db(
    run_id: str,
    *,
    error_code: str = "PLANNING_FAILED",
    error_summary: str = "",
) -> None:
    """Registra falha de planejamento no banco quando a fonte oficial está ativa."""
    if not ensure_django_ready():
        return
    from apps.replicacao_d1.services.run_lifecycle import mark_run_failed

    mark_run_failed(str(run_id or ""), error_code=error_code, error_summary=error_summary)


def mark_run_started_db(run_id: str) -> None:
    """Marca o início real da execução BRFlow no banco oficial."""
    if not ensure_django_ready():
        raise RuntimeError("PostgreSQL indisponível para iniciar o run D-1.")
    from apps.replicacao_d1.services.run_lifecycle import mark_run_started

    mark_run_started(str(run_id or ""))


def load_planning_dataframes(
    settings: dict | None = None,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Returns dict with keys: mapa_workflow_d1, categoria_clientes, escala_auditores,
    calculadora_params, persistent (dict), snapshot_hash, config_version,
    consumo_mensal (optional).

    Raises clear error if fonte active but Django/DB unavailable — NO Excel fallback.
    Freezes snapshot onto settings['_execution_snapshot'] for reuse.
    """
    if settings is None:
        settings = {}
    if not is_fonte_banco_ativa(settings):
        raise RuntimeError(
            "load_planning_dataframes requer fonte_banco_ativa=True."
        )

    if not ensure_django_ready():
        raise RuntimeError(
            "fonte_banco_ativa=True, mas Django/PostgreSQL indisponível no subprocess. "
            "Verifique PPLID_BACKEND_DIR, conexão com o banco e DJANGO_SETTINGS_MODULE."
        )

    from apps.replicacao_d1.exceptions import ConfigBancoIndisponivelError, ConfigIncompletaError
    from apps.replicacao_d1.services.planning_adapter import (
        load_snapshot_for_planning,
        snapshot_to_dataframes,
    )

    rid = run_id or str(settings.get("run_id", "") or "").strip() or None
    if not settings.get("_execution_snapshot") and rid:
        persisted = load_snapshot_from_run_id(rid)
        if persisted:
            frames = snapshot_to_dataframes(persisted)
            _freeze_snapshot_on_settings(settings, persisted, frames)
            plan_log(
                log,
                logging.INFO,
                "Snapshot restaurado do run",
                run_id=rid,
                phase=PLANNING_CONFIG,
                config_version=persisted.config_version,
                config_hash=truncate_hash(persisted.config_hash),
                source="run_snapshot",
            )

    cached = settings.get("_execution_snapshot")
    if cached:
        snapshot = _execution_snapshot_from_dict(cached)
        if settings.get("_mapa_workflow_d1_df") is None:
            frames = snapshot_to_dataframes(snapshot)
            _freeze_snapshot_on_settings(settings, snapshot, frames)
        else:
            frames = {
                "mapa_workflow_d1": settings["_mapa_workflow_d1_df"],
                "categoria_clientes": settings.get("_categoria_clientes_df", pd.DataFrame()),
                "escala_auditores": settings.get("_escala_df", pd.DataFrame()),
            }
        calc = dict(settings.get("_calculadora_params") or _planning_calculadora_params(snapshot.persistent or {}))
        persistent = snapshot.persistent or {}
        plan_log(
            log,
            logging.INFO,
            "Snapshot em cache",
            run_id=rid or "",
            phase=PLANNING_CONFIG,
            config_version=snapshot.config_version,
            config_hash=truncate_hash(snapshot.config_hash),
            workflows=len(persistent.get("workflows") or []),
            escala_dias=len(persistent.get("escala") or []),
            source="cache",
        )
        return {
            **frames,
            "calculadora_params": calc,
            "persistent": dict(snapshot.persistent or {}),
            "snapshot_hash": snapshot.config_hash,
            "config_version": snapshot.config_version,
            "consumo_mensal": dict(snapshot.consumo_mensal or {}),
            "competencia_resolvida": snapshot.competencia_resolvida,
        }

    run_options = _run_options_from_settings(settings)

    try:
        snapshot = load_snapshot_for_planning(run_options, run_id=rid)
    except ConfigIncompletaError as exc:
        raise RuntimeError(
            f"Configuração D-1 incompleta no banco: {exc}"
        ) from exc
    except ConfigBancoIndisponivelError as exc:
        raise RuntimeError(str(exc)) from exc

    frames = snapshot_to_dataframes(snapshot)
    _freeze_snapshot_on_settings(settings, snapshot, frames)

    calc = _planning_calculadora_params(snapshot.persistent or {})
    persistent = snapshot.persistent or {}
    plan_log(
        log,
        logging.INFO,
        "Snapshot carregado do banco",
        run_id=rid or "",
        phase=PLANNING_CONFIG,
        config_version=snapshot.config_version,
        config_hash=truncate_hash(snapshot.config_hash),
        workflows=len(persistent.get("workflows") or []),
        escala_dias=len(persistent.get("escala") or []),
        competencia=snapshot.competencia_resolvida or "",
        source="fresh",
    )
    return {
        **frames,
        "calculadora_params": calc,
        "persistent": dict(snapshot.persistent or {}),
        "snapshot_hash": snapshot.config_hash,
        "config_version": snapshot.config_version,
        "consumo_mensal": dict(snapshot.consumo_mensal or {}),
        "competencia_resolvida": snapshot.competencia_resolvida,
    }


def register_pending_workflow(nome: str, cliente: str | None = None) -> tuple[str, bool]:
    """Cria workflow pendente/inativo no banco; retorna (chave, created)."""
    if not ensure_django_ready():
        raise RuntimeError(
            "fonte_banco_ativa=True, mas Django indisponível para registrar workflow pendente."
        )
    from apps.replicacao_d1.services.planning_adapter import registrar_workflow_desconhecido_parquet

    return registrar_workflow_desconhecido_parquet(nome, cliente_nome=cliente)


def carregar_consumo_meta_mensal_db(competencia: str) -> dict[str, int]:
    if not ensure_django_ready():
        raise RuntimeError(
            "fonte_banco_ativa=True, mas Django indisponível para ler ledger mensal."
        )
    from apps.replicacao_d1.services.ledger import carregar_consumo_meta_mensal

    return carregar_consumo_meta_mensal(str(competencia))


def registrar_consumo_meta_run_db(
    *,
    competencia: str,
    run_id: str,
    consumo_por_workflow: dict[str, int],
    cliente_por_workflow: dict[str, str] | None = None,
    origem: str = "confirmado",
    observacao: str = "",
    data_execucao: datetime | None = None,
    snapshot_hash: str = "",
    config_version: int | None = None,
) -> bool:
    if not ensure_django_ready():
        raise RuntimeError(
            "fonte_banco_ativa=True, mas Django indisponível para gravar ledger mensal."
        )
    from apps.replicacao_d1.services.ledger import registrar_consumo_meta_run

    return registrar_consumo_meta_run(
        competencia=str(competencia),
        run_id=str(run_id),
        consumo_por_workflow=consumo_por_workflow or {},
        cliente_por_workflow=cliente_por_workflow,
        origem=str(origem),
        observacao=str(observacao or ""),
        data_execucao=data_execucao,
        snapshot_hash=str(snapshot_hash or ""),
        config_version=config_version,
    )


def load_snapshot_from_run_id(run_id: str) -> Any | None:
    """Carrega snapshot persistido pelo run_id (subprocesso sem env completo)."""
    if not ensure_django_ready():
        return None
    try:
        from apps.replicacao_d1.config_models import ReplicacaoD1ConfigSnapshot
    except ImportError:
        return None

    snap = ReplicacaoD1ConfigSnapshot.objects.filter(run_id=str(run_id).strip()).first()
    if not snap or not snap.snapshot_json:
        return None
    return _execution_snapshot_from_dict(snap.snapshot_json)


def settings_for_d1_env_json(settings: dict) -> dict:
    """Remove snapshot/DataFrames do payload de ambiente quando fonte=banco."""
    settings = dict(settings or {})
    skip = {
        "_execution_snapshot",
        "_mapa_workflow_d1_df",
        "_categoria_clientes_df",
        "_escala_df",
        "_calculadora_params",
        "_competencia_meta_resolvida",
    }
    if is_fonte_banco_ativa(settings):
        allowed = {
            "apenas_planejamento",
            "run_id",
            "gerar_novo_plano",
            "forcar_reexecucao",
            "apenas_pendentes",
            "replicacao_aud_data_ref",
            "usar_escala_auditores",
            "headless",
            "fonte_banco_ativa",
            "fallback_parquet_dias_ausentes",
            "config_version",
            "config_hash",
            "competencia_meta",
            "agendamento_ativo",
            "agendamento_hora_planejamento",
            "agendamento_hora_execucao",
        }
        slim = {k: v for k, v in settings.items() if k in allowed and v is not None}
        rid = str(settings.get("run_id") or "").strip()
        if rid:
            slim["run_id"] = rid
        if settings.get("config_version") is not None:
            slim["config_version"] = settings["config_version"]
        if settings.get("config_hash"):
            slim["config_hash"] = settings["config_hash"]
        slim["fonte_banco_ativa"] = True
        return slim
    return {k: v for k, v in settings.items() if k not in skip}


def avaliar_retencao_artifact_run_db(
    run_id: str,
    *,
    min_age_days: int = 0,
    require_ingestion: bool = True,
) -> dict | None:
    """Consulta gate de retenção no banco. None se Django indisponível."""
    if not ensure_django_ready():
        return None
    try:
        from apps.replicacao_d1.services.artifact_retention import evaluate_run_retention_gate

        return evaluate_run_retention_gate(
            str(run_id or "").strip(),
            min_age_days=int(min_age_days or 0),
            require_ingestion=bool(require_ingestion),
        ).as_dict()
    except Exception as exc:
        log.debug("Gate retenção indisponível | run=%s | %s", run_id, exc)
        return None


def load_retro_config_db() -> dict[str, Any] | None:
    """Carrega apenas a politica retroativa, sem exigir fonte de protocolos no banco."""
    if not ensure_django_ready():
        return None
    try:
        from apps.replicacao_d1.services.config_snapshot import load_persistent_config

        persistent = load_persistent_config().to_dict()
        return dict(persistent.get("retroativo") or {})
    except Exception as exc:
        log.debug("Falha ao carregar configuracao retroativa: %s", exc)
        return None


def try_inject_execution_snapshot(settings: dict | None = None) -> dict:
    """
    Quando fonte_banco_ativa, carrega snapshot congelado em settings.
    Silencioso se Django indisponível e flag não está ativa.
    """
    settings = dict(settings or {})
    if not is_fonte_banco_ativa(settings):
        return settings
    if settings.get("_execution_snapshot") and settings.get("_escala_df") is not None:
        plan_log(
            log,
            logging.INFO,
            "Snapshot já presente em settings",
            run_id=str(settings.get("run_id") or ""),
            phase=PLANNING_CONFIG,
            config_version=settings.get("config_version", ""),
            config_hash=truncate_hash(settings.get("config_hash")),
            source="injected",
        )
        return settings
    load_planning_dataframes(settings)
    return settings


def load_source_dataframe_db(data_ref: datetime | Any = None) -> dict[str, Any]:
    """Carrega a partição da Rotina no contrato auditável esperado pelo planejamento."""
    if not ensure_django_ready():
        raise RuntimeError("Django/PostgreSQL indisponível para carregar a fonte D-1.")
    from apps.replicacao_d1.services.source_batch import source_batch_from_rotina

    report_date = data_ref.date() if hasattr(data_ref, "date") else data_ref
    batch = source_batch_from_rotina(report_date).batch
    return _source_dict_from_batch(batch)


def _source_dict_from_batch(
    batch: Any,
    *,
    source_name: str | None = None,
    from_parquet_fallback: bool = False,
) -> dict[str, Any]:
    from apps.replicacao_d1.models import ReplicacaoD1FonteRegistro

    rows = list(
        batch.registros.order_by("source_row_number").values(
            "id",
            "protocolo",
            "protocolo_normalizado",
            "workflow",
            "data_analise",
            "hora",
            "matricula_tipo",
            "matricula",
        )
    )
    flag_map = {
        ReplicacaoD1FonteRegistro.MATRICULA_MANUAL: 0,
        ReplicacaoD1FonteRegistro.MATRICULA_AUTOMATICA: 1,
    }
    frame = pd.DataFrame(
        [
            {
                "Protocolo": row["protocolo"],
                "Workflow": row["workflow"],
                "Data de Análise": row["data_analise"],
                "matrícula": flag_map.get(row["matricula_tipo"], row["matricula"]),
                "_source_record_id": row["id"],
                "_protocolo_normalizado": row["protocolo_normalizado"],
            }
            for row in rows
        ]
    )
    resolved_name = source_name or f"postgresql:rotina_detalhado_bruto_record:{batch.report_date.isoformat()}"
    return {
        "dataframe": frame,
        "source_batch_id": batch.pk,
        "source_batch_hash": batch.content_hash,
        "source_name": resolved_name,
        "report_date": batch.report_date,
        "from_parquet_fallback": from_parquet_fallback,
    }


def fallback_parquet_dias_ausentes_ativo(settings: dict | None = None) -> bool:
    settings = dict(settings or {})
    if "fallback_parquet_dias_ausentes" in settings:
        return bool(settings["fallback_parquet_dias_ausentes"])
    snap = settings.get("_execution_snapshot")
    if isinstance(snap, dict):
        persistent = snap.get("persistent")
        if isinstance(persistent, dict) and "fallback_parquet_dias_ausentes" in persistent:
            return bool(persistent["fallback_parquet_dias_ausentes"])
    persistent = settings.get("persistent")
    if isinstance(persistent, dict) and "fallback_parquet_dias_ausentes" in persistent:
        return bool(persistent["fallback_parquet_dias_ausentes"])
    return False


def load_source_from_parquet_fallback_db(
    data_ref: datetime | Any = None,
    *,
    warnings: list[str] | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Materializa a fonte D-1 a partir do parquet tratado do dia exato."""
    if not ensure_django_ready():
        raise RuntimeError("Django/PostgreSQL indisponível para carregar fallback parquet D-1.")
    from apps.replicacao_d1.services.source_batch import source_batch_from_parquet_rows
    from app.bots.replicacao_aud_d1_planning import _validar_parquet, ler_parquet, resolver_parquet_d1

    report_date = data_ref.date() if hasattr(data_ref, "date") else data_ref
    parquet_path = resolver_parquet_d1(data_ref=data_ref, fallback_ultimo=False)
    df = ler_parquet(parquet_path, log=log)
    _validar_parquet(df)
    batch_result = source_batch_from_parquet_rows(report_date, df.to_dict("records"))
    batch = batch_result.batch
    msg = (
        f"Fonte D-1: dia {report_date.isoformat()} ausente no banco; "
        f"carregado via parquet {parquet_path.name}."
    )
    log.warning(msg)
    if warnings is not None:
        warnings.append(msg)
    plan_log(
        log,
        logging.WARNING,
        "Fonte D-1 via parquet fallback",
        run_id=run_id,
        phase=PLANNING_SOURCE,
        batch_id=batch.pk,
        batch_created=batch_result.created,
        content_hash=truncate_hash(batch.content_hash),
        parquet=parquet_path.name,
        rows=len(df),
        report_date=report_date.isoformat(),
    )
    return _source_dict_from_batch(
        batch,
        source_name=f"parquet-fallback:{parquet_path.name}",
        from_parquet_fallback=True,
    )


def load_source_dataframe_hybrid_db(
    data_ref: datetime | Any = None,
    *,
    settings: dict | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Banco primeiro; parquet tratado do dia exato quando partição Rotina vazia."""
    if not ensure_django_ready():
        raise RuntimeError("Django/PostgreSQL indisponível para carregar a fonte D-1.")
    from apps.replicacao_d1.services.source_batch import rotina_day_has_records
    from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

    report_date = data_ref.date() if hasattr(data_ref, "date") else data_ref
    run_id = str((settings or {}).get("run_id") or "")
    if rotina_day_has_records(report_date):
        result = load_source_dataframe_db(data_ref)
        plan_log(
            log,
            logging.INFO,
            "Fonte híbrida: banco",
            run_id=run_id,
            phase=PLANNING_SOURCE,
            source="db",
            batch_id=result.get("source_batch_id"),
            rows=len(result.get("dataframe", [])),
            report_date=report_date.isoformat(),
        )
        return result
    if not fallback_parquet_dias_ausentes_ativo(settings):
        raise RotinaDetalhadoBrutoRecord.DoesNotExist(
            "Nenhum registro D-1 encontrado em rotina_detalhado_bruto_record "
            f"para {report_date.isoformat()}."
        )
    plan_log(
        log,
        logging.WARNING,
        "Fonte híbrida: partição vazia, tentando parquet",
        run_id=run_id,
        phase=PLANNING_SOURCE,
        report_date=report_date.isoformat(),
    )
    return load_source_from_parquet_fallback_db(data_ref, warnings=warnings, run_id=run_id)


def _retro_rows_to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    from apps.replicacao_d1.models import ReplicacaoD1FonteRegistro

    flag_map = {
        ReplicacaoD1FonteRegistro.MATRICULA_MANUAL: 0,
        ReplicacaoD1FonteRegistro.MATRICULA_AUTOMATICA: 1,
    }
    frame_rows = []
    for row in rows:
        extra = row.get("extra") or {}
        report_day = str(extra.get("retroativo_report_date") or "")[:10]
        suffix = ":parquet" if extra.get("retroativo_source") == "parquet" else ""
        reason_day = report_day or str(row.get("data_analise") or "")[:10]
        frame_rows.append(
            {
                "Protocolo": row["protocolo"],
                "Workflow": row["workflow"],
                "Data de Análise": row["data_analise"],
                "matrícula": flag_map.get(row["matricula_tipo"], row.get("matricula")),
                "_protocolo_normalizado": row["protocolo_normalizado"],
                "_selection_reason": f"retroativo:{reason_day}{suffix}",
                "_matricula_tipo": row["matricula_tipo"],
            }
        )
    columns = [
        "Protocolo",
        "Workflow",
        "Data de Análise",
        "matrícula",
        "_protocolo_normalizado",
        "_selection_reason",
    ]
    return pd.DataFrame(frame_rows) if frame_rows else pd.DataFrame(columns=columns)


def _enriquecer_horarios_retroativo_parquet(
    db_df: pd.DataFrame,
    parquet_df: pd.DataFrame,
) -> pd.DataFrame:
    """Substitui timestamps só-data (Rotina) pelos horários reais do parquet tratado."""
    from app.bots.replicacao_aud_planning import (
        COLUNA_DATA_ANALISE,
        COLUNA_PROTOCOLO,
        _data_analise_sem_hora_util,
        _parse_data_analise,
        _protocolo_chave_plano,
    )

    if db_df.empty or parquet_df.empty:
        return db_df
    required = {COLUNA_PROTOCOLO, COLUNA_DATA_ANALISE}
    if not required.issubset(db_df.columns) or not required.issubset(parquet_df.columns):
        return db_df

    out = db_df.copy()
    pq = parquet_df[[COLUNA_PROTOCOLO, COLUNA_DATA_ANALISE]].copy()
    pq["_pk"] = pq[COLUNA_PROTOCOLO].astype(str).map(_protocolo_chave_plano)
    pq = pq[pq["_pk"].astype(bool)].drop_duplicates("_pk", keep="last")
    parquet_dates = pq.set_index("_pk")[COLUNA_DATA_ANALISE]

    out["_pk"] = out[COLUNA_PROTOCOLO].astype(str).map(_protocolo_chave_plano)
    parsed = _parse_data_analise(out[COLUNA_DATA_ANALISE])
    needs = _data_analise_sem_hora_util(parsed)
    if not needs.any():
        return out.drop(columns=["_pk"], errors="ignore")

    mapped = out.loc[needs, "_pk"].map(parquet_dates)
    fill_idx = mapped.index[mapped.notna()]
    if len(fill_idx):
        out.loc[fill_idx, COLUNA_DATA_ANALISE] = mapped.loc[fill_idx].values
    return out.drop(columns=["_pk"], errors="ignore")


def load_retro_source_hybrid_db_parquet(
    settings: dict | None,
    data_ref: datetime | Any,
    retro_config: dict[str, Any],
    plano: Any,
) -> pd.DataFrame:
    """Retroativo híbrido: banco por dia, parquet tratado quando partição vazia."""
    if not ensure_django_ready():
        raise RuntimeError("Django/PostgreSQL indisponível para carregar retroativo D-1.")
    from datetime import timedelta

    from apps.replicacao_d1.services.source_batch import source_rows_by_day_range_from_rotina
    from app.bots.replicacao_aud_d1_planning import _carregar_dia_parquet_tratado

    inicio = date.fromisoformat(str(retro_config["data_inicio"])[:10])
    fim = date.fromisoformat(str(retro_config["data_fim"])[:10])
    workflow_keys = set(retro_config.get("workflow_d1_keys") or [])
    frames: list[pd.DataFrame] = []
    parquet_days = 0
    db_days = 0
    missing_days = 0
    run_id = str((settings or {}).get("run_id") or getattr(plano, "run_id", "") or "")
    rows_by_day = source_rows_by_day_range_from_rotina(
        inicio,
        fim,
        workflow_names=workflow_keys,
    )
    dia = inicio
    while dia <= fim:
        rows = rows_by_day.get(dia, [])
        if rows:
            day_df = _retro_rows_to_dataframe(rows)
            if fallback_parquet_dias_ausentes_ativo(settings):
                parquet_df = _carregar_dia_parquet_tratado(
                    dia,
                    workflow_keys,
                    plano,
                    parquet_suffix=":parquet",
                )
                if not parquet_df.empty:
                    day_df = _enriquecer_horarios_retroativo_parquet(day_df, parquet_df)
            frames.append(day_df)
            db_days += 1
        elif fallback_parquet_dias_ausentes_ativo(settings):
            day_df = _carregar_dia_parquet_tratado(
                dia,
                workflow_keys,
                plano,
                parquet_suffix=":parquet",
            )
            if not day_df.empty:
                frames.append(day_df)
                parquet_days += 1
            else:
                missing_days += 1
                plano.warnings.append(
                    f"Retroativo: dia {dia.isoformat()} ausente no banco e parquet tratado."
                )
        else:
            missing_days += 1
            plano.warnings.append(
                f"Retroativo: dia {dia.isoformat()} ausente no banco (fallback parquet desligado)."
            )
        dia += timedelta(days=1)
    if parquet_days:
        plano.warnings.append(
            f"Retroativo: {parquet_days} dia(s) completado(s) via parquet (ausentes no banco)."
        )
    if not frames:
        plan_log(
            log,
            logging.WARNING,
            "Retroativo híbrido vazio",
            run_id=run_id,
            phase=PLANNING_RETRO,
            intervalo=f"{inicio.isoformat()}..{fim.isoformat()}",
            days_db=db_days,
            days_parquet=parquet_days,
            days_missing=missing_days,
            rows_total=0,
        )
        return pd.DataFrame(
            columns=[
                "Protocolo",
                "Workflow",
                "Data de Análise",
                "matrícula",
                "_protocolo_normalizado",
                "_selection_reason",
            ]
        )
    merged = pd.concat(frames, ignore_index=True)
    plan_log(
        log,
        logging.INFO,
        "Retroativo híbrido carregado",
        run_id=run_id,
        phase=PLANNING_RETRO,
        intervalo=f"{inicio.isoformat()}..{fim.isoformat()}",
        days_db=db_days,
        days_parquet=parquet_days,
        days_missing=missing_days,
        rows_total=len(merged),
    )
    return merged


def load_retro_source_dataframe_db(
    settings: dict | None,
    data_ref: datetime | Any,
    retro_config: dict[str, Any],
) -> pd.DataFrame:
    """Carrega protocolos retroativos da Rotina dia a dia (sem dedup cross-day)."""
    if not ensure_django_ready():
        raise RuntimeError("Django/PostgreSQL indisponível para carregar retroativo D-1.")
    from datetime import timedelta

    from apps.replicacao_d1.services.source_batch import source_rows_by_day_range_from_rotina

    inicio = date.fromisoformat(str(retro_config["data_inicio"])[:10])
    fim = date.fromisoformat(str(retro_config["data_fim"])[:10])
    workflow_keys = set(retro_config.get("workflow_d1_keys") or [])
    frames: list[pd.DataFrame] = []
    db_days = 0
    missing_days = 0
    run_id = str((settings or {}).get("run_id") or "")
    rows_by_day = source_rows_by_day_range_from_rotina(
        inicio,
        fim,
        workflow_names=workflow_keys,
    )
    dia = inicio
    while dia <= fim:
        rows = rows_by_day.get(dia, [])
        if rows:
            frames.append(_retro_rows_to_dataframe(rows))
            db_days += 1
        else:
            missing_days += 1
        dia += timedelta(days=1)
    if not frames:
        plan_log(
            log,
            logging.WARNING,
            "Retroativo DB vazio",
            run_id=run_id,
            phase=PLANNING_RETRO,
            intervalo=f"{inicio.isoformat()}..{fim.isoformat()}",
            days_db=db_days,
            days_missing=missing_days,
            rows_total=0,
        )
        return pd.DataFrame(
            columns=[
                "Protocolo",
                "Workflow",
                "Data de Análise",
                "matrícula",
                "_protocolo_normalizado",
                "_selection_reason",
            ]
        )
    merged = pd.concat(frames, ignore_index=True)
    plan_log(
        log,
        logging.INFO,
        "Retroativo DB carregado",
        run_id=run_id,
        phase=PLANNING_RETRO,
        intervalo=f"{inicio.isoformat()}..{fim.isoformat()}",
        days_db=db_days,
        days_missing=missing_days,
        rows_total=len(merged),
    )
    return merged


def ingest_source_dataframe_db(df: pd.DataFrame, data_ref: str | datetime) -> dict[str, Any]:
    """Publica a extração detalhada da Rotina como lote oficial da Replicação D-1."""
    if not ensure_django_ready():
        raise RuntimeError("Django/PostgreSQL indisponível para publicar a fonte D-1.")
    from apps.replicacao_d1.services.source_batch import ingest_source_rows

    if hasattr(data_ref, "date"):
        report_date = data_ref.date()
    else:
        text = str(data_ref or "").strip()
        report_date = datetime.strptime(text, "%Y%m%d").date()
    rows = df.to_dict("records") if df is not None else []
    result = ingest_source_rows(report_date, rows)
    return {
        "source_batch_id": result.batch.pk,
        "created": result.created,
        "rows_read": result.batch.rows_read,
        "rows_valid": result.batch.rows_valid,
        "rows_duplicate": result.batch.rows_duplicate,
        "rows_rejected": result.batch.rows_rejected,
        "content_hash": result.batch.content_hash,
    }


def load_historical_protocols_db(
    *,
    excluir_run_id: str = "",
    dias_historico: int = 30,
    referencia: datetime | None = None,
) -> set[str]:
    if not ensure_django_ready():
        raise RuntimeError("PostgreSQL indisponível para consultar o histórico D-1.")
    from apps.replicacao_d1.models import ReplicacaoD1Protocolo

    ref = referencia or datetime.now()
    limite = ref.date() - timedelta(days=max(0, int(dias_historico or 0)))
    qs = ReplicacaoD1Protocolo.objects.filter(data_referencia_d1__gte=limite)
    if excluir_run_id:
        qs = qs.exclude(run_id=excluir_run_id)
    return set(qs.values_list("protocolo", flat=True).iterator(chunk_size=5000))


def persist_plan_db(
    plano: Any,
    *,
    settings: dict,
    source_batch_id: int,
) -> Any:
    """Converte o DTO legado em registros relacionais, sem criar Excel/CSV/JSON."""
    if not ensure_django_ready():
        raise RuntimeError("PostgreSQL indisponível para persistir o plano D-1.")
    from apps.replicacao_d1.config_models import ReplicacaoD1ConfigSnapshot
    from apps.replicacao_d1.models import ReplicacaoD1FonteRegistro
    from apps.replicacao_d1.normalization import normalize_protocolo
    from apps.replicacao_d1.services.plan_validation import persist_plan

    qtd_por_workflow = getattr(plano, "qtd_por_workflow", {}) or {}
    workflow_modo_replicacao = getattr(plano, "workflow_modo_replicacao", {}) or {}
    summaries = {
        str(row.get("Workflow") or "").strip(): row
        for row in (plano.resumo or [])
        if str(row.get("Workflow") or "").strip() and str(row.get("Workflow") or "").strip() != "TOTAL"
    }

    selected_norms: list[str] = []
    selected_norms_seen: set[str] = set()
    for workflow in plano.workflows:
        summary = summaries.get(workflow, {})
        fila = str(summary.get("Fila") or plano.workflow_fila.get(workflow, "") or "")
        modo_replicacao = _resolver_modo_replicacao_persistido(
            workflow_modo_replicacao.get(workflow) if isinstance(workflow_modo_replicacao, dict) else None,
            fila,
        )
        if modo_replicacao == "qtd":
            continue
        for protocolo in plano.protocolos_por_workflow.get(workflow, []):
            norm = normalize_protocolo(protocolo)
            if norm and norm not in selected_norms_seen:
                selected_norms_seen.add(norm)
                selected_norms.append(norm)

    source_rows: list[dict[str, Any]] = []
    for offset in range(0, len(selected_norms), _SOURCE_PROTOCOL_QUERY_CHUNK_SIZE):
        norm_chunk = selected_norms[offset:offset + _SOURCE_PROTOCOL_QUERY_CHUNK_SIZE]
        source_rows.extend(
            ReplicacaoD1FonteRegistro.objects.filter(
                lote_id=source_batch_id,
                protocolo_normalizado__in=norm_chunk,
            ).order_by("source_row_number").values(
                "id", "protocolo", "protocolo_normalizado", "workflow", "data_analise",
                "hora", "matricula_tipo",
            )
        )
    by_key: dict[tuple[str, str], deque] = defaultdict(deque)
    by_protocol: dict[str, deque] = defaultdict(deque)
    for row in source_rows:
        key = (str(row["protocolo_normalizado"]), str(row["workflow"] or "").strip().casefold())
        by_key[key].append(row)
        by_protocol[str(row["protocolo_normalizado"])].append(row)

    protocol_rows: list[dict[str, Any]] = []
    protocolos_por_workflow: dict[str, list[str]] = defaultdict(list)
    selected_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"manual": 0, "automatico": 0})
    retro_counts: dict[str, int] = defaultdict(int)
    selection_map = getattr(plano, "selection_reason_por_protocolo", {}) or {}
    meta_map = getattr(plano, "protocolo_meta_por_chave", {}) or {}
    vistos_norm: set[str] = set()
    for workflow in plano.workflows:
        summary = summaries.get(workflow, {})
        fila = str(summary.get("Fila") or plano.workflow_fila.get(workflow, "") or "")
        modo_replicacao = _resolver_modo_replicacao_persistido(
            workflow_modo_replicacao.get(workflow) if isinstance(workflow_modo_replicacao, dict) else None,
            fila,
        )
        if modo_replicacao == "qtd":
            continue
        workflow_d1 = str(summary.get("Workflow D1") or workflow).strip()
        canal = str(summary.get("Canal Destino") or "").strip()
        for protocolo in plano.protocolos_por_workflow.get(workflow, []):
            norm = normalize_protocolo(protocolo)
            if not norm or norm in vistos_norm:
                continue
            vistos_norm.add(norm)
            protocolos_por_workflow[workflow].append(str(protocolo))
            key = (norm, workflow_d1.casefold())
            meta_key = f"{workflow}|{norm}"
            source = by_key[key].popleft() if by_key[key] else None
            if source is None and by_protocol[norm]:
                source = by_protocol[norm].popleft()
            extra_meta = meta_map.get(meta_key) or {}
            matricula_tipo = str(
                (source or {}).get("matricula_tipo") or extra_meta.get("matricula_tipo") or "desconhecido"
            )
            if matricula_tipo in selected_counts[workflow]:
                selected_counts[workflow][matricula_tipo] += 1
            selection_reason = str(selection_map.get(meta_key) or "amostra_d1")
            if selection_reason.startswith("retroativo:"):
                retro_counts[workflow] += 1
            if selection_reason.startswith("retroativo:"):
                data_analise = extra_meta.get("data_analise") or (source or {}).get("data_analise")
                hora = extra_meta.get("hora")
            else:
                data_analise = (source or {}).get("data_analise") or extra_meta.get("data_analise")
                hora = (
                    (source or {}).get("hora")
                    if (source or {}).get("hora") is not None
                    else extra_meta.get("hora")
                )
            protocol_rows.append(
                {
                    "protocolo": str(protocolo),
                    "protocolo_normalizado": norm,
                    "workflow_config": workflow,
                    "workflow_d1": workflow_d1,
                    "data_analise": data_analise,
                    "hora": hora,
                    "canal_destino": canal,
                    "matricula_tipo": matricula_tipo,
                    "selection_reason": selection_reason,
                    "source_record_id": (source or {}).get("id"),
                }
            )

    workflow_rows: list[dict[str, Any]] = []
    for workflow in plano.workflows:
        row = summaries.get(workflow, {})
        observation = str(row.get("Observacao") or "").strip()
        fila = str(row.get("Fila") or plano.workflow_fila.get(workflow, "") or "")
        modo_replicacao = _resolver_modo_replicacao_persistido(
            workflow_modo_replicacao.get(workflow) if isinstance(workflow_modo_replicacao, dict) else None,
            fila,
        )
        if modo_replicacao == "qtd":
            qtd_planejada = int(qtd_por_workflow.get(workflow, 0) or row.get("Amostra Efetiva") or 0)
            protocolos_planejados = qtd_planejada
        else:
            protocolos_planejados = len(protocolos_por_workflow.get(workflow, []))
        workflow_rows.append(
            {
                "workflow_config": workflow,
                "workflow_d1": str(row.get("Workflow D1") or workflow),
                "workflow_brflow": str(plano.workflow_brflow.get(workflow, workflow)),
                "canal_destino": str(row.get("Canal Destino") or ""),
                "cliente": str(row.get("Cliente") or ""),
                "segmento": str(row.get("Segmento") or ""),
                "categoria": str(row.get("Categoria") or ""),
                "fila": str(row.get("Fila") or plano.workflow_fila.get(workflow, "")),
                "modo_replicacao": modo_replicacao,
                "amostra_diaria": int(row.get("Amostra Diaria") or 0),
                "amostra_solicitada": int(row.get("Amostra Solicitada") or 0) if str(row.get("Amostra Solicitada") or "").isdigit() else None,
                "amostra_efetiva": int(row.get("Amostra Efetiva") or 0),
                "protocolos_planejados": protocolos_planejados,
                "disponivel_d1": int(row.get("Disponivel D1") or 0),
                "status_amostra": str(row.get("Status") or ""),
                "faixa_horaria": str(row.get("Faixa Horaria") or ""),
                "excluidos_historico": int(row.get("Excluidos Historico") or 0),
                "redistribuidos": int(row.get("Amostra Redistribuida") or 0),
                "protocolos_manuais": selected_counts[workflow]["manual"],
                "protocolos_automaticos": selected_counts[workflow]["automatico"],
                "protocolos_retroativos": retro_counts.get(workflow, 0),
                "warnings": [observation] if observation else [],
            }
        )

    snapshot = ReplicacaoD1ConfigSnapshot.objects.filter(run_id=plano.run_id).first()
    data_ref = plano.data_referencia.date() if hasattr(plano.data_referencia, "date") else plano.data_referencia
    total_retro = sum(retro_counts.values())
    result = persist_plan(
        run_id=plano.run_id,
        data_referencia_d1=data_ref,
        source_batch_id=source_batch_id,
        workflows=workflow_rows,
        protocolos=protocol_rows,
        config_snapshot_id=snapshot.pk if snapshot else None,
        config_version=(snapshot.config_version if snapshot else settings.get("config_version")),
        config_hash=(snapshot.config_hash if snapshot else str(settings.get("config_hash") or "")),
        warnings=list(plano.warnings or []),
        auditores_ativos_brflow=int(plano.auditores_ativos or 0),
        auditores_ativos_case=int(plano.auditores_ativos_case or 0),
    )
    plan_log(
        log,
        logging.INFO,
        "Plano persistido",
        run_id=str(plano.run_id),
        phase=PLANNING_PERSIST,
        protocols=len(protocol_rows),
        workflows=len(workflow_rows),
        retro=total_retro,
        snapshot_id=snapshot.pk if snapshot else "",
        backend="postgresql",
    )
    return result


def latest_plan_run_id_db(*, approved_only: bool = False) -> str | None:
    if not ensure_django_ready():
        return None
    from apps.replicacao_d1.models import ReplicacaoD1PlanDeletion, ReplicacaoD1Run

    deleted_run_ids = ReplicacaoD1PlanDeletion.objects.values_list("run_id", flat=True)
    qs = ReplicacaoD1Run.objects.exclude(plan_hash="").exclude(run_id__in=deleted_run_ids)
    if approved_only:
        qs = qs.filter(validation_status=ReplicacaoD1Run.VALIDATION_APPROVED)
    return qs.order_by("-created_at", "-id").values_list("run_id", flat=True).first()


def list_plan_runs_db(*, limit: int = 15) -> list[dict[str, Any]]:
    """Lista planos recentes e contagens executáveis exclusivamente do PostgreSQL."""
    if not ensure_django_ready():
        return []
    from apps.replicacao_d1.models import ReplicacaoD1PlanDeletion, ReplicacaoD1Run
    from apps.replicacao_d1.services.runs_operational import (
        build_run_operational_summaries,
        serialize_run_operational,
    )

    deleted_run_ids = ReplicacaoD1PlanDeletion.objects.values_list("run_id", flat=True)
    runs = list(
        ReplicacaoD1Run.objects.exclude(plan_hash="")
        .exclude(run_id__in=deleted_run_ids)
        .filter(protocolos_total__gt=0, workflows__protocolos_planejados__gt=0)
        .distinct()
        .order_by("-created_at", "-id")[: max(1, min(int(limit or 15), 50))]
    )
    summaries = build_run_operational_summaries(runs)
    return [
        serialize_run_operational(run, summaries.get(run.run_id))
        for run in runs
    ]


def summarize_plan_warnings_db(warnings: list[str] | None) -> list[dict[str, Any]]:
    if not ensure_django_ready():
        return []
    from apps.replicacao_d1.services.plan_validation import build_plan_warning_groups

    return build_plan_warning_groups(warnings)


def ensure_plan_approved_db(run_id: str) -> None:
    if not ensure_django_ready():
        raise RuntimeError("PostgreSQL indisponível para validar a aprovação do plano D-1.")
    from apps.replicacao_d1.services.plan_validation import ensure_plan_approved

    ensure_plan_approved(run_id)


def load_execution_state_db(run_id: str) -> dict | None:
    if not ensure_django_ready():
        return None
    from apps.replicacao_d1.models import ReplicacaoD1Run

    run = ReplicacaoD1Run.objects.filter(run_id=run_id).first()
    if run is None or not run.plan_hash:
        return None
    workflows: dict[str, dict[str, Any]] = {}
    for row in run.workflows.order_by("workflow_config"):
        modo_replicacao = _resolver_modo_replicacao_persistido(
            getattr(row, "modo_replicacao", None),
            str(row.fila or ""),
        )
        entry = {
            "workflow": row.workflow_config,
            "workflow_brflow": row.workflow_brflow or row.workflow_config,
            "fila": row.fila,
            "status": row.status_brflow or "PENDENTE",
            "protocolos": row.protocolos_planejados,
            "protocolos_salvos": row.protocolos_aceitos,
            "motivo": row.erro_resumo,
            "resultado": getattr(row, "resultado", "pendente") or "pendente",
            "severidade": getattr(row, "severidade", "info") or "info",
            "motivo_codigo": getattr(row, "motivo_codigo", "") or "",
            "motivo_resumo": getattr(row, "motivo_resumo", "") or row.erro_resumo or "",
            "fase_execucao": getattr(row, "fase_execucao", "") or "",
            "quantidade_alvo": getattr(row, "quantidade_alvo", None),
            "quantidade_encontrada": getattr(row, "quantidade_encontrada", None),
            "attempt_number": int(row.attempt_number or 0),
            "started_at": row.started_at.isoformat() if row.started_at else "",
            "finished_at": row.finished_at.isoformat() if row.finished_at else "",
            "atualizado_em": row.finished_at.isoformat() if row.finished_at else "",
            "csv": "",
            "modo": modo_replicacao,
        }
        if modo_replicacao == "qtd":
            entry["qtd_calculada"] = int(row.protocolos_planejados or 0)
        workflows[row.workflow_config] = entry
    return {
        "run_id": run.run_id,
        "data_referencia_d1": run.data_referencia_d1.strftime("%d/%m/%Y"),
        "data_execucao": (run.data_execucao or run.created_at).strftime("%d/%m/%Y %H:%M"),
        "parquet_referencia": "",
        "source_batch_id": run.source_batch_id,
        "plan_hash": run.plan_hash,
        "plan_revision": run.plan_revision,
        "validation_status": run.validation_status,
        "auditores_ativos_brflow": run.auditores_ativos_brflow or 0,
        "auditores_ativos_case": run.auditores_ativos_case or 0,
        "workflows": workflows,
    }


def save_execution_state_db(estado: dict) -> bool:
    if not ensure_django_ready():
        return False
    from django.db import transaction
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime
    from apps.replicacao_d1.models import ReplicacaoD1Run, ReplicacaoD1WorkflowDia
    from apps.replicacao_d1.normalization import normalizar_resultado_workflow

    run_id = str(estado.get("run_id") or "").strip()
    run = ReplicacaoD1Run.objects.filter(run_id=run_id).first()
    if run is None or not run.plan_hash:
        return False
    with transaction.atomic():
        for workflow, info in dict(estado.get("workflows") or {}).items():
            raw_status = str(info.get("status") or "PENDENTE")[:64]
            motivo_codigo = str(info.get("motivo_codigo") or "")[:64]
            motivo_resumo = str(
                info.get("motivo_resumo")
                if info.get("motivo_resumo") is not None
                else info.get("motivo") or ""
            )[:255]
            normalizado = normalizar_resultado_workflow(raw_status, motivo_codigo)
            updated = parse_datetime(str(info.get("atualizado_em") or ""))
            if updated and timezone.is_naive(updated):
                updated = timezone.make_aware(updated, timezone.get_current_timezone())
            started = parse_datetime(str(info.get("started_at") or ""))
            if started and timezone.is_naive(started):
                started = timezone.make_aware(started, timezone.get_current_timezone())
            finished = parse_datetime(str(info.get("finished_at") or ""))
            if finished and timezone.is_naive(finished):
                finished = timezone.make_aware(finished, timezone.get_current_timezone())
            falha_bloqueante = bool(normalizado["falha_bloqueante"])
            quantidade_alvo = info.get("quantidade_alvo", info.get("qtd_calculada"))
            quantidade_encontrada = info.get("quantidade_encontrada")
            houve_envio = raw_status.upper() in {"SALVO_OK", "UPLOAD_OK"}
            defaults = {
                "status_brflow": raw_status,
                "resultado": str(normalizado["resultado"]),
                "severidade": str(normalizado["severidade"]),
                "motivo_codigo": motivo_codigo,
                "motivo_resumo": motivo_resumo,
                "fase_execucao": str(info.get("fase_execucao") or "")[:64],
                "quantidade_alvo": int(quantidade_alvo) if quantidade_alvo is not None else None,
                "quantidade_encontrada": (
                    int(quantidade_encontrada) if quantidade_encontrada is not None else None
                ),
                "status_operacional": str(normalizado["status_operacional"]),
                "protocolos_enviados": (
                    int(info.get("protocolos") or quantidade_alvo or 0)
                    if houve_envio
                    else 0
                ),
                "protocolos_aceitos": int(info.get("protocolos_salvos") or 0),
                "attempt_number": max(1, int(info.get("attempt_number") or 1)),
                "erro_codigo": motivo_codigo if falha_bloqueante else "",
                "erro_resumo": motivo_resumo if falha_bloqueante else "",
            }
            if started:
                defaults["started_at"] = started
            if raw_status.upper() in {
                "SALVO_OK",
                "UPLOAD_OK",
                "SEM_ALTERACAO",
                "ERRO",
                "FALHOU",
                "CANCELADO",
                "PULADO",
                "INATIVO",
                "NAO_SALVO",
            }:
                defaults["finished_at"] = finished or updated or timezone.now()
            elif raw_status.upper() == "PROCESSANDO":
                defaults["started_at"] = started or updated or timezone.now()
                defaults["finished_at"] = None
            ReplicacaoD1WorkflowDia.objects.filter(run=run, workflow_config=workflow).update(**defaults)
        if any(str(v.get("status") or "").upper() == "PROCESSANDO" for v in dict(estado.get("workflows") or {}).values()):
            run.status_canonical = ReplicacaoD1Run.STATUS_RUNNING
            run.started_at = run.started_at or timezone.now()
            run.save(update_fields=["status_canonical", "started_at", "synced_at"])
    return True


def update_scheduler_state_db(
    *,
    enabled: bool,
    status: str,
    next_planning_at: datetime | None = None,
    next_execution_at: datetime | None = None,
    run_id: str = "",
    force: bool = False,
) -> bool:
    """Publica a saúde do agendador para a Central D-1, no máximo a cada 5 minutos."""
    global _LAST_SCHEDULER_HEARTBEAT
    monotonic_now = time.monotonic()
    if not force and monotonic_now - _LAST_SCHEDULER_HEARTBEAT < 300:
        return True
    if not ensure_django_ready():
        return False
    try:
        from django.utils import timezone
        from apps.replicacao_d1.models import ReplicacaoD1SchedulerState

        payload = {
            "enabled": bool(enabled),
            "status": str(status or "unknown"),
            "last_heartbeat": timezone.now().isoformat(),
            "next_planning_at": next_planning_at.isoformat() if next_planning_at else None,
            "next_execution_at": next_execution_at.isoformat() if next_execution_at else None,
            "run_id": str(run_id or ""),
            "pid": os.getpid(),
        }
        ReplicacaoD1SchedulerState.objects.update_or_create(
            key="replicacao_d1",
            defaults={"state": payload},
        )
        _LAST_SCHEDULER_HEARTBEAT = monotonic_now
        return True
    except Exception as exc:
        log.warning("Falha ao publicar heartbeat do agendador D-1: %s", exc)
        return False


def _workflow_regra_map_from_run(run: Any) -> dict[str, str]:
    """Resolve nome_regra_brflow por workflow_config a partir do snapshot ou cadastro."""
    out: dict[str, str] = {}
    snapshot = getattr(run, "config_snapshot", None)
    if snapshot and isinstance(getattr(snapshot, "snapshot_json", None), dict):
        for wf in ((snapshot.snapshot_json.get("persistent") or {}).get("workflows") or []):
            nome = str(wf.get("nome_canonico") or "").strip()
            regra = str(wf.get("nome_regra_brflow") or "").strip()
            if nome and regra:
                out[nome] = regra
    if out:
        return out
    try:
        from apps.replicacao_d1.config_models import ReplicacaoD1Workflow

        for row in ReplicacaoD1Workflow.objects.filter(ativo=True).values(
            "nome_canonico", "nome_regra_brflow",
        ):
            nome = str(row["nome_canonico"] or "").strip()
            regra = str(row["nome_regra_brflow"] or "").strip()
            if nome and regra:
                out[nome] = regra
    except Exception as exc:
        log.debug("Mapa nome_regra_brflow indisponível: %s", exc)
    return out


def _resolver_modo_replicacao_persistido(modo: Any, fila: str = "") -> str:
    """Usa o modo congelado; a fila existe apenas para runs/planos legados."""
    modo_normalizado = str(modo or "").strip().casefold()
    if modo_normalizado in {"protocolos", "qtd"}:
        return modo_normalizado
    fila_normalizada = str(fila or "").strip().casefold()
    return "qtd" if fila_normalizada in {"bio", "redoc"} else "protocolos"


def load_plan_for_execution_db(run_id: str, settings: dict | None = None) -> tuple[Any, dict]:
    """Materializa CSVs temporários (protocolos) e metadados qtd para execução BRFlow."""
    if not bool((settings or {}).get("execucao_agendada")):
        ensure_plan_approved_db(run_id)
    from apps.replicacao_d1.models import ReplicacaoD1Run
    from app.bots.replicacao_aud_planning import PlanoReplicacao

    run = (
        ReplicacaoD1Run.objects.select_related("config_snapshot")
        .prefetch_related("workflows", "protocolos")
        .get(run_id=run_id)
    )
    regra_map = _workflow_regra_map_from_run(run)
    temp_handle = tempfile.TemporaryDirectory(prefix=f"replicacao-d1-{run.run_id}-")
    temp_root = Path(temp_handle.name)
    plano = PlanoReplicacao(
        data_referencia=datetime.combine(run.data_referencia_d1, datetime.min.time()),
        pasta_protocolos=temp_root,
        pasta_resumo=temp_root,
        run_id=run.run_id,
        pasta_execucao=temp_root,
        estado_execucao_path=None,
        auditores_ativos=run.auditores_ativos_brflow or 0,
        auditores_ativos_case=run.auditores_ativos_case or 0,
    )
    plano._temporary_directory_handle = temp_handle
    plano.warnings = list(run.plan_warnings or [])
    plano.data_referencia_d1_fmt = run.data_referencia_d1.strftime("%d/%m/%Y")
    if not isinstance(getattr(plano, "workflow_modo_replicacao", None), dict):
        plano.workflow_modo_replicacao = {}
    protocols_by_workflow: dict[str, list[str]] = defaultdict(list)
    for protocol in run.protocolos.order_by("workflow_config", "id"):
        protocols_by_workflow[protocol.workflow_config].append(protocol.protocolo)
    state = load_execution_state_db(run_id) or {"run_id": run_id, "workflows": {}}
    only_pending = bool((settings or {}).get("apenas_pendentes", True))
    force = bool((settings or {}).get("forcar_reexecucao", False))
    skip_statuses = frozenset({"UPLOAD_OK", "SALVO_OK", "SEM_ALTERACAO", "INATIVO", "PULADO"})
    for workflow in run.workflows.order_by("workflow_config"):
        wf_key = workflow.workflow_config
        raw_status = str(workflow.status_brflow or "PENDENTE").upper()
        fila = str(workflow.fila or "")
        protocols = protocols_by_workflow.get(wf_key, [])
        modo_replicacao = _resolver_modo_replicacao_persistido(
            getattr(workflow, "modo_replicacao", None),
            fila,
        )
        modo_qtd = modo_replicacao == "qtd"
        qtd_planejada = int(workflow.protocolos_planejados or 0)

        plano.protocolos_por_workflow[wf_key] = [] if modo_qtd else protocols
        plano.workflow_brflow[wf_key] = workflow.workflow_brflow or wf_key
        plano.workflow_fila[wf_key] = fila
        plano.workflow_modo_replicacao[wf_key] = modo_replicacao
        regra = str(regra_map.get(wf_key) or "").strip()
        if regra:
            plano.workflow_regra_brflow[wf_key] = regra
        if modo_qtd:
            plano.qtd_por_workflow[wf_key] = qtd_planejada

        if only_pending and not force and raw_status in skip_statuses:
            continue
        if modo_qtd:
            if qtd_planejada <= 0:
                continue
        elif not protocols:
            continue
        else:
            csv_path = temp_root / f"{workflow.pk}.csv"
            pd.Series(protocols, dtype="string").to_csv(csv_path, index=False, header=False, encoding="utf-8-sig")
            plano.csv_paths[wf_key] = csv_path
        log.info(
            "Plano de execucao carregado | workflow=%s | fila=%s | modo=%s | qtd=%s | csv=%s",
            wf_key,
            fila,
            modo_replicacao,
            qtd_planejada if modo_qtd else len(protocols),
            bool(plano.csv_paths.get(wf_key)),
        )
        plano.workflows.append(wf_key)
    return plano, state


def cleanup_temporary_plan(plano: Any) -> None:
    handle = getattr(plano, "_temporary_directory_handle", None)
    if handle is not None:
        handle.cleanup()
        plano._temporary_directory_handle = None


def append_execution_event_db(
    run_id: str,
    *,
    phase: str,
    status: str,
    message: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    if not ensure_django_ready():
        raise RuntimeError("PostgreSQL indisponível para persistir evento D-1.")
    from apps.replicacao_d1.services.plan_validation import append_execution_event

    append_execution_event(
        run_id,
        phase=phase,
        status=status,
        message=message,
        payload=payload,
    )


def close_run_result_db(result: Any) -> None:
    if not ensure_django_ready():
        raise RuntimeError("PostgreSQL indisponível para fechar o run D-1.")
    from django.utils import timezone
    from apps.replicacao_d1.models import ReplicacaoD1Run
    from apps.replicacao_d1.services.run_lifecycle import close_run, update_run_status

    status_value = str(getattr(getattr(result, "status", None), "value", None) or getattr(result, "status", ""))
    mapping = {
        "planned": ReplicacaoD1Run.STATUS_PLANNED,
        "running": ReplicacaoD1Run.STATUS_RUNNING,
        "completed": ReplicacaoD1Run.STATUS_COMPLETED,
        "partial": ReplicacaoD1Run.STATUS_PARTIAL,
        "failed": ReplicacaoD1Run.STATUS_FAILED,
        "cancelled": ReplicacaoD1Run.STATUS_CANCELLED,
    }
    run_id = str(getattr(result, "run_id", "") or "")
    close_run(run_id, data_execucao=timezone.now(), allow_regress=True)
    target = mapping.get(status_value)
    if target:
        update_run_status(
            run_id,
            target,
            workflows_salvo_ok=int(getattr(result, "workflows_success", 0) or 0),
            workflows_total=int(getattr(result, "workflows_total", 0) or 0),
            started_at=getattr(result, "started_at", None),
            finished_at=getattr(result, "finished_at", None),
            allow_regress=True,
        )
    append_execution_event_db(
        run_id,
        phase="close",
        status=status_value or "unknown",
        payload={
            "workflows_total": int(getattr(result, "workflows_total", 0) or 0),
            "workflows_success": int(getattr(result, "workflows_success", 0) or 0),
            "workflows_failed": int(getattr(result, "workflows_failed", 0) or 0),
            "workflows_skipped": int(getattr(result, "workflows_skipped", 0) or 0),
        },
    )
