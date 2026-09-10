# -*- coding: utf-8 -*-
"""Relatório executivo PDF da projeção de replicação D-1."""
from __future__ import annotations

from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import Flowable, KeepTogether, LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.replicacao_d1.services.dashboard import (
    WorkflowProjectionParams,
    _projection_filtered_summary,
    build_projection_destino_breakdown,
    build_workflow_projection_export_data,
)
from apps.suporte_claro.services.pdf_fonts import PDF_FONT, PDF_FONT_BOLD, ensure_pdf_fonts, xml_escape
from apps.suporte_claro.services.pdf_theme import (
    C,
    CONTENT_W,
    GAP_BLOCK,
    GAP_SECTION,
    INNER_W,
    MARGIN_B,
    MARGIN_L,
    MARGIN_R,
    MARGIN_T,
    PAD_PAGE,
    attention_list,
    build_styles,
    chart_cell,
    data_table,
    kpi_card,
    kpi_grid,
    narrative_block,
    report_footnote,
    report_header,
    section_header,
    td,
    td_num,
    th,
)

REPORT_TITLE = "Análise Executiva de Replicação D-1"
REPORT_SUBTITLE = "Realizado (15 dias) · projeção (15 dias) · horizonte de 30 dias"
FOOTER_BRAND = "Indicadores | Serasa Experian"
HEADER_BRAND = "INDICADORES · SERASA EXPERIAN"
SERASA_BLUE = "#1E4F91"
SERASA_BLUE_LIGHT = "#436DA9"
SERASA_PURPLE = "#77127A"
SERASA_MAGENTA = "#E80070"
SERASA_PINK = "#C1188B"


def _fmt_number(value: int | float) -> str:
    return f"{int(round(value)):,}".replace(",", ".")


def _fmt_pct(value: float | None, *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:.1f}%".replace(".", ",")


def _fmt_date(value: str) -> str:
    try:
        year, month, day = value[:10].split("-")
        return f"{day}/{month}/{year}"
    except (AttributeError, ValueError):
        return value or "-"


class _ProjectionReportDoc(SimpleDocTemplate):
    def __init__(self, buffer, *, generated_at: str, **kwargs):
        self._generated_at = generated_at
        super().__init__(buffer, **kwargs)

    def build(self, flowables, **kwargs):
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
        canvas.drawString(MARGIN_L, y, f"{FOOTER_BRAND} | {REPORT_TITLE} | {self._generated_at}")
        canvas.drawRightString(A4[0] - MARGIN_R, y, f"Página {doc.page}")
        canvas.restoreState()


class _ActualHistoryChart(Flowable):
    SERIES = (
        ("automatico", "Automático", SERASA_BLUE),
        ("manual", "Manual", SERASA_MAGENTA),
        ("nao_classificado", "Não classificado", "#94A3B8"),
    )

    def __init__(self, rows: list[dict], width: float = INNER_W, height: float = 168):
        super().__init__()
        self.rows = rows
        self.width = width
        self.height = height

    def wrap(self, available_width, available_height):
        return min(self.width, available_width), self.height

    def draw(self):
        canvas = self.canv
        left, bottom, top = 34, 24, 28
        plot_w = self.width - left - 8
        plot_h = self.height - bottom - top
        totals = [sum(int(row[key]) for key, _, _ in self.SERIES) for row in self.rows]
        scale_max = max(1, max(totals or [0]))
        _draw_chart_grid(canvas, left, bottom, plot_w, plot_h, scale_max)
        _draw_legend(canvas, self.SERIES, self.height)

        slot = plot_w / max(1, len(self.rows))
        bar_w = min(18, slot * 0.62)
        for index, row in enumerate(self.rows):
            x = left + index * slot + (slot - bar_w) / 2
            y = bottom
            for key, _, color in self.SERIES:
                value = int(row[key])
                segment_h = plot_h * value / scale_max
                if segment_h > 0:
                    canvas.setFillColor(colors.HexColor(color))
                    canvas.rect(x, y, bar_w, segment_h, stroke=0, fill=1)
                    y += segment_h
            total = totals[index]
            if total > 0:
                canvas.setFont(PDF_FONT_BOLD, 6.2)
                canvas.setFillColor(colors.HexColor("#374151"))
                canvas.drawCentredString(x + bar_w / 2, y + 3, _fmt_number(total))
            _draw_x_label(canvas, row["data"], index, len(self.rows), x + bar_w / 2)


