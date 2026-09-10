# -*- coding: utf-8 -*-
"""Tempo entre análise contestada e recebimento da contestação."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from report_brb.brb_filters import parse_excel_date


def _recebimento_span(de: object, ate: object) -> str:
    if de is None or ate is None or pd.isna(de) or pd.isna(ate):
        return "—"
    de_ts = pd.Timestamp(de).normalize()
    ate_ts = pd.Timestamp(ate).normalize()
    if de_ts == ate_ts:
        return de_ts.strftime("%d/%m/%Y")
    return f"{de_ts.strftime('%d/%m/%Y')} – {ate_ts.strftime('%d/%m/%Y')}"


def _subset_top_clientes(
    hist: pd.DataFrame,
    mask: pd.Series,
    *,
    top_n: int = 5,
    include_recebimento: bool = False,
) -> tuple[list[dict], int]:
    if "_client_slug" not in hist.columns:
        return [], 0
    subset = hist.loc[mask]
    if subset.empty:
        return [], 0
    nome_col = "_client_nome" if "_client_nome" in hist.columns else "_client_slug"
    if include_recebimento and "_data_referencia" in subset.columns:
        grouped = (
            subset.groupby(["_client_slug", nome_col], dropna=False)
            .agg(
                qtd=("_data_referencia", "size"),
                recebimento_de=("_data_referencia", "min"),
                recebimento_ate=("_data_referencia", "max"),
            )
            .reset_index()
            .sort_values("qtd", ascending=False)
        )
    else:
        grouped = (
            subset.groupby(["_client_slug", nome_col], dropna=False)
            .size()
            .reset_index(name="qtd")
            .sort_values("qtd", ascending=False)
        )
    total_clientes = len(grouped)
    top = grouped.head(top_n)
    clientes: list[dict] = []
    for _, row in top.iterrows():
        item = {
            "slug": str(row["_client_slug"]),
            "nome": str(row[nome_col]),
            "qtd": int(row["qtd"]),
        }
        if include_recebimento and "recebimento_de" in row.index:
            item["recebimento"] = _recebimento_span(row["recebimento_de"], row["recebimento_ate"])
        clientes.append(item)
    outros = max(0, total_clientes - len(top))
    return clientes, outros


def _faixa_top_clientes(
    hist: pd.DataFrame,
    mask: pd.Series,
    *,
    top_n: int = 5,
) -> tuple[list[dict], int]:
    return _subset_top_clientes(hist, mask, top_n=top_n, include_recebimento=False)


def contestacao_temporal(df: pd.DataFrame | None, generated_at: datetime) -> dict:
    """Tempo entre a análise contestada e o recebimento desde janeiro/2026."""
    empty = {
        "total": 0,
        "protocolos": 0,
        "falhas": 0,
        "protocolos_falha": 0,
        "taxa": 0.0,
        "mediana_dias": 0,
        "mais_90": 0,
        "pct_mais_90": 0.0,
        "inicio_ref": "—",
        "fim_ref": "—",
        "inicio_analise": "—",
        "fim_analise": "—",
        "analises_2026": 0,
        "protocolos_2026": 0,
        "analises_anteriores_2026": 0,
        "protocolos_anteriores_2026": 0,
        "falhas_anteriores_2026": 0,
        "faixas": [],
        "anos": [],
        "meses": [],
        "antigos": [],
    }
    if (
        df is None
        or df.empty
        or "Data de Análise" not in df.columns
        or "Data" not in df.columns
    ):
        return empty

    hist = df.copy()
    hist["_data_analise"] = parse_excel_date(hist["Data de Análise"])
    hist["_data_referencia"] = parse_excel_date(hist["Data"])
    inicio_ref = pd.Timestamp("2026-01-01")
    fim_ref = pd.Timestamp(generated_at).tz_localize(None).normalize()
    hist = hist[
        hist["_data_analise"].notna()
        & hist["_data_referencia"].notna()
        & hist["_data_referencia"].dt.normalize().between(inicio_ref, fim_ref)
    ].copy()
    if hist.empty:
        return empty

    hist["_idade_dias"] = (
        hist["_data_referencia"].dt.tz_localize(None).dt.normalize()
        - hist["_data_analise"].dt.tz_localize(None).dt.normalize()
    ).dt.days.clip(lower=0)
    falha_mask = hist.get(
        "classificacao_conforme", pd.Series("", index=hist.index)
    ).eq("falha")
    hist["_falha"] = falha_mask.astype(int)
    protocolos_validos = hist.get(
        "_protocolo_norm", pd.Series("", index=hist.index)
    ).fillna("").astype(str).str.strip()
    hist["_protocolo_tempo"] = protocolos_validos

    atuais_mask = hist["_data_analise"].ge(inicio_ref)
    anteriores_mask = ~atuais_mask
    analises_2026 = int(atuais_mask.sum())
    protocolos_2026 = int(
        hist.loc[atuais_mask & protocolos_validos.ne(""), "_protocolo_tempo"].nunique()
    )
    analises_anteriores_2026 = int(anteriores_mask.sum())
    protocolos_anteriores_2026 = int(
        hist.loc[anteriores_mask & protocolos_validos.ne(""), "_protocolo_tempo"].nunique()
    )
    falhas_anteriores_2026 = int(hist.loc[anteriores_mask, "_falha"].sum())

    total = len(hist)
    protocolos = int(protocolos_validos[protocolos_validos.ne("")].nunique())
    falhas = int(falha_mask.sum())
    protocolos_falha = int(
        hist.loc[falha_mask & protocolos_validos.ne(""), "_protocolo_tempo"].nunique()
    )

    definicoes = (
        ("Até 30 dias", 0, 30),
        ("31–90 dias", 31, 90),
        ("91–180 dias", 91, 180),
        ("181–365 dias", 181, 365),
        ("1–2 anos", 366, 730),
        ("Mais de 2 anos", 731, None),
    )
    faixas = []
    for label, minimo, maximo in definicoes:
        mask = hist["_idade_dias"].ge(minimo)
        if maximo is not None:
            mask &= hist["_idade_dias"].le(maximo)
        qtd = int(mask.sum())
        clientes, outros_clientes = _faixa_top_clientes(hist, mask)
        faixas.append(
            {
                "label": label,
                "qtd": qtd,
                "pct": round(100.0 * qtd / total, 1) if total else 0.0,
                "clientes": clientes,
                "outros_clientes": outros_clientes,
            }
        )

    hist["_ano"] = hist["_data_analise"].dt.year.astype(int)
    anos = []
    for ano, grp in hist.groupby("_ano", sort=True):
        grp_protocolos = grp["_protocolo_tempo"]
        qtd_analises = len(grp)
        qtd_falhas = int(grp["_falha"].sum())
        qtd_acertos = qtd_analises - qtd_falhas
        ano_mask = hist["_ano"].eq(int(ano))
        clientes, outros_clientes = _subset_top_clientes(
            hist,
            ano_mask,
            include_recebimento=True,
        )
        anos.append(
            {
                "ano": int(ano),
                "analises": qtd_analises,
                "protocolos": int(grp_protocolos[grp_protocolos.ne("")].nunique()),
                "falhas": qtd_falhas,
                "procedentes": qtd_falhas,
                "improcedentes": qtd_acertos,
                "taxa": round(100.0 * qtd_falhas / qtd_analises, 1) if qtd_analises else 0.0,
                "participacao": round(100.0 * qtd_analises / total, 1) if total else 0.0,
                "clientes": clientes,
                "outros_clientes": outros_clientes,
            }
        )

    meses_pt = (
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    )
    hist["_mes"] = hist["_data_analise"].dt.to_period("M")
    meses = []
    for mes, grp in hist.groupby("_mes", sort=True):
        grp_protocolos = grp["_protocolo_tempo"]
        qtd_falhas = int(grp["_falha"].sum())
        taxa_mes = round(100.0 * qtd_falhas / len(grp), 1) if len(grp) else 0.0
        meses.append(
            {
                "mes": f"{meses_pt[mes.month - 1].capitalize()} {mes.year}",
                "analises": len(grp),
                "protocolos": int(grp_protocolos[grp_protocolos.ne("")].nunique()),
                "falhas": qtd_falhas,
                "taxa": taxa_mes,
                "participacao": round(100.0 * len(grp) / total, 1) if total else 0.0,
                "base_baixa": len(grp) < 10,
                "parcial": bool(
                    mes.year == fim_ref.year
                    and mes.month == fim_ref.month
                    and fim_ref.day < fim_ref.days_in_month
                ),
            }
        )

    antigos = []
    col_tipo = "Tipo de falha" if "Tipo de falha" in hist.columns else None
    col_etapa = "Etapa" if "Etapa" in hist.columns else None
    for _, row in hist.sort_values(
        ["_idade_dias", "_data_analise"], ascending=[False, True]
    ).head(12).iterrows():
        idade = int(row["_idade_dias"])
        if idade >= 365:
            anos_idade, resto_dias = divmod(idade, 365)
            idade_label = f"{anos_idade} ano" + ("s" if anos_idade != 1 else "")
            if resto_dias:
                idade_label += f" e {resto_dias} dias"
        else:
            idade_label = f"{idade} dias"

        def _cell(col: str | None) -> str:
            if not col:
                return "—"
            value = row.get(col)
            text = "" if value is None or pd.isna(value) else str(value).strip()
            return "—" if not text or text.lower() in {"nan", "<na>"} or text == "�" else text

        antigos.append(
            {
                "protocolo": _cell("Protocolo"),
                "data_analise": row["_data_analise"].strftime("%d/%m/%Y"),
                "data_referencia": row["_data_referencia"].strftime("%d/%m/%Y"),
                "idade": idade_label,
                "resultado": "Falha confirmada" if row.get("_falha", 0) else "Sem falha confirmada",
                "tipo": _cell(col_tipo),
                "etapa": _cell(col_etapa),
            }
        )

    return {
        "total": total,
        "protocolos": protocolos,
        "falhas": falhas,
        "protocolos_falha": protocolos_falha,
        "taxa": round(100.0 * falhas / total, 1) if total else 0.0,
        "mediana_dias": int(round(float(hist["_idade_dias"].median()))),
        "mais_90": int(hist["_idade_dias"].gt(90).sum()),
        "pct_mais_90": float(round(100.0 * hist["_idade_dias"].gt(90).sum() / total, 1)),
        "inicio_ref": hist["_data_referencia"].min().strftime("%d/%m/%Y"),
        "fim_ref": hist["_data_referencia"].max().strftime("%d/%m/%Y"),
        "inicio_analise": hist["_data_analise"].min().strftime("%d/%m/%Y"),
        "fim_analise": hist["_data_analise"].max().strftime("%d/%m/%Y"),
        "analises_2026": analises_2026,
        "protocolos_2026": protocolos_2026,
        "analises_anteriores_2026": analises_anteriores_2026,
        "protocolos_anteriores_2026": protocolos_anteriores_2026,
        "falhas_anteriores_2026": falhas_anteriores_2026,
        "faixas": faixas,
        "anos": anos,
        "meses": meses,
        "antigos": antigos,
    }
