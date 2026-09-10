"""Orquestração do bot Prioridades por nível hierárquico."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.bots.prioridades_nh.brflow_fetch import (
    open_prioridade_page,
)
from app.bots.prioridades_nh.ui_pesquisa import pesquisar_nh_via_ui
from app.config.selectors import brflow, okta
from app.core.bot_runtime import BotRuntime
from app.core.common import safe_close_driver
from app.core.credentials import get_credentials
from app.infrastructure.selenium_helpers import (
    create_driver,
    login_okta_resiliente,
    send_keys_to_element,
)

log = logging.getLogger("robots.prioridades_nh")

_runtime = BotRuntime(mode="prioridades_nh")
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
parar_event = _runtime.parar_event
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress

HORA_PADRAO = 5
MINUTO_PADRAO = 0
SAVED_PREFIX = "PRIORIDADES_NH_SAVED|"


def _parse_schedule(settings: dict) -> tuple[int, int]:
    raw_h = str(settings.get("prioridades_nh_hora", HORA_PADRAO)).strip()
    raw_m = str(settings.get("prioridades_nh_minuto", MINUTO_PADRAO)).strip()
    try:
        hora = max(0, min(23, int(raw_h)))
    except (TypeError, ValueError):
        hora = HORA_PADRAO
    try:
        minuto = max(0, min(59, int(raw_m)))
    except (TypeError, ValueError):
        minuto = MINUTO_PADRAO
    return hora, minuto


def _seconds_until(hour: int, minute: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _load_nh_list(settings: dict) -> list[dict[str, Any]]:
    path = str(settings.get("nh_list_path") or os.environ.get("PRIORIDADES_NH_LIST_PATH") or "").strip()
    if not path or not Path(path).is_file():
        raise RuntimeError(
            "Lista de NHs de atendimento não encontrada. "
            "Reinicie o robô pelo portal para regenerar o arquivo."
        )
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        items = data.get("items") or data.get("results") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("nome") or "").strip()
        if not name:
            continue
        sid = item.get("sharepoint_id")
        try:
            sharepoint_id = int(sid) if sid not in (None, "") else None
        except (TypeError, ValueError):
            sharepoint_id = None
        out.append(
            {
                "id": item.get("id"),
                "name": name,
                "sharepoint_id": sharepoint_id,
            }
        )
    return out


def _output_dir(settings: dict) -> Path:
    raw = (
        str(settings.get("output_dir") or "").strip()
        or os.environ.get("ROBOT_OUTPUT_DIR")
        or os.environ.get("PRIORIDADES_NH_OUTPUT_DIR")
        or ""
    ).strip()
    if raw:
        path = Path(raw)
    else:
        path = Path.home() / "PPLID" / "prioridades_nh"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _login_brflow(driver, user: str, pwd: str) -> None:
    from app.bots.prioridades_nh.brflow_fetch import (
        _canonicalize_brflow_host,
        _ensure_brflow_index,
        _switch_to_brflow_window,
        _wait_loading_gone,
    )

    if not user or not pwd:
        creds = get_credentials()
        user = user or creds[0]
        pwd = pwd or creds[1]
    if not user or not pwd:
        raise RuntimeError("Credenciais Okta não fornecidas")

    driver.get(okta.O_LINK)
    login_okta_resiliente(driver, user, pwd, timeout=25, wait_for_post_login=True, max_retries=1)

    before = list(driver.window_handles)
    search = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))
    search.clear()
    search.send_keys("brflow")
    search.send_keys(Keys.ENTER)
    try:
        WebDriverWait(driver, 30).until(lambda d: len(d.window_handles) > len(before))
        driver.switch_to.window(driver.window_handles[-1])
    except Exception:
        send_keys_to_element(driver, By.ID, okta.O_pesquisar, "brflow" + Keys.ENTER)
        WebDriverWait(driver, 20).until(lambda d: len(d.window_handles) > 1)
        driver.switch_to.window(driver.window_handles[-1])

    # Fecha abas extras (Okta) e estabiliza sessão BrFlow no host apex
    keep = driver.current_window_handle
    for handle in list(driver.window_handles):
        if handle == keep:
            continue
        try:
            driver.switch_to.window(handle)
            driver.close()
        except Exception:
            pass
    driver.switch_to.window(keep)
    time.sleep(3.0)
    _switch_to_brflow_window(driver)
    _canonicalize_brflow_host(driver)
    WebDriverWait(driver, 40).until(EC.presence_of_element_located((By.XPATH, brflow.B_usuario)))
    _wait_loading_gone(driver)
    _ensure_brflow_index(driver)


def executar_ciclo(settings: dict | None = None) -> Path:
    settings = dict(settings or {})
    nhs = _load_nh_list(settings)
    if not nhs:
        raise RuntimeError("Nenhum NH ativo em nivel-hierarquico-atendimento")

    matricula = settings.get("matricula") or os.getenv("NIVEL_USER") or os.getenv("OKTA_USER")
    senha = settings.get("senha") or os.getenv("NIVEL_PASS") or os.getenv("OKTA_PASS")
    headless_raw = (
        os.getenv("PRIORIDADES_NH_HEADLESS")
        or os.getenv("ROBOT_HEADLESS")
        or ("1" if settings.get("headless") else "0")
    )
    headless = str(headless_raw).strip().lower() in ("1", "true", "yes", "on")

    out_dir = _output_dir(settings)
    drv = None
    rows: list[dict[str, Any]] = []
    mismatches: list[str] = []

    try:
        _set_status("Prioridades NH: iniciando Chrome")
        _set_progress(5)
        drv = create_driver(headless=headless, download_dir=str(out_dir))
        _set_status("Prioridades NH: autenticando Okta/BrFlow")
        _set_progress(15)
        _login_brflow(drv, str(matricula or ""), str(senha or ""))
        if parar_event.is_set():
            raise KeyboardInterrupt("parado")

        _set_status("Prioridades NH: abrindo tela Prioridade NH x WF")
        _set_progress(20)
        open_prioridade_page(drv, is_cancelled=parar_event.is_set)
        _set_status("Prioridades NH: tela aberta — pesquisando NHs via Select2")
        _set_progress(25)
        total = len(nhs)
        for idx, nh in enumerate(nhs, start=1):
            if parar_event.is_set():
                raise KeyboardInterrupt("parado")
            name = nh["name"]
            _set_status(f"Prioridades NH: Select2 {idx}/{total} — {name[:60]}")
            pct = 25 + int(70 * (idx - 1) / max(total, 1))
            _set_progress(pct)

            try:
                data = pesquisar_nh_via_ui(drv, name)
            except Exception as exc:
                log.exception("Falha pesquisa UI NH=%s", name)
                mismatches.append(f"{name} ({exc})")
                continue
            for item in data:
                row = dict(item)
                row["hierarchical_level_id"] = nh.get("id")
                rows.append(row)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"prioridades_nh_{stamp}.json"
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "nh_count": total,
            "row_count": len(rows),
            "mismatches": mismatches,
            "rows": rows,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"{SAVED_PREFIX}{out_path}", flush=True)
        _set_progress(100)
        _set_status(
            f"Prioridades NH: gravado {len(rows)} linhas"
            + (f" · {len(mismatches)} NH sem match" if mismatches else "")
        )
        return out_path
    finally:
        if drv is not None:
            safe_close_driver(drv)


def start(settings=None):
    settings = dict(settings or {})
    parar_event.clear()
    executar_imediatamente = bool(settings.get("executar_imediatamente", False))
    hora, minuto = _parse_schedule(settings)

    if executar_imediatamente:
        _set_status("Prioridades NH: execução imediata")
        executar_ciclo(settings)
        _set_status("Prioridades NH: concluído")
        return

    _set_status(f"Prioridades NH: aguardando {hora:02d}:{minuto:02d}")
    while not parar_event.is_set():
        wait_sec = _seconds_until(hora, minuto)
        log.info("Aguardando %.0fs até %02d:%02d", wait_sec, hora, minuto)
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            if parar_event.is_set():
                _set_status("Prioridades NH: parado")
                return
            time.sleep(1)

        _set_status("Prioridades NH: iniciando ciclo agendado")
        try:
            executar_ciclo(settings)
            _set_status(f"Prioridades NH: ciclo concluído — próximo às {hora:02d}:{minuto:02d}")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.exception("Erro no ciclo Prioridades NH")
            _set_status(f"Prioridades NH: erro — {exc}")
        time.sleep(2)


def stop():
    parar_event.set()
    _set_status("Prioridades NH: parando...")
