# -*- coding: utf-8 -*-
"""Situação e prefixo de treinamentos (compartilhado portal + report)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from report_falhas.io.data_loader import normalize_text, safe_str


def training_prefix(title: str) -> str:
    s = safe_str(title)
    if not s:
        return 'Outros'
    for sep in [' - ', ' | ', ':', ' — ', ' – ']:
        if sep in s:
            return s.split(sep)[0].strip() or 'Outros'
    return s.split()[0].strip() if s.split() else 'Outros'


def _parse_deadline(row) -> date | None:
    d = row.get('Event:EventDeadline') if hasattr(row, 'get') else None
    if d is None or (hasattr(pd, 'isna') and pd.isna(d)):
        return None
    try:
        return pd.Timestamp(d).date()
    except Exception:
        return None


def _is_signed(row) -> bool:
    sig = row.get('SignatureDate') if hasattr(row, 'get') else None
    if sig is not None and not (hasattr(pd, 'isna') and pd.isna(sig)):
        try:
            if pd.notna(sig):
                return True
        except Exception:
            pass
    st = normalize_text(safe_str(row.get('Status', '') if hasattr(row, 'get') else ''))
    return st == 'assinado'


def training_situacao_calculada(row, ref_date: date | None = None) -> str:
    """Classifica a situação do treinamento.

    - Finalizado: assinado / SignatureDate
    - Vencido: prazo (EventDeadline) ultrapassado e ainda não assinado
    - A vencer: prazo nos próximos 7 dias e ainda não assinado
    - Pendente de assinatura do agente: status ministrado (dentro do prazo)
    - Treinamento não concluído: status previsto (dentro do prazo)
    - Dentro do prazo: demais casos abertos com prazo futuro
    """
    ref_date = ref_date or date.today()
    if _is_signed(row):
        return 'Finalizado'

    st = normalize_text(safe_str(row.get('Status', '') if hasattr(row, 'get') else ''))
    dd = _parse_deadline(row)

    # Prazo ultrapassado tem precedência sobre o rótulo de status operacional.
    if dd is not None and dd < ref_date:
        return 'Vencido'
    if dd is not None and dd < (ref_date + timedelta(days=7)):
        return 'A vencer'

    if st == 'ministrado':
        return 'Pendente de assinatura do agente'
    if st == 'previsto':
        return 'Treinamento não concluído'

    if dd is not None:
        return 'Dentro do prazo'
    return ''
