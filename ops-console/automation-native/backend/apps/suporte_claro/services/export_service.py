# -*- coding: utf-8 -*-
from __future__ import annotations

from io import BytesIO

import pandas as pd
from django.db.models import Count, QuerySet
from django.utils import timezone

from apps.suporte_claro.services.externo import resolve_chamado_url
from apps.suporte_claro.services.protocolos import display_titulo, serialize_protocolos
from apps.suporte_claro.services.emails import serialize_emails


def _fmt_dt(value) -> str:
    if not value:
        return ""
    local = timezone.localtime(value)
    return local.strftime("%d/%m/%Y %H:%M")


def export_registros_xlsx(qs: QuerySet) -> bytes:
    rows_qs = (
        qs.annotate(anexos_total=Count("anexos"))
        .select_related("created_by")
        .prefetch_related("protocolos", "emails")
        .order_by("-received_at", "-id")
    )
    rows = []
    for reg in rows_qs:
        protocolos = serialize_protocolos(reg)
        protocolos_numeros = ", ".join(item["numero"] for item in protocolos)
        protocolos_comentarios = " | ".join(
            item["comentario"] for item in protocolos if item["comentario"]
        )
        emails = serialize_emails(reg)
        emails_enderecos = ", ".join(item["endereco"] for item in emails)
        rows.append(
            {
                "ID": reg.pk,
                "Titulo": display_titulo(reg),
                "Protocolo": reg.protocolo,
                "Protocolos": protocolos_numeros,
                "Comentarios protocolo": protocolos_comentarios,
                "Status": reg.get_status_display(),
                "Canal": reg.get_origem_display() if reg.origem else "",
                "Irregularidade (cliente)": reg.irregularidade,
                "Avaliação (retorno)": reg.avaliacao,
                "Recebido em": _fmt_dt(reg.received_at),
                "Quem enviou": reg.sent_by,
                "E-mails": emails_enderecos,
                "Chamado externo": reg.get_chamado_sistema_display() if reg.chamado_sistema else "",
                "Codigo chamado": reg.chamado_codigo,
                "Link chamado": resolve_chamado_url(
                    reg.chamado_sistema,
                    reg.chamado_codigo,
                    reg.chamado_url,
                ),
                "Cadastrado por": reg.created_by.username if reg.created_by else "",
                "Criado em": _fmt_dt(reg.created_at),
                "Atualizado em": _fmt_dt(reg.updated_at),
                "Qtd. anexos": reg.anexos_total,
            }
        )

    columns = [
        "ID",
        "Titulo",
        "Protocolo",
        "Protocolos",
        "Comentarios protocolo",
        "Status",
        "Canal",
        "Irregularidade (cliente)",
        "Avaliação (retorno)",
        "Recebido em",
        "Quem enviou",
        "E-mails",
        "Chamado externo",
        "Codigo chamado",
        "Link chamado",
        "Cadastrado por",
        "Criado em",
        "Atualizado em",
        "Qtd. anexos",
    ]
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Suporte Claro")
    buffer.seek(0)
    return buffer.getvalue()
