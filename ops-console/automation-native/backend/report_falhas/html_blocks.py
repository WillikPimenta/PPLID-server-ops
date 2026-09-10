# report_falhas/html_blocks.py
# -*- coding: utf-8 -*-

"""
Blocos HTML Outlook-safe para o Relatório de Falhas Críticas.

Regras:
- Sem CSS externo
- Sem flex/grid
- Sem position
- Sem pseudo-elementos
- Testado para Outlook Classic e New Outlook (.eml)
"""

from html import escape as _esc
import re
import unicodedata

from report_falhas.io.data_loader import safe_str

# ============================================================
# CONFIG VISUAL (mantém compatibilidade Outlook)
# ============================================================

CONTAINER_MAX_WIDTH_PX = 640
OUTLOOK_WIDTH_CM = 18.57  # largura efetiva no Outlook Desktop

FONT_FAMILY = "Segoe UI, Arial, sans-serif"

COLOR_BG = "#f6f7fb"
COLOR_CARD = "#ffffff"
COLOR_LINE = "#e5e7eb"
COLOR_MUTED = "#64748b"
COLOR_TEXT = "#0f172a"

COLOR_RED = "#dc2626"
COLOR_GREEN = "#15803d"


# ============================================================
# BASE
# ============================================================

def _html_container(inner_html: str) -> str:
    """Container principal Outlook-safe."""
    return f"""
    <html>
    <body style="
        margin:0;
        padding:16px;
        background:{COLOR_BG};
        font-family:{FONT_FAMILY};
        color:{COLOR_TEXT};
    ">
      <div style="
        background:{COLOR_CARD};
        border:1px solid {COLOR_LINE};
        border-radius:12px;
        padding:16px;
        max-width:{CONTAINER_MAX_WIDTH_PX}px;
        width:{OUTLOOK_WIDTH_CM}cm;
        margin:0 auto;
        box-shadow:0 1px 6px rgba(0,0,0,0.06);
      ">
        {inner_html}
      </div>
    </body>
    </html>
    """


def hr() -> str:
    return "<hr style='border:none;border-top:1px solid #edf2f7;margin:16px 0'>"


# ============================================================
# TÍTULO / HEADER
# ============================================================

def title_block(title: str, subtitle: str = "") -> str:
    sub = (
        f"<p style='margin:4px 0 12px;color:{COLOR_MUTED}'>{safe_str(subtitle)}</p>"
        if subtitle else ""
    )
    return f"""
    <h2 style="margin:0;font-size:18px">{safe_str(title)}</h2>
    {sub}
    """


# ============================================================
# KPIs
# ============================================================

def kpi_tile(title: str, value: str | int, sub: str | None = None) -> str:
    sub_html = (
        f"<div style='font-size:11px;color:{COLOR_MUTED}'>{safe_str(sub)}</div>"
        if sub else ""
    )

    return f"""
    <td style="padding:8px 10px;vertical-align:top">
      <div style="
        border:1px solid {COLOR_LINE};
        border-radius:12px;
        padding:12px;
        background:#fafafa;
      ">
        <div style="font-size:11px;color:{COLOR_MUTED};font-weight:700">
          {safe_str(title)}
        </div>
        <div style="font-size:20px;font-weight:900;margin:4px 0">
          {safe_str(value)}
        </div>
        {sub_html}
      </div>
    </td>
    """


