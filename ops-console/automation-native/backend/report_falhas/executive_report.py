# -*- coding: utf-8 -*-
"""Relatório Executivo (gerência) — comparativo entre BUs Brasília x São Carlos.

Módulo OPCIONAL e autocontido. Não altera nenhuma geração de HTML/e-mail existente.
É acionado apenas quando o usuário responde "S" no prompt do fluxo principal.

Foco: leitura executiva, com storytelling claro do que MELHOROU e do que PIOROU
em cada praça, comparando o período atual com os mesmos dias de calendário no mês anterior.
"""

from __future__ import annotations

import html as _html
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from report_falhas.io.data_loader import normalize_text, norm_matricula, safe_str
import report_falhas.config_report as cfg_report
from report_falhas.matricula_utils import clean_matricula_unified, normalize_dificuldade
from report_falhas.periods import (
    dias_corridos_periodo,
    filter_by_date_range,
    get_comparativo_by_same_period,
    get_reincidence_periods,
    month_last_day,
    prev_month_first_day,
)
from report_falhas.hc_maps import build_team_categoria_map
from report_falhas.filters import aplicar_recorte_oficial, uses_dual_metric_mode
from report_falhas.reincidence import novos_no_mes, reincidencia_table_full
from report_falhas.team_category import (
    CATEGORIA_NAO_CLASSIFICADO,
    CATEGORIA_OPERACIONAL,
    CATEGORIA_OUTROS,
    CATEGORIA_QUALIDADE,
    CATEGORIAS_ORDEM,
)

COL_MATRICULA = cfg_report.COL_MATRICULA
COL_DIFICULDADE = "Nível de Dificuldade"

_NIVEL_FACIL = "Fácil"
_NIVEL_MEDIO = "Médio"
_NIVEL_DIFICIL = "Difícil"
_NIVEIS_DIFICULDADE = (_NIVEL_FACIL, _NIVEL_MEDIO, _NIVEL_DIFICIL)
_COR_FACIL = "#15803d"
_COR_MEDIO = "#d97706"
_COR_DIFICIL = "#b91c1c"

# Faixas de tempo de casa (anos completos aproximados)
_FAIXA_MENOS_1 = "< 1 ano"
_FAIXA_1_A_3 = "1 a 3 anos"
_FAIXA_MAIS_3 = "3+ anos"
_FAIXA_SEM_DADO = "sem data de admissão"
_FAIXAS_ORDEM = (_FAIXA_MENOS_1, _FAIXA_1_A_3, _FAIXA_MAIS_3, _FAIXA_SEM_DADO)

# Mínimo de júniores (< 1 ano) entre quem falhou para sugerir ramp-up no comparativo
_PCT_JUNIOR_INSIGHT_MIN = 25.0


# Cores da marca (Serasa Experian)
_AZUL = "#174e97"
_AZUL_ESCURO = "#0b2a59"
_VERDE = "#15803d"
_VERMELHO = "#b91c1c"
_CINZA = "#64748b"
_CINZA_BORDA = "#e2e8f0"
_COR_OPERACIONAL = "#15803d"
_COR_QUALIDADE = "#174e97"
_COR_OUTROS = "#d97706"
_COR_NAO_CLASSIFICADO = "#64748b"


def _esc(x) -> str:
    try:
        return _html.escape(safe_str(x))
    except Exception:
        return _html.escape("" if x is None else str(x))


def _brand_logo_img(height_px: int = 36) -> str:
    """Logo branco embutido (data URI) para cabeçalho em fundo azul."""
    from report_falhas.assets import resolve_logo_data_uri

    uri = resolve_logo_data_uri()
    if not uri:
        return ""
    return (
        f'<img src="{uri}" alt="Serasa Experian" '
        f'style="height:{height_px}px;width:auto;display:block;flex-shrink:0;">'
    )


def _fmt_data_br(d) -> str:
    try:
        return pd.Timestamp(d).strftime("%d/%m/%Y")
    except Exception:
        return "—"


def _pct(parte: float, total: float) -> float:
    if not total:
        return 0.0
    return (parte / total) * 100.0


def _tenure_years(admissao, ref: date) -> float | None:
    try:
        adm = pd.Timestamp(admissao).date()
    except Exception:
        return None
    if adm is None or pd.isna(adm):
        return None
    dias = (ref - adm).days
    if dias < 0:
        return 0.0
    return dias / 365.25


def _faixa_tempo_casa(anos: float | None) -> str:
    if anos is None:
        return _FAIXA_SEM_DADO
    if anos < 1.0:
        return _FAIXA_MENOS_1
    if anos < 3.0:
        return _FAIXA_1_A_3
    return _FAIXA_MAIS_3


def _filtrar_hc_por_localidades(df_hc: pd.DataFrame, aliases: set) -> pd.DataFrame:
    """Filtra HC pela praça (coluna localidade, case-insensitive)."""
    if df_hc is None or df_hc.empty:
        return pd.DataFrame()
    col_loc = None
    for c in df_hc.columns:
        if normalize_text(c) == normalize_text("localidade"):
            col_loc = c
            break
    if not col_loc:
        return df_hc.copy()
    d = df_hc.copy()
    d["_loc_norm"] = d[col_loc].apply(normalize_text)
    aliases_norm = {normalize_text(a) for a in aliases}
    mask = d["_loc_norm"].isin(aliases_norm)
    if not mask.any():
        pattern = "|".join(re.escape(a) for a in aliases_norm)
        mask = d["_loc_norm"].str.contains(pattern, na=False, regex=True)
    return d.loc[mask].drop(columns=["_loc_norm"], errors="ignore").copy()


def _admissao_por_matricula(df_hc: pd.DataFrame) -> dict[str, date]:
    """Última admissão conhecida por matrícula no HC."""
    out: dict[str, date] = {}
    if df_hc is None or df_hc.empty or "matricula_agente" not in df_hc.columns:
        return out
    if "data_admissao" not in df_hc.columns:
        return out
    dfw = df_hc.dropna(subset=["matricula_agente"]).copy()
    sort_cols = ["matricula_agente"]
    if "data_inicial" in dfw.columns:
        sort_cols.append("data_inicial")
        dfw = dfw.sort_values(sort_cols, ascending=[True, False], na_position="last")
    grp = dfw.groupby("matricula_agente", as_index=False).first()
    for _, r in grp.iterrows():
        mat = norm_matricula(r["matricula_agente"])
        if not mat:
            continue
        adm = r.get("data_admissao")
        if pd.isna(adm):
            continue
        try:
            out[mat] = pd.Timestamp(adm).date()
        except Exception:
            continue
    return out


def _mats_agentes_hc(df_hc: pd.DataFrame) -> set[str]:
    if df_hc is None or df_hc.empty or "matricula_agente" not in df_hc.columns:
        return set()
    mats = set()
    for raw in df_hc["matricula_agente"].dropna().unique():
        m = norm_matricula(raw)
        if m:
            mats.add(m)
    return mats


