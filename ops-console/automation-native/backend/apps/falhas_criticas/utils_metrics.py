# -*- coding: utf-8 -*-
"""Helpers para métricas com percentuais padronizados."""


def pct(part, total, digits=1):
    if not total:
        return None
    return round(float(part) / float(total) * 100.0, digits)


def metric_block(valor, total=None, delta_abs=None, delta_pct=None, label=''):
    block = {
        'valor': int(valor) if valor is not None else 0,
        'label': label,
    }
    if total is not None:
        block['total'] = int(total)
        block['percentual'] = pct(valor, total)
    if delta_abs is not None:
        block['delta_abs'] = int(delta_abs)
    if delta_pct is not None:
        block['delta_pct'] = delta_pct
    return block


def fmt_delta_pct(delta_abs, total_prev):
    if total_prev and total_prev > 0:
        perc = (float(delta_abs) / float(total_prev)) * 100.0
        return f"{'+' if perc >= 0 else ''}{perc:.1f}%"
    return '—' if delta_abs == 0 else 'N/A'
