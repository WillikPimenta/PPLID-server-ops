# -*- coding: utf-8 -*-
"""PDF executivo de incidentes com comparativo e relação completa."""
from __future__ import annotations

from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import CondPageBreak, Flowable, PageBreak, Paragraph, Spacer, Table, TableStyle

from apps.suporte_claro.services.incidentes_analytics import (
    IncidentesMomReport,
    MotivoBucket,
    MotivoIncidenteItem,
)
from apps.suporte_claro.services.pdf_fonts import (
    PDF_FONT,
    PDF_FONT_BOLD,
    ensure_pdf_fonts,
    xml_escape,
)
from apps.suporte_claro.services.pdf_report import _ReportDoc
from apps.suporte_claro.services.pdf_theme import (
    C,
    GAP_BLOCK,
    GAP_SECTION,
    INNER_W,
    MARGIN_B,
    MARGIN_L,
    MARGIN_R,
    MARGIN_T,
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
    th,
)

REPORT_TITLE_INC = "Relatório executivo de incidentes"
REPORT_SUB_INC = "Visão mensal, recorrência e rastreabilidade dos chamados"


class _MonthlyVolumeChart(Flowable):
    def __init__(self, labels: list[str], values: list[int], width: float = INNER_W, height: float = 145):
        super().__init__()
        self.labels = labels
        self.values = values
        self.width = width
        self.height = height

    def wrap(self, available_width, available_height):
        return min(self.width, available_width), self.height

    def draw(self):
        canvas = self.canv
        left, bottom, top = 28, 23, 14
        plot_w = self.width - left - 8
        plot_h = self.height - bottom - top
        max_value = max(self.values or [0])
        scale_max = max(1, max_value)

        canvas.setFont(PDF_FONT, 6.8)
        canvas.setStrokeColor(colors.HexColor("#E5E7EB"))
        canvas.setFillColor(colors.HexColor("#6B7280"))
        for step in range(4):
            value = round(scale_max * step / 3)
            y = bottom + plot_h * step / 3
            canvas.line(left, y, left + plot_w, y)
            canvas.drawRightString(left - 5, y - 2, str(value))

        count = max(1, len(self.values))
        slot = plot_w / count
        bar_w = min(28, slot * 0.58)
        points = []
        for idx, value in enumerate(self.values):
            x = left + idx * slot + (slot - bar_w) / 2
            bar_h = plot_h * value / scale_max if scale_max else 0
            if idx == len(self.values) - 1:
                fill = colors.HexColor("#E30613")
            elif idx == len(self.values) - 2:
                fill = colors.HexColor("#1E40AF")
            else:
                fill = colors.HexColor("#94A3B8")
            canvas.setFillColor(fill)
            canvas.roundRect(x, bottom, bar_w, max(1, bar_h), 2, stroke=0, fill=1)
            canvas.setFont(PDF_FONT_BOLD, 7)
            canvas.setFillColor(colors.HexColor("#1A1A1A"))
            canvas.drawCentredString(x + bar_w / 2, bottom + bar_h + 4, str(value))
            canvas.setFont(PDF_FONT, 6.5)
            canvas.setFillColor(colors.HexColor("#6B7280"))
            canvas.drawCentredString(x + bar_w / 2, 7, self.labels[idx])
            points.append((x + bar_w / 2, bottom + bar_h))

        if len(points) > 1:
            canvas.setStrokeColor(colors.HexColor("#334155"))
            canvas.setLineWidth(1.2)
            path = canvas.beginPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            canvas.drawPath(path, stroke=1, fill=0)


