# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MES_ABREV = {
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
}

_PERIODO_RE = re.compile(
    r"(?P<mes>jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)-(?P<ano>\d{4})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceFileInfo:
    path: Path
    mtime: float
    size: int


def file_signature(source: SourceFileInfo) -> tuple[str, float, int]:
    return (str(source.path.resolve()), source.mtime, source.size)


def extract_periodo_mes(path: str | Path) -> str:
    """Extrai pasta/periodo tipo mai-2026 do path (pai ou nome do arquivo)."""
    p = Path(path)
    for part in reversed(p.parts):
        m = _PERIODO_RE.search(part)
        if m and m.group("mes").lower() in MES_ABREV:
            return f"{m.group('mes').lower()}-{m.group('ano')}"
    # Fallback: Relatorio_...01-15mai.xlsx → usa mai + ano do mtime se possível
    name = p.name.lower()
    for mes in MES_ABREV:
        if mes in name:
            # tenta achar ano 20xx no path
            ym = re.search(r"(20\d{2})", str(p))
            if ym:
                return f"{mes}-{ym.group(1)}"
    raise ValueError(f"Não foi possível extrair periodo_mes de: {path}")


def get_source_file(path: str | None) -> SourceFileInfo:
    if not path or not str(path).strip():
        raise FileNotFoundError("source_path vazio — o bot deve enviar o caminho do Excel")
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"Arquivo fonte não encontrado: {p}")
    st = p.stat()
    return SourceFileInfo(path=p.resolve(), mtime=st.st_mtime, size=st.st_size)