def kpi_row(tiles: list[str]) -> str:
    if not tiles:
        return ""
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
      <tr>
        {''.join(tiles)}
      </tr>
    </table>
    """


def table_row_cols(cols: list[str], gray_container: bool = True) -> str:
  if not cols:
    return ""
  bg = 'background:#F6F7F9;border:1px solid #e9ecef;border-radius:12px;' if gray_container else ''
  w = f"{100.0/len(cols):.2f}%"
  tds = "\n".join([f"<td valign='top' width='{w}' style='padding:6px;'>{c}</td>" for c in cols])
  return (
    "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
    f"style='{bg} margin:8px 0 14px; width:100%; table-layout:fixed; border-collapse:collapse;'>"
    "<tr><td style='padding:12px;'>"
    "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
    "style='width:100%; table-layout:fixed; border-collapse:collapse;'>"
    f"<tr>{tds}</tr>"
    "</table>"
    "</td></tr>"
    "</table>"
  )


# ============================================================
# TABELA – REINCIDÊNCIA
# ============================================================

def reincidencia_table(df) -> str:
    if df is None or df.empty:
        return "<p style='color:#6b7280'>Sem dados no período.</p>"

    def _clean_matricula(raw) -> str:
        s = safe_str(raw).strip()
        if not s:
            return ""
        s = s.replace("\u00A0", " ")
        try:
            s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
        except Exception:
            pass
        m = re.search(r"Nome\s+(?:do\s+agente|Agente)?\b|\bNome\b", s, flags=re.IGNORECASE)
        if m:
            s = s[:m.start()].strip()
        mm = re.search(r"([A-Za-z]?\d{3,}[A-Za-z]?)", s)
        return safe_str(mm.group(1)) if mm else s

    def _extract_nome(raw) -> str:
        s = safe_str(raw).strip()
        if not s:
            return ""
        m = re.search(r"Nome\s*(?:do\s*agente|Agente)?\s*[:\-]?\s*(.+)$", s, flags=re.IGNORECASE)
        return safe_str(m.group(1)) if m else ""

    rows_html = []

    for _, r in df.iterrows():
        reinc = safe_str(r.get("Reincidente"))
        if reinc.lower() == "sim":
            color = COLOR_RED
            weight = "font-weight:900;"
        else:
            color = COLOR_GREEN
            weight = "font-weight:700;"

        mat_raw = safe_str(r.get("Matrícula Agente"))
        mat_disp = _clean_matricula(mat_raw) or "—"
        nome_agente = safe_str(r.get("Nome do agente")) or safe_str(r.get("Nome Agente")) or _extract_nome(mat_raw)
        mat_html = _esc(mat_disp)
        nome_html = _esc(nome_agente) if nome_agente else "—"

        rows_html.append(f"""
        <tr>
          <td>{mat_html}</td>
          <td>{nome_html}</td>
          <td style="{weight}color:{color}">
            {reinc}
          </td>
          <td align="center">{safe_str(r.get("Ocorrências Mês Atual"))}</td>
          <td>{safe_str(r.get("Cenário (Mês Atual)"))}</td>
          <td>{safe_str(r.get("Protocolos (Mês Atual)"))}</td>
        </tr>
        """)

    return f"""
    <table width="100%" cellpadding="6" cellspacing="0"
           style="border-collapse:collapse;font-size:12px">
      <thead>
        <tr style="background:#f8fafc">
          <th align="left">Matrícula</th>
          <th align="left">Nome do agente</th>
          <th align="left">Reincidente</th>
          <th align="center">Ocorr.</th>
          <th align="left">Cenário</th>
          <th align="left">Protocolos</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
      </tbody>
    </table>
    """


# ============================================================
# GRÁFICOS (BASE64)
# ============================================================

def chart_block(title: str, base64_png: str) -> str:
    if not base64_png:
        return ""
    return f"""
    <h3 style="font-size:14px;margin:16px 0 8px 0">
      {safe_str(title)}
    </h3>
    <img
      src="data:image/png;base64,{base64_png}"
      style="max-width:100%;border:1px solid {COLOR_LINE};border-radius:10px"
      alt="{safe_str(title)}"
    >
    """


# ============================================================
# BUILDER FINAL
# ============================================================

def format_html(
    *,
    kpis: dict,
    reincidencia_df,
    charts: dict | None = None,
    context=None,
) -> str:
    """
    Builder final do HTML.

    Espera:
    - kpis: dict retornado por make_kpis
    - reincidencia_df: DataFrame de reincidência
    - charts: dict opcional { titulo: base64_png }
    """

    blocks: list[str] = []

    # Header
    blocks.append(
        title_block(
            "Relatório de Falhas Críticas",
            f"Período: {safe_str(kpis.get('periodo_mtd'))}"
        )
    )

    blocks.append(hr())

    # KPIs
    blocks.append(
        kpi_row([
            kpi_tile("Falhas (MTD)", kpis.get("fail_mtd_atual")),
            kpi_tile("Período anterior", kpis.get("fail_prev_equal")),
            kpi_tile("Variação", kpis.get("variacao_perc"), kpis.get("variacao_delta")),
            kpi_tile("Reincidentes", kpis.get("reinc_total")),
        ])
    )

    blocks.append(hr())

    # Reincidência
    blocks.append(
        "<h3 style='font-size:14px;margin:0 0 8px 0'>Reincidência (mês atual)</h3>"
    )
    blocks.append(reincidencia_table(reincidencia_df))

    # Gráficos
    if charts:
        for title, b64 in charts.items():
            blocks.append(hr())
            blocks.append(chart_block(title, b64))

    return _html_container("\n".join(blocks))