def _mats_com_falha_oficial(
    df_bu_of: pd.DataFrame,
    cur_start: date,
    cur_end: date,
    col_data: str,
) -> set[str]:
    df_cur = filter_by_date_range(df_bu_of, cur_start, cur_end, col_data)
    if df_cur.empty or COL_MATRICULA not in df_cur.columns:
        return set()
    mats: set[str] = set()
    for raw in df_cur[COL_MATRICULA].dropna().unique():
        mat, _ = clean_matricula_unified(raw)
        mat = norm_matricula(mat or raw)
        if mat:
            mats.add(mat)
    return mats


def _contagem_por_faixa(mats: set[str], admissao_map: dict[str, date], ref: date) -> dict:
    por_faixa = {f: 0 for f in _FAIXAS_ORDEM}
    for mat in mats:
        anos = _tenure_years(admissao_map.get(mat), ref)
        por_faixa[_faixa_tempo_casa(anos)] += 1
    total = len(mats)
    menos_1 = por_faixa[_FAIXA_MENOS_1]
    return {
        "total": total,
        "menos_1_ano": menos_1,
        "pct_menos_1": _pct(menos_1, total),
        "por_faixa": por_faixa,
    }


def _kpis_tempo_casa(
    df_bu_of: pd.DataFrame,
    df_hc_bu: pd.DataFrame,
    cur_start: date,
    cur_end: date,
    col_data: str,
) -> dict:
    ref = cur_end or date.today()
    admissao_map = _admissao_por_matricula(df_hc_bu)
    mats_falha = _mats_com_falha_oficial(df_bu_of, cur_start, cur_end, col_data)
    mats_hc = _mats_agentes_hc(df_hc_bu)
    return {
        "ref": ref,
        "falha": _contagem_por_faixa(mats_falha, admissao_map, ref),
        "hc": _contagem_por_faixa(mats_hc, admissao_map, ref),
    }


def _narrativa_tempo_casa(nome: str, tc: dict) -> str:
    f = tc["falha"]
    if f["total"] == 0:
        return ""

    partes = []
    if f["total"] > 0:
        faixa_txt = ", ".join(
            f"{n} ({c})"
            for faixa, n, c in (
                (_FAIXA_MENOS_1, f["por_faixa"][_FAIXA_MENOS_1], "menos de 1 ano"),
                (_FAIXA_1_A_3, f["por_faixa"][_FAIXA_1_A_3], "1 a 3 anos"),
                (_FAIXA_MAIS_3, f["por_faixa"][_FAIXA_MAIS_3], "3+ anos"),
            )
            if f["por_faixa"].get(faixa, 0) > 0
        )
        intro = (
            f"Entre os <b>{f['total']} agente(s)</b> com falha oficial no período em "
            f"<b>{_esc(nome)}</b>"
        )
        if f["menos_1_ano"] > 0:
            intro += (
                f", <b>{f['menos_1_ano']}</b> têm <b>menos de 1 ano de casa</b> "
                f"({f['pct_menos_1']:.0f}% desse grupo)"
            )
        intro += "."
        if faixa_txt:
            intro += f" Distribuição: {faixa_txt}."
        partes.append(intro)

    return " ".join(partes)


def _insight_tempo_casa_comparativo(nome_a: str, ka: dict, nome_b: str, kb: dict) -> str:
    """Uma frase no resumo executivo ligando volume e perfil júnior, quando fizer sentido."""
    fa = ka.get("tempo_casa", {}).get("falha", {})
    fb = kb.get("tempo_casa", {}).get("falha", {})
    if not fa.get("total") and not fb.get("total"):
        return ""

    of_a, of_b = ka["oficial_atual"], kb["oficial_atual"]
    partes = []

    if of_a != of_b and fa.get("total") and fb.get("total"):
        maior_vol = nome_a if of_a > of_b else nome_b
        maior_tc = ka if maior_vol == nome_a else kb
        menor_vol = nome_b if maior_vol == nome_a else nome_a
        mf = maior_tc["tempo_casa"]["falha"]
        if mf["menos_1_ano"] > 0 and mf["pct_menos_1"] >= _PCT_JUNIOR_INSIGHT_MIN:
            outro = kb if maior_vol == nome_a else ka
            of_outro = outro["tempo_casa"]["falha"]
            comparativo = ""
            if of_outro.get("menos_1_ano", 0) > 0 or of_outro.get("total", 0):
                if of_outro["menos_1_ano"] < mf["menos_1_ano"]:
                    comparativo = (
                        f", enquanto em <b>{_esc(menor_vol)}</b> são "
                        f"<b>{of_outro['menos_1_ano']}</b> ({of_outro['pct_menos_1']:.0f}%)"
                    )
                elif of_outro["menos_1_ano"] == 0 and of_outro["total"]:
                    comparativo = f", enquanto em <b>{_esc(menor_vol)}</b> nenhum agente com falha tem menos de 1 ano de casa"
            partes.append(
                f"<b>{_esc(maior_vol)}</b> concentra o maior volume de falhas e, entre seus "
                f"agentes com falha, <b>{mf['menos_1_ano']}</b> têm menos de 1 ano de casa "
                f"({mf['pct_menos_1']:.0f}%){comparativo} — o perfil mais júnior pode "
                f"contribuir para explicar parte do volume."
            )
            return partes[0]

    # Sem diferença clara de volume: ainda destacar quem tem mais júniores com falha
    candidatos = []
    for nome, k in ((nome_a, ka), (nome_b, kb)):
        f = k.get("tempo_casa", {}).get("falha", {})
        if f.get("menos_1_ano", 0) > 0 and f.get("pct_menos_1", 0) >= _PCT_JUNIOR_INSIGHT_MIN:
            candidatos.append((nome, f["menos_1_ano"], f["pct_menos_1"], f["total"]))
    if not candidatos:
        return ""
    candidatos.sort(key=lambda x: (-x[1], -x[2]))
    nome, qtd, pct, total = candidatos[0]
    return (
        f"Em <b>{_esc(nome)}</b>, <b>{qtd}</b> dos {total} agente(s) com falha no período "
        f"têm menos de 1 ano de casa ({pct:.0f}%) — vale considerar reforço de "
        f"acompanhamento para quem está em ramp-up."
    )


