"""Copy output files to secondary SharePoint/OneDrive folders."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

_log = logging.getLogger(__name__)


def espelhar_arquivo(
    origem: Path,
    destinos: list[Path] | tuple[Path, ...],
    *,
    log: logging.Logger | None = None,
) -> None:
    """Copy *origem* to each destination path; failures are logged as warnings."""
    logger = log or _log
    origem = Path(origem)
    if not origem.exists():
        logger.warning("Espelhamento ignorado: arquivo de origem inexistente (%s)", origem)
        return

    for destino in destinos:
        destino = Path(destino)
        try:
            if destino.resolve() == origem.resolve():
                continue
        except OSError:
            pass

        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origem, destino)
            logger.info("Arquivo espelhado | origem=%s | destino=%s", origem.name, destino)
        except Exception as exc:
            logger.warning(
                "Falha ao espelhar arquivo | origem=%s | destino=%s | erro=%s",
                origem,
                destino,
                exc,
            )
