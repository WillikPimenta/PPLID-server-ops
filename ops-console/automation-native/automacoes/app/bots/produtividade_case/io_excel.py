"""Helpers de I/O Excel (grava temp → replace destino)."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


def write_excel_atomic(df: pd.DataFrame, arquivo_final: str | Path, sheet_nome: str, dir_temp: str | Path) -> Path:
    """Grava Excel fora do OneDrive e move com os.replace."""
    arquivo_final = Path(arquivo_final)
    dir_temp = Path(dir_temp)
    dir_temp.mkdir(parents=True, exist_ok=True)
    arquivo_final.parent.mkdir(parents=True, exist_ok=True)

    arquivo_local = dir_temp / arquivo_final.name
    if arquivo_local.exists():
        arquivo_local.unlink()

    with pd.ExcelWriter(arquivo_local, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_nome[:31] or "Sheet1")

    if arquivo_final.exists():
        arquivo_final.unlink()
    os.replace(arquivo_local, arquivo_final)
    return arquivo_final
