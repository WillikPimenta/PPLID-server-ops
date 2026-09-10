# -*- coding: utf-8 -*-
"""Textos de e-mail para relatórios de falhas críticas."""

from __future__ import annotations

from datetime import date

from report_falhas.utils_report import MESES_NOME_BR


def _fmt_periodo(cur_start: date, cur_end: date) -> str:
    return f"{cur_start.strftime('%d/%m/%Y')} a {cur_end.strftime('%d/%m/%Y')}"


def build_executive_period_ref(cur_start: date, cur_end: date, *, fechamento_mes: bool) -> str:
    """Frase de referência temporal para o corpo do e-mail executivo."""
    periodo = _fmt_periodo(cur_start, cur_end)
    if fechamento_mes:
        mes_nome = MESES_NOME_BR.get(cur_start.month, str(cur_start.month))
        return f"fechamento de {mes_nome} ({periodo})"
    return f"período de {periodo}"


def build_executive_email_subject(cur_start: date, cur_end: date) -> str:
    return (
        f"Report Executivo Falhas Críticas - Brasília x São Carlos "
        f"({_fmt_periodo(cur_start, cur_end)})"
    )


def build_executive_email_body(
    cur_start: date,
    cur_end: date,
    *,
    fechamento_mes: bool,
) -> str:
    ref_periodo = build_executive_period_ref(cur_start, cur_end, fechamento_mes=fechamento_mes)
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="utf-8"></head>
<body style="font-family:Segoe UI,Roboto,Arial,Helvetica,sans-serif;font-size:14px;color:#212529;line-height:1.6;margin:0;padding:16px 20px;">
  <p style="margin:0 0 12px;">Olá, pessoal.</p>
  <p style="margin:0 0 12px;">
    Segue o report semanal de falhas críticas, com o comparativo executivo entre
    <b>Brasília</b> e <b>São Carlos</b>, referente ao {ref_periodo}.
  </p>
  <p style="margin:0;color:#6c757d;font-size:13px;">
    Abra o <b>HTML anexo</b> para a visão comparativa completa.
  </p>
</body>
</html>"""
