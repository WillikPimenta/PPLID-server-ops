# -*- coding: utf-8 -*-
"""PDF da ficha individual de uma demanda - Suporte Claro."""
from __future__ import annotations

import re
from io import BytesIO

from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle

from apps.falhas_criticas.services.user_display import resolve_user_display_name
from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.services.externo import chamado_display_label, resolve_chamado_url
from apps.suporte_claro.services.protocolos import display_titulo, serialize_protocolos
from apps.suporte_claro.services.pdf_report import _ReportDoc, _fmt_dt
from apps.suporte_claro.services.pdf_theme import (
    CLIENT_LABEL,
    GAP_BLOCK,
    GAP_SECTION,
    INNER_W,
    MARGIN_B,
    MARGIN_L,
    MARGIN_R,
    MARGIN_T,
    build_styles,
    report_header,
    section_panel,
    C,
)

_FICHA_TITLE = "Ficha da demanda"
_FICHA_SUBTITLE = "Registro individual para envio ao cliente"
_ANEXO_MAX_W = INNER_W * 0.45
_ANEXO_MAX_H = 5 * cm


def _safe_text(text: str) -> str:
    return (text or "-").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _field_block(label: str, value: str) -> list:
    st = build_styles()
    return [
        Paragraph(f"<b>{label}</b>", st["chart_title"]),
        Spacer(1, 4),
        Paragraph(_safe_text(value).replace("\n", "<br/>"), st["body"]),
    ]


def _info_table(rows: list[tuple[str, str]]) -> Table:
    st = build_styles()
    data = [[Paragraph(k, st["meta_label"]), Paragraph(v, st["meta_value"])] for k, v in rows]
    table = Table(data, colWidths=[INNER_W * 0.28, INNER_W * 0.72])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, C["border"]),
    ]))
    return table


def _anexo_images(registro: SuporteClaroRegistro) -> list:
    items: list = []
    for anexo in registro.anexos.all():
        if not anexo.file:
            continue
        ct = (anexo.content_type or "").lower()
        name = (anexo.original_name or "").lower()
        is_image = ct.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp"))
        if not is_image:
            label = anexo.original_name or f"Anexo #{anexo.id}"
            if name.endswith(".eml"):
                label = f"E-mail (.eml): {label}"
            items.append(Paragraph(_safe_text(label), build_styles()["body"]))
            continue
        try:
            img = Image(anexo.file.path)
            iw = float(img.imageWidth or 1)
            ih = float(img.imageHeight or 1)
            ratio = ih / iw
            width = _ANEXO_MAX_W
            height = width * ratio
            if height > _ANEXO_MAX_H:
                height = _ANEXO_MAX_H
                width = height / ratio
            img.drawWidth = width
            img.drawHeight = height
            caption = Paragraph(
                _safe_text(anexo.original_name or "Imagem"),
                build_styles()["chart_sub"],
            )
            items.append(Table([[caption], [img]], colWidths=[width]))
            items.append(Spacer(1, GAP_BLOCK))
        except OSError:
            items.append(Paragraph(_safe_text(anexo.original_name or "Anexo"), build_styles()["body"]))
    return items


def export_registro_pdf(registro: SuporteClaroRegistro, *, generated_by: str) -> bytes:
    buffer = BytesIO()
    generated_at = timezone.localtime(timezone.now()).strftime("%d/%m/%Y as %H:%M")
    received = _fmt_dt(registro.received_at)
    cadastrador = resolve_user_display_name(registro.created_by) if registro.created_by else "-"

    titulo = display_titulo(registro)
    protocolos = serialize_protocolos(registro)

    doc = _ReportDoc(
        buffer,
        generated_at=generated_at,
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=f"{_FICHA_TITLE} - {titulo}",
    )

    story: list = []
    story.append(report_header(
        received,
        generated_at,
        generated_by,
        title=_FICHA_TITLE,
        subtitle=f"{_FICHA_SUBTITLE} | {titulo}",
        period_label="Recebido em",
    ))
    story.append(Spacer(1, GAP_SECTION))

    ident_rows = [
        ("Titulo", titulo),
        ("Protocolo(s)", registro.protocolo),
        ("Status", registro.get_status_display()),
        ("Canal", registro.get_origem_display() if registro.origem else "-"),
        ("Cadastrado por", cadastrador),
        ("Recebido em", received),
        ("Quem enviou", registro.sent_by or "-"),
    ]
    if registro.chamado_sistema:
        ident_rows.append(
            (
                "Chamado externo",
                chamado_display_label(registro.chamado_sistema, registro.chamado_codigo),
            )
        )
        link = resolve_chamado_url(
            registro.chamado_sistema,
            registro.chamado_codigo,
            registro.chamado_url,
        )
        if link:
            ident_rows.append(("Link do chamado", link))
    ident_rows.extend(
        [
        ("Registrado em", _fmt_dt(registro.created_at)),
        ("Atualizado em", _fmt_dt(registro.updated_at)),
        ]
    )
    ident_content = [_info_table(ident_rows)]
    if protocolos:
        protocolo_rows = []
        for item in protocolos:
            comentario = item.get("comentario") or "-"
            protocolo_rows.append((item["numero"], comentario))
        ident_content.append(Spacer(1, GAP_BLOCK))
        ident_content.append(_info_table(protocolo_rows))

    story.append(section_panel(
        "Identificacao da demanda",
        f"{_FICHA_SUBTITLE} | Cliente {CLIENT_LABEL}",
        ident_content,
    ))
    story.append(Spacer(1, GAP_SECTION))

    solicitacao = _field_block("Irregularidade / solicitacao", registro.irregularidade or "-")
    story.append(section_panel(
        "Solicitacao do cliente",
        "Descricao recebida e dados do envio.",
        solicitacao,
    ))
    story.append(Spacer(1, GAP_SECTION))

    retorno = _field_block("Avaliacao / retorno do suporte", registro.avaliacao or "-")
    story.append(section_panel(
        "Retorno do suporte",
        "Resposta documentada pela equipe.",
        retorno,
    ))

    anexos = list(registro.anexos.all())
    if anexos:
        story.append(Spacer(1, GAP_SECTION))
        anexo_content: list = [
            Paragraph(
                f"<b>{len(anexos)}</b> anexo(s) vinculado(s) a esta demanda.",
                build_styles()["body"],
            ),
            Spacer(1, GAP_BLOCK),
            *_anexo_images(registro),
        ]
        story.append(section_panel(
            "Evidencias / anexos",
            "Imagens e arquivos associados ao registro.",
            anexo_content,
        ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def registro_pdf_filename(registro: SuporteClaroRegistro) -> str:
    safe = re.sub(r"[^\w\-]+", "_", (display_titulo(registro) or "demanda").strip())[:48].strip("_")
    return f"suporte_claro_{safe or 'demanda'}_{registro.id}.pdf"
