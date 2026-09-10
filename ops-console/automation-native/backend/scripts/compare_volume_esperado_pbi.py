"""Compara um export PBI (TSV) com o volume horario calculado pelo portal."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import django

ROOT = Path(__file__).resolve().parents[0]
BACKEND = ROOT.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.dimensoes_processos.models import DimNivelHierarquico  # noqa: E402
from apps.dimensoes_processos.services.capacity_volume_esperado import (  # noqa: E402
    _projection_volume_for_date,
    build_volume_hora_ajustado,
    build_volume_hora_contrato_detailed,
)

DEFAULT_TSV = Path(__file__).resolve().parents[2] / "07-06-2026-arquivo.tsv"
DEFAULT_DATE = date(2026, 6, 7)
DEFAULT_TOLERANCE = Decimal("0.10")
ZERO = Decimal("0")


def parse_br_decimal(value: str) -> Decimal:
    text = (value or "").strip()
    if not text:
        return ZERO
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return Decimal(text)


def positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("tolerance deve ser decimal") from exc
    if parsed < ZERO:
        raise argparse.ArgumentTypeError("tolerance deve ser maior ou igual a zero")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tsv", type=Path, default=DEFAULT_TSV, help="Export TSV do Power BI")
    parser.add_argument("--date", type=date.fromisoformat, default=DEFAULT_DATE, help="Data YYYY-MM-DD")
    parser.add_argument(
        "--tolerance",
        type=positive_decimal,
        default=DEFAULT_TOLERANCE,
        help="Tolerancia absoluta por chave e no total (default: 0.10)",
    )
    parser.add_argument(
        "--allow-differences",
        action="store_true",
        help="Mantem exit 0 para uso exploratorio, mesmo com divergencias",
    )
    return parser.parse_args()


def load_pbi_rows(tsv: Path) -> list[dict]:
    rows: list[dict] = []
    with tsv.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            rows.append(
                {
                    "cliente": int(row["ID_Cliente"]),
                    "workflow": int(row["ID_Workflow"]),
                    "nh": int(row["ID_Nivel_Hierarquico"]),
                    "hour": int(row["Hora"]),
                    "vol_ajustado": parse_br_decimal(row["Volume Hora"]),
                    "vol_contrato": parse_br_decimal(row["Volume Hora Contrato"]),
                    "recebido": parse_br_decimal(row["Recebido hora"]),
                }
            )
    return rows


def main() -> int:
    args = parse_args()
    pbi_rows = load_pbi_rows(args.tsv)
    tolerance: Decimal = args.tolerance

    pbi_contract = {
        (row["cliente"], row["workflow"], row["nh"], row["hour"]): row["vol_contrato"]
        for row in pbi_rows
    }
    pbi_adjusted: dict[tuple[int, int, int], Decimal] = defaultdict(lambda: ZERO)
    for row in pbi_rows:
        pbi_adjusted[(row["cliente"], row["workflow"], row["hour"])] += row["vol_ajustado"]

    portal_contract = build_volume_hora_contrato_detailed(args.date)
    portal_adjusted = build_volume_hora_ajustado(args.date)
    projection_keys = set(_projection_volume_for_date(args.date))
    pbi_nh_ids = {key[2] for key in pbi_contract}
    catalog_nh_ids = set(
        DimNivelHierarquico.objects.filter(id_nh__in=pbi_nh_ids).values_list("id_nh", flat=True)
    )

    matches = 0
    differences: list[tuple[tuple[int, int, int, int], Decimal, Decimal]] = []
    missing_catalog: list[tuple[tuple[int, int, int, int], Decimal]] = []
    missing_projection: list[tuple[tuple[int, int, int, int], Decimal]] = []
    for key, pbi_value in pbi_contract.items():
        portal_value = portal_contract.get(key, ZERO)
        if key[2] not in catalog_nh_ids:
            missing_catalog.append((key, pbi_value))
        elif key[:3] not in projection_keys:
            missing_projection.append((key, pbi_value))
        elif abs(portal_value - pbi_value) > tolerance:
            differences.append((key, pbi_value, portal_value))
        else:
            matches += 1

    extras = [
        (key, value)
        for key, value in portal_contract.items()
        if key not in pbi_contract and value > tolerance
    ]
    pbi_total = sum(pbi_contract.values(), ZERO)
    portal_total = sum(portal_contract.values(), ZERO)
    total_delta = portal_total - pbi_total

    adjusted_differences = [
        (key, pbi_value, portal_adjusted.get(key, ZERO))
        for key, pbi_value in pbi_adjusted.items()
        if abs(portal_adjusted.get(key, ZERO) - pbi_value) > tolerance
    ]
    adjusted_extras = [
        (key, value)
        for key, value in portal_adjusted.items()
        if key not in pbi_adjusted and value > tolerance
    ]

    print(f"Data: {args.date.isoformat()}")
    print(f"TSV: {args.tsv}")
    print(f"PBI linhas: {len(pbi_rows)}")
    print(f"Portal chaves detalhadas: {len(portal_contract)}")
    print(f"Match contrato (tol={tolerance}): {matches}/{len(pbi_contract)}")
    print(f"Divergencias comparaveis: {len(differences)}")
    print(f"Chaves PBI com NH ausente no catalogo local: {len(missing_catalog)}")
    print(f"Chaves PBI sem projecao local elegivel: {len(missing_projection)}")
    print(f"Extras portal: {len(extras)}")
    print()
    print(f"Soma PBI Volume Hora Contrato: {pbi_total:.2f}")
    print(f"Soma portal contrato: {portal_total:.2f}")
    print(f"Delta agregado contrato: {total_delta:.2f}")
    print()
    print("Volume Hora ajustado (comparacao correta no grao C+WF+hora):")
    print(f"  Chaves PBI agregadas: {len(pbi_adjusted)}")
    print(f"  Divergencias: {len(adjusted_differences)}")
    print(f"  Extras portal: {len(adjusted_extras)}")
    print(f"  Soma PBI: {sum(pbi_adjusted.values(), ZERO):.2f}")
    print(f"  Soma portal: {sum(portal_adjusted.values(), ZERO):.2f}")

    if differences:
        print()
        print("Top 20 divergencias contrato (PBI vs portal, C+WF+NH+hora):")
        differences.sort(key=lambda item: abs(item[1] - item[2]), reverse=True)
        for (client, workflow, nh, hour), pbi_value, portal_value in differences[:20]:
            print(
                f"  C{client} WF{workflow} NH{nh} h{hour:02d}: "
                f"{pbi_value:.4f} vs {portal_value:.4f}"
            )

    for label, rows in (
        ("NH ausente no catalogo local", missing_catalog),
        ("Sem projecao local elegivel", missing_projection),
    ):
        if not rows:
            continue
        print()
        print(f"{label} (amostra):")
        for (client, workflow, nh, hour), pbi_value in rows[:10]:
            print(f"  C{client} WF{workflow} NH{nh} h{hour:02d}: PBI={pbi_value:.4f}")

    failed = bool(
        differences
        or missing_catalog
        or missing_projection
        or extras
        or adjusted_differences
        or adjusted_extras
        or abs(total_delta) > tolerance
    )
    if failed:
        print()
        if args.allow_differences:
            print("RESULTADO: DIVERGENTE (permitido por --allow-differences)")
            return 0
        print("RESULTADO: FALHOU")
        return 1

    print()
    print("RESULTADO: APROVADO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