class _FutureProjectionChart(Flowable):
    def __init__(self, rows: list[dict], width: float = INNER_W, height: float = 168):
        super().__init__()
        self.rows = rows
        self.width = width
        self.height = height

    def wrap(self, available_width, available_height):
        return min(self.width, available_width), self.height

    def draw(self):
        canvas = self.canv
        left, bottom, top = 34, 24, 28
        plot_w = self.width - left - 8
        plot_h = self.height - bottom - top
        projected_values = [int(row["projetado"]) for row in self.rows]
        capacity_values = [int(row.get("capacidade") or 0) for row in self.rows]
        scale_max = max(1, max(projected_values + capacity_values or [0]))
        _draw_chart_grid(canvas, left, bottom, plot_w, plot_h, scale_max)
        _draw_legend(
            canvas,
            (
                ("capacidade", "Capacidade", "#CBD5E1"),
                ("projetado", "Projeção", SERASA_BLUE),
            ),
            self.height,
        )

        slot = plot_w / max(1, len(self.rows))
        bar_w = min(18, slot * 0.62)
        for index, row in enumerate(self.rows):
            x = left + index * slot + (slot - bar_w) / 2
            point_x = x + bar_w / 2
            capacity_h = plot_h * int(row.get("capacidade") or 0) / scale_max
            if capacity_h > 0:
                canvas.setFillColor(colors.HexColor("#E2E8F0"))
                canvas.roundRect(x, bottom, bar_w, max(0.8, capacity_h), 1.5, stroke=0, fill=1)
            bar_h = plot_h * row["projetado"] / scale_max
            canvas.setFillColor(colors.HexColor(SERASA_BLUE))
            canvas.roundRect(x, bottom, bar_w, max(0.8, bar_h), 1.5, stroke=0, fill=1)
            if row["projetado"] > 0:
                _draw_data_label(
                    canvas,
                    point_x,
                    bottom + bar_h + 4,
                    _fmt_number(row["projetado"]),
                    SERASA_BLUE,
                )
            _draw_x_label(canvas, row["data"], index, len(self.rows), point_x)



def _draw_chart_grid(canvas, left: float, bottom: float, plot_w: float, plot_h: float, scale_max: int):
    canvas.setFont(PDF_FONT, 6.5)
    canvas.setStrokeColor(colors.HexColor("#E5E7EB"))
    canvas.setFillColor(colors.HexColor("#6B7280"))
    for step in range(4):
        value = round(scale_max * step / 3)
        y = bottom + plot_h * step / 3
        canvas.line(left, y, left + plot_w, y)
        canvas.drawRightString(left - 5, y - 2, _fmt_number(value))


def _draw_data_label(
    canvas,
    x: float,
    baseline_y: float,
    text: str,
    color: str,
    *,
    font_size: float = 6.1,
):
    """Desenha valor em etiqueta clara para manter contraste sobre barras e linhas."""
    text_width = canvas.stringWidth(text, PDF_FONT_BOLD, font_size)
    badge_width = text_width + 5
    badge_height = font_size + 3.5
    canvas.setFillColor(colors.white)
    canvas.setStrokeColor(colors.HexColor("#E5E7EB"))
    canvas.setLineWidth(0.35)
    canvas.roundRect(
        x - badge_width / 2,
        baseline_y - 2.2,
        badge_width,
        badge_height,
        1.4,
        stroke=1,
        fill=1,
    )
    canvas.setFont(PDF_FONT_BOLD, font_size)
    canvas.setFillColor(colors.HexColor(color))
    canvas.drawCentredString(x, baseline_y, text)


