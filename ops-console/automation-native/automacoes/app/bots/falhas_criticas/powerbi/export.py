"""Exportação da tabela Falhas para CSV."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from playwright.sync_api import Download, Frame, Page

import app.bots.falhas_criticas.powerbi.config as config


def _log(msg: str) -> None:
    print(f"[export] {msg}")


def _retry(action, attempts: int = 3, delay_ms: int = 1000):
    last_error = None
    for i in range(attempts):
        try:
            return action()
        except Exception as exc:
            last_error = exc
            _log(f"Tentativa {i + 1}/{attempts} falhou: {exc}")
            time.sleep(delay_ms / 1000)
    raise last_error


def select_falhas_tab(frame: Frame) -> None:
    """Garante que a aba 'Falhas' está selecionada."""
    _log("Selecionando aba Falhas...")
    frame.page.wait_for_timeout(1000)

    # Rolar até Relatório detalhado
    header = frame.get_by_text("Relatório detalhado", exact=False)
    if header.count() > 0:
        header.first.scroll_into_view_if_needed()
        frame.page.wait_for_timeout(500)

    falhas_btn = frame.get_by_role("button", name="Falhas", exact=True)
    if falhas_btn.count() == 0:
        falhas_btn = frame.get_by_text("Falhas", exact=True)

    btn = falhas_btn.first
    btn.scroll_into_view_if_needed()
    btn.click(timeout=10000)
    frame.page.wait_for_timeout(config.FILTER_SETTLE_MS)
    _log("Aba Falhas selecionada.")


def _find_table_visual(frame: Frame):
    """Localiza o visual da tabela de falhas."""
    markers = ["Data Auditoria", "Protocolo", "Etapa", "Cliente"]
    for marker in markers:
        col = frame.get_by_text(marker, exact=False)
        if col.count() > 0:
            visual = col.first.locator(
                "xpath=ancestor::*[contains(@class,'visual') or contains(@class,'tableEx') or @role='grid'][1]"
            )
            if visual.count() > 0:
                return visual.first

    # Fallback: tabela/grid visível
    table = frame.locator("[role='grid'], .tableEx, .pivotTableVisual").first
    if table.count() > 0 and table.is_visible():
        return table

    raise RuntimeError("Tabela 'Falhas' não encontrada no relatório.")


def _find_export_dialog(page: Page):
    """Localiza o modal 'Quais dados você deseja exportar?' na página."""
    for text in ("Quais dados você deseja exportar", "Which data do you want to export"):
        dialog = page.locator("[role='dialog']").filter(has_text=text)
        if dialog.count() > 0:
            return dialog.last

    dialog = page.locator("[role='dialog']").filter(has=page.get_by_role("button", name=re.compile(r"Exportar|Export", re.I)))
    if dialog.count() > 0:
        return dialog.last

    return page.locator("[role='dialog']").last


def _configure_export_dialog(page: Page, dialog) -> None:
    """Seleciona 'Dados com layout atual' e mantém o formato padrão do Power BI."""
    layout = dialog.get_by_text("Dados com layout atual", exact=False)
    if layout.count() == 0:
        layout = dialog.get_by_text("Data with current layout", exact=False)
    if layout.count() > 0:
        layout.first.click(force=True)
        page.wait_for_timeout(500)
        _log("Opção 'Dados com layout atual' selecionada.")
    else:
        _log("Opção 'Dados com layout atual' já selecionada ou não encontrada — seguindo com padrão.")


def export_table(page: Page, frame: Frame) -> Path:
    """Exporta tabela via menu ... > Exportar dados (formato padrão: xlsx ou csv)."""
    select_falhas_tab(frame)
    visual = _find_table_visual(frame)
    visual.scroll_into_view_if_needed()
    frame.page.wait_for_timeout(1000)

    def _open_export_menu():
        visual.hover(force=True)
        frame.page.wait_for_timeout(800)

        more_btn = visual.locator(
            "[aria-label*='Mais opções'], [aria-label*='More options'], "
            "[title*='Mais opções'], [title*='More options'], "
            "button[class*='ellipsis']"
        ).first
        if more_btn.count() == 0:
            more_btn = frame.locator(
                "[aria-label*='Mais opções'], [aria-label*='More options']"
            ).last
        more_btn.click(force=True, timeout=8000)
        frame.page.wait_for_timeout(500)

        export_item = frame.get_by_text("Exportar dados", exact=False)
        if export_item.count() == 0:
            export_item = frame.get_by_text("Export data", exact=False)
        export_item.first.click(force=True, timeout=8000)

    _retry(_open_export_menu)

    _log("Confirmando exportação...")
    page.wait_for_timeout(1500)

    dialog = _find_export_dialog(page)
    dialog.wait_for(state="visible", timeout=15_000)
    _configure_export_dialog(page, dialog)

    config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    _log("Aguardando download...")
    with page.expect_download(timeout=config.DOWNLOAD_TIMEOUT) as download_info:
        export_btn = dialog.get_by_role("button", name=re.compile(r"^Exportar$|^Export$", re.I))
        if export_btn.count() == 0:
            export_btn = page.get_by_role("button", name=re.compile(r"Exportar|Export", re.I))
        export_btn.last.click(force=True, timeout=15_000)

    download: Download = download_info.value
    suggested = download.suggested_filename or f"falhas_{timestamp}.xlsx"
    ext = Path(suggested).suffix or ".xlsx"
    dest_path = config.DOWNLOADS_DIR / f"falhas_{timestamp}{ext}"
    download.save_as(str(dest_path))
    _log(f"Arquivo salvo em: {dest_path}")
    return dest_path


def open_file(path: Path) -> None:
    """Abre o arquivo exportado com o aplicativo padrão do Windows."""
    _log(f"Abrindo arquivo: {path}")
    os.startfile(str(path))


# Alias legado
export_table_csv = export_table
open_csv = open_file