def _bloco_tempo_casa_html(nome_a: str, ka: dict, nome_b: str, kb: dict) -> str:
    def _mini_card(nome: str, tc: dict) -> str:
        f = tc.get("falha", {})
        if f.get("total", 0) == 0:
            return ""
        linhas = []
        itens = "".join(
            f"<li><b>{_esc(faixa)}:</b> {f['por_faixa'].get(faixa, 0)} agente(s) com falha</li>"
            for faixa in _FAIXAS_ORDEM
            if f["por_faixa"].get(faixa, 0) > 0
        )
        linhas.append(
            f"<div style='font-size:12px;color:{_CINZA};margin-bottom:6px;"
            f"text-transform:uppercase;'>Com falha oficial no período ({f['total']})</div>"
            f"<ul style='margin:0 0 12px;padding-left:18px;font-size:13px;line-height:1.5;'>{itens}</ul>"
        )
        return f"""
        <div style="flex:1;min-width:260px;background:#f8fafc;border:1px solid {_CINZA_BORDA};
                    border-radius:12px;padding:16px 18px;">
          <div style="font-size:13px;font-weight:700;color:{_AZUL};margin-bottom:10px;">{_esc(nome)}</div>
          {''.join(linhas)}
        </div>
        """

    cards = _mini_card(nome_a, ka.get("tempo_casa", {})) + _mini_card(nome_b, kb.get("tempo_casa", {}))
    if not cards.strip():
        return ""
    return f"""
    <div style="background:#ffffff;border:1px solid {_CINZA_BORDA};border-radius:14px;
                padding:20px 22px;margin-top:18px;box-shadow:0 1px 3px rgba(15,23,42,.06);">
      <div style="font-size:13px;font-weight:700;color:{_AZUL};text-transform:uppercase;
                  letter-spacing:.05em;margin-bottom:8px;">Perfil do time — tempo de casa</div>
      <p style="margin:0 0 14px;font-size:13px;color:{_CINZA};line-height:1.5;">
        Recorte somente dos agentes com <b>falha oficial</b> no período. Equipes com mais gente em
        ramp-up (&lt; 1 ano) tendem a concentrar falhas em agentes menos experientes.
      </p>
      <div style="display:flex;flex-wrap:wrap;gap:14px;">{cards}</div>
    </div>
    """


def _tendencia(atual: int, anterior: int) -> dict:
    """Classifica a variação de falhas. Menos falhas = melhora."""
    delta = int(atual) - int(anterior)
    if anterior > 0:
        perc = (delta / anterior) * 100.0
    else:
        perc = 100.0 if atual > 0 else 0.0

    if delta < 0:
        rotulo, direcao, cor, seta = "melhorou", "queda", _VERDE, "▼"
    elif delta > 0:
        rotulo, direcao, cor, seta = "piorou", "alta", _VERMELHO, "▲"
    else:
        rotulo, direcao, cor, seta = "estável", "estabilidade", _CINZA, "■"

    return {
        "delta": delta,
        "delta_abs": abs(delta),
        "perc": perc,
        "perc_abs": abs(perc),
        "rotulo": rotulo,
        "direcao": direcao,
        "cor": cor,
        "seta": seta,
    }


def _contagem_por_dificuldade(df_periodo: pd.DataFrame) -> dict[str, int]:
    """Conta falhas por nível normalizado (Fácil / Médio / Difícil)."""
    out = {n: 0 for n in _NIVEIS_DIFICULDADE}
    if df_periodo is None or df_periodo.empty or COL_DIFICULDADE not in df_periodo.columns:
        return out
    for raw in df_periodo[COL_DIFICULDADE]:
        nivel = normalize_dificuldade(raw)
        if nivel in out:
            out[nivel] += 1
    return out


def _kpis_dificuldade(
    df_bu_of: pd.DataFrame,
    cur_start: date,
    cur_end: date,
    col_data: str,
    prev_eq_start: date | None,
    prev_eq_end: date | None,
) -> dict:
    """Distribuição por dificuldade (métrica oficial) com comparativo MTD."""
    df_cur = filter_by_date_range(df_bu_of, cur_start, cur_end, col_data)
    cur_counts = _contagem_por_dificuldade(df_cur)

    prev_counts = {n: 0 for n in _NIVEIS_DIFICULDADE}
    if prev_eq_start and prev_eq_end:
        df_prev = filter_by_date_range(df_bu_of, prev_eq_start, prev_eq_end, col_data)
        prev_counts = _contagem_por_dificuldade(df_prev)

    total_atual = sum(cur_counts.values())
    niveis: dict = {}
    for nivel in _NIVEIS_DIFICULDADE:
        atual = cur_counts[nivel]
        prev = prev_counts[nivel]
        niveis[nivel] = {
            "atual": atual,
            "prev": prev,
            "pct": _pct(atual, total_atual),
            "trend": _tendencia(atual, prev),
        }

    dominante = ""
    if total_atual > 0:
        dominante = max(_NIVEIS_DIFICULDADE, key=lambda n: cur_counts[n])

    return {
        "niveis": niveis,
        "total_atual": total_atual,
        "dominante": dominante,
    }


def _linha_trend_dificuldade(nivel: dict) -> str:
    t = nivel["trend"]
    sinal = "+" if t["delta"] > 0 else ""
    return (
        f"<span style='color:{t['cor']};font-weight:700;'>{t['seta']} {sinal}{t['delta']} "
        f"({t['perc_abs']:.0f}%)</span>"
        f"<span style='color:{_CINZA};font-size:11px;margin-left:6px;'>{t['rotulo']}</span>"
    )


def _bloco_dificuldade_html(nome_a: str, ka: dict, nome_b: str, kb: dict) -> str:
    """Bloco comparativo Fácil / Médio / Difícil por praça."""
    cores = {_NIVEL_FACIL: _COR_FACIL, _NIVEL_MEDIO: _COR_MEDIO, _NIVEL_DIFICIL: _COR_DIFICIL}

    def _mini_card(nome: str, k: dict) -> str:
        dif = k.get("dificuldade", {})
        if not dif.get("total_atual"):
            return ""
        dominante = dif.get("dominante", "")
        linhas = []
        for nivel in _NIVEIS_DIFICULDADE:
            info = dif["niveis"][nivel]
            destaque = (
                "border-left:3px solid %s;padding-left:10px;background:#f8fafc;" % cores[nivel]
                if nivel == dominante
                else ""
            )
            badge = (
                f"<span style='font-size:10px;font-weight:700;color:{cores[nivel]};"
                f"text-transform:uppercase;margin-left:6px;'>maior concentração</span>"
                if nivel == dominante
                else ""
            )
            trend_html = _linha_trend_dificuldade(info)
            linhas.append(
                f"<div style='margin-bottom:10px;{destaque}'>"
                f"<div style='display:flex;align-items:baseline;flex-wrap:wrap;gap:4px;'>"
                f"<span style='font-weight:700;color:{cores[nivel]};min-width:52px;'>{_esc(nivel)}</span>"
                f"<span style='font-size:18px;font-weight:800;color:{_AZUL_ESCURO};'>{info['atual']}</span>"
                f"<span style='font-size:12px;color:{_CINZA};'>({info['pct']:.0f}%)</span>"
                f"{badge}"
                f"</div>"
                f"<div style='font-size:12px;margin-top:3px;'>"
                f"<span style='color:{_CINZA};'>anterior: <b>{info['prev']}</b> · </span>{trend_html}"
                f"</div></div>"
            )
        return f"""
        <div style="flex:1;min-width:260px;background:#ffffff;border:1px solid {_CINZA_BORDA};
                    border-radius:12px;padding:16px 18px;">
          <div style="font-size:13px;font-weight:700;color:{_AZUL};margin-bottom:12px;">{_esc(nome)}</div>
          {''.join(linhas)}
        </div>
        """

    cards = _mini_card(nome_a, ka) + _mini_card(nome_b, kb)
    if not cards.strip():
        return ""
    return f"""
    <div style="background:#ffffff;border:1px solid {_CINZA_BORDA};border-radius:14px;
                padding:20px 22px;margin-top:18px;box-shadow:0 1px 3px rgba(15,23,42,.06);">
      <div style="font-size:13px;font-weight:700;color:{_AZUL};text-transform:uppercase;
                  letter-spacing:.05em;margin-bottom:8px;">Distribuição por dificuldade (métrica oficial)</div>
      <p style="margin:0 0 14px;font-size:13px;color:{_CINZA};line-height:1.5;">
        Onde está a maior dificuldade nas falhas oficiais? Falhas <b style="color:{_COR_FACIL};">fáceis</b>
        são erros evitáveis; <b style="color:{_COR_MEDIO};">médias</b> e
        <b style="color:{_COR_DIFICIL};">difíceis</b> exigem mais análise. A seta compara com o MTD equivalente anterior.
      </p>
      <div style="display:flex;flex-wrap:wrap;gap:14px;">{cards}</div>
    </div>
    """