class _ProblemTrendChart(Flowable):
    PALETTE = ("#E30613", "#1E40AF", "#B45309", "#059669")

    def __init__(self, report: IncidentesMomReport, width: float = INNER_W, height: float = 175):
        super().__init__()
        self.report = report
        self.series = report.top_history_buckets[:4]
        self.width = width
        self.height = height

    def wrap(self, available_width, available_height):
        return min(self.width, available_width), self.height

    def draw(self):
        canvas = self.canv
        left, bottom, top = 28, 23, 36
        plot_w = self.width - left - 8
        plot_h = self.height - bottom - top
        max_value = max(
            [value for bucket in self.series for value in bucket.monthly_counts] or [0]
        )
        scale_max = max(1, max_value)

        canvas.setFont(PDF_FONT, 6.5)
        canvas.setStrokeColor(colors.HexColor("#E5E7EB"))
        canvas.setFillColor(colors.HexColor("#6B7280"))
        for step in range(4):
            value = round(scale_max * step / 3)
            y = bottom + plot_h * step / 3
            canvas.line(left, y, left + plot_w, y)
            canvas.drawRightString(left - 5, y - 2, str(value))

        labels = self.report.history_labels
        count = max(1, len(labels))
        slot = plot_w / max(1, count - 1)
        for idx, label in enumerate(labels):
            x = left + (idx * slot if count > 1 else plot_w / 2)
            canvas.setFillColor(colors.HexColor("#6B7280"))
            canvas.drawCentredString(x, 7, label)

        for series_idx, bucket in enumerate(self.series):
            color = colors.HexColor(self.PALETTE[series_idx])
            points = []
            for idx, value in enumerate(bucket.monthly_counts):
                x = left + (idx * slot if count > 1 else plot_w / 2)
                y = bottom + plot_h * value / scale_max
                points.append((x, y))
            if points:
                canvas.setStrokeColor(color)
                canvas.setFillColor(color)
                canvas.setLineWidth(1.7)
                path = canvas.beginPath()
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
                canvas.drawPath(path, stroke=1, fill=0)
                for x, y in points:
                    canvas.circle(x, y, 2.1, stroke=0, fill=1)

            legend_x = 8 + (series_idx % 2) * (self.width / 2)
            legend_y = self.height - 10 - (series_idx // 2) * 12
            canvas.setStrokeColor(color)
            canvas.setLineWidth(2)
            canvas.line(legend_x, legend_y, legend_x + 12, legend_y)
            canvas.setFillColor(colors.HexColor("#374151"))
            canvas.setFont(PDF_FONT, 6.5)
            label = bucket.label[:42] + ("..." if len(bucket.label) > 42 else "")
            canvas.drawString(legend_x + 16, legend_y - 2, label)


class _Sparkline(Flowable):
    def __init__(self, values: list[int], width: float = 82, height: float = 18):
        super().__init__()
        self.values = values
        self.width = width
        self.height = height

    def wrap(self, available_width, available_height):
        return min(self.width, available_width), self.height

    def draw(self):
        if not self.values:
            return
        canvas = self.canv
        maximum = max(1, max(self.values))
        step = self.width / max(1, len(self.values) - 1)
        points = [
            (idx * step, 2 + (self.height - 4) * value / maximum)
            for idx, value in enumerate(self.values)
        ]
        canvas.setStrokeColor(colors.HexColor("#1E40AF"))
        canvas.setFillColor(colors.HexColor("#E30613"))
        canvas.setLineWidth(1.2)
        path = canvas.beginPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        canvas.drawPath(path, stroke=1, fill=0)
        for idx, (x, y) in enumerate(points):
            canvas.setFillColor(
                colors.HexColor("#E30613") if idx == len(points) - 1 else colors.HexColor("#1E40AF")
            )
            canvas.circle(x, y, 1.7, stroke=0, fill=1)


def _fmt_delta(bucket: MotivoBucket) -> str:
    delta = bucket.delta
    if delta == 0:
        return "Estável"
    sign = "+" if delta > 0 else ""
    pct = bucket.delta_pct
    if pct is None:
        return f"{sign}{delta}"
    return f"{sign}{delta} ({sign}{pct:.0f}%)"


def _tag(bucket: MotivoBucket) -> str:
    if bucket.is_recurring:
        return "Recorrente"
    if bucket.is_new:
        return "Novo"
    return "Sem novo registro"


def _narrative_paragraphs(report: IncidentesMomReport) -> list[str]:
    delta = report.delta_total
    if delta == 0:
        movement = "volume estável"
    elif delta > 0:
        movement = f"aumento de {delta} ocorrência(s)"
    else:
        movement = f"redução de {abs(delta)} ocorrência(s)"

    paragraphs = [
        (
            f"No período {report.current_label}, foram registrados {report.current_total} incidente(s), "
            f"ante {report.previous_total} em {report.previous_label}: {movement}."
        )
    ]
    top = max(
        (bucket for bucket in report.buckets if bucket.current_count > 0),
        key=lambda bucket: (bucket.current_count, bucket.history_total),
        default=None,
    )
    if top:
        paragraphs.append(
            f"Principal motivo no mês: {top.label} ({top.current_count} ocorrência(s))."
        )
    if report.recurring_motivos:
        paragraphs.append(
            f"Prioridade recomendada: atuar na causa raiz dos {report.recurring_motivos} "
            "motivo(s) recorrente(s)."
        )
    elif report.current_total:
        paragraphs.append("Não houve motivo recorrente entre os dois meses comparados.")
    return paragraphs


def _history_narrative(report: IncidentesMomReport) -> list[str]:
    totals = report.history_totals
    if not totals:
        return ["Não há histórico disponível para a janela selecionada."]
    total = sum(totals)
    average = total / len(totals)
    peak_idx = max(range(len(totals)), key=totals.__getitem__)
    first_nonzero_idx = next((idx for idx, value in enumerate(totals) if value > 0), 0)
    first, latest = totals[first_nonzero_idx], totals[-1]
    direction_reference = (
        f"entre {report.history_labels[first_nonzero_idx]} e o fim da janela"
        if first_nonzero_idx
        else "entre o início e o fim da janela"
    )
    if latest > first:
        direction = f"alta de {latest - first} ocorrência(s) {direction_reference}"
    elif latest < first:
        direction = f"queda de {first - latest} ocorrência(s) {direction_reference}"
    else:
        direction = f"mesmo volume {direction_reference}"

    paragraphs = [
        (
            f"Nos últimos {report.history_months} meses foram registrados {total} incidente(s), "
            f"com média de {average:.1f} por mês e {direction}."
        ),
        (
            f"Pico do período: {report.history_labels[peak_idx]} com {totals[peak_idx]} "
            "ocorrência(s)."
        ),
    ]
    if first_nonzero_idx:
        paragraphs.append(
            f"A janela possui {first_nonzero_idx} mês(es) inicial(is) sem registros na base do portal."
        )
    risers = sorted(
        (bucket for bucket in report.buckets if bucket.delta > 0),
        key=lambda bucket: (-bucket.delta, -bucket.current_count, bucket.label.lower()),
    )
    fallers = sorted(
        (bucket for bucket in report.buckets if bucket.delta < 0),
        key=lambda bucket: (bucket.delta, -bucket.previous_count, bucket.label.lower()),
    )
    if risers:
        paragraphs.append(
            "Maior aumento no comparativo mensal: "
            f"{risers[0].label} ({risers[0].delta:+d})."
        )
    if fallers:
        paragraphs.append(
            "Maior redução no comparativo mensal: "
            f"{fallers[0].label} ({fallers[0].delta:+d})."
        )
    return paragraphs


def _month_change_panel(report: IncidentesMomReport) -> Table:
    st = build_styles()
    risers = sorted(
        (bucket for bucket in report.buckets if bucket.delta > 0),
        key=lambda bucket: (-bucket.delta, -bucket.current_count, bucket.label.lower()),
    )[:3]
    fallers = sorted(
        (bucket for bucket in report.buckets if bucket.delta < 0),
        key=lambda bucket: (bucket.delta, -bucket.previous_count, bucket.label.lower()),
    )[:3]

    def block(title: str, buckets: list[MotivoBucket], color: str, empty: str) -> Table:
        lines = [
            f"{xml_escape(bucket.label[:52])} <b>({bucket.delta:+d})</b>"
            for bucket in buckets
        ] or [empty]
        return Table(
            [
                [Paragraph(title, ParagraphStyle(
                    f"change_{title}",
                    parent=st["kpi_label"],
                    textColor=colors.HexColor(color),
                ))],
                [Paragraph("<br/>".join(lines), st["body"])],
            ],
            colWidths=[INNER_W / 2 - 10],
        )

    content = Table(
        [[
            block("AUMENTARAM", risers, "#B91C1C", "Nenhum aumento no mês."),
            block("REDUZIRAM", fallers, "#047857", "Nenhuma redução no mês."),
        ]],
        colWidths=[INNER_W / 2] * 2,
    )
    content.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#FEF2F2")),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#ECFDF5")),
        ("BOX", (0, 0), (-1, -1), 0.5, C["border"]),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, C["border"]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return section_panel(
        "Sinais do fechamento",
        "Problemas que mais aumentaram ou reduziram frente ao mês anterior.",
        [content],
    )


def _kpi_cards(report: IncidentesMomReport) -> list:
    delta = report.delta_total
    delta_text = "Estável" if delta == 0 else f"{delta:+d} vs mês anterior"
    return [
        kpi_card(
            "Mês atual",
            str(report.current_total),
            report.current_label,
            delta_text,
            C["red"],
        ),
        kpi_card(
            "Mês anterior",
            str(report.previous_total),
            report.previous_label,
            "Base de comparação",
            C["blue"],
        ),
        kpi_card(
            "Recorrentes",
            str(report.recurring_motivos),
            "Motivos presentes nos 2 meses",
            "Foco em causa raiz",
            colors.HexColor("#B45309"),
        ),
        kpi_card(
            "Novos",
            str(report.new_motivos),
            "Motivos do mês atual",
            f"{report.closed_motivos} sem novo registro",
            colors.HexColor("#2563EB"),
        ),
    ]


def _coverage_strip(report: IncidentesMomReport) -> Table:
    st = build_styles()
    label_style = ParagraphStyle(
        "inc_coverage_label",
        parent=st["kpi_label"],
        fontSize=7.2,
        leading=9,
    )
    value_style = ParagraphStyle(
        "inc_coverage_value",
        parent=st["kpi_value"],
        fontSize=15,
        leading=17,
    )
    cells = [
        ("COBERTURA", f"{len(report.all_items)} de {len(report.all_items)}", "registros incluídos"),
        ("COM SERVICENOW", str(report.service_now_count), "chamados identificados"),
        ("SEM SERVICENOW", str(report.without_service_now_count), "mantidos no relatório"),
    ]
    row = []
    for label, value, hint in cells:
        row.append(
            Table(
                [
                    [Paragraph(label, label_style)],
                    [Paragraph(value, value_style)],
                    [Paragraph(hint, st["kpi_hint"])],
                ],
                colWidths=[INNER_W / 3 - 8],
            )
        )
    table = Table([row], colWidths=[INNER_W / 3] * 3)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.75, C["border"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, C["border"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _filters_line(report: IncidentesMomReport) -> Paragraph:
    st = build_styles()
    style = ParagraphStyle(
        "inc_filters",
        parent=st["body"],
        fontSize=7.6,
        leading=10.5,
        textColor=C["gray"],
    )
    values = report.filters_applied or ["Todos os registros da categoria Incidente"]
    text = "Filtros aplicados: " + " | ".join(values)
    return Paragraph(xml_escape(text), style)


def _appendix_coverage_line(report: IncidentesMomReport) -> Paragraph:
    st = build_styles()
    style = ParagraphStyle(
        "inc_appendix_coverage",
        parent=st["body"],
        fontName=PDF_FONT_BOLD,
        fontSize=8,
        leading=11,
        textColor=C["gray"],
    )
    text = (
        f"Cobertura: {len(report.all_items)} de {len(report.all_items)} registros | "
        f"Com ServiceNow: {report.service_now_count} | "
        f"Sem ServiceNow: {report.without_service_now_count}"
    )
    return Paragraph(xml_escape(text), style)


def _mom_table(report: IncidentesMomReport) -> Table:
    header = [
        th("Motivo"),
        th("Tipo"),
        th(report.current_label),
        th(report.previous_label),
        th("Variação"),
        th("Leitura"),
    ]
    rows = [
        [
            td(bucket.label[:80]),
            td(bucket.tipo_label),
            td(str(bucket.current_count), muted=bucket.current_count == 0),
            td(str(bucket.previous_count), muted=bucket.previous_count == 0),
            td(_fmt_delta(bucket)),
            td(_tag(bucket)),
        ]
        for bucket in report.buckets
        if bucket.current_count > 0 or bucket.previous_count > 0
    ]
    if not rows:
        rows.append([td("Nenhum incidente no comparativo."), td("-"), td("0"), td("0"), td("-"), td("-")])
    table = data_table(
        header,
        rows,
        [
            INNER_W * 0.35,
            INNER_W * 0.13,
            INNER_W * 0.11,
            INNER_W * 0.11,
            INNER_W * 0.15,
            INNER_W * 0.15,
        ],
    )
    table.hAlign = "CENTER"
    return table


def _history_problem_table(report: IncidentesMomReport) -> Table:
    rows = []
    for bucket in report.top_history_buckets[:10]:
        rows.append(
            [
                td(bucket.label[:75]),
                td(bucket.tipo_label),
                td(str(bucket.history_total)),
                _Sparkline(bucket.monthly_counts),
                td(str(bucket.previous_count)),
                td(str(bucket.current_count)),
                td(_fmt_delta(bucket)),
            ]
        )
    if not rows:
        rows.append([td("Sem problemas no histórico."), td("-"), td("0"), td("-"), td("0"), td("0"), td("-")])
    table = data_table(
        [
            th("Problema / motivo"),
            th("Tipo"),
            th("Total"),
            th("Evolução mês a mês"),
            th(report.previous_label),
            th(report.current_label),
            th("Variação"),
        ],
        rows,
        [
            INNER_W * 0.29,
            INNER_W * 0.11,
            INNER_W * 0.08,
            INNER_W * 0.20,
            INNER_W * 0.10,
            INNER_W * 0.10,
            INNER_W * 0.12,
        ],
    )
    table.hAlign = "CENTER"
    return table


def _ticket_text(item: MotivoIncidenteItem) -> Paragraph:
    st = build_styles()
    style = ParagraphStyle("inc_ticket", parent=st["td"], fontSize=7.2, leading=9.5)
    codes = item.chamados_codigos or []
    code_text = ", ".join(codes) if codes else "Sem chamado"
    who = item.tratado_por.strip()
    text = xml_escape(code_text)
    if who:
        text += f"<br/><font color='#6B7280'>{xml_escape(who)}</font>"
    return Paragraph(text, style)


def _all_incidents_table(report: IncidentesMomReport) -> Table:
    st = build_styles()
    title_style = ParagraphStyle("inc_title_cell", parent=st["td"], fontSize=7.3, leading=9.5)
    rows = []
    pending_rows: set[int] = set()
    for idx, item in enumerate(report.all_items):
        when = timezone.localtime(item.received_at) if item.received_at else None
        period = when.strftime("%m/%Y") if when else "-"
        if item.status != "concluido":
            pending_rows.add(idx)
        rows.append(
            [
                td(period),
                td(when.strftime("%d/%m/%Y") if when else "-"),
                td(f"#{item.registro_id}"),
                Paragraph(xml_escape(item.titulo[:95]), title_style),
                td(item.tipo_label or "Outro"),
                status_badge(item.status, item.status_label, col_width=INNER_W * 0.13),
                _ticket_text(item),
            ]
        )
    if not rows:
        rows.append([td("-"), td("-"), td("-"), td("Nenhum incidente."), td("-"), td("-"), td("-")])
    table = data_table(
        [th("Mês"), th("Recebido"), th("ID"), th("Incidente / motivo"), th("Tipo"), th("Status"), th("Chamado / tratativa")],
        rows,
        [
            INNER_W * 0.10,
            INNER_W * 0.12,
            INNER_W * 0.08,
            INNER_W * 0.23,
            INNER_W * 0.12,
            INNER_W * 0.14,
            INNER_W * 0.21,
        ],
        highlight_rows=pending_rows,
    )
    table.hAlign = "CENTER"
    return table


def export_incidentes_mom_pdf(
    report: IncidentesMomReport,
    *,
    generated_by: str = "",
) -> bytes:
    ensure_pdf_fonts()
    generated_at = timezone.localtime().strftime("%d/%m/%Y às %H:%M")
    buffer = BytesIO()
    doc = _ReportDoc(
        buffer,
        generated_at=generated_at,
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=REPORT_TITLE_INC,
    )
    period_text = (
        f"{report.current_from.strftime('%d/%m/%Y')} a {report.current_to.strftime('%d/%m/%Y')} "
        f"vs {report.previous_from.strftime('%d/%m/%Y')} a {report.previous_to.strftime('%d/%m/%Y')}"
    )

    motives_header = section_header(
        "Comparativo detalhado: atual vs anterior",
        "Recorrências, novos motivos e reduções do último fechamento mensal.",
    )
    motives_header.keepWithNext = True
    problem_history_header = section_header(
        "Problemas mês a mês",
        "Principais motivos da janela histórica, com tendência e variação recente.",
    )
    problem_history_header.keepWithNext = True
    appendix_header = section_header(
        "Relação completa de incidentes",
        f"Todos os registros dos últimos {report.history_months} meses, inclusive sem ServiceNow.",
    )
    appendix_header.keepWithNext = True

    story = [
        report_header(
            period_text,
            generated_at,
            generated_by or "portal",
            title=REPORT_TITLE_INC,
            subtitle=REPORT_SUB_INC,
            period_label="Comparativo",
        ),
        Spacer(1, GAP_SECTION),
        section_header("Resumo executivo", "Leitura objetiva para acompanhamento com o cliente."),
        Spacer(1, GAP_BLOCK),
        narrative_block(_narrative_paragraphs(report)),
        Spacer(1, GAP_BLOCK),
        kpi_grid(_kpi_cards(report)),
        Spacer(1, GAP_BLOCK),
        _coverage_strip(report),
        Spacer(1, 6),
        _filters_line(report),
        Spacer(1, GAP_SECTION),
        _month_change_panel(report),
        PageBreak(),
        section_panel(
            f"Tendência dos últimos {report.history_months} meses",
            "Volume mensal e leitura do comportamento ao longo da janela selecionada.",
            [
                narrative_block(_history_narrative(report)),
                Spacer(1, GAP_BLOCK),
                _MonthlyVolumeChart(report.history_labels, report.history_totals),
            ],
        ),
        Spacer(1, GAP_SECTION),
        problem_history_header,
        Spacer(1, GAP_BLOCK),
        _ProblemTrendChart(report),
        Spacer(1, GAP_BLOCK),
        _history_problem_table(report),
        Spacer(1, GAP_SECTION),
        motives_header,
        Spacer(1, GAP_BLOCK),
        _mom_table(report),
        CondPageBreak(260),
        appendix_header,
        Spacer(1, GAP_BLOCK),
        _appendix_coverage_line(report),
        Spacer(1, GAP_BLOCK),
        _all_incidents_table(report),
        Spacer(1, GAP_BLOCK),
        report_footnote(
            f"Cobertura integral dos registros de incidente dos últimos {report.history_months} meses. "
            "O resumo agrupa ocorrências por tipo, título normalizado e vínculos manuais. "
            f"Gerado por {generated_by or 'portal'} em {generated_at}."
        ),
    ]
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def incidentes_mom_pdf_filename(report: IncidentesMomReport) -> str:
    return f"suporte_claro_incidentes_{report.current_from.strftime('%Y-%m')}.pdf"
