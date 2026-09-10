# -*- coding: utf-8 -*-
"""Analytics avançado: cruzamentos, reincidência, temporal, qualidade dos dados."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from report_brb.brb_classify import (
    CATEGORIA_RECOMENDACOES,
    classificar_categoria_falha,
    classificar_severidade,
    categoria_label,
    severidade_label,
)
from report_brb.brb_filters import parse_excel_date, na_effective_date
from report_brb.brb_loaders import BRBDataBundle
from report_brb.brb_normalize import is_empty_value, norm_demanda, padronizar_descricao
from report_brb.brb_recommendations import Recomendacao, gerar_recomendacoes
from report_brb.brb_segmento import fill_segmento_vazio


@dataclass
class BRBAnalytics:
    """Resultado consolidado das análises operacionais."""

    base_consolidada: pd.DataFrame = field(default_factory=pd.DataFrame)
    cruzamentos: dict = field(default_factory=dict)
    qualidade: pd.DataFrame = field(default_factory=pd.DataFrame)
    alertas_qualidade: list[dict] = field(default_factory=list)
    temporal: dict = field(default_factory=dict)
    temporal_avisos: list[str] = field(default_factory=list)
    top_categorias: pd.DataFrame = field(default_factory=pd.DataFrame)
    top_agentes: pd.DataFrame = field(default_factory=pd.DataFrame)
    top_protocolos: pd.DataFrame = field(default_factory=pd.DataFrame)
    top_demandas: pd.DataFrame = field(default_factory=pd.DataFrame)
    por_severidade: pd.DataFrame = field(default_factory=pd.DataFrame)
    categoria_severidade: pd.DataFrame = field(default_factory=pd.DataFrame)
    top_descricoes: pd.DataFrame = field(default_factory=pd.DataFrame)
    reincidencia_agentes: pd.DataFrame = field(default_factory=pd.DataFrame)
    reincidencia_protocolos: pd.DataFrame = field(default_factory=pd.DataFrame)
    reincidencia_demandas: pd.DataFrame = field(default_factory=pd.DataFrame)
    reincidencia_categorias: pd.DataFrame = field(default_factory=pd.DataFrame)
    reincidencia_mes: pd.DataFrame = field(default_factory=pd.DataFrame)
    treinamento_pos_falha: pd.DataFrame = field(default_factory=pd.DataFrame)
    fg_sem_match_na: pd.DataFrame = field(default_factory=pd.DataFrame)
    fg_sem_contestacao: pd.DataFrame = field(default_factory=pd.DataFrame)
    multi_matricula: pd.DataFrame = field(default_factory=pd.DataFrame)
    dados_incompletos: pd.DataFrame = field(default_factory=pd.DataFrame)
    sem_categoria_severidade: pd.DataFrame = field(default_factory=pd.DataFrame)
    sequencia_protocolos: pd.DataFrame = field(default_factory=pd.DataFrame)
    sequencia_resumo: dict = field(default_factory=dict)
    recomendacoes: list[Recomendacao] = field(default_factory=list)
    prioridades: list[str] = field(default_factory=list)
    oportunidades: list[str] = field(default_factory=list)
    acao_agora: list[str] = field(default_factory=list)
    resumo_executivo: str = ""


def _mes_label(dt_series: pd.Series) -> pd.Series:
    parsed = parse_excel_date(dt_series)
    ok = parsed.notna()
    out = pd.Series(index=dt_series.index, dtype=object)
    out.loc[ok] = parsed.loc[ok].dt.strftime("%Y-%m")
    out.loc[~ok] = None
    return out


def _pct(n: int, total: int) -> float:
    return round(100 * n / total, 1) if total else 0.0


def _fg_descricao_row(row: pd.Series) -> str:
    parts = []
    for c in ("Novo cenário", "Cenário", "Tipo de Falha", "Tendência"):
        if c in row.index and not is_empty_value(row.get(c)):
            parts.append(str(row[c]))
    return " | ".join(parts) if parts else ""


def enrich_falhas_gerais(fg: pd.DataFrame) -> pd.DataFrame:
    if fg.empty:
        return fg
    out = fill_segmento_vazio(fg, inplace_oficial=True)
    tend_col = next((c for c in out.columns if "end" in str(c).lower()), None)
    desc_raw = out.apply(_fg_descricao_row, axis=1)
    out["descricao_padrao"] = desc_raw.map(padronizar_descricao)
    out["categoria_macro"] = out.apply(
        lambda r: classificar_categoria_falha(
            _fg_descricao_row(r),
            str(r.get(tend_col, "")) if tend_col else "",
            str(r.get("fn_fp", "")),
        ),
        axis=1,
    )
    out["severidade"] = out.apply(classificar_severidade, axis=1)
    if "Data de Análise" in out.columns:
        out["mes_ref"] = _mes_label(out["Data de Análise"])
    else:
        out["mes_ref"] = None
    return out


def enrich_na_falhas(na: pd.DataFrame) -> pd.DataFrame:
    if na.empty:
        return na
    out = na.copy()
    if "MOTIVO DA FALHA" in out.columns:
        out["descricao_padrao"] = out["MOTIVO DA FALHA"].map(padronizar_descricao)
        out["categoria_macro"] = out["MOTIVO DA FALHA"].map(
            lambda x: classificar_categoria_falha(str(x))
        )
        out["severidade"] = out.apply(classificar_severidade, axis=1)
    if "DEMANDA" in out.columns:
        out["chave_demanda"] = out["DEMANDA"].map(norm_demanda)
    eff = na_effective_date(out)
    if eff.notna().any():
        out["mes_ref"] = _mes_label(eff)
    return out


def build_base_consolidada(bundle: BRBDataBundle) -> pd.DataFrame:
    """FG enriquecido com flags de cruzamento NA / Contestação / Treinamento."""
    fg = enrich_falhas_gerais(bundle.falhas_gerais)
    if fg.empty:
        return fg

    na_protos = set(bundle.na_falhas["_protocolo_norm"]) if not bundle.na_falhas.empty else set()
    cont_keys = set(bundle.contestacao["chave_caso"]) if not bundle.contestacao.empty else set()

    agent_trein = set()
    if not bundle.treinamentos.empty and "Agent: UserLanID" in bundle.treinamentos.columns:
        agent_trein = set(bundle.treinamentos["Agent: UserLanID"].astype(str).str.lower())

    out = fg.copy()
    out["match_na"] = out["_protocolo_norm"].isin(na_protos)
    out["avaliado_contestacao"] = out["chave_caso"].isin(cont_keys)
    out["sem_avaliacao_contestacao"] = ~out["avaliado_contestacao"]
    out["agente_treinado"] = out["_matricula_norm"].isin(agent_trein) | out.get("Nome Agente", pd.Series()).astype(
        str
    ).str.lower().isin(agent_trein)
    out["origem_base"] = "Falhas_Gerais-BRB"
    if "chave_demanda" not in out.columns or out["chave_demanda"].eq("").all():
        if not bundle.na_falhas.empty and "DEMANDA" in bundle.na_falhas.columns:
            dem_map = (
                bundle.na_falhas.groupby("_protocolo_norm")["DEMANDA"]
                .agg(lambda s: next((x for x in s if not is_empty_value(x)), ""))
            )
            out["chave_demanda"] = out["_protocolo_norm"].map(
                lambda p: norm_demanda(dem_map.get(p, "")) if p in dem_map.index else ""
            )
        else:
            out["chave_demanda"] = ""
    return out


def _quality_metric(label: str, n_bad: int, total: int, warn_pct: float = 5.0, crit_pct: float = 15.0) -> dict:
    pct = _pct(n_bad, total)
    if pct >= crit_pct:
        nivel = "vermelho"
    elif pct >= warn_pct:
        nivel = "amarelo"
    else:
        nivel = "verde"
    return {"metrica": label, "quantidade": n_bad, "total": total, "percentual": pct, "nivel": nivel}


def compute_qualidade(bundle: BRBDataBundle, base: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    rows = []
    fg = bundle.falhas_gerais
    na = bundle.na_falhas
    cont = bundle.contestacao
    total_fg = len(fg) if not fg.empty else 0

    if total_fg:
        rows.append(_quality_metric("Protocolos vazios (FG)", int((fg["_protocolo_norm"] == "").sum()), total_fg))
        rows.append(_quality_metric("Matrículas vazias (FG)", int(fg["matricula_ausente"].sum()), total_fg))
        rows.append(
            _quality_metric(
                "Agentes vazios (FG)",
                int(fg.get("Nome Agente", pd.Series(dtype=str)).map(is_empty_value).sum()),
                total_fg,
            )
        )
        if "Data de Análise" in fg.columns:
            dt = parse_excel_date(fg["Data de Análise"])
            rows.append(_quality_metric("Datas inválidas FG", int(dt.isna().sum()), total_fg, 10, 25))
        rows.append(_quality_metric("Duplicatas chave_caso", bundle.fg_duplicatas, bundle.fg_linhas_brutas or total_fg))
        if not base.empty:
            rows.append(
                _quality_metric(
                    "Sem categoria (OUTROS)",
                    int((base["categoria_macro"] == "OUTROS").sum()),
                    len(base),
                    15,
                    30,
                )
            )
            rows.append(
                _quality_metric(
                    "Severidade indefinida",
                    int((base["severidade"] == "INDEFINIDA").sum()),
                    len(base),
                    10,
                    25,
                )
            )
            rows.append(
                _quality_metric(
                    "FG sem match NA",
                    int((~base["match_na"]).sum()),
                    len(base),
                    10,
                    25,
                )
            )
            rows.append(
                _quality_metric(
                    "FG sem avaliação Contestação",
                    int(base["sem_avaliacao_contestacao"].sum()),
                    len(base),
                    10,
                    25,
                )
            )

    if not na.empty:
        total_na = len(na)
        rows.append(
            _quality_metric(
                "Demanda vazia (NA)",
                int(na.get("DEMANDA", pd.Series(dtype=str)).map(is_empty_value).sum()),
                total_na,
            )
        )

    if not cont.empty:
        total_c = len(cont)
        rows.append(
            _quality_metric(
                "CONFORME indefinido",
                int((cont["classificacao_conforme"] == "indefinido").sum()),
                total_c,
            )
        )

    df = pd.DataFrame(rows)
    alertas = [
        {
            "nivel": r["nivel"],
            "texto": f"{r['metrica']}: {r['quantidade']} ({r['percentual']}%)",
        }
        for r in rows
        if r["nivel"] in ("amarelo", "vermelho")
    ]
    return df, alertas


def compute_cruzamentos(base: pd.DataFrame, bundle: BRBDataBundle) -> dict:
    fg_n = len(base)
    protos_fg = int(base["_protocolo_norm"].nunique()) if not base.empty else 0
    na_n = len(bundle.na_falhas)
    na_protos = int(bundle.na_falhas["_protocolo_norm"].nunique()) if not bundle.na_falhas.empty else 0

    fg_com_na = int(base["match_na"].sum()) if not base.empty else 0
    fg_sem_na = fg_n - fg_com_na
    # O vínculo é feito por protocolo; mantenha também a leitura por protocolo
    # único para que os percentuais não sejam inflados por etapas/agentes.
    fg_protocols_with_na = int(
        base.loc[base["match_na"], "_protocolo_norm"].replace("", pd.NA).dropna().nunique()
    ) if not base.empty else 0
    fg_protocols_without_na = max(0, protos_fg - fg_protocols_with_na)
    fg_avaliados = int(base["avaliado_contestacao"].sum()) if not base.empty else 0
    fg_sem_av = fg_n - fg_avaliados

    conforme = bundle.contestacao
    sim = int((conforme["classificacao_conforme"] == "nao_falha").sum()) if not conforme.empty else 0
    nao = int((conforme["classificacao_conforme"] == "falha").sum()) if not conforme.empty else 0
    total_dec = sim + nao
    taxa_conf = _pct(sim, total_dec)

    tre = bundle.treinamentos
    tre_n = len(tre)
    tre_ag = int(tre["Agent: UserLanID"].nunique()) if not tre.empty and "Agent: UserLanID" in tre.columns else 0

    return {
        "casos_fg": fg_n,
        "protocolos_fg": protos_fg,
        "falhas_na": na_n,
        "protocolos_na": na_protos,
        "fg_com_match_na": fg_com_na,
        "fg_sem_match_na": fg_sem_na,
        "pct_fg_com_na": _pct(fg_com_na, fg_n),
        "pct_fg_sem_na": _pct(fg_sem_na, fg_n),
        "fg_protocolos_com_match_na": fg_protocols_with_na,
        "fg_protocolos_sem_match_na": fg_protocols_without_na,
        "pct_protocolos_fg_com_na": _pct(fg_protocols_with_na, protos_fg),
        "pct_protocolos_fg_sem_na": _pct(fg_protocols_without_na, protos_fg),
        "fg_com_contestacao": fg_avaliados,
        "fg_sem_contestacao": fg_sem_av,
        "pct_fg_avaliados": _pct(fg_avaliados, fg_n),
        "pct_fg_sem_av": _pct(fg_sem_av, fg_n),
        "conforme_sim": sim,
        "conforme_nao": nao,
        "taxa_conformidade": taxa_conf,
        "treinamentos": tre_n,
        "agentes_treinados": tre_ag,
    }


def _min_date_by_proto(df: pd.DataFrame, proto_col: str, date_col: str) -> pd.Series:
    """Retorna Series protocolo -> data mínima válida."""
    if df.empty or proto_col not in df.columns or date_col not in df.columns:
        return pd.Series(dtype="datetime64[ns]")
    work = df[[proto_col, date_col]].copy()
    work[proto_col] = work[proto_col].fillna("").astype(str).str.strip()
    work = work[work[proto_col] != ""]
    if work.empty:
        return pd.Series(dtype="datetime64[ns]")
    work["_dt"] = parse_excel_date(work[date_col])
    work = work.dropna(subset=["_dt"])
    if work.empty:
        return pd.Series(dtype="datetime64[ns]")
    return work.groupby(proto_col)["_dt"].min()


def _na_notify_date_series(na: pd.DataFrame) -> pd.Series:
    """Data de notificação preferencial: DATA DE NOTIFICAÇÃO se válida; senão DATA DE CADASTRO."""
    if na.empty or "_protocolo_norm" not in na.columns:
        return pd.Series(dtype="datetime64[ns]")
    work = na.copy()
    work["_protocolo_norm"] = work["_protocolo_norm"].fillna("").astype(str).str.strip()
    work = work[work["_protocolo_norm"] != ""]
    if work.empty:
        return pd.Series(dtype="datetime64[ns]")

    cad = (
        parse_excel_date(work["DATA DE CADASTRO"])
        if "DATA DE CADASTRO" in work.columns
        else pd.Series(pd.NaT, index=work.index)
    )
    notif = (
        parse_excel_date(work["DATA DE NOTIFICAÇÃO"])
        if "DATA DE NOTIFICAÇÃO" in work.columns
        else pd.Series(pd.NaT, index=work.index)
    )
    work["_dt"] = notif.where(notif.notna(), cad)
    work = work.dropna(subset=["_dt"])
    if work.empty:
        return pd.Series(dtype="datetime64[ns]")
    return work.groupby("_protocolo_norm")["_dt"].min()


def _classify_protocol_sequence(row: pd.Series) -> str:
    has_fg = bool(row.get("has_fg"))
    has_na = bool(row.get("has_na"))
    has_cont = bool(row.get("has_cont"))
    dt_fg = row.get("dt_fg")
    dt_na = row.get("dt_na")
    dt_cont = row.get("dt_cont")

    n_elos = int(has_fg) + int(has_na) + int(has_cont)
    if n_elos == 0:
        return "parcial"
    if n_elos == 1:
        return "parcial"

    # Elos presentes sem data válida
    if has_fg and pd.isna(dt_fg):
        return "datas_incompletas"
    if has_na and pd.isna(dt_na):
        return "datas_incompletas"
    if has_cont and pd.isna(dt_cont):
        return "datas_incompletas"

    if (has_fg or has_na) and not has_cont:
        return "sem_contestacao"

    # Tem contestação + pelo menos um outro elo
    if has_fg and has_na and has_cont:
        if dt_fg <= dt_na <= dt_cont:
            return "completo_esperado"
        return "ordem_invertida"

    # Dois elos incluindo contestação
    if has_na and has_cont and not has_fg:
        if dt_na <= dt_cont:
            return "parcial"  # fluxo parcial coerente NA→Cont
        return "ordem_invertida"
    if has_fg and has_cont and not has_na:
        if dt_fg <= dt_cont:
            return "parcial"
        return "ordem_invertida"
    if has_fg and has_na and not has_cont:
        return "sem_contestacao"

    return "parcial"


def compute_protocol_sequence(bundle: BRBDataBundle) -> tuple[pd.DataFrame, dict]:
    """
    Cruzamento temporal por protocolo: auditoria (FG) → notificação (Qualidade→CS) → contestação.

    Datas: min por protocolo no período.
    Classes: completo_esperado, sem_contestacao, ordem_invertida, parcial, datas_incompletas.
    """
    empty_cols = [
        "Protocolo",
        "dt_fg",
        "dt_na",
        "dt_cont",
        "has_fg",
        "has_na",
        "has_cont",
        "classe",
    ]
    empty = pd.DataFrame(columns=empty_cols)
    empty_resumo = {
        "n_protocolos": 0,
        "n_completo_esperado": 0,
        "n_sem_contestacao": 0,
        "n_ordem_invertida": 0,
        "n_parcial": 0,
        "n_datas_incompletas": 0,
        "n_fg_com_na": 0,
        "n_na_sem_fg": 0,
        "n_fg_e_na": 0,
        "n_fg_na_com_cont_esperada": 0,
    }

    fg = bundle.falhas_gerais
    na = bundle.na_falhas
    cont = bundle.contestacao

    def _proto_set(df: pd.DataFrame) -> set[str]:
        if df.empty or "_protocolo_norm" not in df.columns:
            return set()
        vals = df["_protocolo_norm"].fillna("").astype(str).str.strip()
        return set(vals[vals != ""])

    fg_protos = _proto_set(fg)
    na_protos = _proto_set(na)
    cont_protos = _proto_set(cont)

    fg_dates = _min_date_by_proto(fg, "_protocolo_norm", "Data de Análise")
    na_dates = _na_notify_date_series(na)
    cont_dates = _min_date_by_proto(cont, "_protocolo_norm", "Data de Análise")

    protos = sorted(fg_protos | na_protos | cont_protos)
    if not protos:
        return empty, empty_resumo

    rows = []
    for p in protos:
        has_fg = p in fg_protos
        has_na = p in na_protos
        has_cont = p in cont_protos
        dt_fg = fg_dates.get(p, pd.NaT) if p in fg_dates.index else pd.NaT
        dt_na = na_dates.get(p, pd.NaT) if p in na_dates.index else pd.NaT
        dt_cont = cont_dates.get(p, pd.NaT) if p in cont_dates.index else pd.NaT
        row = {
            "Protocolo": p,
            "dt_fg": dt_fg,
            "dt_na": dt_na,
            "dt_cont": dt_cont,
            "has_fg": has_fg,
            "has_na": has_na,
            "has_cont": has_cont,
        }
        row["classe"] = _classify_protocol_sequence(row)
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return empty, empty_resumo

    n_fg_com_na = len(fg_protos & na_protos)
    n_na_sem_fg = len(na_protos - fg_protos)
    n_esperado_entre_fg_na = int(
        ((df["has_fg"]) & (df["has_na"]) & (df["classe"] == "completo_esperado")).sum()
    )

    resumo = {
        "n_protocolos": len(df),
        "n_completo_esperado": int((df["classe"] == "completo_esperado").sum()),
        "n_sem_contestacao": int((df["classe"] == "sem_contestacao").sum()),
        "n_ordem_invertida": int((df["classe"] == "ordem_invertida").sum()),
        "n_parcial": int((df["classe"] == "parcial").sum()),
        "n_datas_incompletas": int((df["classe"] == "datas_incompletas").sum()),
        "n_fg_com_na": n_fg_com_na,
        "n_na_sem_fg": n_na_sem_fg,
        "n_fg_e_na": n_fg_com_na,
        "n_fg_na_com_cont_esperada": n_esperado_entre_fg_na,
    }
    return df.sort_values(["classe", "Protocolo"]).reset_index(drop=True), resumo


def _monthly_counts(df: pd.DataFrame, date_col: str, label: str) -> tuple[dict, str | None]:
    if df.empty or date_col not in df.columns:
        return {}, f"Não foi possível calcular evolução temporal de {label} — coluna de data ausente."
    mes = _mes_label(df[date_col])
    if mes.isna().all():
        return {}, f"Não foi possível calcular evolução temporal de {label} — ausência de data válida."
    return mes.value_counts().sort_index().astype(int).to_dict(), None


def compute_temporal(bundle: BRBDataBundle, base: pd.DataFrame) -> tuple[dict, list[str]]:
    avisos: list[str] = []
    temporal: dict = {}

    d, msg = _monthly_counts(bundle.falhas_gerais, "Data de Análise", "Falhas Gerais")
    temporal["fg_por_mes"] = d
    if msg:
        avisos.append(msg)

    if bundle.na_falhas.empty:
        temporal["na_por_mes"] = {}
        avisos.append("Não foi possível calcular evolução temporal de NA_Falhas — base vazia.")
    else:
        na_eff = na_effective_date(bundle.na_falhas)
        mes = _mes_label(na_eff)
        if mes.isna().all():
            temporal["na_por_mes"] = {}
            avisos.append(
                "Não foi possível calcular evolução temporal de NA_Falhas — ausência de data válida."
            )
        else:
            temporal["na_por_mes"] = mes.value_counts().sort_index().astype(int).to_dict()

    d, msg = _monthly_counts(bundle.contestacao, "Data", "Contestação recebida")
    temporal["contestacao_por_mes"] = d
    if msg:
        avisos.append(msg)

    ref_tre = "Session: StartDate" if "Session: StartDate" in bundle.treinamentos.columns else "AssignmentDate"
    d, msg = _monthly_counts(bundle.treinamentos, ref_tre, "Treinamentos")
    temporal["treinamentos_por_mes"] = d
    if msg and not bundle.treinamentos.empty:
        avisos.append(msg)

    # Conformidade mensal
    if not bundle.contestacao.empty and "Data" in bundle.contestacao.columns:
        c = bundle.contestacao.copy()
        c["mes_ref"] = _mes_label(c["Data"])
        c = c[c["mes_ref"].notna()]
        if not c.empty:
            g = c.groupby("mes_ref")["classificacao_conforme"].apply(
                lambda s: _pct(int((s == "nao_falha").sum()), int(s.isin(["nao_falha", "falha"]).sum()))
            )
            temporal["taxa_conformidade_mes"] = g.to_dict()

    if not base.empty and "mes_ref" in base.columns:
        bm = base[base["mes_ref"].notna()]
        if not bm.empty:
            temporal["categoria_por_mes"] = (
                bm.groupby(["mes_ref", "categoria_macro"]).size().unstack(fill_value=0).astype(int).to_dict()
            )
            temporal["severidade_por_mes"] = (
                bm.groupby(["mes_ref", "severidade"]).size().unstack(fill_value=0).astype(int).to_dict()
            )
            if "fn_fp" in bm.columns:
                temporal["fn_fp_por_mes"] = (
                    bm.groupby(["mes_ref", "fn_fp"]).size().unstack(fill_value=0).astype(int).to_dict()
                )
            temporal["reincidencia_mes"] = bm.groupby("mes_ref").size().astype(int).to_dict()

    return temporal, avisos


def _ranking(df: pd.DataFrame, col: str, label_col: str, n: int = 10) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return pd.DataFrame(columns=[label_col, "Quantidade"])
    s = df[df[col].astype(str).str.len() > 0][col].value_counts().head(n).reset_index()
    s.columns = [label_col, "Quantidade"]
    return s


def compute_reincidencia(base: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if base.empty:
        empty = pd.DataFrame()
        return {
            "agentes": empty,
            "protocolos": empty,
            "demandas": empty,
            "categorias": empty,
            "mes": empty,
        }

    agent_col = "Nome Agente" if "Nome Agente" in base.columns else "_matricula_norm"
    ag = base.groupby(agent_col).size().reset_index(name="falhas")
    ag = ag[ag["falhas"] > 1].sort_values("falhas", ascending=False).head(20)
    ag.columns = ["Agente", "Falhas"]

    pr = base.groupby("_protocolo_norm").size().reset_index(name="falhas")
    pr = pr[pr["falhas"] > 1].sort_values("falhas", ascending=False).head(20)
    pr.columns = ["Protocolo", "Ocorrências"]

    dem = pd.DataFrame()
    if "chave_demanda" in base.columns:
        dem = _ranking(base[base["chave_demanda"] != ""], "chave_demanda", "Demanda QI")

    cat = _ranking(base, "categoria_macro", "Categoria", 15)

    mes = pd.DataFrame()
    if "mes_ref" in base.columns:
        mes = base["mes_ref"].value_counts().sort_index().reset_index()
        mes.columns = ["Mês", "Casos"]

    return {"agentes": ag, "protocolos": pr, "demandas": dem, "categorias": cat, "mes": mes}


def compute_treinamento_pos_falha(bundle: BRBDataBundle, base: pd.DataFrame) -> pd.DataFrame:
    """Agentes treinados que continuaram com falha após a data do treinamento."""
    tre = bundle.treinamentos
    if tre.empty or base.empty:
        return pd.DataFrame(columns=["Agente", "Treinamentos", "Falhas pós-treino"])

    ref = "Session: StartDate" if "Session: StartDate" in tre.columns else "AssignmentDate"
    if ref not in tre.columns:
        return pd.DataFrame(columns=["Agente", "Treinamentos", "Falhas pós-treino"])

    tre = tre.copy()
    tre["dt_trein"] = parse_excel_date(tre[ref])
    agent_col = "Agent: UserLanID"
    if agent_col not in tre.columns:
        return pd.DataFrame(columns=["Agente", "Treinamentos", "Falhas pós-treino"])

    ultimo_tre = tre.groupby(agent_col)["dt_trein"].max().reset_index()
    fg = base.copy()
    if "Data de Análise" not in fg.columns:
        return pd.DataFrame(columns=["Agente", "Treinamentos", "Falhas pós-treino"])
    fg["dt_falha"] = parse_excel_date(fg["Data de Análise"])
    mat_col = "_matricula_norm"
    rows = []
    for _, t in ultimo_tre.iterrows():
        ag = str(t[agent_col]).lower()
        mask = (fg[mat_col].astype(str).str.lower() == ag) | (
            fg.get("Nome Agente", pd.Series(dtype=str)).astype(str).str.lower() == ag
        )
        pos = fg[mask & (fg["dt_falha"] > t["dt_trein"])]
        if len(pos):
            rows.append(
                {
                    "Agente": ag,
                    "Treinamentos": int((tre[agent_col].astype(str).str.lower() == ag).sum()),
                    "Falhas pós-treino": len(pos),
                }
            )
    return pd.DataFrame(rows).sort_values("Falhas pós-treino", ascending=False).head(20)


def _trace_table(df: pd.DataFrame, cols: list[tuple[str, str]], limit: int = 20) -> pd.DataFrame:
    if df.empty:
        return df
    use = [c for c, _ in cols if c in df.columns]
    if not use:
        return pd.DataFrame()
    return df[use].head(limit)


TRACE_COLS = [
    ("_protocolo_norm", "Protocolo"),
    ("_matricula_norm", "Matrícula"),
    ("Nome Agente", "Agente"),
    ("chave_demanda", "Demanda"),
    ("Data de Análise", "Data"),
    ("categoria_macro", "Categoria"),
    ("severidade", "Severidade"),
    ("origem_base", "Origem"),
    ("descricao_padrao", "Descrição"),
]


def compute_analytics(bundle: BRBDataBundle) -> BRBAnalytics:
    """Pipeline principal de analytics operacional."""
    a = BRBAnalytics()
    base = build_base_consolidada(bundle)
    a.base_consolidada = base

    a.cruzamentos = compute_cruzamentos(base, bundle)
    a.qualidade, a.alertas_qualidade = compute_qualidade(bundle, base)
    a.temporal, a.temporal_avisos = compute_temporal(bundle, base)

    seq_df, seq_resumo = compute_protocol_sequence(bundle)
    a.sequencia_protocolos = seq_df
    a.sequencia_resumo = seq_resumo
    a.cruzamentos.update({f"seq_{k}": v for k, v in seq_resumo.items()})

    if not base.empty:
        a.top_categorias = _ranking(base, "categoria_macro", "Categoria", 10)
        if not a.top_categorias.empty:
            a.top_categorias["Categoria"] = a.top_categorias["Categoria"].map(categoria_label)
        a.top_agentes = _ranking(base, "Nome Agente", "Agente", 10)
        a.top_protocolos = _ranking(base, "_protocolo_norm", "Protocolo", 10)
        a.por_severidade = _ranking(base, "severidade", "Severidade", 10)
        if not a.por_severidade.empty:
            a.por_severidade["Severidade"] = a.por_severidade["Severidade"].map(severidade_label)
        a.top_descricoes = _ranking(base, "descricao_padrao", "Descrição", 10)

        if "chave_demanda" in base.columns:
            a.top_demandas = _ranking(base[base["chave_demanda"] != ""], "chave_demanda", "Demanda", 10)

        a.categoria_severidade = (
            base.groupby(["categoria_macro", "severidade"]).size().reset_index(name="Quantidade")
        )

        rein = compute_reincidencia(base)
        a.reincidencia_agentes = rein["agentes"]
        a.reincidencia_protocolos = rein["protocolos"]
        a.reincidencia_demandas = rein["demandas"]
        a.reincidencia_categorias = rein["categorias"]
        if not a.reincidencia_categorias.empty:
            a.reincidencia_categorias["Categoria"] = a.reincidencia_categorias["Categoria"].map(categoria_label)
        a.reincidencia_mes = rein["mes"]

        a.fg_sem_match_na = base[~base["match_na"]].copy()
        a.fg_sem_contestacao = base[base["sem_avaliacao_contestacao"]].copy()
        multi = (
            base.groupby("_protocolo_norm")["_matricula_norm"]
            .nunique()
            .reset_index(name="n_matriculas")
        )
        a.multi_matricula = multi[multi["n_matriculas"] > 1]

        incompletos = base[
            (base["_protocolo_norm"] == "")
            | (base["_matricula_norm"] == "")
            | base.get("Nome Agente", pd.Series(dtype=str)).map(is_empty_value)
        ]
        a.dados_incompletos = incompletos
        a.sem_categoria_severidade = base[
            (base["categoria_macro"] == "OUTROS") | (base["severidade"] == "INDEFINIDA")
        ]

    a.treinamento_pos_falha = compute_treinamento_pos_falha(bundle, base)
    a.recomendacoes = gerar_recomendacoes(a)
    a.prioridades = [r.texto for r in a.recomendacoes if r.prioridade == "alta"][:5]
    a.oportunidades = [r.texto for r in a.recomendacoes if r.prioridade in ("media", "baixa")][:5]
    a.acao_agora = _build_acao_agora(a)
    a.resumo_executivo = _build_resumo_executivo(a)
    return a


def _build_acao_agora(a: BRBAnalytics) -> list[str]:
    acoes = []
    c = a.cruzamentos
    if c.get("pct_fg_sem_na", 0) > 10:
        acoes.append("Revisar integração entre Falhas Gerais e NA_Falhas.")
    if c.get("pct_fg_sem_av", 0) > 10:
        acoes.append("Priorizar conclusão das avaliações CONFORME pendentes.")
    if not a.reincidencia_agentes.empty:
        top = a.reincidencia_agentes.iloc[0]
        acoes.append(f"Feedback direcionado ao agente {top['Agente']} ({top['Falhas']} falhas).")
    if not a.top_categorias.empty:
        t = a.top_categorias.iloc[0]
        acoes.append(f"Plano de ação para categoria {t['Categoria']} ({t['Quantidade']} casos).")
    if not a.treinamento_pos_falha.empty:
        acoes.append("Avaliar efetividade dos treinamentos — há falhas após capacitação.")
    if not acoes:
        acoes.append("Manter monitoramento das métricas e revisar alertas de qualidade semanalmente.")
    return acoes[:5]


def _build_resumo_executivo(a: BRBAnalytics) -> str:
    c = a.cruzamentos
    parts = [
        f"Operação com <b>{c.get('casos_fg', 0)}</b> casos auditados (FG) e "
        f"<b>{c.get('falhas_na', 0)}</b> falhas notificadas (NA). "
        f"Taxa de conformidade: <b>{c.get('taxa_conformidade', 0)}%</b>."
    ]
    if not a.top_categorias.empty:
        t = a.top_categorias.iloc[0]
        parts.append(f"Principal causa: <b>{t['Categoria']}</b> ({t['Quantidade']} casos).")
    if c.get("fg_sem_match_na", 0):
        parts.append(
            f"<b>{c['fg_sem_match_na']}</b> casos FG ({c.get('pct_fg_sem_na', 0)}%) sem match em NA."
        )
    if c.get("fg_sem_contestacao", 0):
        parts.append(
            f"<b>{c['fg_sem_contestacao']}</b> casos ({c.get('pct_fg_sem_av', 0)}%) sem avaliação CONFORME."
        )
    if c.get("seq_n_completo_esperado", 0):
        parts.append(
            f"<b>{c['seq_n_completo_esperado']}</b> protocolo(s) na sequência esperada "
            "(auditoria → notificação → contestação)."
        )
    if c.get("seq_n_ordem_invertida", 0):
        parts.append(
            f"<b>{c['seq_n_ordem_invertida']}</b> protocolo(s) com ordem temporal invertida."
        )
    return " ".join(parts)
