# -*- coding: utf-8 -*-
"""Benchmark sync detalhado — resultados gravados em stdout.

Mostra que parse_date por célula domina; pandas.to_datetime + COPY melhoram de verdade.
"""
from __future__ import annotations

import csv
import io
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.db import connection, transaction

from apps.rotina_bruto.models import RotinaDetalhadoBrutoAlerta, RotinaDetalhadoBrutoRecord
from apps.rotina_bruto.services.parquet_reader import (
    DETALHADO_COLUMN_MAP,
    _build_detalhado_record,
    _detalhado_row_payload,
    read_parquet,
)
from apps.rotina_bruto.services.parsers import parse_protocolo
from apps.rotina_bruto.services.pii import hash_cpf

BENCH_DATE = date(2099, 12, 31)
PARQUET = Path(
    r"C:\Users\c92928a\OneDrive - EXPERIAN SERVICES CORP"
    r"\Planejamento - IDF - Bases\Bots\brflow-detalhado-bruto"
    r"\brflow-detalhado-bruto_20260716.parquet"
)
MAIN_COLS = [
    "report_date",
    "protocolo",
    "cliente",
    "workflow",
    "cpf",
    "data_cadastro",
    "data_conclusao",
    "status_registro",
    "resultado",
    "nivel_hierarquico",
    "matricula",
    "data_primeira_conclusao",
    "data_analise",
]


def sql_delete():
    with connection.cursor() as cur:
        cur.execute(
            """
            DELETE FROM rotina_detalhado_bruto_alerta a
            USING rotina_detalhado_bruto_record r
            WHERE a.record_id = r.id AND r.report_date = %s
            """,
            [BENCH_DATE],
        )
        cur.execute(
            "DELETE FROM rotina_detalhado_bruto_record WHERE report_date = %s",
            [BENCH_DATE],
        )


def orm_delete():
    with transaction.atomic():
        RotinaDetalhadoBrutoRecord.objects.filter(report_date=BENCH_DATE).delete()


def transform_current(df):
    t0 = time.perf_counter()
    mains, alertas = [], []
    for _, row in df.iterrows():
        payload = _detalhado_row_payload(row)
        mains.append(_build_detalhado_record(payload, BENCH_DATE))
        alertas.append(payload.get("alertas", ""))
    return mains, alertas, time.perf_counter() - t0


def _col(df, *names):
    for n in names:
        if n in df.columns:
            return df[n]
    import pandas as pd

    return pd.Series([None] * len(df))


def transform_vectorized(df):
    """Datas via pandas.to_datetime; CPF hash em loop; monta ORM no fim."""
    import pandas as pd

    t0 = time.perf_counter()
    n = len(df)
    prot = _col(df, "Protocolo")
    cliente = _col(df, "Cliente").fillna("").astype(str).str.strip()
    workflow = _col(df, "Workflow").fillna("").astype(str).str.strip()
    cpf_s = _col(df, "CPF").fillna("").astype(str)
    status = _col(df, "Status do Registro").fillna("").astype(str).str.strip()
    resultado = _col(df, "Resultado").fillna("").astype(str).str.strip()
    nivel = _col(df, "Nível Hierárquico", "Nivel Hierarquico").fillna("").astype(str).str.strip()
    mat = _col(df, "matrícula", "matricula").fillna("").astype(str).str.strip()
    alertas_s = _col(df, "Alertas").fillna("").astype(str).str.strip()

    d_cad = pd.to_datetime(_col(df, "Data de Cadastro"), errors="coerce", dayfirst=True)
    d_conc = pd.to_datetime(
        _col(df, "Data de Conclusão", "Data de Conclusao"), errors="coerce", dayfirst=True
    )
    d_pri = pd.to_datetime(
        _col(df, "Data da Primeira Conclusão", "Data da Primeira Conclusao"),
        errors="coerce",
        dayfirst=True,
    )
    d_an = pd.to_datetime(
        _col(df, "Data de Análise", "Data de Analise"), errors="coerce", dayfirst=True
    )

    def as_date(ts):
        if pd.isna(ts):
            return None
        return ts.date()

    cpfs = [hash_cpf(x) for x in cpf_s]
    mains = []
    for i in range(n):
        mains.append(
            RotinaDetalhadoBrutoRecord(
                report_date=BENCH_DATE,
                protocolo=parse_protocolo(str(prot.iloc[i]) if pd.notna(prot.iloc[i]) else ""),
                cliente=cliente.iloc[i][:255],
                workflow=workflow.iloc[i][:255],
                cpf=(cpfs[i] or "")[:64],
                data_cadastro=as_date(d_cad.iloc[i]),
                data_conclusao=as_date(d_conc.iloc[i]),
                status_registro=status.iloc[i][:255],
                resultado=resultado.iloc[i][:255],
                nivel_hierarquico=nivel.iloc[i][:255],
                matricula=mat.iloc[i][:64],
                data_primeira_conclusao=as_date(d_pri.iloc[i]),
                data_analise=as_date(d_an.iloc[i]),
            )
        )
    alertas = list(alertas_s)
    return mains, alertas, time.perf_counter() - t0


def write_bulk(mains, alertas, batch=2000):
    t0 = time.perf_counter()
    for i in range(0, len(mains), batch):
        chunk = mains[i : i + batch]
        ach = alertas[i : i + batch]
        with transaction.atomic():
            created = RotinaDetalhadoBrutoRecord.objects.bulk_create(chunk, batch_size=batch)
            rows = [
                RotinaDetalhadoBrutoAlerta(record_id=r.pk, alertas=t)
                for r, t in zip(created, ach)
                if t
            ]
            if rows:
                RotinaDetalhadoBrutoAlerta.objects.bulk_create(rows, batch_size=batch)
    return time.perf_counter() - t0


