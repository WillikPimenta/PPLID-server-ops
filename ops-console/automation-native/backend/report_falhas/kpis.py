# report_falhas/kpis.py
# -*- coding: utf-8 -*-

"""
KPIs do Relatório de Falhas Críticas.

Responsabilidades:
- Consolidar números do período atual (MTD)
- Comparar com o mesmo número de dias corridos no mês anterior
- Calcular variações (% e delta absoluto)
- Derivar KPIs auxiliares (reincidência, novos no mês, top cenário)

Não:
- Não gera HTML
- Não gera gráficos
- Não conhece DataFrame bruto
"""

from report_falhas.io.data_loader import normalize_text, safe_str
from report_falhas.periods import dias_corridos_periodo


# ============================================================
# Helpers internos
# ============================================================

def _fmt_delta_abs(cur: int, prev: int) -> str:
    """
    Formata delta absoluto (ex.: +3, -2, 0)
    """
    delta = cur - prev
    if delta > 0:
        return f"+{delta}"
    if delta < 0:
        return str(delta)
    return "0"


def _fmt_delta_perc(cur: int, prev: int) -> str:
    """
    Formata delta percentual.
    Regra preservada:
    - prev == 0 e cur > 0 → texto explicativo
    - prev == 0 e cur == 0 → 0%
    """
    if prev and prev != 0:
        perc = round(((cur - prev) / prev) * 100)
        return f"{'+' if perc > 0 else ''}{perc}%"

    if prev == 0 and cur > 0:
        return "— (sem ocorrências no período de comparação)"

    return "0%"


def is_formatacao_fonte(cenario: str) -> bool:
    """Detecta se o cenário pertence ao cluster de Formatação/Fonte.

    Baseado em palavras-chave após normalização (sem acentos).
    Ex.: 'NÃO SINALIZADO - FORMATAÇÃO/FONTE E DESALINHAMENTO ADULTERADA'.
    """
    s = normalize_text(safe_str(cenario))
    if not s:
        return False
    return ('format' in s) or ('fonte' in s) or ('desalinh' in s)


# ============================================================
# KPIs PRINCIPAIS
# ============================================================

def make_kpis(
    cur_start,
    cur_end_mtd,
    total_atual,
    total_prev_equal,
    prev_equal_start,
    prev_equal_end,
    df_reinc_full,
    top3_cenarios,
    novos_mes_list,
):
    """Consolida KPIs — regras idênticas ao fluxo principal (report_falhas_criticas)."""
    perc_txt = "0%"
    delta_txt = f"{('+' if total_atual >= 0 else '')}{total_atual}"
    reinc_total = 0
    top1_nome, top1_qtd = "Sem dados", 0
    if total_prev_equal and total_prev_equal != 0:
        perc = round(((total_atual - total_prev_equal) / total_prev_equal) * 100)
        perc_txt = f"{('+' if perc >= 0 else '')}{perc}%"
        delta_abs = total_atual - total_prev_equal
        delta_txt = f"{('+' if delta_abs >= 0 else '')}{delta_abs}"
    else:
        if total_prev_equal == 0 and total_atual > 0:
            perc_txt = "- (sem ocorrências no período de comparação)"
    if df_reinc_full is not None and not df_reinc_full.empty and "Reincidente" in df_reinc_full.columns:
        reinc_total = int((df_reinc_full["Reincidente"].astype(str).str.strip() == "Sim").sum())
    if top3_cenarios:
        top1_nome, top1_qtd = top3_cenarios[0]
    return {
        "periodo_mtd": (
            f"{cur_start.strftime('%d/%m/%Y')} → {cur_end_mtd.strftime('%d/%m/%Y')}"
            if cur_end_mtd
            else "Sem dados"
        ),
        "fail_mtd_atual": total_atual,
        "fail_prev_equal": total_prev_equal,
        "variacao_perc": perc_txt,
        "variacao_delta": delta_txt,
        "reinc_total": reinc_total,
        "novos_mes_count": len(novos_mes_list) if novos_mes_list else 0,
        "top1_cenario_nome": top1_nome,
        "top1_cenario_qtd": top1_qtd,
        "periodo_equal_prev": (
            f"{prev_equal_start.strftime('%d/%m/%Y')} → {prev_equal_end.strftime('%d/%m/%Y')}"
            if (prev_equal_start and prev_equal_end)
            else "Sem dados"
        ),
        "comparativo_dias_corridos": dias_corridos_periodo(cur_start, cur_end_mtd)
        if (cur_start and cur_end_mtd)
        else 0,
    }
