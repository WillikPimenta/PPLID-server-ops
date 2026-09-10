"""Orquestração do bot Falhas Críticas (Power BI + Report)."""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.bots.falhas_criticas.powerbi import config as pbi_config
from app.bots.falhas_criticas.powerbi.pipeline import run_powerbi_pipeline
from app.bots.falhas_criticas.reveal_process import clear_reveal, is_reveal_requested
from app.core.bot_runtime import BotRuntime

log = logging.getLogger("robots.bot_falhas_criticas")

_runtime = BotRuntime(mode="falhas_criticas")
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
parar_event = _runtime.parar_event
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress

HORA_PADRAO = 8
MINUTO_PADRAO = 0


def _backend_root() -> Path:
    env = __import__("os").environ.get("REPORT_FALHAS_BACKEND_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[4] / "backend"


def _ensure_report_falhas_importable() -> None:
    backend = _backend_root()
    backend_str = str(backend)
    if backend.exists() and backend_str not in sys.path:
        sys.path.insert(0, backend_str)


def _parse_schedule(settings: dict) -> tuple[int, int]:
    raw_h = str(settings.get("falhas_criticas_hora", HORA_PADRAO)).strip()
    raw_m = str(settings.get("falhas_criticas_minuto", MINUTO_PADRAO)).strip()
    try:
        hora = max(0, min(23, int(raw_h)))
    except (TypeError, ValueError):
        hora = HORA_PADRAO
    try:
        minuto = max(0, min(59, int(raw_m)))
    except (TypeError, ValueError):
        minuto = MINUTO_PADRAO
    return hora, minuto


def _is_background_mode(settings: dict) -> bool:
    return bool(settings.get("modo_segundo_plano"))


def _wait_login(message: str) -> None:
    """Callback para Playwright/Excel — poll curto para permitir retentativas de auto-login."""
    if is_reveal_requested():
        _set_status("Aguardando: revelando processo (Excel)…")
    else:
        _set_status(f"Aguardando: {message[:120]}")
    if parar_event.is_set():
        raise KeyboardInterrupt("Bot interrompido durante espera de login")
    time.sleep(pbi_config.LOGIN_POLL_SECONDS)


def _progress(pct: int, msg: str) -> None:
    _set_progress(max(0, min(100, pct)))
    _set_status(msg)


def _resolve_headless(settings: dict) -> bool:
    if _is_background_mode(settings):
        return True
    return bool(settings.get("headless"))


def _should_skip_refresh(settings: dict) -> bool:
    """Respeita falhas_refresh_queries (UI); ignora chave legada skip_refresh_queries."""
    if "falhas_refresh_queries" in settings:
        return not bool(settings.get("falhas_refresh_queries"))
    return bool(settings.get("skip_refresh_queries"))


def executar_pipeline(settings: dict | None = None) -> None:
    settings = dict(settings or {})
    background = _is_background_mode(settings)
    clear_reveal()

    if background:
        settings["headless"] = True
        settings["excel_visible"] = False
        _set_status("Falhas Críticas: em 2º plano (Power BI/Excel)")
    else:
        settings.setdefault("excel_visible", True)

    pbi_config.apply_settings(settings)
    pbi_config.set_wait_fn(_wait_login)
    log.info("Export Power BI → %s", pbi_config.DOWNLOADS_DIR)
    log.info("Merge na master → %s", pbi_config.TARGET_EXCEL)

    def _prog(pct: int, msg: str) -> None:
        if background and pct < 65:
            _progress(pct, f"2º plano · {msg}")
        else:
            _progress(pct, msg)

    try:
        run_powerbi_pipeline(
            settings,
            progress_fn=_prog,
            headless=_resolve_headless(settings),
            skip_merge=bool(settings.get("skip_merge")),
            skip_refresh=_should_skip_refresh(settings),
        )
    finally:
        pbi_config.set_wait_fn(None)

    if settings.get("gerar_executivo"):
        _set_status("Falhas: relatório executivo ATIVO — será gerado após BSB/SC")
    else:
        _set_status(
            "Falhas: relatório executivo desativado "
            "(marque Executivo comparativo e Salvar, ou Inicie com o modal aberto)"
        )

    _progress(65, "Falhas: gerando relatórios e abrindo e-mail")
    from app.bots.falhas_criticas.powerbi.merge_base import ensure_master_workbook

    master_path = ensure_master_workbook(pbi_config.TARGET_EXCEL, wait_s=12.0)
    _ensure_report_falhas_importable()
    from report_falhas.legacy_main import run_report

    report_settings = {
        "falhas_excel_path": str(master_path),
        "output_dir": str(settings.get("output_dir") or "").strip(),
        "mes_referencia": settings.get("mes_referencia"),
        "gerar_consolidado": settings.get("gerar_consolidado", True),
        "usar_periodo_custom": settings.get("usar_periodo_custom", False),
        "custom_start": settings.get("custom_start"),
        "custom_end": settings.get("custom_end"),
        "preview_email": settings.get("preview_email", True),
        "email_from": settings.get("email_from"),
        "gerar_executivo": settings.get("gerar_executivo", False),
        "salvar_html_individuais": settings.get("salvar_html_individuais", False),
    }
    output_result = run_report(report_settings)
    if output_result:
        print(f"FALHAS_OUTPUT|{json.dumps(output_result, ensure_ascii=False)}", flush=True)
    clear_reveal()
    _progress(100, "Falhas: pipeline concluído")


def _seconds_until(hour: int, minute: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def start(settings=None):
    settings = dict(settings or {})
    parar_event.clear()
    clear_reveal()
    executar_imediatamente = bool(settings.get("executar_imediatamente", False))
    hora, minuto = _parse_schedule(settings)

    if executar_imediatamente:
        if _is_background_mode(settings):
            _set_status("Falhas Críticas: execução imediata em 2º plano")
        else:
            _set_status("Falhas Críticas: execução imediata")
        executar_pipeline(settings)
        _set_status("Falhas Críticas: concluído")
        return

    _set_status(f"Falhas Críticas: aguardando {hora:02d}:{minuto:02d}")
    while not parar_event.is_set():
        wait_sec = _seconds_until(hora, minuto)
        log.info("Aguardando %.0fs até %02d:%02d", wait_sec, hora, minuto)
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            if parar_event.is_set():
                _set_status("Falhas Críticas: parado")
                return
            time.sleep(1)

        _set_status("Falhas Críticas: iniciando ciclo agendado")
        try:
            executar_pipeline(settings)
            _set_status(f"Falhas Críticas: ciclo concluído — próximo às {hora:02d}:{minuto:02d}")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.exception("Erro no pipeline Falhas Críticas")
            _set_status(f"Falhas Críticas: erro — {exc}")
        time.sleep(2)


def stop():
    parar_event.set()
    clear_reveal()
    _set_status("Falhas Críticas: parando...")