def _mat_norm_from_raw(raw) -> str:
    mat, _ = clean_matricula_unified(raw)
    return norm_matricula(mat or raw)


def _contagem_por_team_categoria(
    df_periodo: pd.DataFrame,
    team_categoria_map: dict[str, str],
    col_matricula: str,
) -> dict[str, int]:
    out = {c: 0 for c in CATEGORIAS_ORDEM}
    if df_periodo is None or df_periodo.empty or col_matricula not in df_periodo.columns:
        return out
    for raw in df_periodo[col_matricula].dropna():
        mat_n = _mat_norm_from_raw(raw)
        cat = team_categoria_map.get(mat_n, CATEGORIA_NAO_CLASSIFICADO)
        if cat in out:
            out[cat] += 1
        else:
            out[CATEGORIA_NAO_CLASSIFICADO] += 1
    return out


def _kpis_team_categoria(
    df_bu_of: pd.DataFrame,
    cur_start: date,
    cur_end: date,
    col_data: str,
    prev_eq_start: date | None,
    prev_eq_end: date | None,
    team_categoria_map: dict[str, str],
) -> dict:
    """Distribuição por Team/Category (métrica oficial) com comparativo MTD."""
    df_cur = filter_by_date_range(df_bu_of, cur_start, cur_end, col_data)
    cur_counts = _contagem_por_team_categoria(df_cur, team_categoria_map, COL_MATRICULA)

    prev_counts = {c: 0 for c in CATEGORIAS_ORDEM}
    if prev_eq_start and prev_eq_end:
        df_prev = filter_by_date_range(df_bu_of, prev_eq_start, prev_eq_end, col_data)
        prev_counts = _contagem_por_team_categoria(df_prev, team_categoria_map, COL_MATRICULA)

    total_atual = sum(cur_counts.values())
    categorias: dict = {}
    for cat in CATEGORIAS_ORDEM:
        atual = cur_counts[cat]
        prev = prev_counts[cat]
        categorias[cat] = {
            "atual": atual,
            "prev": prev,
            "pct": _pct(atual, total_atual),
            "trend": _tendencia(atual, prev),
        }

    dominante = ""
    if total_atual > 0:
        dominante = max(CATEGORIAS_ORDEM, key=lambda c: cur_counts[c])

    nao_operacional = total_atual - cur_counts.get(CATEGORIA_OPERACIONAL, 0)
    pct_nao_operacional = _pct(nao_operacional, total_atual)

    return {
        "categorias": categorias,
        "total_atual": total_atual,
        "dominante": dominante,
        "pct_nao_operacional": pct_nao_operacional,
        "nao_operacional": nao_operacional,
    }


def _bloco_team_categoria_html(nome_a: str, ka: dict, nome_b: str, kb: dict) -> str:
    cores = {
        CATEGORIA_OPERACIONAL: _COR_OPERACIONAL,
        CATEGORIA_QUALIDADE: _COR_QUALIDADE,
        CATEGORIA_OUTROS: _COR_OUTROS,
        CATEGORIA_NAO_CLASSIFICADO: _COR_NAO_CLASSIFICADO,
    }

    def _mini_card(nome: str, k: dict) -> str:
        tc = k.get("team_categoria", {})
        if not tc.get("total_atual"):
            return ""
        dominante = tc.get("dominante", "")
        linhas = []
        for cat in CATEGORIAS_ORDEM:
            info = tc["categorias"][cat]
            if info["atual"] == 0 and info["prev"] == 0:
                continue
            destaque = (
                "border-left:3px solid %s;padding-left:10px;background:#f8fafc;" % cores[cat]
                if cat == dominante
                else ""
            )
            badge = (
                f"<span style='font-size:10px;font-weight:700;color:{cores[cat]};"
                f"text-transform:uppercase;margin-left:6px;'>maior concentração</span>"
                if cat == dominante
                else ""
            )
            trend_html = _linha_trend_dificuldade(info)
            linhas.append(
                f"<div style='margin-bottom:10px;{destaque}'>"
                f"<div style='display:flex;align-items:baseline;flex-wrap:wrap;gap:4px;'>"
                f"<span style='font-weight:700;color:{cores[cat]};min-width:90px;'>{_esc(cat)}</span>"
                f"<span style='font-size:18px;font-weight:800;color:{_AZUL_ESCURO};'>{info['atual']}</span>"
                f"<span style='font-size:12px;color:{_CINZA};'>({info['pct']:.0f}%)</span>"
                f"{badge}"
                f"</div>"
                f"<div style='font-size:12px;margin-top:3px;'>"
                f"<span style='color:{_CINZA};'>anterior: <b>{info['prev']}</b> · </span>{trend_html}"
                f"</div></div>"
            )
        if not linhas:
            return ""
        return f"""
        <div style="flex:1;min-width:260px;background:#ffffff;border:1px solid {_CINZA_BORDA};
                    border-radius:12px;padding:16px 18px;">
          <div style="font-size:13px;font-weight:700;color:{_AZUL};margin-bottom:12px;">{_esc(nome)}</div>
          {''.join(linhas)}
        </div>
        """

    cards = _mini_card(nome_a, ka) + _mini_card(nome_b, kb)
    if not cards.strip():
        return ""
    return f"""
    <div style="background:#ffffff;border:1px solid {_CINZA_BORDA};border-radius:14px;
                padding:20px 22px;margin-top:18px;box-shadow:0 1px 3px rgba(15,23,42,.06);">
      <div style="font-size:13px;font-weight:700;color:{_AZUL};text-transform:uppercase;
                  letter-spacing:.05em;margin-bottom:8px;">Origem por Team/Category (métrica oficial)</div>
      <p style="margin:0 0 14px;font-size:13px;color:{_CINZA};line-height:1.5;">
        Mostra de qual área veio o agente — nem toda falha é da operação de linha.
        Classificação do time no HC:
        <b style="color:{_COR_OPERACIONAL};">Operacional</b> (linha),
        <b style="color:{_COR_QUALIDADE};">Qualidade</b> (auditoria/capacitação),
        <b style="color:{_COR_OUTROS};">Outros</b> (apoio) e
        <b style="color:{_COR_NAO_CLASSIFICADO};">Não classificado</b> (sem vínculo na base).
      </p>
      <div style="display:flex;flex-wrap:wrap;gap:14px;">{cards}</div>
    </div>
    """


