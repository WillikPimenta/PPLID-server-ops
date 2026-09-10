# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TZ_BR = ZoneInfo("America/Sao_Paulo")


def _repair_mojibake(text: str) -> str:
    """Repara somente o padrão UTF-8 interpretado como latin-1, sem adivinhar rótulos."""
    if not any(marker in text for marker in ("Ã", "Â", "â")):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired if repaired != text else text


def _cell(row: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        if key in row and row[key] is not None and not (isinstance(row[key], float) and pd.isna(row[key])):
            val = row[key]
            if isinstance(val, float) and val == int(val):
                return str(int(val))
            text = str(val).strip()
            if text and text.lower() != "nan":
                return _repair_mojibake(text)
    return default


def _parse_br_datetime(data_s: str, hora_s: str) -> datetime | None:
    data_s = (data_s or "").strip()
    hora_s = (hora_s or "").strip()
    if not data_s or data_s == "-":
        return None
    try:
        d = datetime.strptime(data_s, "%d/%m/%Y").date()
    except ValueError:
        return None
    t = time(0, 0, 0)
    if hora_s and hora_s != "-":
        try:
            t = datetime.strptime(hora_s, "%H:%M:%S").time()
        except ValueError:
            try:
                t = datetime.strptime(hora_s, "%H:%M").time()
            except ValueError:
                pass
    return datetime.combine(d, t, tzinfo=TZ_BR)


def _parse_tempo_analise(text: str) -> int | None:
    text = (text or "").strip()
    if not text or text == "-":
        return None
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = (int(parts[0]), int(parts[1]), int(parts[2]))
        return h * 3600 + m * 60 + s
    except ValueError:
        return None


def read_consolidado_rows(path: Path, periodo_mes: str) -> list[dict]:
    df = pd.read_excel(path, sheet_name="Produtividade", engine="openpyxl")
    if df.empty:
        return []
    source = str(path.resolve())
    out: list[dict] = []
    for raw in df.to_dict(orient="records"):
        protocolo = _cell(raw, "Protocolo Destino")
        if not protocolo:
            continue
        out.append(
            {
                "periodo_mes": periodo_mes,
                "protocolo_destino": protocolo[:128],
                "cliente_origem": _cell(raw, "Cliente Origem")[:255],
                "workflow_origem": _cell(raw, "Workflow Origem")[:255],
                "nh_origem": _cell(raw, "Nivel Hierarquico Origem")[:64],
                "usuario_origem": _cell(raw, "Usuario Origem")[:128],
                "protocolo_origem": _cell(raw, "Protocolo Origem")[:128],
                "cpf": _cell(raw, "Numero do CPF")[:32],
                "contrato": _cell(raw, "N. do Contrato/Proposta")[:128],
                "cadastro_origem_at": _parse_br_datetime(
                    _cell(raw, "Data Cadastro Origem"),
                    _cell(raw, "Hora Cadastro Origem"),
                ),
                "conclusao_origem_at": _parse_br_datetime(
                    _cell(raw, "Data Conclusao Origem"),
                    _cell(raw, "Hora Conclusao Origem"),
                ),
                "resultado_origem": _cell(raw, "Resultado Origem")[:128],
                "status_origem": _cell(raw, "Status Origem")[:64],
                "tipo_conclusao_origem": _cell(raw, "Tipo Conclusao Origem")[:32],
                "matricula_origem": _cell(raw, "Matricula Origem")[:64],
                "alertas_origem": _cell(raw, "Alertas Origem"),
                "cliente_destino": _cell(raw, "Cliente Destino", default="GA - Case Manager")[:128],
                "workflow_destino": _cell(raw, "Workflow Destino", default="GA - Case Manager")[:128],
                "cadastro_destino_at": _parse_br_datetime(
                    _cell(raw, "Data Cadastro Destino"),
                    _cell(raw, "Hora Cadastro Destino"),
                ),
                "inspecao_at": _parse_br_datetime(
                    _cell(raw, "Data Inspecao"),
                    _cell(raw, "Hora Inspecao"),
                ),
                "conclusao_destino_at": _parse_br_datetime(
                    _cell(raw, "Data Conclusao Destino"),
                    _cell(raw, "Hora Conclusao Destino"),
                ),
                "tempo_analise_segundos": _parse_tempo_analise(_cell(raw, "Tempo de Analise")),
                "resultado_destino": _cell(raw, "Resultado Destino")[:128],
                "status_destino": _cell(raw, "Status Destino")[:64],
                "tipo_conclusao_destino": _cell(raw, "Tipo Conclusao Destino")[:32],
                "matricula_destino": _cell(raw, "Matricula Destino")[:64],
                "alertas_destino": _cell(raw, "Alertas Destino"),
                "source_file": source,
            }
        )
    return out


def read_hora_rows(path: Path, periodo_mes: str) -> list[dict]:
    df = pd.read_excel(path, engine="openpyxl", index_col=0)
    if df.empty:
        return []
    source = str(path.resolve())
    out: list[dict] = []
    for matricula, row in df.iterrows():
        mat = str(matricula).strip()
        if not mat or mat.upper() == "TOTAL HORA":
            continue
        for col, val in row.items():
            if str(col).upper() == "TOTAL AGENTE":
                continue
            try:
                hora = int(col)
            except (TypeError, ValueError):
                continue
            if hora < 0 or hora > 23:
                continue
            try:
                qtd = int(float(val)) if pd.notna(val) else 0
            except (TypeError, ValueError):
                qtd = 0
            if qtd <= 0:
                continue
            out.append(
                {
                    "periodo_mes": periodo_mes,
                    "matricula": mat[:64],
                    "hora": hora,
                    "qtd": qtd,
                    "source_file": source,
                }
            )
    return out


def read_tempo_logado_rows(path: Path, periodo_mes: str) -> list[dict]:
    df = pd.read_excel(path, engine="openpyxl", index_col=0)
    if df.empty:
        return []
    source = str(path.resolve())
    out: list[dict] = []
    for matricula, row in df.iterrows():
        mat = str(matricula).strip()
        if not mat:
            continue
        for col, val in row.items():
            dia_s = str(col).strip()
            try:
                dia = date.fromisoformat(dia_s[:10])
            except ValueError:
                continue
            try:
                secs = int(float(val)) if pd.notna(val) else 0
            except (TypeError, ValueError):
                secs = 0
            if secs == 0:
                continue
            out.append(
                {
                    "periodo_mes": periodo_mes,
                    "matricula": mat[:64],
                    "dia": dia,
                    "diff_segundos": secs,
                    "source_file": source,
                }
            )
    return out
