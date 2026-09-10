"""Robust parquet reading for OneDrive Files On-Demand placeholders."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pandas as pd

from app.config.paths import PLAN_IDF_SERASA_BOTS

_log = logging.getLogger(__name__)

_CACHE_PARQUET = PLAN_IDF_SERASA_BOTS / "cache_parquet"
_ONEDRIVE_MARKERS = ("onedrive",)


def _is_onedrive_path(path: Path) -> bool:
    texto = str(path).lower()
    return any(marker in texto for marker in _ONEDRIVE_MARKERS)


def _cache_path(origem: Path) -> Path:
    return _CACHE_PARQUET / origem.name


def parquet_legivel(caminho: Path) -> bool:
    """Return True if parquet metadata can be read (file is hydrated and valid)."""
    try:
        import pyarrow.parquet as pq

        return pq.read_metadata(caminho).num_rows > 0
    except Exception:
        return False


def ler_parquet(
    caminho: Path | str,
    *,
    log: logging.Logger | None = None,
) -> pd.DataFrame:
    """Read a parquet file, copying to local cache when OneDrive blocks direct access."""
    logger = log or _log
    origem = Path(caminho)

    try:
        return pd.read_parquet(origem)
    except OSError as exc:
        if not _is_onedrive_path(origem):
            raise
        logger.warning(
            "Falha ao ler parquet diretamente (%s); tentando cache local: %s",
            origem.name,
            exc,
        )
    except IOError as exc:
        if not _is_onedrive_path(origem):
            raise
        logger.warning(
            "Falha ao ler parquet diretamente (%s); tentando cache local: %s",
            origem.name,
            exc,
        )

    destino = _cache_path(origem)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origem, destino)
        logger.info("Parquet copiado para cache local: %s", destino)
        return pd.read_parquet(destino)
    except Exception as exc:
        raise OSError(
            f"Parquet inacessível no OneDrive ({origem.name}): {exc}. "
            "Use 'Manter sempre neste dispositivo' no Explorer ou aguarde a sincronização."
        ) from exc
