# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SuporteClaroJiraRunnerError(RuntimeError):
    pass


class SuporteClaroJiraNotAvailable(SuporteClaroJiraRunnerError):
    pass


def is_automacoes_available() -> bool:
    """Pacote automacoes instalado e importável no venv do backend."""
    try:
        from apps.automacoes.services import get_robot_manager

        get_robot_manager()
        return True
    except Exception:
        return False


def _get_robot_manager():
    try:
        from apps.automacoes.services import get_robot_manager

        return get_robot_manager()
    except Exception as exc:
        raise SuporteClaroJiraNotAvailable(
            "Serviço de automações indisponível. Instale automacoes no backend "
            "(pip install -e ../automacoes) e reinicie o servidor."
        ) from exc


def _default_jira_job(*, message: str = "Aguardando") -> dict:
    return {
        "running": False,
        "message": message,
        "step": None,
        "step_label": None,
        "detail": None,
        "result": None,
        "started_at": None,
        "ended_at": None,
    }


def _sanitize_jira_job(job: dict) -> dict:
    """Limita campos grandes — evita resposta pesada e falhas no portal."""
    safe = dict(job or {})
    for key in ("message", "detail"):
        val = safe.get(key)
        if isinstance(val, str) and len(val) > 500:
            safe[key] = val[:497] + "..."
    result = safe.get("result")
    if isinstance(result, dict):
        result = dict(result)
        msg = result.get("message")
        if isinstance(msg, str) and len(msg) > 500:
            result["message"] = msg[:497] + "..."
        safe["result"] = result
    return safe


def jira_config_payload(profile_key: str, profile_label: str) -> dict:
    return {
        "profile_key": profile_key,
        "profile_label": profile_label,
        "automacoes_available": is_automacoes_available(),
        "job": _sanitize_jira_job(jira_job_status(strict=False)),
    }


def start_jira_okta_job(
    *,
    matricula: str,
    senha: str,
    payload: dict[str, Any],
    headless: bool = False,
) -> tuple[bool, str, dict]:
    manager = _get_robot_manager()
    return manager.start_suporte_claro_jira(
        matricula=matricula,
        senha=senha,
        payload=payload,
        headless=headless,
    )


def jira_job_status(*, strict: bool = True) -> dict:
    try:
        manager = _get_robot_manager()
        return manager.suporte_claro_jira_status()
    except SuporteClaroJiraNotAvailable:
        if strict:
            raise
        return _default_jira_job(message="Automações indisponíveis.")


def cancel_jira_job() -> tuple[bool, str, dict]:
    manager = _get_robot_manager()
    return manager.cancel_suporte_claro_jira()