def _draw_legend(canvas, series, height: float, *, line_keys: set[str] | None = None):
    line_keys = line_keys or set()
    x = 4
    y = height - 11
    for key, label, color in series:
        canvas.setStrokeColor(colors.HexColor(color))
        canvas.setFillColor(colors.HexColor(color))
        if key in line_keys:
            canvas.setLineWidth(1.6)
            canvas.line(x, y + 2, x + 12, y + 2)
            canvas.circle(x + 6, y + 2, 1.5, stroke=0, fill=1)
        else:
            canvas.rect(x, y - 1, 10, 7, stroke=0, fill=1)
        canvas.setFillColor(colors.HexColor("#374151"))
        canvas.setFont(PDF_FONT_BOLD, 6.5)
        canvas.drawString(x + 15, y, label)
        x += 20 + canvas.stringWidth(label, PDF_FONT_BOLD, 6.5)


def _draw_x_label(canvas, value: str, index: int, count: int, x: float):
    if count <= 7 or index == 0 or index == count - 1 or index % 3 == 0:
        canvas.setFont(PDF_FONT, 6.2)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawCentredString(x, 7, _fmt_date(value)[:5])


def _projection_summary(meta: dict, rows: list[dict]) -> dict:
    filtered = _projection_filtered_summary(meta, rows)
    actual = filtered["global_actual_15"]
    projected = filtered["global_projected_15"]
    automatic = filtered["global_actual_automatic_15"]
    manual = filtered["global_actual_manual_15"]
    unclassified = filtered["global_actual_unclassified_15"]
    variation = filtered["variation_15_pct"]
    daily = []
    for index, global_day in enumerate(filtered["global_series"]):
        daily.append(
            {
                "data": global_day["data"],
                "capacidade": int(global_day["meta"]),
                "capacidades": {
                    channel["canal"]: int(channel["series"][index]["capacidade"])
                    for channel in filtered["channel_series"]
                },
                "projetado": int(global_day["projetado"]),
            }
        )
    historical_daily = []
    historical_source = meta.get("historical_series", [])
    for index, historical_day in enumerate(historical_source):
        historical_daily.append(
            {
                "data": historical_day["data"],
                "automatico": sum(int(row["historical_series"][index]["automatic"]) for row in rows),
                "manual": sum(int(row["historical_series"][index]["manual"]) for row in rows),
                "nao_classificado": sum(int(row["historical_series"][index]["unclassified"]) for row in rows),
            }
        )
    return {
        "actual": actual,
        "projected": projected,
        "projected_7": sum(day["projetado"] for day in daily[:7]),
        "automatic": automatic,
        "manual": manual,
        "unclassified": unclassified,
        "variation": variation,
        "workflow_count": len(rows),
        "daily": daily,
        "channels": filtered["channel_series"],
        "historical_daily": historical_daily,
    }


def _filter_chips(params: WorkflowProjectionParams) -> Table:
    chip = Table([[Paragraph(xml_escape(_filter_chips_text(params)), build_styles()["section_sub"])]], colWidths=[CONTENT_W - 24])
    chip.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7EFF8")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E6D4E8")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return chip


def _filter_chips_text(params: WorkflowProjectionParams) -> str:
    mapping = [
        ("Workflow", params.workflow),
        ("Cliente", params.cliente),
        ("Canal", params.canal),
        ("Fila", params.fila),
    ]
    active = [f"{label}: {value}" for label, value in mapping if value]
    return " · ".join(active) if active else "Recorte completo — sem filtros adicionais"


def _executive_highlights(summary: dict, rows: list[dict], meta: dict) -> list[str]:
    highlights: list[str] = []
    peak = max(summary["daily"], key=lambda item: item["projetado"], default=None)
    if peak and peak["projetado"] > 0:
        highlights.append(
            f"Pico projetado: {_fmt_number(peak['projetado'])} replicações em {_fmt_date(peak['data'])}."
        )
    capacity_total = sum(day["capacidade"] for day in summary["daily"])
    if capacity_total and summary["projected"]:
        use = summary["projected"] / capacity_total * 100
        highlights.append(
            f"Uso estimado da capacidade: {_fmt_pct(use)} do total disponível nos canais do filtro."
        )
    if len(rows) >= 3 and summary["projected"]:
        top_three = sum(row["projected_15"] for row in rows[:3])
        highlights.append(
            f"Concentração: os 3 maiores workflows respondem por {_fmt_pct(top_three / summary['projected'] * 100)} "
            "da projeção."
        )
    scale_days = int(meta.get("scale_days_covered", 15))
    base_status = "escala fallback" if meta["fallback_rate_used"] else "escala oficial"
    highlights.append(
        f"Premissa de projeção: {base_status}, cobertura {scale_days}/15 dias · histórico de {meta['history_days']} dias."
    )
    return highlights


