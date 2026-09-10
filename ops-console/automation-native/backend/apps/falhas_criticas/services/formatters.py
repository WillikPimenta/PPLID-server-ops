# -*- coding: utf-8 -*-
"""Formatação consistente para API e portal."""
import re

from report_falhas.io.data_loader import safe_str


def fmt_protocolo(val) -> str:
    """Protocolo sem casas decimais (6620289.0 → 6620289)."""
    s = safe_str(val).strip()
    if not s:
        return ''
    if re.fullmatch(r'-?\d+\.0+', s):
        return s.split('.')[0]
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s


def round_num(val, digits=1):
    if val is None:
        return None
    try:
        return round(float(val), digits)
    except (TypeError, ValueError):
        return val


def fmt_pct(val, digits=1):
    r = round_num(val, digits)
    return None if r is None else r


def parse_scenario_list(text: str) -> list:
    """'Cenário A (3), Cenário B (2)' → [{nome, qtd}, ...]"""
    if not text:
        return []
    out = []
    for part in str(text).split(','):
        part = part.strip()
        if not part:
            continue
        m = re.match(r'(.+?)\s*\((\d+)\)\s*$', part)
        if m:
            out.append({'nome': m.group(1).strip(), 'qtd': int(m.group(2))})
        else:
            out.append({'nome': part, 'qtd': None})
    return out


def parse_doc_uf_list(text: str) -> list:
    """'RG: 3 (SP, RJ); CNH: 1 (SP)' → [{doc, qtd, ufs}, ...]"""
    if not text:
        return []
    out = []
    for block in str(text).split(';'):
        block = block.strip()
        if not block:
            continue
        m = re.match(r'(.+?):\s*(\d+)\s*\(([^)]*)\)', block)
        if m:
            ufs = [u.strip() for u in m.group(3).split(',') if u.strip()]
            out.append({'doc': m.group(1).strip(), 'qtd': int(m.group(2)), 'ufs': ufs})
        else:
            out.append({'doc': block, 'qtd': None, 'ufs': []})
    return out
