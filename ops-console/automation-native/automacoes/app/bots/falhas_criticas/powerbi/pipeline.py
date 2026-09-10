"""Pipeline Power BI: exportar Falhas e consolidar na Base."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from app.bots.falhas_criticas.powerbi import config
from app.bots.falhas_criticas.powerbi.browser import (
    BrowserSession,
    close_browser,
    find_report_frame,
    open_browser,
    save_error_screenshot,
)
from app.bots.falhas_criticas.powerbi.export import export_table
from app.bots.falhas_criticas.powerbi.filters import apply_all_filters
from app.bots.falhas_criticas.powerbi.merge_base import append_to_base


ProgressFn = Callable[[int, str], None]


def _noop_progress(pct: int, msg: str) -> None:
    pass


def _parse_period() -> tuple:
    start_s, end_s = config.get_date_range()
    try:
        start = datetime.strptime(start_s, "%d/%m/%Y").date()
        end = datetime.strptime(end_s, "%d/%m/%Y").date()
        return start, end
    except ValueError:
        return None, None


def run_powerbi_pipeline(
    settings: dict | None = None,
    *,
    progress_fn: ProgressFn | None = None,
    headless: bool = False,
    skip_merge: bool = False,
    skip_refresh: bool = False,
) -> Path:
    """Exporta do Power BI, consolida na aba Base e retorna path da planilha master."""
    progress = progress_fn or _noop_progress
    settings = settings or {}
    config.apply_settings(settings)

    progress(5, "Falhas: abrindo Power BI")
    session: BrowserSession | None = None
    try:
        session = open_browser(headless=headless or bool(settings.get("headless")))
        frame = session.report_frame or find_report_frame(session.page)

        progress(20, "Falhas: aplicando filtros")
        apply_all_filters(frame)

        progress(35, "Falhas: exportando tabela")
        export_path = export_table(session.page, frame)
        print(f"[export] Pasta BI: {config.DOWNLOADS_DIR}")

        if skip_merge:
            progress(55, "Falhas: export concluído (sem merge)")
            return export_path

        progress(50, "Falhas: consolidando na Base")
        period_start, period_end = _parse_period()
        master, merge_stats = append_to_base(
            export_path,
            refresh_queries=not skip_refresh,
            period_start=period_start,
            period_end=period_end,
        )
        merge_status = merge_stats.status_message()
        if merge_stats.warnings:
            merge_status = f"{merge_status} · {merge_stats.warnings[0]}"
        progress(60, merge_status)
        return master
    except Exception:
        if session:
            save_error_screenshot(session, "erro")
        raise
    finally:
        if session:
            close_browser(session)