def _narrative(summary: dict, rows: list[dict]) -> list[str]:
    actual = summary["actual"]
    projected = summary["projected"]
    variation = summary["variation"]
    paragraphs = [
        (
            f"Nos últimos 15 dias foram registradas {_fmt_number(actual)} replicações. "
            f"Para os próximos 15 dias, a projeção estima {_fmt_number(projected)} replicações "
            f"em {_fmt_number(summary['workflow_count'])} workflows no recorte selecionado."
        )
    ]
    if summary["projected_7"]:
        paragraphs.append(
            f"Na primeira semana projetada, o volume acumulado chega a {_fmt_number(summary['projected_7'])} replicações."
        )
    if variation is None:
        paragraphs.append(
            "Não há base realizada suficiente para calcular variação percentual. "
            "Priorize volumes absolutos, mix manual/automático e concentração por workflow."
        )
    else:
        direction = "crescimento" if variation > 0 else "redução" if variation < 0 else "estabilidade"
        paragraphs.append(
            f"Em relação à janela anterior comparável, a projeção indica {direction} de "
            f"{_fmt_pct(abs(variation))}. Trata-se de estimativa operacional, não meta contratual."
        )
    classified = summary["automatic"] + summary["manual"]
    if classified:
        auto_share = summary["automatic"] / classified * 100
        manual_share = summary["manual"] / classified * 100
        paragraphs.append(
            f"Foram replicados {_fmt_number(summary['automatic'])} casos automáticos ({_fmt_pct(auto_share)}) e "
            f"{_fmt_number(summary['manual'])} casos manuais ({_fmt_pct(manual_share)})."
        )
    if summary["unclassified"]:
        paragraphs.append(
            f"Há ainda {_fmt_number(summary['unclassified'])} casos não classificados no realizado "
            "(sem matrícula ou origem identificável)."
        )
    if rows and projected:
        top = rows[0]
        paragraphs.append(
            f"O workflow de maior replicação é {top['workflow']}, com {_fmt_number(top['projected_15'])} "
            f"replicações projetadas ({_fmt_pct(top['projected_15'] / projected * 100)} do volume do filtro)."
        )
    return paragraphs


def _kpis(summary: dict) -> list:
    variation = summary["variation"]
    variation_color = colors.HexColor(SERASA_PURPLE if variation is not None and variation >= 0 else SERASA_MAGENTA)
    return [
        kpi_card("Realizado 15d", _fmt_number(summary["actual"]), "Janela histórica oficial", "", colors.HexColor(SERASA_BLUE)),
        kpi_card("Projetado 15d", _fmt_number(summary["projected"]), "Próximos 15 dias", "", colors.HexColor(SERASA_PURPLE)),
        kpi_card("Projetado 7d", _fmt_number(summary["projected_7"]), "Primeira semana", "", colors.HexColor(SERASA_BLUE_LIGHT)),
        kpi_card("Variação", _fmt_pct(variation, sign=True), "Vs. 15 dias anteriores", "", variation_color),
    ]


def _context_card(label: str, value: str, hint: str, accent: colors.Color) -> Table:
    st = build_styles()
    width = INNER_W / 3 - 6
    card = Table(
        [
            [Paragraph(label.upper(), st["kpi_label"])],
            [Paragraph(value, st["meta_value"])],
            [Paragraph(hint, st["kpi_hint"])],
        ],
        colWidths=[width],
    )
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C["white"]),
                ("BOX", (0, 0), (-1, -1), 0.5, C["border"]),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return card


