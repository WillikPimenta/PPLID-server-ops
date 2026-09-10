# -*- coding: utf-8 -*-
from __future__ import annotations

from apps.suporte_claro.models import SuporteClaroHistorico, SuporteClaroRegistro
from apps.suporte_claro.services.externo import CHAMADO_SISTEMA_LABELS

FIELD_LABELS = {
    "status": "Status",
    "titulo": "Titulo",
    "protocolo": "Protocolo",
    "protocolos": "Protocolos",
    "irregularidade": "Irregularidade",
    "avaliacao": "Avaliacao",
    "sent_by": "Quem enviou",
    "emails": "E-mails",
    "origem": "Canal",
    "categoria": "Categoria",
    "tipo_incidente": "Tipo de incidente",
    "vinculos": "Incidentes vinculados",
    "received_at": "Recebido em",
    "retorno_at": "Concluído em",
    "chamado_sistema": "Chamado externo",
    "chamado_codigo": "Codigo do chamado",
    "chamado_url": "Link do chamado",
    "chamados_externos": "Chamados externos",
    "anexos": "Anexos",
}

ORIGEM_LABELS = dict(SuporteClaroRegistro.ORIGEM_CHOICES)
STATUS_LABELS = dict(SuporteClaroRegistro.STATUS_CHOICES)
CATEGORIA_LABELS = dict(SuporteClaroRegistro.CATEGORIA_CHOICES)
TIPO_INCIDENTE_LABELS = dict(SuporteClaroRegistro.TIPO_INCIDENTE_CHOICES)


def _display_value(field_name: str, value: str) -> str:
    if field_name == "status":
        return STATUS_LABELS.get(value, value or "-")
    if field_name == "origem":
        return ORIGEM_LABELS.get(value, value or "-")
    if field_name == "categoria":
        return CATEGORIA_LABELS.get(value, value or "-")
    if field_name == "tipo_incidente":
        return TIPO_INCIDENTE_LABELS.get(value, value or "-")
    if field_name == "chamado_sistema":
        return CHAMADO_SISTEMA_LABELS.get(value, value or "-")
    text = (value or "").strip()
    return text if text else "-"


def log_registro_change(
    *,
    registro: SuporteClaroRegistro,
    user,
    action: str,
    field_name: str,
    old_value: str,
    new_value: str,
) -> SuporteClaroHistorico | None:
    old_norm = (old_value or "").strip()
    new_norm = (new_value or "").strip()
    if old_norm == new_norm:
        return None
    return SuporteClaroHistorico.objects.create(
        registro=registro,
        user=user,
        action=action,
        field_name=field_name,
        old_value=old_norm,
        new_value=new_norm,
    )


def historico_summary(entry: SuporteClaroHistorico) -> str:
    label = FIELD_LABELS.get(entry.field_name, entry.field_name or "Campo")
    old_disp = _display_value(entry.field_name, entry.old_value)
    new_disp = _display_value(entry.field_name, entry.new_value)
    if entry.action == SuporteClaroHistorico.ACTION_STATUS:
        return f"Status alterado de {old_disp} para {new_disp}"
    return f"{label} alterado de \"{old_disp}\" para \"{new_disp}\""
