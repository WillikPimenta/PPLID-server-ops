"""Catálogo fixo de categorias de suporte operacional."""

from __future__ import annotations

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("procedimento", "Procedimento"),
    ("sistema", "Sistema"),
    ("escala", "Escala"),
    ("ferramenta", "Ferramenta"),
    ("outros", "Outros"),
)

CATEGORY_CODES = frozenset(code for code, _ in CATEGORIES)


def categories_payload() -> list[dict[str, str]]:
    return [{"code": code, "label": label} for code, label in CATEGORIES]


def is_valid_category(code: str) -> bool:
    return (code or "").strip().lower() in CATEGORY_CODES