def _operational_context(summary: dict, meta: dict, rows: list[dict]) -> Table:
    capacity_total = sum(day["capacidade"] for day in summary["daily"])
    capacity_use = summary["projected"] / capacity_total * 100 if capacity_total else None
    classified = summary["automatic"] + summary["manual"]
    automatic_share = summary["automatic"] / classified * 100 if classified else None
    manual_share = summary["manual"] / classified * 100 if classified else None
    top_three = sum(row["projected_15"] for row in rows[:3])
    top_three_share = top_three / summary["projected"] * 100 if summary["projected"] else None
    peak = max(summary["daily"], key=lambda item: item["projetado"], default=None)
    average = summary["projected"] / max(1, len(summary["daily"]))
    base_status = "Fallback" if meta["fallback_rate_used"] else "Oficial"
    scale_days = int(meta.get("scale_days_covered", 15))
    cards = [
        _context_card("Média diária", _fmt_number(average), "Volume médio projetado", colors.HexColor(SERASA_BLUE)),
        _context_card(
            "Pico diário",
            _fmt_number(peak["projetado"] if peak else 0),
            f"Em {_fmt_date(peak['data']) if peak else '-'}",
            colors.HexColor(SERASA_MAGENTA),
        ),
        _context_card("Capacidade", _fmt_pct(capacity_use), "Projeção ÷ capacidade dos canais", colors.HexColor(SERASA_PURPLE)),
        _context_card("Automático", _fmt_pct(automatic_share), f"{_fmt_number(summary['automatic'])} casos no realizado", colors.HexColor(SERASA_BLUE_LIGHT)),
        _context_card("Manual", _fmt_pct(manual_share), f"{_fmt_number(summary['manual'])} casos no realizado", colors.HexColor(SERASA_PINK)),
        _context_card("Top 3 workflows", _fmt_pct(top_three_share), f"Escala {base_status} · {scale_days}/15 dias", colors.HexColor(SERASA_PURPLE)),
    ]
    width = INNER_W / 3
    grid = Table([cards[:3], cards[3:]], colWidths=[width] * 3, hAlign="LEFT")
    grid.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-2, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ]
        )
    )
    return grid


def _section_body_panel(content: list[Flowable]) -> Table:
    """Corpo de seção com borda, separado do cabeçalho."""
    body_rows = [[item] for item in content]
    inner = Table(body_rows, colWidths=[INNER_W])
    inner.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    shell = Table([[inner]], colWidths=[CONTENT_W])
    shell.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.75, C["border"]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.75, C["border"]),
                ("LEFTPADDING", (0, 0), (-1, -1), PAD_PAGE),
                ("RIGHTPADDING", (0, 0), (-1, -1), PAD_PAGE),
                ("TOPPADDING", (0, 0), (-1, -1), GAP_BLOCK),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return shell


def _append_section(story: list, title: str, subtitle: str, content: list[Flowable]) -> None:
    """Cabeçalho e conteúdo iniciam juntos; evita título órfão no fim da página."""
    header = section_header(title, subtitle)
    body = _section_body_panel(content)
    story.append(KeepTogether([header, body]))
    story.append(Spacer(1, GAP_SECTION))


def _workflow_table(rows: list[dict], summary: dict) -> Table:
    detail_rows = []
    total_actual = 0
    total_projected = 0
    total = summary["projected"]
    for row in rows:
        variation = (
            (row["projected_15"] - row["actual_15"]) / row["actual_15"] * 100
            if row["actual_15"]
            else None
        )
        share = row["projected_15"] / total * 100 if total else 0
        total_actual += row["actual_15"]
        total_projected += row["projected_15"]
        detail_rows.append(
            [
                td(row["workflow"]),
                td(row["cliente"] or "-", muted=True),
                td(row["canal"], muted=True),
                td_num(_fmt_number(row["actual_15"])),
                td_num(_fmt_number(row["projected_15"])),
                td_num(_fmt_pct(share)),
                td_num(_fmt_pct(variation, sign=True)),
            ]
        )
    if detail_rows:
        total_variation = (
            (total_projected - total_actual) / total_actual * 100 if total_actual else None
        )
        detail_rows.append(
            [
                th("Total"),
                th(""),
                th(""),
                td_num(_fmt_number(total_actual)),
                td_num(_fmt_number(total_projected)),
                td_num(_fmt_pct(100.0 if total else 0)),
                td_num(_fmt_pct(total_variation, sign=True)),
            ]
        )
    col_widths = [
        INNER_W * 0.24,
        INNER_W * 0.17,
        INNER_W * 0.13,
        INNER_W * 0.11,
        INNER_W * 0.12,
        INNER_W * 0.12,
        INNER_W * 0.11,
    ]
    header_cells = [
        th("Workflow"),
        th("Cliente"),
        th("Canal"),
        th("Realizado"),
        th("Projetado"),
        th("Participação"),
        th("Variação"),
    ]
    table = LongTable([header_cells, *detail_rows], colWidths=col_widths, hAlign="LEFT", repeatRows=1)
    accent = colors.HexColor(SERASA_MAGENTA)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), C["gray_light"]),
                ("LINEBELOW", (0, 0), (-1, 0), 1, accent),
                ("FONTNAME", (0, 0), (-1, 0), PDF_FONT_BOLD),
                ("GRID", (0, 0), (-1, -1), 0.25, C["border"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C["white"], colors.HexColor("#F3F4F6")]),
            ]
        )
    )
    return table