def _kpis_bu(
    df_bu: pd.DataFrame,
    df_bu_of: pd.DataFrame,
    cur_start: date,
    cur_end: date,
    col_data: str,
    col_cenario: str,
    team_categoria_map: dict[str, str] | None = None,
) -> dict:
    """Calcula os indicadores comparativos de uma praça (BU)."""
    prev_month_start = prev_month_first_day(cur_start)

    # Volume (mesmos dias corridos no mês anterior) — Total e Oficial
    tot_atual, tot_prev, _, _ = get_comparativo_by_same_period(
        df_bu, cur_start, cur_end, prev_month_start, col_data
    )
    of_atual, of_prev, prev_eq_start, prev_eq_end = get_comparativo_by_same_period(
        df_bu_of, cur_start, cur_end, prev_month_start, col_data
    )

    # Reincidência (mês anterior cheio vs período atual) — base Oficial
    prev_start_full, prev_end_full, _, _ = get_reincidence_periods(cur_end)
    df_prev_reinc = filter_by_date_range(df_bu_of, prev_start_full, prev_end_full, col_data)
    df_cur_reinc = filter_by_date_range(df_bu_of, cur_start, cur_end, col_data)
    df_reinc = reincidencia_table_full(df_prev_reinc, df_cur_reinc)

    reincidentes = 0
    agentes_com_falha = 0
    if df_reinc is not None and not df_reinc.empty:
        agentes_com_falha = int(df_reinc["Matrícula Agente"].nunique())
        reincidentes = int((df_reinc["Reincidente"] == "Sim").sum())
    novos = len(novos_no_mes(df_prev_reinc, df_cur_reinc))

    # Cenário dominante (Oficial, período atual)
    top_cenario = ("", 0)
    if col_cenario in df_cur_reinc.columns and not df_cur_reinc.empty:
        vc = df_cur_reinc[col_cenario].apply(safe_str)
        vc = vc[vc != ""].value_counts()
        if not vc.empty:
            top_cenario = (str(vc.index[0]), int(vc.iloc[0]))

    return {
        "total_atual": int(tot_atual),
        "total_prev": int(tot_prev),
        "oficial_atual": int(of_atual),
        "oficial_prev": int(of_prev),
        "trend_total": _tendencia(tot_atual, tot_prev),
        "trend_oficial": _tendencia(of_atual, of_prev),
        "reincidentes": reincidentes,
        "agentes_com_falha": agentes_com_falha,
        "reinc_pct": _pct(reincidentes, agentes_com_falha),
        "novos": novos,
        "top_cenario_nome": top_cenario[0],
        "top_cenario_qtd": top_cenario[1],
        "prev_eq_start": prev_eq_start,
        "prev_eq_end": prev_eq_end,
        "dias_comparativo": dias_corridos_periodo(cur_start, cur_end),
        "cur_start": cur_start,
        "cur_end": cur_end,
        "dificuldade": _kpis_dificuldade(
            df_bu_of, cur_start, cur_end, col_data, prev_eq_start, prev_eq_end
        ),
        "team_categoria": _kpis_team_categoria(
            df_bu_of,
            cur_start,
            cur_end,
            col_data,
            prev_eq_start,
            prev_eq_end,
            team_categoria_map or {},
        ),
    }


def _truncate_label(text: str, max_len: int = 52) -> str:
    s = safe_str(text).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _linha_trend_curta(t: dict) -> str:
    sinal = "+" if t["delta"] > 0 else ""
    return (
        f"<span style='color:{t['cor']};font-weight:700;'>{t['seta']} {sinal}{t['delta']} "
        f"({t['perc_abs']:.0f}%)</span>"
    )


def _abertura_volume_bu(nome: str, of_atual: int, cur_start: date, cur_end: date) -> str:
    """Abertura da narrativa com o período analisado (MTD ou mês fechado)."""
    periodo_txt = f"{_fmt_data_br(cur_start)} a {_fmt_data_br(cur_end)}"
    if cur_end >= month_last_day(cur_start):
        return (
            f"<b>{_esc(nome)}</b> encerrou o período <b>{periodo_txt}</b> com "
            f"<b>{of_atual} falha(s) oficiais</b>"
        )
    return (
        f"<b>{_esc(nome)}</b> acumula <b>{of_atual} falha(s) oficiais</b> "
        f"no período <b>{periodo_txt}</b>"
    )


