# -*- coding: utf-8 -*-
"""
Relatorio PDF - Suporte Claro.
Layout enxuto: resumo breve, KPIs de SLA e tabela unica de demandas.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from django.db.models import Count, QuerySet
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.services.analytics import ReportStats, build_report_narrative
from apps.suporte_claro.services.jira_copy import compute_sla_info
from apps.suporte_claro.services.pdf_fonts import PDF_FONT, ensure_pdf_fonts
from apps.suporte_claro.services.pdf_theme import (
    CONTENT_W,
    FOOTER_BRAND,
    GAP_BLOCK,
    GAP_SECTION,
    DEMANDA_TABLE_COL_WIDTHS,
    MARGIN_B,
    MARGIN_L,
    MARGIN_R,
    MARGIN_T,
    REPORT_FOOTNOTE,
    REPORT_TITLE,
    STATUS_THEME,
    build_styles,
    data_table,
    kpi_card,
    kpi_grid,
    narrative_block,
    report_footnote,
    report_header,
    section_header,
    section_panel,
    status_badge,
    td,
    td_sla,
    th,
    C,
)

_DETAIL_LIMIT = 50
CLARO_SLA_BLUE = "#1E40AF"


def _fmt_dt(value: datetime | None) -> str:
    if not value:
        return "-"
    return timezone.localtime(value).strftime("%d/%m/%Y %H:%M")


def _demanda_text(text: str) -> str:
    t = (text or "").strip().replace("\n", " ")
    return t or "-"


def _sla_cell(registro: SuporteClaroRegistro):
    sla = compute_sla_info(registro)
    pending = bool(sla["sla_pending"])
    label = "Pendente" if pending else (sla.get("sla_label") or "-")
    return td_sla(label, pending=pending), pending


def _is_pending_row(registro: SuporteClaroRegistro) -> bool:
    if registro.status == SuporteClaroRegistro.STATUS_CONCLUIDO:
        return False
    return bool(compute_sla_info(registro)["sla_pending"])


class _ReportDoc(SimpleDocTemplate):
    def __init__(self, buffer, *, generated_at: str, **kwargs):
        self._generated_at = generated_at
        super().__init__(buffer, **kwargs)

    def build(self, flowables, **kwargs):
        self._meta_generated_at = self._generated_at
        return super().build(
            flowables,
            onFirstPage=self._footer,
            onLaterPages=self._footer,
            **kwargs,
        )

    def _footer(self, canvas, doc):
        ensure_pdf_fonts()
        canvas.saveState()
        y = MARGIN_B - 0.5 * cm
        canvas.setStrokeColor(C["border"])
        canvas.line(MARGIN_L, y + 0.4 * cm, A4[0] - MARGIN_R, y + 0.4 * cm)
        canvas.setFont(PDF_FONT, 7)
        canvas.setFillColor(C["gray"])
        canvas.drawString(MARGIN_L, y, f"{FOOTER_BRAND} | {REPORT_TITLE} | {self._meta_generated_at}")
        canvas.drawRightString(A4[0] - MARGIN_R, y, f"Página {doc.page}")
        canvas.restoreState()


def _status_count(stats: ReportStats, key: str) -> int:
    bucket = next((b for b in stats.by_status if b.key == key), None)
    return bucket.count if bucket else 0


def _kpi_cards(stats: ReportStats) -> list:
    aberto = _status_count(stats, SuporteClaroRegistro.STATUS_ABERTO)
    andamento = _status_count(stats, SuporteClaroRegistro.STATUS_EM_ATENDIMENTO)
    concluido = _status_count(stats, SuporteClaroRegistro.STATUS_CONCLUIDO)
    em_fluxo = aberto + andamento

    sla_value = stats.sla_avg_label or "-"
    sla_hint = (
        f"{stats.sla_resolved} com retorno informado"
        if stats.sla_resolved
        else "Aguardando retorno"
    )
    sla_pct = (
        f"{stats.sla_pending} em acompanhamento" if stats.sla_pending else ""
    )

    concluidas_hint = (
        f"{stats.without_retorno} resposta pendente"
        if stats.without_retorno
        else "Com retorno informado"
    )
    concluidas_pct = (
        f"{stats.with_retorno_pct:.0f}% com retorno" if stats.total else ""
    )

    return [
        kpi_card("Total", str(stats.total), "Demandas", "", C["red"]),
        kpi_card(
            "Em fluxo",
            str(em_fluxo),
            f"{aberto} aguardando início · {andamento} em tratativa",
            "",
            colors.HexColor(STATUS_THEME["em_atendimento"]["bar"]),
        ),
        kpi_card(
            "SLA médio",
            sla_value,
            sla_hint,
            sla_pct,
            colors.HexColor(CLARO_SLA_BLUE),
        ),
        kpi_card(
            "Concluídas",
            str(concluido),
            concluidas_hint,
            concluidas_pct,
            colors.HexColor(STATUS_THEME["concluido"]["bar"]),
        ),
    ]


def _status_sort_key(registro: SuporteClaroRegistro) -> tuple:
    order = {
        SuporteClaroRegistro.STATUS_ABERTO: 0,
        SuporteClaroRegistro.STATUS_EM_ATENDIMENTO: 1,
        SuporteClaroRegistro.STATUS_CONCLUIDO: 2,
    }
    return (order.get(registro.status, 9), -(registro.received_at.timestamp() if registro.received_at else 0))


def _detail_rows(qs: QuerySet) -> tuple[list, int, set[int]]:
    rows_qs = list(
        qs.annotate(anexos_total=Count("anexos"))
        .select_related("created_by")
        .order_by("-received_at", "-id")
    )
    rows_qs.sort(key=_status_sort_key)
    total = len(rows_qs)
    displayed = rows_qs[:_DETAIL_LIMIT]

    rows = []
    highlight_rows: set[int] = set()
    for idx, reg in enumerate(displayed):
        sla_cell, pending = _sla_cell(reg)
        if _is_pending_row(reg):
            highlight_rows.add(idx)
        rows.append([
            td(reg.protocolo[:18]),
            status_badge(
                reg.status,
                reg.get_status_display(),
                col_width=DEMANDA_TABLE_COL_WIDTHS[1],
            ),
            td(reg.get_origem_display() if reg.origem else "-", muted=True),
            td(_fmt_dt(reg.received_at), muted=True),
            sla_cell,
            td(_demanda_text(reg.irregularidade)),
        ])
    return rows, total, highlight_rows


def export_registros_pdf(
    qs: QuerySet,
    stats: ReportStats,
    *,
    period_text: str,
    generated_by: str,
) -> bytes:
    buffer = BytesIO()
    generated_at = timezone.localtime(timezone.now()).strftime("%d/%m/%Y às %H:%M")
    st = build_styles()

    doc = _ReportDoc(
        buffer,
        generated_at=generated_at,
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=REPORT_TITLE,
    )
    story: list = []

    story.append(report_header(period_text, generated_at, generated_by))
    story.append(Spacer(1, GAP_SECTION))

    story.append(KeepTogether([
        section_panel(
            "Síntese do atendimento",
            "Resumo do período para acompanhamento.",
            [
                narrative_block(build_report_narrative(stats, period_text)),
                Spacer(1, GAP_BLOCK),
                kpi_grid(_kpi_cards(stats)),
            ],
        ),
    ]))

    detail_rows, detail_total, highlight_rows = _detail_rows(qs)
    if detail_rows:
        story.append(Spacer(1, GAP_SECTION))
        table = data_table(
            [
                th("Protocolo"),
                th("Status"),
                th("Canal"),
                th("Recebido"),
                th("SLA"),
                th("Demanda"),
            ],
            detail_rows,
            DEMANDA_TABLE_COL_WIDTHS,
            highlight_rows=highlight_rows,
        )
        demandas_tail: list = []
        if detail_total > _DETAIL_LIMIT:
            demandas_tail.append(Spacer(1, 6))
            demandas_tail.append(Paragraph(
                f"Listagem parcial: {detail_total} demandas no período.",
                st["body"],
            ))
        demandas_tail.append(Spacer(1, 6))
        footnote_wrap = Table([[report_footnote(REPORT_FOOTNOTE)]], colWidths=[CONTENT_W])
        footnote_wrap.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ]))
        demandas_tail.append(footnote_wrap)
        # Keep the data table as a top-level flowable so ReportLab can split it
        # across pages. Wrapping it in another Table creates one oversized,
        # indivisible cell and raises LayoutError on reports with many rows.
        demandas_header = section_header(
            "Demandas",
            "Demandas em tratativa ou concluídas no período.",
        )
        demandas_header.keepWithNext = True
        table.hAlign = "CENTER"
        table.spaceBefore = GAP_BLOCK
        table.spaceAfter = 12
        story.append(demandas_header)
        story.append(table)
        story.extend(demandas_tail)
    elif stats.total == 0:
        story.append(Spacer(1, GAP_SECTION))
        story.append(KeepTogether([
            section_header("Demandas"),
            Paragraph("Nenhuma demanda no período selecionado.", st["body"]),
        ]))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