def _destino_table(destino_breakdown: dict) -> Table:
    rows = destino_breakdown.get("por_destino") or []
    total = int(destino_breakdown.get("total_replicados") or 0)
    detail_rows = []
    for row in rows:
        share = row.get("share_pct")
        if share is None and total:
            share = round((int(row["total"]) / total) * 100, 1)
        detail_rows.append(
            [
                td(row["label"]),
                td_num(_fmt_number(row["total"])),
                td_num(_fmt_pct(share)),
            ]
        )
    if not detail_rows:
        detail_rows.append([td("Sem replicações oficiais no recorte.", muted=True), td_num("-"), td_num("-")])
    return data_table(
        [th("Workflow destino"), th("Volume"), th("% do total")],
        detail_rows,
        [INNER_W * 0.52, INNER_W * 0.24, INNER_W * 0.24],
        accent=colors.HexColor(SERASA_BLUE),
    )


def _destino_narrative(destino_breakdown: dict) -> list[str]:
    rows = destino_breakdown.get("por_destino") or []
    total = int(destino_breakdown.get("total_replicados") or 0)
    if not total or not rows:
        return [
            "Não há confirmações oficiais por workflow destino na janela realizada de 15 dias "
            "para os filtros selecionados."
        ]
    period = (
        f"{_fmt_date(destino_breakdown['periodo_de'])} a {_fmt_date(destino_breakdown['periodo_ate'])}"
    )
    lead = (
        f"No realizado oficial ({period}), foram confirmadas {_fmt_number(total)} replicações "
        "no BRFlow, distribuídas por workflow destino:"
    )
    bullets = [
        f"{row['label']}: {_fmt_number(row['total'])} ({_fmt_pct(row.get('share_pct'))})"
        for row in rows
    ]
    return [lead, *bullets]