def _narrativa_bu(nome: str, k: dict) -> str:
    """Resumo curto por praça (bullets). Detalhes ficam nos blocos visuais abaixo."""
    of_atual = k["oficial_atual"]
    t = k["trend_oficial"]
    cur_start, cur_end = k.get("cur_start"), k.get("cur_end")
    periodo_txt = (
        f"{_fmt_data_br(cur_start)} – {_fmt_data_br(cur_end)}"
        if cur_start and cur_end
        else "—"
    )

    if t["delta"] < 0:
        rotulo = "melhora"
    elif t["delta"] > 0:
        rotulo = "piora"
    else:
        rotulo = "estável"

    bullets = [
        (
            f"<b>{of_atual}</b> falha(s) oficiais · {_linha_trend_curta(t)} "
            f"<span style='color:{_CINZA};'>({rotulo} vs MTD anterior)</span>"
        ),
    ]

    dif = k.get("dificuldade", {})
    tc = k.get("team_categoria", {})
    perfil_partes = []
    if dif.get("dominante"):
        dom = dif["dominante"]
        pct = dif["niveis"].get(dom, {}).get("pct", 0)
        perfil_partes.append(f"Dificuldade: <b>{_esc(dom)}</b> ({pct:.0f}%)")
    if tc.get("dominante"):
        dom = tc["dominante"]
        pct = tc["categorias"].get(dom, {}).get("pct", 0)
        perfil_partes.append(f"Origem: <b>{_esc(dom)}</b> ({pct:.0f}%)")
    if perfil_partes:
        bullets.append(" · ".join(perfil_partes))

    alertas = []
    if k.get("reincidentes", 0) > 0:
        alertas.append(f"<b>{k['reincidentes']}</b> reincidente(s) ({k['reinc_pct']:.0f}%)")
    if k.get("novos", 0) > 0:
        alertas.append(f"<b>{k['novos']}</b> agente(s) novo(s) com falha")
    if tc.get("pct_nao_operacional", 0) >= 25:
        alertas.append(f"<b>{tc['pct_nao_operacional']:.0f}%</b> fora de operação")
    if alertas:
        bullets.append("Atenção: " + "; ".join(alertas))

    if k.get("top_cenario_nome"):
        cen_curto = _truncate_label(k["top_cenario_nome"])
        bullets.append(
            f"Cenário top: "
            f"<span title=\"{_html.escape(k['top_cenario_nome'], quote=True)}\">"
            f"{_esc(cen_curto)}</span> "
            f"({k['top_cenario_qtd']}×)"
        )

    items = "".join(
        f"<li style='margin-bottom:5px;line-height:1.45;'>{b}</li>" for b in bullets
    )
    return f"""
    <div style="flex:1;min-width:280px;background:#f8fafc;border:1px solid {_CINZA_BORDA};
                border-radius:10px;padding:14px 16px;">
      <div style="font-size:13px;font-weight:700;color:{_AZUL};margin-bottom:4px;">{_esc(nome)}</div>
      <div style="font-size:11px;color:{_CINZA};margin-bottom:8px;">{periodo_txt}</div>
      <ul style="margin:0;padding-left:18px;font-size:13px;color:#1e293b;">{items}</ul>
    </div>
    """


def _veredito_comparativo(nome_a: str, ka: dict, nome_b: str, kb: dict) -> str:
    """Compara as duas praças e gera o parágrafo de conclusão executiva."""
    ta, tb = ka["trend_oficial"], kb["trend_oficial"]
    partes = []

    # Veredito de tendência
    melhoraram = []
    pioraram = []
    for nome, t in ((nome_a, ta), (nome_b, tb)):
        if t["delta"] < 0:
            melhoraram.append(nome)
        elif t["delta"] > 0:
            pioraram.append(nome)

    if len(melhoraram) == 2:
        partes.append("Ambas as praças <b style='color:%s'>reduziram</b> o volume de falhas oficiais no período, movimento positivo para a operação." % _VERDE)
    elif len(pioraram) == 2:
        partes.append("Ambas as praças <b style='color:%s'>aumentaram</b> o volume de falhas oficiais — ponto de atenção que requer ação conjunta." % _VERMELHO)
    elif melhoraram and pioraram:
        partes.append(
            f"<b>{_esc(melhoraram[0])}</b> evoluiu positivamente (queda de falhas), "
            f"enquanto <b>{_esc(pioraram[0])}</b> apresentou piora e merece atenção."
        )
    else:
        partes.append("As praças mantiveram estabilidade no volume de falhas oficiais.")

    # Veredito de volume (quem concentra mais falhas)
    if ka["oficial_atual"] != kb["oficial_atual"]:
        maior = nome_a if ka["oficial_atual"] > kb["oficial_atual"] else nome_b
        v_maior = max(ka["oficial_atual"], kb["oficial_atual"])
        v_menor = min(ka["oficial_atual"], kb["oficial_atual"])
        partes.append(
            f"Em volume absoluto, <b>{_esc(maior)}</b> concentra a maior parte das "
            f"falhas oficiais no período ({v_maior} vs {v_menor})."
        )
    else:
        partes.append(
            f"As duas praças registraram o mesmo volume de falhas oficiais "
            f"({ka['oficial_atual']} cada)."
        )

    return " ".join(partes)


def _destaques(nome_a: str, ka: dict, nome_b: str, kb: dict) -> tuple[list[str], list[str]]:
    """Listas de 'o que melhorou' e 'o que piorou' entre as praças."""
    melhorou, piorou = [], []
    for nome, k in ((nome_a, ka), (nome_b, kb)):
        t = k["trend_oficial"]
        if t["delta"] < 0:
            melhorou.append(
                f"{_esc(nome)}: queda de {t['delta_abs']} falha(s) oficiais "
                f"({t['perc_abs']:.0f}%) vs período anterior."
            )
        elif t["delta"] > 0:
            piorou.append(
                f"{_esc(nome)}: aumento de {t['delta_abs']} falha(s) oficiais "
                f"({t['perc_abs']:.0f}%) vs período anterior."
            )
        if k["reincidentes"] > 0:
            piorou.append(
                f"{_esc(nome)}: {k['reincidentes']} agente(s) reincidente(s) "
                f"({k['reinc_pct']:.0f}%) — risco de recorrência."
            )
        if k["novos"] > 0:
            piorou.append(
                f"{_esc(nome)}: {k['novos']} novo(s) agente(s) com falha no período."
            )
        tc_f = k.get("tempo_casa", {}).get("falha", {})
        if tc_f.get("menos_1_ano", 0) >= 3:
            piorou.append(
                f"{_esc(nome)}: {tc_f['menos_1_ano']} agente(s) com falha têm menos de 1 ano "
                f"de casa ({tc_f['pct_menos_1']:.0f}% dos que falharam no período)."
            )
        dif = k.get("dificuldade", {})
        facil = dif.get("niveis", {}).get(_NIVEL_FACIL, {})
        if facil.get("atual", 0) > 0 or facil.get("prev", 0) > 0:
            tf = facil.get("trend", {})
            if tf.get("delta", 0) < 0:
                melhorou.append(
                    f"{_esc(nome)}: falhas fáceis caíram de {facil['prev']} para "
                    f"{facil['atual']} (▼ {tf['perc_abs']:.0f}%) — menos erros evitáveis."
                )
            elif tf.get("delta", 0) > 0:
                piorou.append(
                    f"{_esc(nome)}: falhas fáceis subiram de {facil['prev']} para "
                    f"{facil['atual']} (▲ {tf['perc_abs']:.0f}%) — atenção a erros evitáveis."
                )
        tc = k.get("team_categoria", {})
        op = tc.get("categorias", {}).get(CATEGORIA_OPERACIONAL, {})
        if op.get("trend", {}).get("delta", 0) > 0 and op.get("atual", 0) > 0:
            piorou.append(
                f"{_esc(nome)}: falhas de Operacional subiram de {op['prev']} para "
                f"{op['atual']} (▲ {op['trend']['perc_abs']:.0f}%)."
            )
        elif op.get("trend", {}).get("delta", 0) < 0 and op.get("prev", 0) > 0:
            melhorou.append(
                f"{_esc(nome)}: falhas de Operacional caíram de {op['prev']} para "
                f"{op['atual']} (▼ {op['trend']['perc_abs']:.0f}%)."
            )
        if tc.get("pct_nao_operacional", 0) >= 40 and tc.get("total_atual", 0) > 0:
            piorou.append(
                f"{_esc(nome)}: {tc['pct_nao_operacional']:.0f}% das falhas oficiais "
                f"não são de operação (Qualidade/Outros/Não classificado)."
            )
    if not melhorou:
        melhorou.append("Sem reduções relevantes de volume neste período.")
    if not piorou:
        piorou.append("Sem pioras relevantes identificadas neste período.")
    return melhorou, piorou


