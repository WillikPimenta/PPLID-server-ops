# -*- coding: utf-8 -*-
"""Vincula chamado Jira criado pela automação ao registro no portal."""

from __future__ import annotations

from apps.suporte_claro.models import SuporteClaroChamadoExterno, SuporteClaroRegistro
from apps.suporte_claro.services.chamados_externos import append_jira_chamado_if_missing


def get_registro_jira_key(registro: SuporteClaroRegistro) -> str | None:
    """Chave Jira do chamado interno (auto). Não usa externos para sync de status."""
    interno = (
        registro.chamados_externos.filter(
            natureza=SuporteClaroChamadoExterno.NATUREZA_INTERNO,
            sistema=SuporteClaroRegistro.CHAMADO_JIRA,
        )
        .exclude(codigo="")
        .order_by("id")
        .first()
    )
    if interno:
        return (interno.codigo or "").strip().upper() or None
    return None


def registro_has_jira_externo(registro: SuporteClaroRegistro) -> bool:
    return (
        registro.chamados_externos.filter(
            natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
            sistema=SuporteClaroRegistro.CHAMADO_JIRA,
        )
        .exclude(codigo="")
        .exists()
    )


def list_jira_externos(registro: SuporteClaroRegistro):
    """Chamados externos Jira elegíveis para formalização de texto."""
    return list(
        registro.chamados_externos.filter(
            natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
            sistema=SuporteClaroRegistro.CHAMADO_JIRA,
        )
        .exclude(codigo="")
        .order_by("ordem", "id")
    )


def registro_has_jira_chamado(registro: SuporteClaroRegistro) -> bool:
    """True se há chamado Jira interno (sync de status) ou qualquer Jira vinculado."""
    if get_registro_jira_key(registro):
        return True
    return (
        registro.chamados_externos.filter(sistema=SuporteClaroRegistro.CHAMADO_JIRA)
        .exclude(codigo="")
        .exists()
    )


def chamado_items_has_jira(items: list[tuple[str, str, str]] | None) -> bool:
    """True se o payload de create/patch já traz chamado Jira."""
    for sistema, codigo, _url in items or []:
        if sistema == SuporteClaroRegistro.CHAMADO_JIRA and (codigo or "").strip():
            return True
    return False


def _registro_has_jira_key(registro: SuporteClaroRegistro, key: str) -> bool:
    current = get_registro_jira_key(registro)
    if current and current == (key or "").strip().upper():
        return True
    return registro.chamados_externos.filter(
        sistema=SuporteClaroRegistro.CHAMADO_JIRA,
        codigo__iexact=key,
    ).exists()


def auto_link_jira_issue_to_registro(
    *,
    registro_id: int,
    issue_key: str,
    user,
) -> dict | None:
    """
    Adiciona chamado Jira à lista de chamados externos do registro.
    Idempotente: não duplica o mesmo issue_key.
    """
    key = (issue_key or "").strip().upper()
    if not key or not registro_id:
        return None

    registro = SuporteClaroRegistro.objects.filter(pk=registro_id).first()
    if not registro:
        return {"skipped": True, "reason": "registro_not_found", "registro_id": registro_id}

    if _registro_has_jira_key(registro, key):
        return {
            "already_linked": True,
            "registro_id": registro.id,
            "issue_key": key,
        }

    if append_jira_chamado_if_missing(registro, key, user=user):
        return {
            "linked": True,
            "registro_id": registro.id,
            "issue_key": key,
        }

    return {
        "skipped": True,
        "reason": "link_failed",
        "registro_id": registro.id,
        "issue_key": key,
    }


def _link_batch_items(items: list, user) -> list[dict]:
    links: list[dict] = []
    for raw in items or []:
        if not isinstance(raw, dict) or not raw.get("ok"):
            continue
        registro_id = raw.get("registro_id")
        issue_key = (raw.get("issue_key") or "").strip()
        if not registro_id or not issue_key:
            continue
        link = auto_link_jira_issue_to_registro(
            registro_id=int(registro_id),
            issue_key=issue_key,
            user=user,
        )
        if link:
            links.append(link)
    return links


def enrich_jira_job_with_auto_link(job: dict, user) -> dict:
    """Vincula chamado(s) Jira ao(s) registro(s) após job concluir ou parcialmente."""
    safe = dict(job or {})
    kind = str(safe.get("kind") or "").strip().lower()
    result = safe.get("result")
    links: list[dict] = []

    batch_items = safe.get("batch_items")
    if isinstance(batch_items, list) and batch_items:
        links.extend(_link_batch_items(batch_items, user))

    if isinstance(result, dict):
        result_items = result.get("items")
        if isinstance(result_items, list):
            links.extend(_link_batch_items(result_items, user))

        if kind == "registro" and result.get("ok"):
            issue_key = (result.get("issue_key") or "").strip()
            registro_id = safe.get("registro_id")
            if issue_key and registro_id:
                link = auto_link_jira_issue_to_registro(
                    registro_id=int(registro_id),
                    issue_key=issue_key,
                    user=user,
                )
                if link:
                    links.append(link)
                    safe["auto_link"] = link
                    msg = f"Chamado {issue_key.upper()} criado e vinculado à demanda."
                    result = dict(result)
                    result["message"] = msg
                    safe["result"] = result
                    safe["message"] = msg

    if links:
        safe["auto_links"] = links
        if kind == "batch" and isinstance(result, dict) and result.get("batch"):
            linked_count = sum(1 for link in links if link.get("linked") or link.get("already_linked"))
            if linked_count:
                msg = f"{linked_count} chamado(s) vinculado(s) automaticamente."
                result = dict(result)
                result["message"] = msg
                safe["result"] = result
                if not safe.get("message"):
                    safe["message"] = msg

    return safe
