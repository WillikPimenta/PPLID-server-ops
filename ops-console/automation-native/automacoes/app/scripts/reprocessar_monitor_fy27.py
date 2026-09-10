"""Reprocessa monitor BRFlow em lote para MONITOR_EVENTOS_FY_27."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from app.config import (
    PASTA_MONITOR_BRUTA,
    PASTA_MONITOR_CONFER_TRATADO,
    PASTA_MONITOR_CONFER_UNIFICADO,
    PASTA_PRODUTIVIDADE_D1_BRUTA,
    PREFIXO_MONITOR_BRFLOW_BRUTO,
    PREFIXO_MONITOR_CONFER_TRATADO,
    PREFIXO_PROD_D1,
)
from app.bots.rotina.tasks.monitor import (  # noqa: E402
    _pretratar_monitor_verifica_usuario,
    tratar_arquivo,
)
from app.bots.rotina.tasks.unificados import _juntar_monitor_tratado_com_monitor_confer_dia

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _listar_datas_fy27(data_inicio: str | None, data_fim: str | None) -> list[str]:
    pasta = Path(PASTA_MONITOR_CONFER_UNIFICADO)
    datas = sorted(
        p.stem.replace("monitor-unificado_", "")
        for p in pasta.glob("monitor-unificado_*.parquet")
    )
    if data_inicio:
        datas = [d for d in datas if d >= data_inicio]
    if data_fim:
        datas = [d for d in datas if d <= data_fim]
    return datas


def _resolver_monitor_bruto(data_ref: str) -> Path | None:
    for ext in (".parquet", ".csv"):
        caminho = PASTA_MONITOR_BRUTA / f"{PREFIXO_MONITOR_BRFLOW_BRUTO}{data_ref}{ext}"
        if caminho.exists():
            return caminho
    return None


def _resolver_prod_bruto(data_ref: str) -> Path | None:
    caminho = PASTA_PRODUTIVIDADE_D1_BRUTA / f"{PREFIXO_PROD_D1}{data_ref}.parquet"
    return caminho if caminho.exists() else None


def _resolver_confer_tratado(data_ref: str) -> Path | None:
    data_confer = datetime.strptime(data_ref, "%Y%m%d").strftime("%d%m%Y")
    caminho = PASTA_MONITOR_CONFER_TRATADO / f"{PREFIXO_MONITOR_CONFER_TRATADO}{data_confer}.parquet"
    return caminho if caminho.exists() else None


def _dia_tem_logout_consecutivo(data_ref: str) -> bool:
    """True se monitor bruto do dia tiver Logout imediatamente após Logout (mesmo usuário)."""
    monitor = _resolver_monitor_bruto(data_ref)
    if monitor is None:
        return False

    if monitor.suffix.lower() == ".parquet":
        df = pd.read_parquet(monitor, columns=["Usuário", "Data do Evento", "Evento"])
    else:
        from app.bots.rotina.io import _ler_csv_tratamento

        df = _ler_csv_tratamento(str(monitor))
        df = df[["Usuário", "Data do Evento", "Evento"]]

    df["Usuário"] = df["Usuário"].astype(str).str[:7]
    df["Data do Evento"] = pd.to_datetime(df["Data do Evento"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Data do Evento"]).sort_values(["Usuário", "Data do Evento"])
    df["Evento_Anterior"] = df.groupby("Usuário")["Evento"].shift(1)
    return bool(
        ((df["Evento"] == "Logout") & (df["Evento_Anterior"] == "Logout")).any()
    )


def _filtrar_datas_afetadas_loop1b(datas: list[str]) -> list[str]:
    return [d for d in datas if _dia_tem_logout_consecutivo(d)]


def reprocessar_dia(data_ref: str) -> str:
    monitor = _resolver_monitor_bruto(data_ref)
    prod = _resolver_prod_bruto(data_ref)

    if monitor is None and prod is None:
        return "PENDENTE (sem monitor bruto e sem prod bruto)"
    if monitor is None:
        return "PENDENTE (sem monitor bruto)"
    if prod is None:
        return "PENDENTE (sem prod bruto)"

    _pretratar_monitor_verifica_usuario(str(monitor))
    data_obj = datetime.strptime(data_ref, "%Y%m%d")
    resultado = tratar_arquivo(str(monitor), str(prod), data_obj)
    if not resultado:
        return "ERRO (tratar_arquivo retornou None)"

    confer = _resolver_confer_tratado(data_ref)
    if confer is None:
        return "OK (sem confer)"

    unificado = _juntar_monitor_tratado_com_monitor_confer_dia(data_ref)
    if unificado is None:
        return "OK (tratado; unificação falhou)"
    return "OK"


def main() -> int:
    parser = argparse.ArgumentParser(description="Reprocessa monitor BRFlow para FY_27")
    parser.add_argument("--data-inicio", help="Filtrar a partir de YYYYMMDD")
    parser.add_argument("--data-fim", help="Filtrar até YYYYMMDD")
    parser.add_argument(
        "--afetados-loop1b",
        action="store_true",
        help="Reprocessar apenas dias com Logout consecutivo no monitor bruto",
    )
    args = parser.parse_args()

    datas = _listar_datas_fy27(args.data_inicio, args.data_fim)
    if args.afetados_loop1b:
        datas = _filtrar_datas_afetadas_loop1b(datas)
    if not datas:
        log.error("Nenhuma data encontrada em %s", PASTA_MONITOR_CONFER_UNIFICADO)
        return 1

    log.info("Iniciando reprocessamento | datas=%s | pasta=%s", len(datas), PASTA_MONITOR_CONFER_UNIFICADO)

    contadores = {"ok": 0, "ok_sem_confer": 0, "pendente": 0, "erro": 0}
    pendentes: list[tuple[str, str]] = []

    for idx, data_ref in enumerate(datas, start=1):
        status = reprocessar_dia(data_ref)
        log.info("[%s/%s] %s -> %s", idx, len(datas), data_ref, status)

        if status == "OK":
            contadores["ok"] += 1
        elif status == "OK (sem confer)":
            contadores["ok_sem_confer"] += 1
        elif status.startswith("PENDENTE"):
            contadores["pendente"] += 1
            pendentes.append((data_ref, status))
        else:
            contadores["erro"] += 1

    reprocessados = contadores["ok"] + contadores["ok_sem_confer"]
    unificados = contadores["ok"]

    print()
    print("=" * 60)
    print(f"Reprocessados: {reprocessados} | Unificados: {unificados} | Pendentes: {contadores['pendente']} | Erros: {contadores['erro']}")
    if pendentes:
        print(f"Pendentes ({len(pendentes)}):")
        for data_ref, motivo in pendentes:
            print(f"  {data_ref}: {motivo}")
    print("=" * 60)

    return 0 if contadores["erro"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