def _periodo_equiv_txt(k: dict) -> str:
    if k.get("prev_eq_start") and k.get("prev_eq_end"):
        return f"{_fmt_data_br(k['prev_eq_start'])} a {_fmt_data_br(k['prev_eq_end'])}"
    return "período equivalente no mês anterior"


def _linha_comparativo_oficial(k: dict) -> str:
    """Texto explícito: o ▼/▲ refere-se só às falhas oficiais vs MTD equivalente anterior."""
    t = k["trend_oficial"]
    atual = k["oficial_atual"]
    anterior = k["oficial_prev"]
    periodo_ant = _periodo_equiv_txt(k)
    sinal = "+" if t["delta"] > 0 else ""
    return (
        f"<span style='color:{t['cor']};font-weight:700;'>{t['seta']} {sinal}{t['delta']} "
        f"({t['perc_abs']:.0f}%)</span>"
        f"<span style='color:{_CINZA};font-weight:500;'> — oficiais: </span>"
        f"<b>{anterior}</b><span style='color:{_CINZA};'> → </span><b>{atual}</b>"
        f"<span style='color:{_CINZA};font-weight:500;font-size:11px;display:block;margin-top:4px;'>"
        f"Comparativo MTD equivalente ({periodo_ant})</span>"
    )


def _card_bu(nome: str, k: dict, *, dual_metric_mode: bool = True) -> str:
    """Card visual com os números de uma praça."""
    t_tot = k["trend_total"]
    cmp_of = _linha_comparativo_oficial(k)
    tot_ant = k["total_prev"]
    tot_sinal = "+" if t_tot["delta"] > 0 else ""
    total_block = ""
    if dual_metric_mode:
        total_block = f"""
        <div style="min-width:90px;">
          <div style="font-size:11px;color:{_CINZA};text-transform:uppercase;">Total (sem métrica oficial)</div>
          <div style="font-size:10px;color:{_CINZA};margin-top:2px;">Todos os cenários · mesmo comparativo MTD</div>
          <div style="font-size:18px;font-weight:700;color:{_AZUL_ESCURO};margin-top:4px;">
            {k['total_atual']}
          </div>
          <div style="font-size:11px;color:{_CINZA};margin-top:2px;">
            anterior: <b>{tot_ant}</b>
            <span style="font-weight:600;color:{t_tot['cor']};margin-left:6px;">
              {t_tot['seta']} {tot_sinal}{t_tot['delta']} ({t_tot['perc_abs']:.0f}%)
            </span>
          </div>
        </div>"""
    return f"""
    <div style="flex:1;min-width:260px;background:#ffffff;border:1px solid {_CINZA_BORDA};
                border-radius:14px;padding:20px 22px;box-shadow:0 1px 3px rgba(15,23,42,.08);">
      <div style="font-size:13px;font-weight:700;color:{_AZUL};text-transform:uppercase;
                  letter-spacing:.06em;margin-bottom:8px;">{_esc(nome)}</div>
      <div>
        <div style="font-size:10px;font-weight:700;color:{_AZUL};text-transform:uppercase;
                    letter-spacing:.05em;">Métrica oficial</div>
        <div style="font-size:40px;font-weight:800;color:{_AZUL_ESCURO};line-height:1;margin-top:4px;">
          {k['oficial_atual']}
        </div>
        <div style="font-size:12px;color:{_CINZA};margin-top:2px;">falhas oficiais no período atual</div>
        <div style="margin-top:10px;font-size:13px;line-height:1.45;">{cmp_of}</div>
      </div>
      <div style="border-top:1px solid {_CINZA_BORDA};margin-top:14px;padding-top:12px;
                  display:flex;flex-wrap:wrap;gap:12px;">
        {total_block}
        <div style="min-width:90px;">
          <div style="font-size:11px;color:{_CINZA};text-transform:uppercase;">Reincidentes</div>
          <div style="font-size:18px;font-weight:700;color:{_AZUL_ESCURO};">
            {k['reincidentes']}
            <span style="font-size:12px;font-weight:600;color:{_CINZA};">
              ({k['reinc_pct']:.0f}%)
            </span>
          </div>
        </div>
        <div style="min-width:80px;">
          <div style="font-size:11px;color:{_CINZA};text-transform:uppercase;">Novos no mês</div>
          <div style="font-size:18px;font-weight:700;color:{_AZUL_ESCURO};">{k['novos']}</div>
        </div>
      </div>
    </div>
    """