def write_copy(mains, alertas):
    def fmt(d):
        return "\\N" if d is None else d.isoformat()

    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    for r in mains:
        w.writerow(
            [
                BENCH_DATE.isoformat(),
                "" if r.protocolo is None else r.protocolo,
                r.cliente,
                r.workflow,
                r.cpf,
                fmt(r.data_cadastro),
                fmt(r.data_conclusao),
                r.status_registro,
                r.resultado,
                r.nivel_hierarquico,
                r.matricula,
                fmt(r.data_primeira_conclusao),
                fmt(r.data_analise),
            ]
        )
    buf.seek(0)
    t0 = time.perf_counter()
    with connection.cursor() as cur:
        cols = ", ".join(MAIN_COLS)
        sql = (
            f"COPY rotina_detalhado_bruto_record ({cols}) FROM STDIN WITH "
            f"(FORMAT csv, DELIMITER E'\\t', NULL '\\N')"
        )
        if hasattr(cur, "copy_expert"):
            cur.copy_expert(sql, buf)
        else:
            with cur.copy(sql) as copy:
                copy.write(buf.read())
    ids = list(
        RotinaDetalhadoBrutoRecord.objects.filter(report_date=BENCH_DATE)
        .order_by("id")
        .values_list("id", flat=True)
    )
    rows = [
        RotinaDetalhadoBrutoAlerta(record_id=ids[i], alertas=t)
        for i, t in enumerate(alertas)
        if t and i < len(ids)
    ]
    if rows:
        RotinaDetalhadoBrutoAlerta.objects.bulk_create(rows, batch_size=2000)
    return time.perf_counter() - t0


def seed(n):
    sql_delete()
    RotinaDetalhadoBrutoRecord.objects.bulk_create(
        [
            RotinaDetalhadoBrutoRecord(
                report_date=BENCH_DATE, protocolo=i, cliente="b", matricula="1"
            )
            for i in range(n)
        ],
        batch_size=2000,
    )
    ids = list(
        RotinaDetalhadoBrutoRecord.objects.filter(report_date=BENCH_DATE).values_list(
            "id", flat=True
        )[::3]
    )
    RotinaDetalhadoBrutoAlerta.objects.bulk_create(
        [RotinaDetalhadoBrutoAlerta(record_id=i, alertas="x") for i in ids],
        batch_size=2000,
    )


def main():
    sizes = [10000, 30000]
    print(f"DB={connection.settings_dict.get('NAME')}")
    t0 = time.perf_counter()
    df = read_parquet(PARQUET)
    print(f"pandas read {time.perf_counter()-t0:.2f}s rows={len(df)}")

    results = []
    for n in sizes:
        sample = df.iloc[:n]
        print(f"\n=== n={n} ===")
        _, _, t_cur = transform_current(sample)
        print(f"  transform ATUAL (iterrows+parse_date): {t_cur:.2f}s")
        mains_v, alertas_v, t_vec = transform_vectorized(sample)
        print(f"  transform VETORIZADO (to_datetime):     {t_vec:.2f}s  ({t_cur/max(t_vec,1e-9):.1f}x)")

        seed(min(n, 30000))
        t0 = time.perf_counter()
        orm_delete()
        t_orm = time.perf_counter() - t0
        seed(min(n, 30000))
        t0 = time.perf_counter()
        sql_delete()
        t_sql = time.perf_counter() - t0
        print(f"  delete ORM {t_orm:.2f}s | SQL {t_sql:.2f}s ({t_orm/max(t_sql,1e-9):.1f}x)")

        sql_delete()
        mains_b, alertas_b, _ = transform_vectorized(sample)
        t_bulk = write_bulk(mains_b, alertas_b)
        print(f"  write bulk_create: {t_bulk:.2f}s")

        sql_delete()
        mains_c, alertas_c, _ = transform_vectorized(sample)
        t_copy = write_copy(mains_c, alertas_c)
        print(f"  write COPY:        {t_copy:.2f}s ({t_bulk/max(t_copy,1e-9):.1f}x)")

        results.append(
            {
                "n": n,
                "cur": t_cur,
                "vec": t_vec,
                "bulk": t_bulk,
                "copy": t_copy,
                "orm_del": t_orm,
                "sql_del": t_sql,
            }
        )

    print("\n========== RESUMO ==========")
    print(f"{'n':>8} {'atual_xf':>10} {'vec_xf':>10} {'xf×':>6} {'bulk':>8} {'COPY':>8} {'w×':>6}")
    for r in results:
        print(
            f"{r['n']:>8} {r['cur']:>10.2f} {r['vec']:>10.2f} {r['cur']/max(r['vec'],1e-9):>5.1f}x "
            f"{r['bulk']:>8.2f} {r['copy']:>8.2f} {r['bulk']/max(r['copy'],1e-9):>5.1f}x"
        )

    last = results[-1]
    scale = len(df) / last["n"]
    atual = (last["cur"] + last["bulk"] + last["orm_del"]) * scale
    melhor = (last["vec"] + last["copy"] + last["sql_del"]) * scale
    print(
        f"\nExtrapolação {len(df)} rows:\n"
        f"  Pipeline ATUAL     ~ {atual/60:.1f} min\n"
        f"  Pipeline MELHORADO ~ {melhor/60:.1f} min\n"
        f"  speedup ~ {atual/max(melhor,1e-9):.1f}x"
    )
    sql_delete()
    print("Cleanup ok.")


if __name__ == "__main__":
    main()
