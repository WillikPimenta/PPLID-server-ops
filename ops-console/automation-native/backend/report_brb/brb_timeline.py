# -*- coding: utf-8 -*-
"""Timeline unificada de eventos BRB."""
from __future__ import annotations

import pandas as pd

from report_brb.brb_format import clean_text, format_date_br, mes_label_pt
from report_brb.brb_loaders import BRBDataBundle


def _first_text(*values, max_len: int = 100) -> str:
    for v in values:
        t = clean_text(v, max_len=500)
        if t != "—":
            return clean_text(t, max_len)
    return "—"


def _norm_day(dt) -> pd.Timestamp | None:
    if dt is None or (isinstance(dt, float) and pd.isna(dt)) or pd.isna(dt):
        return None
    if isinstance(dt, bool):
        return None
    try:
        return pd.Timestamp(dt).normalize()
    except (ValueError, TypeError):
        return None


def _add_event(
    events: list[dict],
    dt,
    categoria: str,
    badge: str,
    tipo: str,
    fonte: str,
    referencia: str,
    detalhe: str,
) -> None:
    day = _norm_day(dt)
    if day is None:
        return
    events.append(
        {
            "data": day,
            "categoria": categoria,
            "badge": badge,
            "tipo": tipo,
            "fonte": fonte,
            "referencia": clean_text(referencia, 60),
            "detalhe": clean_text(detalhe, 100),
        }
    )


def _protocol_key(referencia: str) -> str:
    ref = str(referencia).strip()
    if "|" in ref:
        return ref.split("|")[0].strip()
    return ref


def _dedupe_events(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["_proto_key"] = df["referencia"].map(_protocol_key)
    groups = []
    for _, grp in df.groupby(["data", "categoria", "_proto_key"], sort=False):
        row = grp.iloc[0].to_dict()
        n = len(grp)
        proto = row.get("_proto_key", "")
        if n > 1 and proto and proto != "—":
            row["referencia"] = f"{proto} ×{n} casos"
            if row.get("detalhe") == "—" and n > 1:
                row["detalhe"] = f"{n} casos no mesmo dia"
        groups.append(row)
    out = pd.DataFrame(groups)
    if "_proto_key" in out.columns:
        out = out.drop(columns=["_proto_key"])
    return out.sort_values("data", ascending=False)


def build_timeline(bundle: BRBDataBundle, limit: int = 500) -> pd.DataFrame:
    events: list[dict] = []

    for _, row in bundle.na_demandas.iterrows():
        _add_event(
            events,
            row.get("Data da Abertura"),
            "demanda",
            "Demanda",
            "Demanda NA aberta",
            "NA_Demandas",
            row.get("Demanda", ""),
            _first_text(
                f"{row.get('Mês', '')} · {row.get('Quantidade de Protolocos', 0)} protocolos",
                row.get("Situação", ""),
            ),
        )
        _add_event(
            events,
            row.get("Data do Retorno"),
            "demanda",
            "Retorno",
            "Retorno demanda NA",
            "NA_Demandas",
            row.get("Demanda", ""),
            row.get("Situação", ""),
        )

    for _, row in bundle.na_falhas.iterrows():
        proto = row.get("PROTOCOLO", "")
        _add_event(
            events,
            row.get("DATA DE CADASTRO"),
            "na",
            "Falha NA",
            "Falha NA identificada",
            "NA_Falhas",
            proto,
            row.get("MOTIVO DA FALHA", ""),
        )
        dt_n = row.get("DATA DE NOTIFICAÇÃO")
        if dt_n is not None and not isinstance(dt_n, bool):
            _add_event(
                events,
                dt_n,
                "na",
                "Notificação",
                "Notificação cliente NA",
                "NA_Falhas",
                proto,
                row.get("DEMANDA", ""),
            )

    for _, row in bundle.treinamentos.iterrows():
        agent = row.get("Agent: UserLanID", row.get("Agent", ""))
        title = row.get("Event: EventTitle", "")
        for badge, col in (
            ("Capacitação", "Session: StartDate"),
            ("Assinatura", "SignatureDate"),
        ):
            _add_event(
                events,
                row.get(col),
                "treinamento",
                badge,
                f"Treinamento — {badge.lower()}",
                "Treinamentos",
                agent,
                title,
            )

    for _, row in bundle.contestacao.iterrows():
        conforme = str(row.get("CONFORME", "")).strip().upper()
        cat = "contestacao"
        badge = f"CONFORME {conforme}" if conforme else "CONFORME"
        _add_event(
            events,
            row.get("Data de Análise"),
            cat,
            badge,
            f"Avaliação CONFORME",
            "Contestacao",
            f"{row.get('Protocolo', '')}",
            _first_text(row.get("Cenário"), row.get("Tipo de falha"), row.get("Etapa")),
        )

    tend_col = next(
        (c for c in bundle.falhas_gerais.columns if "end" in str(c).lower()),
        None,
    )
    for _, row in bundle.falhas_gerais.iterrows():
        _add_event(
            events,
            row.get("Data de Análise"),
            "fg",
            "FG BRB",
            "Auditoria Falhas Gerais",
            "Falhas_Gerais-BRB",
            f"{row.get('Protocolo', '')}",
            _first_text(
                row.get(tend_col) if tend_col else None,
                row.get("Novo cenário"),
                row.get("Cenário"),
            ),
        )

    cols = [
        "data",
        "categoria",
        "badge",
        "tipo",
        "fonte",
        "referencia",
        "detalhe",
        "data_fmt",
        "mes_label",
    ]
    if not events:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(events)
    df = _dedupe_events(df)
    df = df.sort_values("data", ascending=False).head(limit)
    df["data_fmt"] = df["data"].map(format_date_br)
    df["mes_label"] = df["data"].map(lambda d: mes_label_pt(pd.Timestamp(d)))
    return df.reset_index(drop=True)