def _build_html(
    nome_a: str,
    ka: dict,
    nome_b: str,
    kb: dict,
    cur_start: date,
    cur_end: date,
    *,
    dual_metric_mode: bool = True,
) -> str:
    periodo_txt = f"{_fmt_data_br(cur_start)} a {_fmt_data_br(cur_end)}"
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")

    narr_a = _narrativa_bu(nome_a, ka)
    narr_b = _narrativa_bu(nome_b, kb)
    veredito = _veredito_comparativo(nome_a, ka, nome_b, kb)
    melhorou, piorou = _destaques(nome_a, ka, nome_b, kb)

    li_melhorou = "".join(f"<li style='margin-bottom:6px;'>{m}</li>" for m in melhorou)
    li_piorou = "".join(f"<li style='margin-bottom:6px;'>{p}</li>" for p in piorou)

    cards = _card_bu(nome_a, ka, dual_metric_mode=dual_metric_mode) + _card_bu(nome_b, kb, dual_metric_mode=dual_metric_mode)
    bloco_team = _bloco_team_categoria_html(nome_a, ka, nome_b, kb)
    bloco_dif = _bloco_dificuldade_html(nome_a, ka, nome_b, kb)
    bloco_tc = _bloco_tempo_casa_html(nome_a, ka, nome_b, kb)
    resumo_praças = f"""
    <div style="display:flex;flex-wrap:wrap;gap:12px;margin-top:12px;">
      {narr_a}{narr_b}
    </div>
    """

    logo_img = _brand_logo_img()

    como_ler_total = (
        " O <b>Total (sem métrica oficial)</b> abaixo é outra contagem (todos os cenários), com comparativo próprio."
        if dual_metric_mode
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Relatório Executivo — Comparativo BU (Brasília x São Carlos)</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Segoe UI,Arial,Helvetica,sans-serif;color:#1e293b;">
  <div style="max-width:900px;margin:0 auto;padding:24px;">

    <!-- Cabeçalho -->
    <div style="background:linear-gradient(135deg,{_AZUL_ESCURO},{_AZUL});border-radius:16px;
                padding:24px 28px;color:#ffffff;">
      <div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap;">
        {logo_img}
        <div style="flex:1;min-width:220px;">
          <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.85;">
            Report de Falhas Críticas · Visão Executiva
          </div>
          <h1 style="margin:8px 0 4px;font-size:26px;font-weight:800;line-height:1.2;">
            Comparativo entre Praças — {_esc(nome_a)} x {_esc(nome_b)}
          </h1>
          <div style="font-size:14px;opacity:.9;">Período analisado: <b>{periodo_txt}</b></div>
        </div>
      </div>
    </div>

    <!-- Resumo executivo (storytelling) -->
    <div style="background:#ffffff;border:1px solid {_CINZA_BORDA};border-radius:14px;
                padding:22px 24px;margin-top:18px;box-shadow:0 1px 3px rgba(15,23,42,.06);">
      <div style="font-size:13px;font-weight:700;color:{_AZUL};text-transform:uppercase;
                  letter-spacing:.05em;margin-bottom:10px;">Resumo executivo</div>
      <p style="margin:0 0 4px;font-size:15px;line-height:1.55;">{veredito}</p>
      {resumo_praças}
    </div>

    <!-- Cards das praças -->
    <div style="display:flex;flex-wrap:wrap;gap:16px;margin-top:18px;">
      {cards}
    </div>

    {bloco_team}

    {bloco_dif}

    {bloco_tc}

    <!-- Destaques: melhorou x piorou -->
    <div style="display:flex;flex-wrap:wrap;gap:16px;margin-top:18px;">
      <div style="flex:1;min-width:260px;background:#f0fdf4;border:1px solid #bbf7d0;
                  border-radius:14px;padding:18px 20px;">
        <div style="font-size:14px;font-weight:800;color:{_VERDE};margin-bottom:10px;">
          ✓ O que melhorou
        </div>
        <ul style="margin:0;padding-left:18px;font-size:14px;line-height:1.5;color:#14532d;">
          {li_melhorou}
        </ul>
      </div>
      <div style="flex:1;min-width:260px;background:#fef2f2;border:1px solid #fecaca;
                  border-radius:14px;padding:18px 20px;">
        <div style="font-size:14px;font-weight:800;color:{_VERMELHO};margin-bottom:10px;">
          ▲ O que piorou / atenção
        </div>
        <ul style="margin:0;padding-left:18px;font-size:14px;line-height:1.5;color:#7f1d1d;">
          {li_piorou}
        </ul>
      </div>
    </div>

    <!-- Como ler -->
    <div style="background:#ffffff;border:1px solid {_CINZA_BORDA};border-radius:14px;
                padding:16px 20px;margin-top:18px;font-size:12px;color:{_CINZA};line-height:1.5;">
      <b style="color:{_AZUL};">Como ler:</b>
      O número grande no topo é sempre <b>Métrica Oficial</b> (com seta e comparativo MTD).
      A linha <b>anterior → atual</b> deixa explícito de onde vem o ▼/▲.{como_ler_total}
      O bloco <b>Distribuição por dificuldade</b> mostra Fácil / Médio / Difícil nas falhas oficiais;
      <b>Team/Category</b> classifica a origem do agente (HC → Team) em Operacional, Qualidade, Outros ou Não classificado.
      <span style="color:{_VERDE};font-weight:700;">▼ verde</span> em fáceis = menos erros evitáveis (melhora).
      <span style="color:{_VERDE};font-weight:700;">▼ verde</span> = menos falhas (melhora);
      <span style="color:{_VERMELHO};font-weight:700;">▲ vermelho</span> = mais falhas (piora).
      Comparativo = mesmos dias de calendário no mês anterior (MTD equivalente).
    </div>

    <div style="text-align:center;font-size:11px;color:#94a3b8;margin-top:18px;">
      Gerado automaticamente em {gerado_em} · Relatório executivo opcional
    </div>
  </div>
</body>
</html>"""


def build_and_save_executive_report(
    *,
    df_base: pd.DataFrame,
    df_hc: pd.DataFrame | None = None,
    localidades_alvo: dict,
    filtrar_por_localidades,
    cur_start: date,
    cur_end: date,
    out_dir: Path,
    ts: str,
    col_data: str,
    col_cenario: str,
    abrir_no_navegador: bool = True,
) -> Path | None:
    """Gera o relatório executivo comparativo entre as duas praças (BUs).

    Retorna o caminho do HTML gerado, ou None em caso de falha controlada.
    Nunca levanta exceção para não interromper o fluxo principal.
    """
    try:
        nomes = list(localidades_alvo.keys())
        if len(nomes) < 2:
            print("⚠️ Relatório executivo requer 2 praças configuradas. Pulando.")
            return None
        nome_a, nome_b = nomes[0], nomes[1]

        df_a = filtrar_por_localidades(df_base, localidades_alvo[nome_a])
        df_b = filtrar_por_localidades(df_base, localidades_alvo[nome_b])

        df_a_of = aplicar_recorte_oficial(df_a, cur_start)
        df_b_of = aplicar_recorte_oficial(df_b, cur_start)
        dual_metric_mode = uses_dual_metric_mode(cur_start)

        df_hc_safe = df_hc if df_hc is not None else pd.DataFrame()
        df_hc_a = _filtrar_hc_por_localidades(df_hc_safe, localidades_alvo[nome_a])
        df_hc_b = _filtrar_hc_por_localidades(df_hc_safe, localidades_alvo[nome_b])
        team_map_a = build_team_categoria_map(df_hc_a)
        team_map_b = build_team_categoria_map(df_hc_b)

        ka = _kpis_bu(
            df_a, df_a_of, cur_start, cur_end, col_data, col_cenario, team_map_a
        )
        kb = _kpis_bu(
            df_b, df_b_of, cur_start, cur_end, col_data, col_cenario, team_map_b
        )
        ka["tempo_casa"] = _kpis_tempo_casa(df_a_of, df_hc_a, cur_start, cur_end, col_data)
        kb["tempo_casa"] = _kpis_tempo_casa(df_b_of, df_hc_b, cur_start, cur_end, col_data)

        html = _build_html(nome_a, ka, nome_b, kb, cur_start, cur_end, dual_metric_mode=dual_metric_mode)

        out_path = Path(out_dir) / f"relatorio_EXECUTIVO_BU_{ts}.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"✅ HTML EXECUTIVO (gerência): {out_path}")

        if abrir_no_navegador:
            try:
                import webbrowser

                webbrowser.open(out_path.as_uri())
            except Exception:
                pass

        return out_path
    except Exception as e:
        print("⚠️ Não foi possível gerar o relatório executivo:", e)
        return None
