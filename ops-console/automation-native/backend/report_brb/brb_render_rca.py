# -*- coding: utf-8 -*-
"""Geração do RCA (Root Cause Analysis) em PDF para o Quality Overview."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from apps.suporte_claro.services.pdf_assets import (
    prepare_logo_for_pdf,
    resolve_serasa_logo_path,
)
from apps.suporte_claro.services.pdf_fonts import (
    PDF_FONT,
    PDF_FONT_BOLD,
    ensure_pdf_fonts,
    xml_escape,
)
from report_brb.brb_loaders import BRBDataBundle
from report_brb.brb_metrics import BRBMetrics
from report_brb.brb_render_cliente import _group_procedentes


C = {
    "navy": colors.HexColor("#102B5B"),
    "blue": colors.HexColor("#265EE8"),
    "blue_soft": colors.HexColor("#EDF3FF"),
    "green": colors.HexColor("#168565"),
    "green_soft": colors.HexColor("#EAF7F2"),
    "amber": colors.HexColor("#B56B13"),
    "amber_soft": colors.HexColor("#FFF6E8"),
    "ink": colors.HexColor("#12213F"),
    "muted": colors.HexColor("#64748B"),
    "border": colors.HexColor("#DCE5F0"),
    "surface": colors.HexColor("#F7F9FC"),
    "white": colors.white,
}

PAGE_W, PAGE_H = A4
MARGIN_L = 16 * mm
MARGIN_R = 16 * mm
MARGIN_T = 14 * mm
MARGIN_B = 17 * mm
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R


def _fmt(n: int | float, decimals: int = 1) -> str:
    return f"{n:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_int(n: int | float) -> str:
    return f"{int(n):,}".replace(",", ".")


def _top_causes(bundle: BRBDataBundle, limit: int = 3) -> list[tuple[str, int]]:
    grouped = _group_procedentes(bundle.contestacao)
    if grouped.empty:
        return []
    count_col = "Quantidade" if "Quantidade" in grouped.columns else "Casos"
    return [
        (str(row["Motivo"]), int(row[count_col]))
        for _, row in grouped.head(limit).iterrows()
    ]


def _p(text: str, style: dict | None = None) -> Paragraph:
    base = {
        "fontName": PDF_FONT,
        "fontSize": 8.5,
        "leading": 12,
        "textColor": C["ink"],
        "spaceAfter": 0,
    }
    if style:
        base.update(style)
    return Paragraph(text, ParagraphStyle("RcaText", **base))


def _section_title(number: str, title: str) -> Table:
    number_cell = _p(
        xml_escape(number),
        {
            "fontName": PDF_FONT_BOLD,
            "fontSize": 8,
            "leading": 10,
            "textColor": C["white"],
            "alignment": TA_CENTER,
        },
    )
    title_cell = _p(
        xml_escape(title),
        {
            "fontName": PDF_FONT_BOLD,
            "fontSize": 11.5,
            "leading": 14,
            "textColor": C["navy"],
        },
    )
    table = Table([[number_cell, title_cell]], colWidths=[9 * mm, CONTENT_W - 9 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), C["blue"]),
        ("BACKGROUND", (1, 0), (1, 0), C["white"]),
        ("BOX", (0, 0), (-1, -1), 0.7, C["border"]),
        ("LINEAFTER", (0, 0), (0, 0), 0.7, C["border"]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _logo() -> Image | None:
    source = resolve_serasa_logo_path()
    if not source:
        return None
    prepared = prepare_logo_for_pdf(source)
    logo = Image(str(prepared))
    max_w, max_h = 39 * mm, 12 * mm
    scale = min(max_w / logo.imageWidth, max_h / logo.imageHeight)
    logo.drawWidth = logo.imageWidth * scale
    logo.drawHeight = logo.imageHeight * scale
    return logo


def _header(client_name: str, periodo_label: str) -> Table:
    logo = _logo() or _p("SERASA EXPERIAN", {"fontName": PDF_FONT_BOLD, "textColor": C["navy"]})
    heading = [
        _p("QUALITY OVERVIEW", {"fontName": PDF_FONT_BOLD, "fontSize": 7.5, "leading": 9, "textColor": C["blue"]}),
        _p("Root Cause Analysis", {"fontName": PDF_FONT_BOLD, "fontSize": 19, "leading": 22, "textColor": C["navy"]}),
        _p("Análise estruturada para validação conjunta", {"fontSize": 8, "leading": 10, "textColor": C["muted"]}),
    ]
    meta = [
        _p("CLIENTE", {"fontName": PDF_FONT_BOLD, "fontSize": 6.5, "leading": 8, "textColor": C["muted"]}),
        _p(xml_escape(client_name), {"fontName": PDF_FONT_BOLD, "fontSize": 9, "leading": 11, "textColor": C["navy"]}),
        Spacer(1, 1.5 * mm),
        _p("PERÍODO", {"fontName": PDF_FONT_BOLD, "fontSize": 6.5, "leading": 8, "textColor": C["muted"]}),
        _p(xml_escape(periodo_label or "Não informado"), {"fontName": PDF_FONT_BOLD, "fontSize": 8, "leading": 10, "textColor": C["ink"]}),
    ]
    table = Table([[logo, heading, meta]], colWidths=[43 * mm, 79 * mm, CONTENT_W - 122 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C["white"]),
        ("BOX", (0, 0), (-1, -1), 0.8, C["border"]),
        ("LINEBEFORE", (2, 0), (2, 0), 0.8, C["border"]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return table


def _kpi_card(label: str, value: str, detail: str, accent) -> Table:
    card = Table([[
        _p(xml_escape(label.upper()), {"fontName": PDF_FONT_BOLD, "fontSize": 6.5, "leading": 8, "textColor": C["muted"]}),
    ], [
        _p(xml_escape(value), {"fontName": PDF_FONT_BOLD, "fontSize": 17, "leading": 19, "textColor": C["navy"]}),
    ], [
        _p(xml_escape(detail), {"fontSize": 6.8, "leading": 8.5, "textColor": C["muted"]}),
    ]], colWidths=[(CONTENT_W - 9 * mm) / 4])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C["white"]),
        ("BOX", (0, 0), (-1, -1), 0.7, C["border"]),
        ("LINEABOVE", (0, 0), (-1, 0), 2.2, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 7),
        ("BOTTOMPADDING", (0, 0), (0, 0), 1),
        ("TOPPADDING", (0, 1), (0, 1), 1),
        ("BOTTOMPADDING", (0, 1), (0, 1), 1),
        ("TOPPADDING", (0, 2), (0, 2), 1),
        ("BOTTOMPADDING", (0, 2), (0, 2), 7),
    ]))
    return card


def _data_table(rows: list[list[object]], widths: list[float]) -> Table:
    wrapped = []
    for row_index, row in enumerate(rows):
        wrapped.append([
            cell if hasattr(cell, "wrap") else _p(
                xml_escape(str(cell)),
                {
                    "fontName": PDF_FONT_BOLD if row_index == 0 else PDF_FONT,
                    "fontSize": 7.4 if row_index == 0 else 7.7,
                    "leading": 9.5 if row_index == 0 else 10.5,
                    "textColor": C["navy"] if row_index == 0 else C["ink"],
                },
            )
            for cell in row
        ])
    table = Table(wrapped, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C["blue_soft"]),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C["white"], C["surface"]]),
        ("BOX", (0, 0), (-1, -1), 0.7, C["border"]),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, C["border"]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


class _RcaDoc(SimpleDocTemplate):
    def __init__(self, filename: str, *, client_name: str, periodo_label: str, **kwargs):
        self.client_name = client_name
        self.periodo_label = periodo_label
        self.generated_at = datetime.now().strftime("%d/%m/%Y às %H:%M")
        super().__init__(filename, **kwargs)

    def build(self, flowables, **kwargs):
        return super().build(flowables, onFirstPage=self._footer, onLaterPages=self._footer, **kwargs)

    def _footer(self, canvas, doc):
        canvas.saveState()
        y = MARGIN_B - 5.2 * mm
        canvas.setStrokeColor(C["border"])
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN_L, y + 3.2 * mm, PAGE_W - MARGIN_R, y + 3.2 * mm)
        canvas.setFont(PDF_FONT, 6.7)
        canvas.setFillColor(C["muted"])
        canvas.drawString(MARGIN_L, y, f"Quality Overview | RCA | {self.client_name}")
        canvas.drawCentredString(PAGE_W / 2, y, f"Gerado em {self.generated_at}")
        canvas.drawRightString(PAGE_W - MARGIN_R, y, f"Página {doc.page}")
        canvas.restoreState()


def render_rca_pdf(
    bundle: BRBDataBundle,
    metrics: BRBMetrics,
    out_path: Path | str,
    *,
    client_name: str = "Cliente",
    periodo_label: str = "",
) -> Path:
    """Cria um RCA executivo, sem afirmar causalidade antes da validação."""
    ensure_pdf_fonts()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    top_causes = _top_causes(bundle)
    causa, causa_n = top_causes[0] if top_causes else ("Não identificada", 0)
    total = int(metrics.conforme_nao + metrics.conforme_sim)
    confirmadas = int(metrics.conforme_nao)
    taxa = float(metrics.pct_procedente or 0)
    causa_share = 100 * causa_n / max(confirmadas, 1)

    doc = _RcaDoc(
        str(out),
        client_name=client_name,
        periodo_label=periodo_label,
        pagesize=A4,
        rightMargin=MARGIN_R,
        leftMargin=MARGIN_L,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=f"RCA - Quality Overview - {client_name}",
        author="Serasa Experian",
        subject="Root Cause Analysis",
    )

    summary_text = (
        f"No período analisado, <b>{_fmt_int(confirmadas)} falhas foram confirmadas</b> em "
        f"<b>{_fmt_int(total)} contestações avaliadas</b>, resultando em taxa de procedência de "
        f"<b>{_fmt(taxa)}%</b>. O maior agrupamento observado foi <b>{xml_escape(causa)}</b>."
    )
    summary = Table([[
        _p("LEITURA EXECUTIVA", {"fontName": PDF_FONT_BOLD, "fontSize": 7, "leading": 9, "textColor": C["white"]}),
        _p(summary_text, {"fontSize": 9, "leading": 13, "textColor": C["white"]}),
        _p("PARA VALIDAÇÃO", {"fontName": PDF_FONT_BOLD, "fontSize": 7, "leading": 9, "textColor": C["navy"], "alignment": TA_CENTER}),
    ]], colWidths=[31 * mm, CONTENT_W - 63 * mm, 32 * mm])
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (1, 0), C["navy"]),
        ("BACKGROUND", (2, 0), (2, 0), C["green_soft"]),
        ("BOX", (0, 0), (-1, -1), 0.7, C["navy"]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))

    cards = Table([[
        _kpi_card("Contestações", _fmt_int(total), "avaliadas no recorte", C["blue"]),
        _kpi_card("Falhas confirmadas", _fmt_int(confirmadas), "classificadas como procedentes", C["amber"]),
        _kpi_card("Taxa de procedência", f"{_fmt(taxa)}%", "falhas / contestações", C["green"]),
        _kpi_card("Protocolos revisados", _fmt_int(metrics.auditados_casos), "casos distintos na auditoria", C["navy"]),
    ]], colWidths=[CONTENT_W / 4] * 4)
    cards.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1.5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.5 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    evidence_rows = [
        ["Indicador", "Resultado", "Leitura"],
        ["Contestações avaliadas", _fmt_int(total), "Base usada para medir a procedência"],
        ["Falhas confirmadas", _fmt_int(confirmadas), "Casos classificados como falha após análise"],
        ["Taxa de procedência", f"{_fmt(taxa)}%", "Proporção de falhas nas contestações avaliadas"],
        ["Falhas notificadas", _fmt_int(metrics.na_falhas_registros), "Notificações válidas ao CS no recorte"],
        ["Protocolos revisados", _fmt_int(metrics.auditados_casos), "Casos distintos encontrados na auditoria"],
    ]

    cause_rows = [["Prioridade", "Motivo consolidado", "Casos", "% das falhas"]]
    if top_causes:
        for position, (label, count) in enumerate(top_causes, start=1):
            cause_rows.append([f"#{position}", label, _fmt_int(count), f"{_fmt(100 * count / max(confirmadas, 1))}%"])
    else:
        cause_rows.append(["#1", "Não identificada na base", "0", "0,0%"])

    analysis_rows = [
        ["Etapa", "Registro do RCA", "Status"],
        ["Sinal observado", f"{xml_escape(causa)} concentra {_fmt(causa_share)}% das falhas confirmadas.", "Evidenciado"],
        ["Hipótese de causa", "O agrupamento pode indicar recorrência em uma mesma decisão, orientação ou ponto operacional.", "A validar"],
        ["Confirmação necessária", "Revisar uma amostra representativa e identificar em qual etapa o desvio foi introduzido.", "Pendente"],
        ["Critério de fechamento", "Evidência operacional documentada, responsável definido e ação associada à causa confirmada.", "Pendente"],
    ]

    governance = Table([[
        _p("RITO DE ACOMPANHAMENTO", {"fontName": PDF_FONT_BOLD, "fontSize": 7, "leading": 9, "textColor": C["green"]}),
        _p("Validação conjunta da causa, definição dos responsáveis e acompanhamento no próximo fechamento do Quality Overview.", {"fontSize": 8.2, "leading": 11, "textColor": C["ink"]}),
        _p("PRÓXIMA DECISÃO", {"fontName": PDF_FONT_BOLD, "fontSize": 6.5, "leading": 8, "textColor": C["muted"], "alignment": TA_CENTER}),
        _p("Confirmar causa #1", {"fontName": PDF_FONT_BOLD, "fontSize": 8, "leading": 10, "textColor": C["navy"], "alignment": TA_CENTER}),
    ]], colWidths=[34 * mm, CONTENT_W - 76 * mm, 22 * mm, 20 * mm])
    governance.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C["green_soft"]),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#BFE3D4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    note = Table([[
        _p("NOTA METODOLÓGICA", {"fontName": PDF_FONT_BOLD, "fontSize": 6.8, "leading": 8.5, "textColor": C["amber"]}),
        _p("Concentração estatística orienta a investigação, mas não comprova causa-raiz. A conclusão deve ser sustentada pela análise dos casos e das evidências operacionais.", {"fontSize": 7.5, "leading": 10, "textColor": C["ink"]}),
    ]], colWidths=[34 * mm, CONTENT_W - 34 * mm])
    note.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C["amber_soft"]),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#F0D7AF")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))

    story = [
        _header(client_name, periodo_label),
        Spacer(1, 4 * mm),
        summary,
        Spacer(1, 4 * mm),
        cards,
        Spacer(1, 5 * mm),
        _section_title("01", "Definição do problema"),
        Spacer(1, 2.5 * mm),
        _p(
            "Foram identificadas falhas confirmadas nas contestações do período. Este RCA organiza os dados disponíveis, prioriza o maior agrupamento observado e define o que precisa ser validado antes de concluir a causa-raiz.",
            {"fontSize": 8.5, "leading": 12, "textColor": C["ink"]},
        ),
        Spacer(1, 4 * mm),
        _section_title("02", "Evidências do período"),
        Spacer(1, 2.5 * mm),
        _data_table(evidence_rows, [49 * mm, 31 * mm, CONTENT_W - 80 * mm]),
        Spacer(1, 4 * mm),
        KeepTogether([
            _section_title("03", "Causas e cenários prioritários"),
            Spacer(1, 2.5 * mm),
            _data_table(cause_rows, [19 * mm, CONTENT_W - 76 * mm, 27 * mm, 30 * mm]),
        ]),
        PageBreak(),
        _header(client_name, periodo_label),
        Spacer(1, 5 * mm),
        _section_title("04", "Análise de causa"),
        Spacer(1, 2.5 * mm),
        _data_table(analysis_rows, [34 * mm, CONTENT_W - 60 * mm, 26 * mm]),
        Spacer(1, 5 * mm),
        _section_title("05", "Governança e próximos passos"),
        Spacer(1, 2.5 * mm),
        governance,
        Spacer(1, 4 * mm),
        note,
    ]
    doc.build(story)
    return out
