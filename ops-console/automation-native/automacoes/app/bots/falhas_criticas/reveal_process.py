"""Sinal compartilhado portal ↔ bot para revelar Excel/browser durante 2º plano."""
from __future__ import annotations

from pathlib import Path

from app.config.paths import PASTA_CONFIG

REVEAL_FLAG_PATH = PASTA_CONFIG / "falhas_criticas" / "reveal_process.flag"


def reveal_flag_path() -> Path:
    REVEAL_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return REVEAL_FLAG_PATH


def request_reveal() -> None:
    reveal_flag_path().write_text("1", encoding="utf-8")


def clear_reveal() -> None:
    try:
        reveal_flag_path().unlink(missing_ok=True)
    except OSError:
        pass


def is_reveal_requested() -> bool:
    return reveal_flag_path().is_file()


def consume_reveal() -> bool:
    if not is_reveal_requested():
        return False
    clear_reveal()
    return True
