"""Transformações puras do consolidado Case Manager (sem Mongo)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.bots.produtividade_case.dates import TZ_BR


def join_all_alerts(alerts):
    if not alerts or not isinstance(alerts, list):
        return ""
    return " | ".join(
        a.get("name", "") for a in alerts if isinstance(a, dict) and a.get("name")
    )


def calcular_tempo_analise(inicio, fim):
    try:
        total = int((fim - inicio).total_seconds())
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        return "-"


def split_datetime_br(value):
    """Converte instante (UTC aware/naive do Mongo) para data/hora de Brasília."""
    if not value:
        return "-", "-"
    if not isinstance(value, datetime):
        return "-", "-"
    # PyMongo costuma devolver datetime naive em UTC; sem tzinfo o Python
    # assume o fuso do SO (ex.: SP) e a hora UTC “vaza” no Excel.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    dt = value.astimezone(TZ_BR)
    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M:%S")


def join_alerts(alerts, only_checked=False):
    if not alerts:
        return ""
    return " | ".join(
        a.get("name", "")
        for a in alerts
        if a.get("name") and (not only_checked or a.get("checked") is True)
    )


def match_consolidado_filter(data_inicio_cons, data_fim_cons) -> dict:
    return {
        "transactionStatus": "COMPLETED",
        "origin.requestType": "AUDIT",
        "conclusionDate": {"$gte": data_inicio_cons, "$lt": data_fim_cons},
    }


def build_consolidado_row(d: dict) -> dict:
    """Monta uma linha do consolidado a partir de um documento do aggregate."""
    cad_origem_data, cad_origem_hora = split_datetime_br(d.get("cad_origem"))
    conc_origem_data, conc_origem_hora = split_datetime_br(d.get("conc_origem"))
    cad_dest_data, cad_dest_hora = split_datetime_br(d.get("cad_dest"))
    blocked_data, blocked_hora = split_datetime_br(d.get("blocked_date"))
    conc_dest_data, conc_dest_hora = split_datetime_br(d.get("conc_dest"))

    return {
        "Cliente Origem": d.get("cliente_origem", ""),
        "Workflow Origem": d.get("workflow_origem", ""),
        "Nivel Hierarquico Origem": d.get("nh_origem", ""),
        "Usuario Origem": d.get("usuario_origem", ""),
        "Protocolo Origem": d.get("protocolo_origem", ""),
        "Numero do CPF": d.get("cpf", ""),
        "N. do Contrato/Proposta": d.get("contrato", ""),
        "Data Cadastro Origem": cad_origem_data,
        "Hora Cadastro Origem": cad_origem_hora,
        "Data Conclusao Origem": conc_origem_data,
        "Hora Conclusao Origem": conc_origem_hora,
        "Resultado Origem": d.get("resultado_origem", ""),
        "Status Origem": d.get("status_origem", ""),
        "Tipo Conclusao Origem": d.get("tipo_conc_origem", ""),
        "Matricula Origem": d.get("matricula_origem", ""),
        "Alertas Origem": join_all_alerts(d.get("alerts_origem", [])),
        "Cliente Destino": "GA - Case Manager",
        "Workflow Destino": "GA - Case Manager",
        "Protocolo Destino": str(d.get("_id")),
        "Data Cadastro Destino": cad_dest_data,
        "Hora Cadastro Destino": cad_dest_hora,
        "Data Inspecao": blocked_data,
        "Hora Inspecao": blocked_hora,
        "Data Conclusao Destino": conc_dest_data,
        "Hora Conclusao Destino": conc_dest_hora,
        "Tempo de Analise": calcular_tempo_analise(d.get("blocked_date"), d.get("conc_dest")),
        "Resultado Destino": d.get("resultado_dest", ""),
        "Status Destino": d.get("status_dest", ""),
        "Tipo Conclusao Destino": "Manual",
        "Matricula Destino": d.get("matricula_dest", ""),
        "Alertas Destino": join_alerts(d.get("alerts_dest"), only_checked=True),
    }


def rows_from_aggregate(docs) -> list[dict]:
    return [build_consolidado_row(d) for d in docs]
