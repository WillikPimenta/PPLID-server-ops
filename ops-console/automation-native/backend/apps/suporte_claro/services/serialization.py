# -*- coding: utf-8 -*-
from django.conf import settings
from django.utils import timezone

from apps.falhas_criticas.services.user_display import resolve_user_display_name

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.services.audit import FIELD_LABELS, historico_summary
from apps.suporte_claro.services.chamados_externos import (
    serialize_chamado_interno,
    serialize_chamados_externos,
)
from apps.suporte_claro.services.protocolos import display_titulo, serialize_protocolos
from apps.suporte_claro.services.emails import serialize_emails
from apps.suporte_claro.services.externo import (
    CHAMADO_SISTEMA_LABELS,
    chamado_display_label,
    resolve_chamado_url,
)
from apps.suporte_claro.services.jira_copy import (
    build_registro_portal_url,
    compute_sla_info,
)
from apps.suporte_claro.services.vinculos import list_vinculos_for

STATUS_LABELS = dict(SuporteClaroRegistro.STATUS_CHOICES)
ORIGEM_LABELS = dict(SuporteClaroRegistro.ORIGEM_CHOICES)
CATEGORIA_LABELS = dict(SuporteClaroRegistro.CATEGORIA_CHOICES)
CATEGORIA_LABELS[SuporteClaroRegistro.CATEGORIA_DEMANDA] = "Suporte N1"
TIPO_INCIDENTE_LABELS = dict(SuporteClaroRegistro.TIPO_INCIDENTE_CHOICES)


def comentarios_etapa_enabled() -> bool:
    return bool(getattr(settings, "SUPORTE_CLARO_COMENTARIOS_ETAPA", True))


def serialize_comentario_etapa(comentario) -> dict:
    keys_raw = (getattr(comentario, "formalizado_issue_keys", None) or "").strip()
    issue_keys = [k.strip() for k in keys_raw.split(",") if k.strip()] if keys_raw else []
    visibilidade = getattr(comentario, "visibilidade", None) or "interno"
    formalizado = bool(getattr(comentario, "formalizado_externo", False))
    return {
        "id": comentario.id,
        "texto": comentario.texto,
        "etapa_status": comentario.etapa_status,
        "etapa_status_label": STATUS_LABELS.get(
            comentario.etapa_status, comentario.etapa_status
        ),
        "visibilidade": visibilidade,
        "visibilidade_label": "Formalizado no externo" if formalizado else (
            "Interno" if visibilidade == "interno" else "Externo (pendente)"
        ),
        "formalizado_externo": formalizado,
        "formalizado_issue_keys": issue_keys,
        "formalizado_jira_refs": list(getattr(comentario, "formalizado_jira_refs", None) or []),
        "created_by": (
            resolve_user_display_name(comentario.created_by) if comentario.created_by else ""
        ),
        "created_by_username": (
            comentario.created_by.username if comentario.created_by else ""
        ),
        "created_at": comentario.created_at.isoformat(),
        "can_delete": False,
    }


def _anexo_url(_request, anexo) -> str:
    if not anexo.file:
        return ""
    return anexo.file.url or ""


def serialize_anexo(anexo, request=None) -> dict:
    return {
        "id": anexo.id,
        "url": _anexo_url(request, anexo),
        "original_name": anexo.original_name,
        "content_type": anexo.content_type,
        "size_bytes": anexo.size_bytes,
        "uploaded_at": anexo.uploaded_at.isoformat(),
    }


def serialize_historico(entry, request=None) -> dict:
    return {
        "id": entry.id,
        "action": entry.action,
        "field_name": entry.field_name,
        "field_label": FIELD_LABELS.get(entry.field_name, entry.field_name),
        "old_value": entry.old_value,
        "new_value": entry.new_value,
        "summary": historico_summary(entry),
        "user": resolve_user_display_name(entry.user) if entry.user else "Sistema",
        "created_at": entry.created_at.isoformat(),
    }


def serialize_registro(registro: SuporteClaroRegistro, request=None, detailed: bool = False) -> dict:
    anexos_qs = registro.anexos.all()
    is_imported = getattr(registro, "_is_imported", None)
    if is_imported is None:
        is_imported = registro.import_refs.exists()
    payload = {
        "id": registro.id,
        "titulo": registro.titulo or "",
        "display_titulo": display_titulo(registro),
        "protocolo": registro.protocolo,
        "protocolos": serialize_protocolos(registro),
        "irregularidade": registro.irregularidade,
        "avaliacao": registro.avaliacao,
        "received_at": registro.received_at.isoformat() if registro.received_at else None,
        "received_date": (
            timezone.localtime(registro.received_at).date().isoformat()
            if registro.received_at
            else None
        ),
        "is_imported": bool(is_imported),
        "comentarios_etapa_enabled": comentarios_etapa_enabled(),
        "sent_by": registro.sent_by,
        "emails": serialize_emails(registro),
        "origem": registro.origem,
        "origem_label": ORIGEM_LABELS.get(registro.origem, registro.origem or "—"),
        "categoria": registro.categoria or SuporteClaroRegistro.CATEGORIA_DEMANDA,
        "categoria_label": CATEGORIA_LABELS.get(
            registro.categoria, registro.categoria or "Suporte N1"
        ),
        "tipo_incidente": registro.tipo_incidente or "",
        "tipo_incidente_label": TIPO_INCIDENTE_LABELS.get(
            registro.tipo_incidente, registro.tipo_incidente or ""
        ),
        "status": registro.status,
        "status_label": STATUS_LABELS.get(registro.status, registro.status),
        "chamado_sistema": registro.chamado_sistema or "",
        "chamado_sistema_label": CHAMADO_SISTEMA_LABELS.get(
            registro.chamado_sistema, registro.chamado_sistema or ""
        ),
        "chamado_codigo": registro.chamado_codigo or "",
        "chamado_url": registro.chamado_url or "",
        "chamado_link": resolve_chamado_url(
            registro.chamado_sistema,
            registro.chamado_codigo,
            registro.chamado_url,
        ),
        "chamado_label": chamado_display_label(
            registro.chamado_sistema,
            registro.chamado_codigo,
        ),
        "chamado_interno": serialize_chamado_interno(registro, request),
        "chamados_externos": serialize_chamados_externos(registro, request),
        "portal_url": build_registro_portal_url(registro.id),
        "created_by": resolve_user_display_name(registro.created_by) if registro.created_by else "",
        "created_by_username": registro.created_by.username if registro.created_by else "",
        "created_at": registro.created_at.isoformat(),
        "updated_at": registro.updated_at.isoformat(),
        "anexos_count": anexos_qs.count(),
        "vinculos": list_vinculos_for(registro),
        **compute_sla_info(registro),
    }
    if detailed:
        payload["anexos"] = [serialize_anexo(a, request) for a in anexos_qs]
        payload["historico"] = [
            serialize_historico(h, request)
            for h in registro.historico.select_related("user").all()
        ]
    return payload
