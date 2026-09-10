# -*- coding: utf-8 -*-
"""Diagnóstico CSV derivacao_etapa vs Megazord."""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from apps.controle_sla.services.sla_eval import _norm_name
from apps.dimensoes_processos.models import DimCliente, DimWorkflow, DimEtapa
from apps.dimensoes_processos.services.meta_etapa_lookup import normalize_etapa_nome
from apps.dimensoes_processos.services.derivacao_etapa.reader import read_derivacao_csv
from apps.dimensoes_processos.services.derivacao_etapa.lookup import MegazordLookup


def char_info(text: str) -> str:
    specials = []
    for i, ch in enumerate(text):
        if ord(ch) > 127 or ch in "\x96\x97":
            specials.append(f"U+{ord(ch):04X}@{i}")
    return ",".join(specials[:8]) or "ascii"


def build_indexes():
    cliente_by: dict[str, str] = {}
    for _, nome in DimCliente.objects.values_list("id_cliente", "nome"):
        k = _norm_name(nome).casefold()
        if k and k not in cliente_by:
            cliente_by[k] = nome

    wf_by: dict[str, str] = {}
    for _, nome in DimWorkflow.objects.values_list("id_workflow", "nome"):
        k = _norm_name(nome).casefold()
        wf_by[k] = nome

    etapa_by: dict[str, str] = {}
    for _, nome in DimEtapa.objects.values_list("id_etapa", "nome"):
        k = normalize_etapa_nome(nome)
        if k and k not in etapa_by:
            etapa_by[k] = nome

    return cliente_by, wf_by, etapa_by


def find_close_wf(csv_name: str, wf_by: dict[str, str], limit: int = 3) -> list[str]:
    key = _norm_name(csv_name).casefold()
    prefix = key.split(" - ")[0] if " - " in key else key[:20]
    hits = [mz for k, mz in wf_by.items() if prefix in k or k in key][:limit]
    return hits


def analyze(files: list[Path], cliente_by, wf_by, etapa_by):
    rej = {
        "cliente": defaultdict(int),
        "workflow": defaultdict(int),
        "etapa": defaultdict(int),
    }
    ok = 0
    total = 0
    ok_cliente_only = 0

    for f in files:
        res = read_derivacao_csv(f)
        for row in res.rows:
            total += 1
            ckey = _norm_name(row.cliente).casefold()
            wkey = _norm_name(row.workflow).casefold()
            ekey = normalize_etapa_nome(row.etapa)

            if ckey not in cliente_by:
                rej["cliente"][row.cliente] += 1
                continue
            ok_cliente_only += 1
            if wkey not in wf_by:
                rej["workflow"][row.workflow] += 1
                continue
            if ekey not in etapa_by:
                rej["etapa"][row.etapa] += 1
                continue
            ok += 1

    return total, ok, ok_cliente_only, rej


def main():
    repo = Path(__file__).resolve().parents[2]
    csv_dir = repo / "derivacao_etapa"
    pattern = sys.argv[1] if len(sys.argv) > 1 else "FINALIZADO_202605*.csv"
    files = sorted(csv_dir.glob(pattern))
    if not files:
        print(f"Nenhum arquivo: {csv_dir / pattern}")
        return 1

    cliente_by, wf_by, etapa_by = build_indexes()
    total, ok, ok_cliente_only, rej = analyze(files, cliente_by, wf_by, etapa_by)

    print("=== DIAGNOSTICO DERIVACAO ETAPA ===")
    print(f"Arquivos: {len(files)}")
    print(f"Megazord: clientes={len(cliente_by)} workflows={len(wf_by)} etapas={len(etapa_by)}")
    print(f"Linhas CSV (sem Total): {total}")
    print(f"Match completo (C+WF+Etapa): {ok} ({100 * ok / total:.2f}%)")
    print(f"Cliente OK mas WF/Etapa falhou: {ok_cliente_only - ok}")
    print(f"Falha no cliente: {sum(rej['cliente'].values())}")
    print(f"Falha no workflow: {sum(rej['workflow'].values())}")
    print(f"Falha na etapa: {sum(rej['etapa'].values())}")
    print()

    print("--- TOP CLIENTES REJEITADOS ---")
    for nome, cnt in sorted(rej["cliente"].items(), key=lambda x: -x[1])[:10]:
        k = _norm_name(nome).casefold()
        close = [mz for ck, mz in cliente_by.items() if k[:20] in ck or ck[:20] in k][:2]
        print(f"  {cnt:5d} | chars: {char_info(nome)}")
        print(f"         CSV: {nome!r}")
        print(f"         Megazord proximo: {close}")
    print()

    print("--- TOP WORKFLOWS REJEITADOS ---")
    for nome, cnt in sorted(rej["workflow"].items(), key=lambda x: -x[1])[:12]:
        print(f"  {cnt:5d} | chars: {char_info(nome)}")
        print(f"         CSV: {nome[:110]}")
        print(f"         Megazord proximo: {find_close_wf(nome, wf_by)}")
    print()

    print("--- TOP ETAPAS REJEITADAS ---")
    for nome, cnt in sorted(rej["etapa"].items(), key=lambda x: -x[1])[:10]:
        print(f"  {cnt:5d} | chars: {char_info(nome)} | {nome[:90]}")

    # Encoding probe on worst client
    worst = max(rej["cliente"].items(), key=lambda x: x[1], default=(None, 0))
    if worst[0]:
        print()
        print("--- PROBE ENCODING (pior cliente) ---")
        for enc in ("latin-1", "cp1252", "utf-8"):
            try:
                raw = worst[0].encode("latin-1").decode(enc)
                print(f"  latin-1 bytes -> {enc}: {raw!r}")
            except Exception as exc:
                print(f"  latin-1 bytes -> {enc}: ERRO {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
