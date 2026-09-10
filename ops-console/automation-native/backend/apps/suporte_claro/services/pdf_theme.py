# -*- coding: utf-8 -*-
"""Design system e primitivas de layout - relatorio PDF Suporte Claro."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Flowable, Image, Paragraph, Spacer, Table, TableStyle

from apps.suporte_claro.services.pdf_assets import prepare_logo_for_pdf, resolve_serasa_logo_path
from apps.suporte_claro.services.pdf_fonts import PDF_FONT, PDF_FONT_BOLD, ensure_pdf_fonts, xml_escape

# ── Paleta ─────────────────────────────────────────────────────────────────
CLARO_RED = "#E30613"
CLARO_RED_DARK = "#B8050F"
CLARO_BLACK = "#1A1A1A"
CLARO_GRAY = "#6B7280"
CLARO_GRAY_LIGHT = "#F3F4F6"
CLARO_BORDER = "#E5E7EB"
CLARO_WHITE = "#FFFFFF"
CLARO_BLUE = "#1E40AF"
CLARO_BLUE_LIGHT = "#EFF6FF"
CLARO_SLA_PENDING = "#92400E"
CLARO_ROW_PENDING = "#FFFBEB"

REPORT_TITLE = "Relatório de Suporte ao Cliente"
REPORT_SUBTITLE = "Relatório de acompanhamento de demandas"
SYSTEM_LABEL = "Suporte Claro | Serasa Experian"
CLIENT_LABEL = "Claro"
FOOTER_BRAND = SYSTEM_LABEL

LOGO_MAX_W = 2.9 * cm
LOGO_MAX_H = 1.05 * cm
HEADER_META_W = 7.0 * cm

# ── Grid da pagina (pt) ────────────────────────────────────────────────────
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_L = 1.75 * cm
MARGIN_R = 1.75 * cm
MARGIN_T = 1.35 * cm
MARGIN_B = 1.85 * cm
CONTENT_W = PAGE_WIDTH - MARGIN_L - MARGIN_R

PAD_PAGE = 14          # padding interno dos paineis
GAP_SECTION = 14       # entre secoes
GAP_BLOCK = 10         # titulo -> conteudo / blocos internos
INNER_W = CONTENT_W - 2 * PAD_PAGE

DEMANDA_TABLE_COL_WIDTHS = [
    INNER_W * 0.12,
    INNER_W * 0.17,
    INNER_W * 0.10,
    INNER_W * 0.13,
    INNER_W * 0.11,
    INNER_W * 0.37,
]
TABLE_CELL_HPAD = 16

REPORT_FOOTNOTE = (
    "Relatório gerado automaticamente com base nos registros do período selecionado. "
    "Demandas pendentes permanecem em acompanhamento."
)

KPI_COLS = 4
KPI_GAP = 6
KPI_CARD_W = (INNER_W - KPI_GAP * (KPI_COLS - 1)) / KPI_COLS

CHART_GAP = 8
CHART_COL_W = (INNER_W - CHART_GAP) / 2
CHART_MAX_H = 6.5 * cm

STATUS_THEME = {
    "aberto": {"bar": "#64748B", "badge_bg": "#F1F5F9", "badge_fg": "#334155"},
    "em_atendimento": {"bar": "#F59E0B", "badge_bg": "#FFFBEB", "badge_fg": "#92400E"},
    "concluido": {"bar": "#10B981", "badge_bg": "#ECFDF5", "badge_fg": "#065F46"},
}
ORIGEM_BAR = {"teams": CLARO_RED, "email": "#374151", "ligacao": "#6B7280", "": "#D1D5DB"}

C = {
    "red": colors.HexColor(CLARO_RED),
    "red_dark": colors.HexColor(CLARO_RED_DARK),
    "black": colors.HexColor(CLARO_BLACK),
    "gray": colors.HexColor(CLARO_GRAY),
    "gray_light": colors.HexColor(CLARO_GRAY_LIGHT),
    "blue": colors.HexColor(CLARO_BLUE),
    "blue_light": colors.HexColor(CLARO_BLUE_LIGHT),
    "border": colors.HexColor(CLARO_BORDER),
    "white": colors.white,
}


def build_styles() -> dict[str, ParagraphStyle]:
    ensure_pdf_fonts()
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "brand", parent=base["Normal"], fontName=PDF_FONT_BOLD,
            fontSize=8, textColor=C["red"], leading=10, spaceAfter=4,
        ),
        "doc_title": ParagraphStyle(
            "doc_title", parent=base["Title"], fontName=PDF_FONT_BOLD,
            fontSize=14, textColor=C["black"], leading=17, spaceAfter=3,
        ),
        "doc_sub": ParagraphStyle(
            "doc_sub", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=9, textColor=C["gray"], leading=12,
        ),
        "meta_label": ParagraphStyle(
            "meta_label", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=7, textColor=C["gray"], leading=9,
        ),
        "meta_value": ParagraphStyle(
            "meta_value", parent=base["Normal"], fontName=PDF_FONT_BOLD,
            fontSize=8.5, textColor=C["black"], leading=11,
        ),
        "section": ParagraphStyle(
            "section", parent=base["Heading2"], fontName=PDF_FONT_BOLD,
            fontSize=11, textColor=C["black"], leading=13, spaceAfter=2,
        ),
        "section_sub": ParagraphStyle(
            "section_sub", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=8, textColor=C["gray"], leading=11, spaceAfter=0,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=9, textColor=C["black"], leading=14,
        ),
        "story": ParagraphStyle(
            "story", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=9.5, textColor=C["black"], leading=15, spaceAfter=10,
        ),
        "kpi_label": ParagraphStyle(
            "kpi_label", parent=base["Normal"], fontName=PDF_FONT_BOLD,
            fontSize=6.5, textColor=C["gray"], leading=8, spaceAfter=6,
        ),
        "kpi_value": ParagraphStyle(
            "kpi_value", parent=base["Normal"], fontName=PDF_FONT_BOLD,
            fontSize=18, textColor=C["black"], leading=21, spaceAfter=3,
        ),
        "kpi_hint": ParagraphStyle(
            "kpi_hint", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=7, textColor=C["gray"], leading=9,
        ),
        "kpi_pct": ParagraphStyle(
            "kpi_pct", parent=base["Normal"], fontName=PDF_FONT_BOLD,
            fontSize=8, textColor=C["blue"], leading=10, spaceAfter=2,
        ),
        "chart_title": ParagraphStyle(
            "chart_title", parent=base["Normal"], fontName=PDF_FONT_BOLD,
            fontSize=8.5, textColor=C["black"], leading=11,
        ),
        "chart_sub": ParagraphStyle(
            "chart_sub", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=7.5, textColor=C["gray"], leading=10, spaceAfter=4,
        ),
        "th": ParagraphStyle(
            "th", parent=base["Normal"], fontName=PDF_FONT_BOLD,
            fontSize=7.5, textColor=C["black"], alignment=TA_LEFT,
        ),
        "td": ParagraphStyle(
            "td", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=7.5, textColor=C["black"], alignment=TA_LEFT, leading=10,
        ),
        "td_muted": ParagraphStyle(
            "td_muted", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=7.5, textColor=C["gray"], alignment=TA_LEFT, leading=10,
        ),
        "td_num": ParagraphStyle(
            "td_num", parent=base["Normal"], fontName=PDF_FONT_BOLD,
            fontSize=7.5, textColor=C["black"], alignment=TA_CENTER,
        ),
        "badge": ParagraphStyle(
            "badge", parent=base["Normal"], fontName=PDF_FONT_BOLD,
            fontSize=6.5, alignment=TA_CENTER, leading=8,
        ),
        "footnote": ParagraphStyle(
            "footnote", parent=base["Normal"], fontName=PDF_FONT,
            fontSize=7.5, textColor=C["gray"], leading=10, spaceBefore=8,
        ),
    }


# ── Primitivas de layout ───────────────────────────────────────────────────

def _fit_logo(path: Path) -> Image | None:
    try:
        prepared = prepare_logo_for_pdf(path)
        img = Image(str(prepared), mask="auto")
    except Exception:
        return None
    iw = float(img.imageWidth or 1)
    ih = float(img.imageHeight or 1)
    ratio = ih / iw
    width = LOGO_MAX_W
    height = width * ratio
    if height > LOGO_MAX_H:
        height = LOGO_MAX_H
        width = height / ratio
    img.drawWidth = width
    img.drawHeight = height
    return img


def report_header(
    period_text: str,
    generated_at: str,
    generated_by: str,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    period_label: str = "Período",
    client_label: str = CLIENT_LABEL,
    system_label: str = SYSTEM_LABEL,
    show_client: bool = True,
    show_generated_at: bool = True,
    show_responsible: bool = True,
    meta_width: float | None = None,
    meta_valign: str = "TOP",
    brand_color: colors.Color | None = None,
    accent_color: colors.Color | None = None,
    header_background: colors.Color | None = None,
    meta_background: colors.Color | None = None,
    meta_border: colors.Color | None = None,
    compact: bool = False,
    title_alignment: int | None = None,
) -> Table:
    """Faixa superior: logo + titulo a esquerda, metadados a direita."""
    st = build_styles()
    meta_w = meta_width or HEADER_META_W
    left_w = INNER_W - meta_w
    logo_w = LOGO_MAX_W + 0.25 * cm
    title_w = max(left_w - logo_w, 4.5 * cm)

    header_brand_style = ParagraphStyle(
        "header_brand",
        parent=st["brand"],
        textColor=brand_color or C["red"],
    )
    header_title_style = ParagraphStyle(
        "header_title",
        parent=st["doc_title"],
        alignment=st["doc_title"].alignment if title_alignment is None else title_alignment,
    )
    title_block = Table([
        [Paragraph(xml_escape(system_label), header_brand_style)],
        [Paragraph(title or REPORT_TITLE, header_title_style)],
        [Paragraph(subtitle or REPORT_SUBTITLE, st["doc_sub"])],
    ], colWidths=[title_w])
    title_block.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))

    logo_path = resolve_serasa_logo_path()
    logo = _fit_logo(logo_path) if logo_path else None
    if logo:
        left = Table([[logo, title_block]], colWidths=[logo_w, title_w])
        left.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
    else:
        left = title_block

    meta_rows = []
    if show_client:
        meta_rows.append([Paragraph("Cliente", st["meta_label"]), Paragraph(xml_escape(client_label), st["meta_value"])])
    meta_rows.append([Paragraph(period_label, st["meta_label"]), Paragraph(period_text, st["meta_value"])])
    if show_generated_at:
        meta_rows.append([Paragraph("Gerado em", st["meta_label"]), Paragraph(generated_at, st["meta_value"])])
    if show_responsible:
        meta_rows.append([Paragraph("Responsável", st["meta_label"]), Paragraph(xml_escape(generated_by), st["meta_value"])])
    meta = Table(meta_rows, colWidths=[meta_w * 0.36, meta_w * 0.64])
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), meta_valign),
        ("TOPPADDING", (0, 0), (-1, -1), 6 if meta_background else 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6 if meta_background else 2),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (0, -1), 8 if meta_background else 0),
        ("RIGHTPADDING", (1, 0), (1, -1), 8 if meta_background else 0),
        *(([("BACKGROUND", (0, 0), (-1, -1), meta_background)] if meta_background else [])),
        *(([("BOX", (0, 0), (-1, -1), 0.6, meta_border or C["border"])] if meta_background else [])),
    ]))

    row = Table([[left, meta]], colWidths=[left_w, meta_w])
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), meta_valign),
        ("LEFTPADDING", (0, 0), (-1, -1), PAD_PAGE),
        ("RIGHTPADDING", (0, 0), (-1, -1), PAD_PAGE),
        ("TOPPADDING", (0, 0), (-1, -1), 8 if compact else 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8 if compact else 12),
    ]))

    shell = Table([[row]], colWidths=[CONTENT_W])
    shell.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, C["border"]),
        ("LINEBELOW", (0, 0), (-1, -1), 2.5, accent_color or C["red"]),
        ("BACKGROUND", (0, 0), (-1, -1), header_background or C["white"]),
    ]))
    return shell


def _section_title_block(title: str, subtitle: str = "") -> Table:
    st = build_styles()
    header_rows = [[Paragraph(title, st["section"])]]
    if subtitle:
        header_rows.append([Paragraph(subtitle, st["section_sub"])])
    return Table(header_rows, colWidths=[INNER_W])


def section_header(title: str, subtitle: str = "") -> Table:
    """Cabecalho de secao isolado (nao quebra do corpo em paginas distintas)."""
    header = _section_title_block(title, subtitle)
    shell = Table([[header]], colWidths=[CONTENT_W])
    shell.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), PAD_PAGE),
        ("RIGHTPADDING", (0, 0), (-1, -1), PAD_PAGE),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("BACKGROUND", (0, 0), (-1, -1), C["gray_light"]),
        ("BOX", (0, 0), (-1, -1), 0.75, C["border"]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, C["border"]),
    ]))
    return shell


def section_table_body(table: Table) -> Table:
    """Corpo de secao com padding alinhado ao cabecalho."""
    body = Table([[table]], colWidths=[CONTENT_W])
    body.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), PAD_PAGE),
        ("RIGHTPADDING", (0, 0), (-1, -1), PAD_PAGE),
        ("TOPPADDING", (0, 0), (-1, -1), GAP_BLOCK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("BOX", (0, 0), (-1, -1), 0.75, C["border"]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.75, C["border"]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return body


def section_panel(title: str, subtitle: str, content: list[Flowable]) -> Table:
    """Secao unificada: cabecalho + corpo dentro do mesmo painel."""
    header = _section_title_block(title, subtitle)

    body_rows = [[item] for item in content]
    body = Table(body_rows, colWidths=[INNER_W])
    body.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    panel = Table([[header], [body]], colWidths=[CONTENT_W])
    panel.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, C["border"]),
        ("BACKGROUND", (0, 0), (-1, 0), C["gray_light"]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, C["border"]),
        ("LEFTPADDING", (0, 0), (-1, -1), PAD_PAGE),
        ("RIGHTPADDING", (0, 0), (-1, -1), PAD_PAGE),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 1), (-1, 1), GAP_BLOCK),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return panel


def kpi_grid(cards: list[Table]) -> Table:
    """Grid uniforme de cards KPI dentro da secao."""
    rows = []
    for i in range(0, len(cards), KPI_COLS):
        chunk = list(cards[i : i + KPI_COLS])
        while len(chunk) < KPI_COLS:
            chunk.append(Spacer(KPI_CARD_W, 1))
        rows.append(chunk)

    grid = Table(rows, colWidths=[KPI_CARD_W] * KPI_COLS, hAlign="LEFT")
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-2, -1), KPI_GAP),
        ("BOTTOMPADDING", (0, 0), (-1, -2), KPI_GAP),
    ]))
    return grid


def kpi_card(label: str, value: str, hint: str, pct: str, accent: colors.Color) -> Table:
    st = build_styles()
    rows = [
        [Paragraph(label.upper(), st["kpi_label"])],
        [Paragraph(value, st["kpi_value"])],
    ]
    if pct:
        rows.append([Paragraph(xml_escape(pct), st["kpi_pct"])])
    if hint:
        rows.append([Paragraph(xml_escape(hint), st["kpi_hint"])])
    card = Table(rows, colWidths=[KPI_CARD_W - 2])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C["white"]),
        ("BOX", (0, 0), (-1, -1), 0.5, C["border"]),
        ("LINEABOVE", (0, 0), (-1, 0), 2.5, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("MINROWHEIGHT", (0, 0), (-1, -1), 58),
    ]))
    return card


def narrative_block(paragraphs: list[str], *, accent: colors.Color | None = None) -> Table:
    """Bloco de storytelling — paragrafos corridos com barra lateral azul."""
    st = build_styles()
    rows = [[Paragraph(xml_escape(text), st["story"])] for text in paragraphs]
    tbl = Table(rows, colWidths=[INNER_W])
    tbl.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, accent or C["blue"]),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tbl


def attention_list(items: list[str]) -> Table:
    """Lista com barra lateral vermelha — sem tabelas aninhadas estreitas."""
    st = build_styles()
    att_style = ParagraphStyle(
        "att_item",
        parent=st["body"],
        fontName=PDF_FONT,
        fontSize=8.5,
        leading=13,
        leftIndent=0,
    )
    rows = [[Paragraph(text, att_style)] for text in items]
    tbl = Table(rows, colWidths=[INNER_W])
    tbl.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, C["red"]),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tbl


def chart_cell(title: str, subtitle: str, image: Flowable, width: float | None = None) -> Table:
    st = build_styles()
    w = width or CHART_COL_W
    return Table([
        [Paragraph(title, st["chart_title"])],
        [Paragraph(subtitle, st["chart_sub"])],
        [image],
    ], colWidths=[w])


def charts_row(cells: list[Table]) -> Table:
    while len(cells) < 2:
        cells.append(Spacer(CHART_COL_W, 1))
    row = Table([cells[:2]], colWidths=[CHART_COL_W, CHART_COL_W], hAlign="LEFT")
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("RIGHTPADDING", (0, 0), (0, -1), CHART_GAP),
    ]))
    return row


def data_table(
    header_cells: list,
    rows: list,
    col_widths: list | None = None,
    *,
    highlight_rows: set[int] | None = None,
    accent: colors.Color | None = None,
) -> Table:
    widths = col_widths or [INNER_W * 0.55, INNER_W * 0.22, INNER_W * 0.23]
    table = Table([header_cells, *rows], colWidths=widths, hAlign="LEFT", repeatRows=1)
    styles = [
        ("BACKGROUND", (0, 0), (-1, 0), C["gray_light"]),
        ("LINEBELOW", (0, 0), (-1, 0), 1, accent or C["red"]),
        ("FONTNAME", (0, 0), (-1, 0), PDF_FONT_BOLD),
        ("GRID", (0, 0), (-1, -1), 0.25, C["border"]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    pending_bg = colors.HexColor(CLARO_ROW_PENDING)
    if highlight_rows:
        for row_idx in range(len(rows)):
            bg = pending_bg if row_idx in highlight_rows else (
                C["white"] if row_idx % 2 == 0 else colors.HexColor(CLARO_GRAY_LIGHT)
            )
            styles.append(("BACKGROUND", (0, row_idx + 1), (-1, row_idx + 1), bg))
    else:
        styles.append(
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C["white"], colors.HexColor(CLARO_GRAY_LIGHT)])
        )
    table.setStyle(TableStyle(styles))
    return table


def status_badge(status_key: str, label: str, *, col_width: float | None = None) -> Table:
    theme = STATUS_THEME.get(status_key, STATUS_THEME["aberto"])
    st = build_styles()
    cell_w = col_width if col_width is not None else DEMANDA_TABLE_COL_WIDTHS[1]
    badge_w = max(cell_w - TABLE_CELL_HPAD, 36)
    p = Paragraph(xml_escape(label), ParagraphStyle(
        "badge_dyn", parent=st["badge"], textColor=colors.HexColor(theme["badge_fg"]),
    ))
    t = Table([[p]], colWidths=[badge_w])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(theme["badge_bg"])),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(theme["bar"])),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def th(text: str) -> Paragraph:
    return Paragraph(xml_escape(text), build_styles()["th"])


def td(text: str, *, muted: bool = False) -> Paragraph:
    return Paragraph(xml_escape(text), build_styles()["td_muted" if muted else "td"])


def td_num(text: str) -> Paragraph:
    return Paragraph(xml_escape(text), build_styles()["td_num"])


def td_sla(text: str, *, pending: bool = False) -> Paragraph:
    st = build_styles()
    style = ParagraphStyle(
        "sla_cell",
        parent=st["td_num"],
        textColor=colors.HexColor(CLARO_SLA_PENDING) if pending else st["td_num"].textColor,
    )
    return Paragraph(xml_escape(text), style)


def report_footnote(text: str) -> Paragraph:
    return Paragraph(xml_escape(text), build_styles()["footnote"])
