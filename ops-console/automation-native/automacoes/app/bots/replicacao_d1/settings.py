# -*- coding: utf-8 -*-
"""Normalização de settings do bot D-1."""
from __future__ import annotations

import os
from typing import Any

from app.bots.replicacao_aud_d1_planning import _ensure_d1_settings
from app.bots.replicacao_d1_db_bridge import try_inject_execution_snapshot
from app.config import REPLICACAO_UPLOAD_CSV_VAZIO_DEFAULT


def parse_bool_setting(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def get_headless(settings: dict | None = None) -> bool:
    raw = None
    if isinstance(settings, dict):
        raw = settings.get("headless")
    if raw is None:
        raw = os.getenv("REPLICACAO_HEADLESS") or os.getenv("ROBOT_HEADLESS") or os.getenv("HEADLESS") or "0"
    return parse_bool_setting(raw, default=False)


def get_credentials(settings: dict | None = None) -> tuple[str, str]:
    matricula = ""
    senha = ""
    if isinstance(settings, dict):
        matricula = str(settings.get("matricula", "") or "").strip()
        senha = str(settings.get("senha", "") or "").strip()

    if not matricula:
        matricula = (
            os.getenv("OKTA_USER")
            or os.getenv("ROBOT_USER")
            or os.getenv("NIVEL_USER")
            or os.getenv("MONITOR_USER")
            or ""
        )
    if not senha:
        senha = (
            os.getenv("OKTA_PASS")
            or os.getenv("ROBOT_PASS")
            or os.getenv("NIVEL_PASS")
            or os.getenv("MONITOR_PASS")
            or ""
        )
    return matricula, senha


def normalize_replicacao_settings(settings: dict | None = None) -> dict:
    """Mescla settings com variáveis de ambiente da replicação."""
    cfg = dict(settings or {})
    if os.getenv("REPLICACAO_D1_APENAS_PLANEJAMENTO", "").strip() in ("1", "true", "True", "yes"):
        cfg.setdefault("apenas_planejamento", True)
    run_env = os.getenv("REPLICACAO_D1_RUN_ID", "").strip()
    if run_env and "run_id" not in cfg:
        cfg["run_id"] = run_env
    cfg = _ensure_d1_settings(cfg)
    return try_inject_execution_snapshot(cfg)


def upload_csv_vazio_habilitado(settings: dict | None = None) -> bool:
    settings = settings or {}
    if "replicacao_upload_csv_vazio" in settings:
        return bool(settings["replicacao_upload_csv_vazio"])
    raw = os.getenv("REPLICACAO_UPLOAD_CSV_VAZIO", "")
    if raw.strip():
        return parse_bool_setting(raw, default=False)
    return REPLICACAO_UPLOAD_CSV_VAZIO_DEFAULT


def filtrar_workflow_destino_habilitado(settings: dict | None = None) -> bool:
    """True quando a pesquisa BRFlow deve filtrar por WorkFlow Destino (fila)."""
    settings = settings or {}
    if "replicacao_filtrar_workflow_destino" in settings:
        return parse_bool_setting(settings["replicacao_filtrar_workflow_destino"], default=False)
    raw = os.getenv("REPLICACAO_FILTRAR_WORKFLOW_DESTINO", "")
    if raw.strip():
        return parse_bool_setting(raw, default=False)
    return False


REPLICACAO_PAGINACAO_TAMANHO_DEFAULT = 500
REPLICACAO_PAGINACAO_MAX_PAGINAS_DEFAULT = 20


def paginacao_tamanho(settings: dict | None = None) -> int:
    settings = settings or {}
    raw = settings.get("replicacao_paginacao_tamanho")
    if raw is None:
        raw = os.getenv("REPLICACAO_PAGINACAO_TAMANHO", "")
    if raw is not None and str(raw).strip():
        try:
            valor = int(str(raw).strip())
            if valor > 0:
                return valor
        except ValueError:
            pass
    return REPLICACAO_PAGINACAO_TAMANHO_DEFAULT


def paginacao_max_paginas(settings: dict | None = None) -> int:
    settings = settings or {}
    raw = settings.get("replicacao_paginacao_max_paginas")
    if raw is None:
        raw = os.getenv("REPLICACAO_PAGINACAO_MAX_PAGINAS", "")
    if raw is not None and str(raw).strip():
        try:
            valor = int(str(raw).strip())
            if valor > 0:
                return valor
        except ValueError:
            pass
    return REPLICACAO_PAGINACAO_MAX_PAGINAS_DEFAULT


def paginacao_varrer_todas(settings: dict | None = None) -> bool:
    settings = settings or {}
    if "replicacao_paginacao_varrer_todas" in settings:
        return parse_bool_setting(settings["replicacao_paginacao_varrer_todas"], default=True)
    raw = os.getenv("REPLICACAO_PAGINACAO_VARRER_TODAS", "")
    if raw.strip():
        return parse_bool_setting(raw, default=True)
    return True


REPLICACAO_LISTAGEM_MODO_INDICE_DEFAULT = True
REPLICACAO_INDEXAR_PARAR_PLANO_COMPLETO_DEFAULT = True
REPLICACAO_REFILTRAR_APOS_SALVAR_DEFAULT = False
REPLICACAO_ESTADO_BATCH_SIZE_DEFAULT = 5


def listagem_modo_indice(settings: dict | None = None) -> bool:
    """Usa índice wf→página e evita refilter DOM redundante."""
    settings = settings or {}
    if "replicacao_listagem_modo_indice" in settings:
        return parse_bool_setting(
            settings["replicacao_listagem_modo_indice"],
            default=REPLICACAO_LISTAGEM_MODO_INDICE_DEFAULT,
        )
    raw = os.getenv("REPLICACAO_LISTAGEM_MODO_INDICE", "")
    if raw.strip():
        return parse_bool_setting(raw, default=REPLICACAO_LISTAGEM_MODO_INDICE_DEFAULT)
    if paginacao_varrer_todas(settings):
        return REPLICACAO_LISTAGEM_MODO_INDICE_DEFAULT
    return False


def indexar_parar_quando_plano_completo(settings: dict | None = None) -> bool:
    settings = settings or {}
    if "replicacao_indexar_parar_quando_plano_completo" in settings:
        return parse_bool_setting(
            settings["replicacao_indexar_parar_quando_plano_completo"],
            default=REPLICACAO_INDEXAR_PARAR_PLANO_COMPLETO_DEFAULT,
        )
    raw = os.getenv("REPLICACAO_INDEXAR_PARAR_PLANO_COMPLETO", "")
    if raw.strip():
        return parse_bool_setting(raw, default=REPLICACAO_INDEXAR_PARAR_PLANO_COMPLETO_DEFAULT)
    return REPLICACAO_INDEXAR_PARAR_PLANO_COMPLETO_DEFAULT


def refiltrar_apos_salvar(settings: dict | None = None) -> bool:
    settings = settings or {}
    if "replicacao_refiltrar_apos_salvar" in settings:
        return parse_bool_setting(
            settings["replicacao_refiltrar_apos_salvar"],
            default=REPLICACAO_REFILTRAR_APOS_SALVAR_DEFAULT,
        )
    raw = os.getenv("REPLICACAO_REFILTRAR_APOS_SALVAR", "")
    if raw.strip():
        return parse_bool_setting(raw, default=REPLICACAO_REFILTRAR_APOS_SALVAR_DEFAULT)
    return REPLICACAO_REFILTRAR_APOS_SALVAR_DEFAULT


def estado_batch_size(settings: dict | None = None) -> int:
    settings = settings or {}
    raw = settings.get("replicacao_estado_batch_size")
    if raw is None:
        raw = os.getenv("REPLICACAO_ESTADO_BATCH_SIZE", "")
    if raw is not None and str(raw).strip():
        try:
            valor = int(str(raw).strip())
            if valor > 0:
                return valor
        except ValueError:
            pass
    return REPLICACAO_ESTADO_BATCH_SIZE_DEFAULT
