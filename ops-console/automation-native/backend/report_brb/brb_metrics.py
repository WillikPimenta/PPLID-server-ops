# -*- coding: utf-8 -*-
"""KPIs, procedência e métricas agregadas."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from report_brb.brb_filters import parse_excel_date, na_effective_date
from report_brb.brb_loaders import BRBDataBundle


@dataclass
class BRBMetrics:
    protocolos_na: int = 0  # soma Quantidade de Protolocos (volume agregado)
    demandas_na_registros: int = 0  # linhas na aba NA_Demandas
    demandas_na_qi_distintas: int = 0  # demandas/QI distintas, se houver coluna
    casos_unicos_fg: int = 0
    protocolos_distintos_fg: int = 0
    linhas_fg: int = 0
    duplicatas_fg: int = 0
    na_falhas_registros: int = 0
    na_falhas_protocolos: int = 0
    possivel_ataque_na: int = 0
    na_demanda_inferida: int = 0
    treinamentos_registros: int = 0
    treinamentos_agentes: int = 0
    treinamentos_horas: float = 0.0
    treinamentos_sessoes: int = 0
    contestacao_registros: int = 0
    contestacao_casos_unicos: int = 0
    conforme_sim: int = 0
    conforme_nao: int = 0
    pct_procedente: float = 0.0
    pct_improcedente: float = 0.0
    protocolos_multi_usuario_fg: int = 0
    fg_sem_contestacao: int = 0
    fg_sem_na: int = 0
    fn_count: int = 0
    fp_count: int = 0
    # Auditados × falhas NA
    auditados_registros: int = 0
    auditados_casos: int = 0  # Protocolo distintos
    auditados_taxa_achado: float = 0.0  # falhas NA / casos auditados (%)
    auditados_pct_sem_falha: float = 0.0  # 100 - taxa
    auditados_mensal: pd.DataFrame = field(default_factory=pd.DataFrame)
    narrativa: str = ""
    por_mes_demandas: dict = field(default_factory=dict)
    por_tendencia: dict = field(default_factory=dict)
    conforme_por_cenario: pd.DataFrame = field(default_factory=pd.DataFrame)
    top_motivos_na: pd.DataFrame = field(default_factory=pd.DataFrame)
    multi_usuario: pd.DataFrame = field(default_factory=pd.DataFrame)
    procedencia_detalhe: pd.DataFrame = field(default_factory=pd.DataFrame)


def compute_metrics(bundle: BRBDataBundle) -> BRBMetrics:
    m = BRBMetrics()
    dem = bundle.na_demandas
    na = bundle.na_falhas
    fg = bundle.falhas_gerais
    tre = bundle.treinamentos
    cont = bundle.contestacao

    if not dem.empty:
        m.demandas_na_registros = len(dem)
        if "Demanda" in dem.columns:
            dem_vals = dem["Demanda"].dropna().astype(str).str.strip()
            dem_vals = dem_vals[dem_vals != ""]
            m.demandas_na_qi_distintas = int(dem_vals.nunique()) if len(dem_vals) else 0
        if "Quantidade de Protolocos" in dem.columns:
            m.protocolos_na = int(dem["Quantidade de Protolocos"].sum())
            m.por_mes_demandas = (
                dem.groupby("Mês", dropna=False)["Quantidade de Protolocos"].sum().astype(int).to_dict()
            )

    m.linhas_fg = bundle.fg_linhas_brutas
    m.duplicatas_fg = bundle.fg_duplicatas

    if not fg.empty:
        m.casos_unicos_fg = int(fg["chave_caso"].nunique())
        m.protocolos_distintos_fg = int(fg["_protocolo_norm"].nunique())
        tend_col = next((c for c in fg.columns if "end" in str(c).lower()), None)
        if tend_col:
            m.por_tendencia = fg[tend_col].fillna("(vazio)").astype(str).value_counts().astype(int).to_dict()
        if "fn_fp" in fg.columns:
            m.fn_count = int((fg["fn_fp"] == "FN").sum())
            m.fp_count = int((fg["fn_fp"] == "FP").sum())
        multi = (
            fg.groupby("_protocolo_norm")["_matricula_norm"]
            .nunique()
            .reset_index(name="n_matriculas")
        )
        multi = multi[multi["n_matriculas"] > 1]
        m.protocolos_multi_usuario_fg = len(multi)
        m.multi_usuario = multi.merge(
            fg[
                [
                    "_protocolo_norm",
                    "_matricula_norm",
                    "Nome Agente",
                    "Novo cenário",
                ]
            ],
            on="_protocolo_norm",
            how="left",
        )

    if not na.empty:
        m.na_falhas_registros = len(na)
        m.na_falhas_protocolos = int(na["_protocolo_norm"].nunique())
        m.possivel_ataque_na = int(na["possivel_ataque"].sum()) if "possivel_ataque" in na.columns else 0
        if "demanda_inferida" in na.columns:
            m.na_demanda_inferida = int(na["demanda_inferida"].fillna(False).astype(bool).sum())
        if "MOTIVO DA FALHA" in na.columns:
            m.top_motivos_na = (
                na["MOTIVO DA FALHA"].value_counts().head(15).reset_index()
            )
            m.top_motivos_na.columns = ["Motivo", "Quantidade"]

    if not tre.empty:
        m.treinamentos_registros = len(tre)
        if "Agent: UserLanID" in tre.columns:
            m.treinamentos_agentes = int(tre["Agent: UserLanID"].nunique())

    tre_h = getattr(bundle, "treinamentos_horas", None)
    if tre_h is not None and not tre_h.empty:
        m.treinamentos_sessoes = len(tre_h)
        m.treinamentos_horas = round(float(tre_h["horas"].sum()), 1) if "horas" in tre_h.columns else 0.0

    if not cont.empty:
        m.contestacao_registros = len(cont)
        m.contestacao_casos_unicos = int(cont["chave_caso"].nunique())
        m.conforme_sim = int((cont["classificacao_conforme"] == "nao_falha").sum())
        m.conforme_nao = int((cont["classificacao_conforme"] == "falha").sum())
        total_dec = m.conforme_sim + m.conforme_nao
        if total_dec:
            m.pct_improcedente = round(100 * m.conforme_sim / total_dec, 1)
            m.pct_procedente = round(100 * m.conforme_nao / total_dec, 1)
        sim = cont[cont["classificacao_conforme"] == "falha"]
        if not sim.empty and "Cenário" in sim.columns:
            m.conforme_por_cenario = (
                sim["Cenário"].value_counts().head(12).reset_index()
            )
            m.conforme_por_cenario.columns = ["Cenário", "Quantidade"]
        m.procedencia_detalhe = cont[
            [
                c
                for c in (
                    "Protocolo",
                    "Matrícula",
                    "Colaborador",
                    "Cliente",
                    "CONFORME",
                    "classificacao_conforme",
                    "Cenário",
                    "Tipo de falha",
                    "Etapa",
                    "Workflow",
                    "Auditor",
                    "Origem",
                    "Data",
                    "Data de Análise",
                )
                if c in cont.columns
            ]
        ].copy()

    if not fg.empty and not cont.empty:
        keys_fg = set(fg["chave_caso"])
        keys_cont = set(cont["chave_caso"])
        m.fg_sem_contestacao = len(keys_fg - keys_cont)

    if not fg.empty and not na.empty:
        protos_fg = set(fg["_protocolo_norm"])
        protos_na = set(na["_protocolo_norm"])
        m.fg_sem_na = len(protos_fg - protos_na)

    _compute_auditados_metrics(m, bundle)

    m.narrativa = _build_narrative(m, bundle)
    return m


def _compute_auditados_metrics(m: BRBMetrics, bundle: BRBDataBundle) -> None:
    """Totais e série mensal: casos auditados × falhas NA."""
    from report_brb.brb_format import mes_label_pt

    aud = getattr(bundle, "auditados", None)
    if aud is None or aud.empty:
        m.auditados_mensal = pd.DataFrame(
            columns=[
                "ym",
                "label",
                "casos",
                "registros",
                "falhas",
                "taxa_achado",
                "pct_sem_falha",
                "atipico",
            ]
        )
        return

    m.auditados_registros = len(aud)
    if "_protocolo_norm" in aud.columns:
        m.auditados_casos = int(aud["_protocolo_norm"].replace("", pd.NA).dropna().nunique())
    elif "Protocolo" in aud.columns:
        m.auditados_casos = int(aud["Protocolo"].nunique())
    else:
        m.auditados_casos = 0

    # Por mês — auditados
    aud_m = aud.copy()
    if "Data" not in aud_m.columns:
        return
    aud_m["_ym"] = parse_excel_date(aud_m["Data"]).dt.to_period("M")
    aud_m = aud_m.dropna(subset=["_ym"])
    proto_col = "_protocolo_norm" if "_protocolo_norm" in aud_m.columns else "Protocolo"
    aud_grp = (
        aud_m.groupby("_ym", sort=True)
        .agg(registros=(proto_col, "size"), casos=(proto_col, "nunique"))
        .reset_index()
    )

    # Por mês — falhas NA (data efetiva: notificação se houver; senão cadastro)
    na = bundle.na_falhas
    falhas_by_ym: dict = {}
    if not na.empty:
        na_m = na.copy()
        na_m["_ym"] = na_effective_date(na_m).dt.to_period("M")
        falhas_by_ym = (
            na_m.dropna(subset=["_ym"]).groupby("_ym").size().astype(int).to_dict()
        )

    rows = []
    casos_vals = []
    for _, r in aud_grp.iterrows():
        ym = r["_ym"]
        casos = int(r["casos"])
        registros = int(r["registros"])
        falhas = int(falhas_by_ym.get(ym, 0))
        taxa = round(100 * falhas / casos, 1) if casos else 0.0
        limpo = round(max(0.0, 100.0 - taxa), 1)
        casos_vals.append(casos)
        rows.append(
            {
                "ym": ym,
                "label": mes_label_pt(ym.to_timestamp()),
                "casos": casos,
                "registros": registros,
                "falhas": falhas,
                "taxa_achado": taxa,
                "pct_sem_falha": limpo,
                "atipico": False,
            }
        )

    # Destaque de volume: máximo do período e acima da média + 1 desvio (heurística).
    # Não constitui anomalia estatística formal nem afirma causa.
    if casos_vals:
        mean_c = sum(casos_vals) / len(casos_vals)
        var_c = sum((v - mean_c) ** 2 for v in casos_vals) / len(casos_vals)
        std_c = var_c ** 0.5
        thr = mean_c + std_c if std_c > 0 else max(casos_vals)
        max_c = max(casos_vals)
        for row in rows:
            if row["casos"] >= thr and row["casos"] == max_c and max_c > 0:
                row["atipico"] = True

    m.auditados_mensal = pd.DataFrame(rows)

    if m.auditados_casos:
        m.auditados_taxa_achado = round(
            100 * m.na_falhas_registros / m.auditados_casos, 1
        )
        m.auditados_pct_sem_falha = round(max(0.0, 100.0 - m.auditados_taxa_achado), 1)
    else:
        m.auditados_taxa_achado = 0.0
        m.auditados_pct_sem_falha = 0.0


def _build_narrative(m: BRBMetrics, bundle: BRBDataBundle) -> str:
    tend_top = ""
    if m.por_tendencia:
        top = max(m.por_tendencia.items(), key=lambda x: x[1])
        tend_top = f" Tendência predominante: <b>{top[0]}</b> ({top[1]} casos)."
    parts = [
        f"No período, o Qualidade acionou o CS do cliente com <b>{m.demandas_na_registros}</b> solicitação(ões), "
        f"volume agregado de <b>{m.protocolos_na}</b> protocolos informados, "
        f"<b>{m.casos_unicos_fg}</b> casos de falhas encontradas em auditorias "
        f"({m.protocolos_distintos_fg} protocolos distintos) e <b>{m.na_falhas_registros}</b> falhas "
        f"notificadas pela Qualidade ao CS."
        + tend_top
    ]
    if m.possivel_ataque_na:
        parts.append(
            f"Identificamos <b>{m.possivel_ataque_na}</b> registros com sinalização por palavra-chave "
            f"({round(100 * m.possivel_ataque_na / max(m.na_falhas_registros, 1), 1)}% das falhas notificadas)."
        )
    if m.conforme_sim + m.conforme_nao:
        parts.append(
            f"Na contestação (CONFORME), <b>{m.pct_procedente}%</b> foram classificados como falha (Não) "
            f"e <b>{m.pct_improcedente}%</b> como conforme/acerto (Sim), em <b>{m.contestacao_casos_unicos}</b> casos únicos avaliados."
        )
    if m.treinamentos_registros:
        parts.append(
            f"Foram ministrados/assinados <b>{m.treinamentos_registros}</b> registros de capacitação BRB "
            f"para <b>{m.treinamentos_agentes}</b> agentes."
        )
    if m.auditados_casos:
        parts.append(
            f"Foram auditados <b>{m.auditados_casos}</b> casos distintos "
            f"(<b>{m.auditados_registros}</b> registros/etapas), com razão descritiva "
            f"<b>{m.auditados_taxa_achado}%</b> frente às falhas NA do período "
            "(bases distintas — não representa acurácia formal)."
        )
    if m.protocolos_multi_usuario_fg:
        parts.append(
            f"<b>{m.protocolos_multi_usuario_fg}</b> protocolo(s) apresentam mais de uma matrícula em Falhas Gerais BRB."
        )
    return " ".join(parts)


def build_procedencia_join(bundle: BRBDataBundle) -> pd.DataFrame:
    fg = bundle.falhas_gerais
    cont = bundle.contestacao
    if fg.empty:
        return pd.DataFrame()
    cols_fg = [
        "chave_caso",
        "_protocolo_norm",
        "_matricula_norm",
        "Nome Agente",
        "Novo cenário",
        "Workflow",
        "Líder",
        "fn_fp",
        "classificacao_conforme",
        "CONFORME",
    ]
    out = fg[[c for c in cols_fg if c in fg.columns]].copy()
    out["status_cruzamento"] = out["classificacao_conforme"].map(
        lambda x: "avaliado" if x in ("falha", "nao_falha") else "sem_avaliacao"
    )
    return out


def resumo_dict(m: BRBMetrics) -> dict:
    return {
        "Demandas abertas (registros NA_Demandas)": m.demandas_na_registros,
        "Demandas/QI distintas": m.demandas_na_qi_distintas,
        "Protocolos informados (soma agregada)": m.protocolos_na,
        "Casos únicos FG (Protocolo+Matrícula)": m.casos_unicos_fg,
        "Protocolos distintos FG": m.protocolos_distintos_fg,
        "Falhas NA notificadas": m.na_falhas_registros,
        "Protocolos distintos NA": m.na_falhas_protocolos,
        "Possível ataque/fraude NA": m.possivel_ataque_na,
        "Treinamentos BRB": m.treinamentos_registros,
        "Agentes capacitados": m.treinamentos_agentes,
        "Contestação registros": m.contestacao_registros,
        "Contestação casos únicos": m.contestacao_casos_unicos,
        "CONFORME Sim (conforme / não é falha)": m.conforme_sim,
        "CONFORME Não (é falha)": m.conforme_nao,
        "% Procedente": m.pct_procedente,
        "% Improcedente": m.pct_improcedente,
        "Linhas brutas FG": m.linhas_fg,
        "Duplicatas FG (chave_caso)": m.duplicatas_fg,
        "Demandas NA inferidas": m.na_demanda_inferida,
        "FN (não sinalizado)": m.fn_count,
        "FP (sinalização incorreta)": m.fp_count,
        "FG sem avaliação CONFORME": m.fg_sem_contestacao,
        "FG sem match NA": m.fg_sem_na,
        "Protocolos multi-usuário FG": m.protocolos_multi_usuario_fg,
        "Casos auditados (protocolos)": m.auditados_casos,
        "Registros auditados": m.auditados_registros,
        "Razão descritiva falhas/auditados %": m.auditados_taxa_achado,
    }