def export_workflow_projection_pdf(
    params: WorkflowProjectionParams,
    *,
    generated_by: str,
) -> tuple[str, bytes]:
    meta, rows = build_workflow_projection_export_data(params)
    summary = _projection_summary(meta, rows)
    destino_breakdown = build_projection_destino_breakdown(params)
    generated_at = timezone.localtime(timezone.now()).strftime("%d/%m/%Y às %H:%M")
    period_text = f"{_fmt_date(meta['horizon_start'])} a {_fmt_date(meta['horizon_end'])}"
    st = build_styles()

    buffer = BytesIO()
    doc = _ProjectionReportDoc(
        buffer,
        generated_at=generated_at,
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=REPORT_TITLE,
    )
    story: list = [
        report_header(
            period_text,
            generated_at,
            generated_by,
            title=REPORT_TITLE,
            subtitle=REPORT_SUBTITLE,
            period_label="Horizonte",
            system_label=HEADER_BRAND,
            show_client=False,
            show_generated_at=True,
            show_responsible=True,
            meta_width=5.4 * cm,
            meta_valign="MIDDLE",
            brand_color=colors.HexColor(SERASA_MAGENTA),
            accent_color=colors.HexColor(SERASA_MAGENTA),
            header_background=colors.HexColor("#FCFAFD"),
            meta_background=colors.HexColor("#F7EFF8"),
            meta_border=colors.HexColor("#E6D4E8"),
            compact=True,
            title_alignment=TA_LEFT,
        ),
        Spacer(1, GAP_BLOCK),
        _filter_chips(params),
        Spacer(1, GAP_SECTION),
        KeepTogether(
            [
                section_header(
                    "Síntese executiva",
                    "Panorama consolidado do realizado, da projeção e dos principais sinais operacionais.",
                ),
                _section_body_panel(
                    [
                        kpi_grid(_kpis(summary)),
                        Spacer(1, GAP_BLOCK),
                        narrative_block(_narrative(summary, rows), accent=colors.HexColor(SERASA_BLUE)),
                        Spacer(1, GAP_BLOCK),
                        attention_list(_executive_highlights(summary, rows, meta)),
                    ]
                ),
            ]
        ),
        Spacer(1, GAP_SECTION),
    ]

    destino_paragraphs = _destino_narrative(destino_breakdown)
    destino_content: list = [
        narrative_block([destino_paragraphs[0]], accent=colors.HexColor(SERASA_BLUE)),
    ]
    if len(destino_paragraphs) > 1:
        destino_content.extend(
            [
                Spacer(1, GAP_BLOCK),
                attention_list(destino_paragraphs[1:]),
            ]
        )
    destino_content.extend([Spacer(1, GAP_BLOCK), _destino_table(destino_breakdown)])

    _append_section(
        story,
        "Replicação por workflow destino",
        "Confirmações oficiais BRFlow na janela realizada, agrupadas por destino.",
        destino_content,
    )

    _append_section(
        story,
        "Indicadores operacionais",
        "Referências rápidas para leitura de volume, mix e concentração.",
        [_operational_context(summary, meta, rows)],
    )

    _append_section(
        story,
        "Evolução realizada e projetada",
        "Composição histórica do realizado e projeção diária com referência de capacidade.",
        [
            chart_cell(
                "Realizado — últimos 15 dias",
                "Empilhado por automático, manual e não classificado.",
                _ActualHistoryChart(summary["historical_daily"]),
                width=INNER_W,
            ),
            Spacer(1, GAP_BLOCK),
            chart_cell(
                "Projetado — próximos 15 dias",
                "Barras azuis: projeção · fundo cinza: capacidade disponível por dia.",
                _FutureProjectionChart(summary["daily"]),
                width=INNER_W,
            ),
        ],
    )

    workflow_subtitle = (
        f"{len(rows)} workflows no recorte, ordenados por maior volume projetado."
        if rows
        else "Nenhum workflow encontrado para os filtros selecionados."
    )
    workflow_header = section_header("Workflows de maior replicação", workflow_subtitle)
    workflow_header.keepWithNext = True
    story.append(workflow_header)
    if rows:
        workflow_table = _workflow_table(rows, summary)
        workflow_table.spaceBefore = GAP_BLOCK
        workflow_table.spaceAfter = GAP_BLOCK
        story.append(workflow_table)
    else:
        story.append(Paragraph("Nenhum workflow encontrado para os filtros selecionados.", st["body"]))
    story.append(Spacer(1, GAP_SECTION))

    filter_text = _filter_chips_text(params)
    methodology = (
        "Metodologia: participação histórica oficial aplicada à escala e capacidade de cada canal; "
        f"base histórica de {meta['history_days']} dias. "
        f"Filtros ativos: {filter_text}."
    )
    disclaimer = (
        "A projeção é indicativa e deve ser acompanhada pela execução diária e pelos relatórios "
        "oficiais de confirmação BRFlow."
    )
    footnote_wrap = Table(
        [[report_footnote(methodology)], [report_footnote(disclaimer)]],
        colWidths=[CONTENT_W],
    )
    footnote_wrap.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.extend([Spacer(1, GAP_BLOCK), footnote_wrap])

    doc.build(story)
    buffer.seek(0)
    filename = f"analise-executiva-projecao-d1-{meta['horizon_start']}-{meta['horizon_end']}.pdf"
    return filename, buffer.getvalue()
