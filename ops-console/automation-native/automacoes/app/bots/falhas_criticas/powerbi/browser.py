"""Gerenciamento do navegador Playwright e autenticação."""
from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from playwright.sync_api import BrowserContext, Frame, Locator, Page, Playwright, sync_playwright

import app.bots.falhas_criticas.powerbi.config as config


@dataclass
class BrowserSession:
    playwright: Playwright
    context: BrowserContext
    page: Page
    report_frame: Frame | None = None


def _get_screen_work_area() -> tuple[int, int]:
    """Largura e altura úteis da tela (sem barra de tarefas)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", wintypes.LONG),
                    ("top", wintypes.LONG),
                    ("right", wintypes.LONG),
                    ("bottom", wintypes.LONG),
                ]

            rect = RECT()
            # SPI_GETWORKAREA = 48
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                return max(800, rect.right - rect.left), max(600, rect.bottom - rect.top)
        except Exception:
            pass
    return 1366, 768


def _maximize_browser_window(page: Page) -> None:
    """Maximiza a janela do Edge para caber na tela do operador."""
    try:
        session = page.context.new_cdp_session(page)
        info = session.send("Browser.getWindowForTarget")
        session.send(
            "Browser.setWindowBounds",
            {"windowId": info["windowId"], "bounds": {"windowState": "maximized"}},
        )
    except Exception:
        pass


def _iter_login_contexts(page: Page) -> Iterator[Page | Frame]:
    yield page
    for frame in page.frames:
        if frame != page.main_frame:
            yield frame


def _try_click_locator(locator: Locator, label: str) -> bool:
    try:
        count = locator.count()
    except Exception:
        return False
    for i in range(count):
        try:
            target = locator.nth(i)
            if not target.is_visible(timeout=400):
                continue
            if not target.is_enabled(timeout=400):
                continue
            try:
                target.click(timeout=3000)
            except Exception:
                target.click(timeout=3000, force=True)
            print(f"Tentativa auto-login Microsoft: clicou em {label}")
            return True
        except Exception:
            continue
    return False


def _try_click_first_visible(
    make_locator: Callable[[Page | Frame], Locator | None],
    label: str,
    page: Page,
) -> bool:
    for ctx in _iter_login_contexts(page):
        try:
            locator = make_locator(ctx)
            if locator is None:
                continue
            if _try_click_locator(locator, label):
                return True
        except Exception:
            continue
    return False


def _try_auto_login(page: Page) -> bool:
    """Tenta avançar telas SSO Microsoft sem digitar senha (conta / Entrar / Sim)."""
    for hint in config.LOGIN_ACCOUNT_EMAIL_HINTS:
        if _try_click_first_visible(
            lambda ctx, h=hint: ctx.get_by_text(h, exact=False),
            f"conta ({hint})",
            page,
        ):
            return True

    for selector in config.LOGIN_AUTO_CLICK_SELECTORS:
        if _try_click_first_visible(
            lambda ctx, sel=selector: ctx.locator(sel),
            selector,
            page,
        ):
            return True

    for pattern in config.LOGIN_AUTO_CLICK_LABELS:
        if _try_click_first_visible(
            lambda ctx, pat=pattern: ctx.get_by_role(
                "button", name=re.compile(pat, re.IGNORECASE)
            ),
            pattern,
            page,
        ):
            return True

    return False


def _page_has_login(page: Page) -> bool:
    url = page.url.lower()
    if "login.microsoftonline.com" in url or "login.live.com" in url:
        return True
    for ctx in _iter_login_contexts(page):
        for marker in config.LOGIN_MARKERS:
            if marker.startswith("login."):
                continue
            try:
                if ctx.get_by_text(marker, exact=False).first.is_visible(timeout=300):
                    return True
            except Exception:
                pass
    return False


def _report_is_loaded(page: Page) -> bool:
    for marker in config.REPORT_LOAD_MARKERS:
        try:
            if page.get_by_text(marker, exact=False).first.is_visible(timeout=500):
                print(f"Relatório detectado: '{marker}'")
                return True
        except Exception:
            pass

    for frame in page.frames:
        for marker in config.REPORT_LOAD_MARKERS:
            try:
                if frame.get_by_text(marker, exact=False).first.is_visible(timeout=300):
                    print(f"Relatório detectado no frame: '{marker}'")
                    return True
            except Exception:
                pass
    return False


def _wait_for_report(page: Page) -> None:
    deadline = time.time() + (config.PAGE_LOAD_TIMEOUT / 1000)
    login_started_at: float | None = None
    manual_hint_shown = False

    while time.time() < deadline:
        if _report_is_loaded(page):
            return

        if _page_has_login(page):
            if login_started_at is None:
                login_started_at = time.time()

            if _try_auto_login(page):
                page.wait_for_timeout(config.LOGIN_AUTO_SETTLE_MS)
                continue

            elapsed = time.time() - (login_started_at or time.time())
            if elapsed >= config.LOGIN_MANUAL_HINT_AFTER_SEC and not manual_hint_shown:
                config.wait_for_user(
                    "Autenticação Microsoft pendente — conclua login manualmente "
                    "(senha/MFA) se necessário."
                )
                manual_hint_shown = True
            else:
                config.wait_for_user("Autenticando no Microsoft...")
            continue

        login_started_at = None
        manual_hint_shown = False
        page.wait_for_timeout(1000)

    raise TimeoutError(
        "Tempo esgotado aguardando o relatório Power BI carregar. "
        "Verifique login e permissões de acesso."
    )


def find_report_frame(page: Page) -> Frame:
    """Localiza o iframe que contém o conteúdo do relatório."""
    markers = ["Filtro operacional", "Tipo de Falha", "Relatório detalhado", "Data Auditoria"]

    for frame in page.frames:
        for marker in markers:
            try:
                if frame.get_by_text(marker, exact=False).first.is_visible(timeout=500):
                    print(f"Frame do relatório encontrado via '{marker}'")
                    return frame
            except Exception:
                pass

    # Fallback: maior iframe visível
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            body = frame.locator("body")
            if body.count() > 0 and body.first.is_visible(timeout=500):
                print("Frame do relatório encontrado via fallback (iframe)")
                return frame
        except Exception:
            pass

    print("Usando frame principal como fallback")
    return page.main_frame


def open_browser(headless: bool = False) -> BrowserSession:
    """Abre navegador com perfil persistente e navega até o relatório."""
    config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    config.ERRORS_DIR.mkdir(parents=True, exist_ok=True)
    config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    playwright = sync_playwright().start()
    screen_w, screen_h = _get_screen_work_area()
    launch_kwargs = {
        "user_data_dir": str(config.BROWSER_PROFILE_DIR),
        "headless": headless,
        "accept_downloads": True,
        "locale": "pt-BR",
    }
    if headless:
        launch_kwargs["viewport"] = {"width": screen_w, "height": screen_h}
    else:
        # Sem viewport fixo — janela real do Edge segue o tamanho da tela.
        launch_kwargs["no_viewport"] = True
        launch_kwargs["args"] = ["--start-maximized"]
    # Usa Edge instalado no Windows (evita download do Chromium em redes corporativas)
    try:
        context = playwright.chromium.launch_persistent_context(
            channel="msedge",
            **launch_kwargs,
        )
    except Exception:
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
    context.set_default_timeout(config.ACTION_TIMEOUT)

    page = context.pages[0] if context.pages else context.new_page()
    if not headless:
        _maximize_browser_window(page)
    print("Abrindo relatório Power BI...")
    try:
        page.goto(config.REPORT_URL, wait_until="commit", timeout=config.PAGE_LOAD_TIMEOUT)
    except Exception as exc:
        print(f"Aviso na navegação: {exc}")
        if "powerbi.com" not in page.url.lower() and "login.microsoftonline.com" not in page.url.lower():
            raise
        print("Continuando — página parece ter carregado parcialmente.")

    _wait_for_report(page)
    page.wait_for_timeout(5000)

    report_frame = find_report_frame(page)
    # Aguarda spinners iniciais do relatório
    try:
        spinner = report_frame.locator(".spinner-background")
        for _ in range(60):
            if spinner.count() == 0 or not spinner.first.is_visible(timeout=300):
                break
            page.wait_for_timeout(500)
    except Exception:
        pass
    return BrowserSession(
        playwright=playwright,
        context=context,
        page=page,
        report_frame=report_frame,
    )


def close_browser(session: BrowserSession) -> None:
    """Encerra contexto e Playwright."""
    try:
        session.context.close()
    finally:
        session.playwright.stop()


def save_error_screenshot(session: BrowserSession, name: str) -> str | None:
    """Salva screenshot de erro e retorna o caminho."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = config.ERRORS_DIR / f"{name}_{timestamp}.png"
    try:
        session.page.screenshot(path=str(path), full_page=True)
        print(f"Screenshot salvo: {path}")
        return str(path)
    except Exception as exc:
        print(f"Não foi possível salvar screenshot: {exc}")
        return None
