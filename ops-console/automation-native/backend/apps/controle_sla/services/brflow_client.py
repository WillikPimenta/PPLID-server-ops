"""Cliente BrFlow: login Okta + sessão Selenium viva para consultar a fila."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable

from apps.controle_sla.exceptions import AuthFailureError, ControleSlaError, SessionExpiredError

log = logging.getLogger(__name__)

BRFLOW_ORIGIN = "https://brflow.com.br"  # HAR real (sem www) — www quebra cookies/sessão
BRFLOW_ORIGIN_WWW = "https://www.brflow.com.br"
BRFLOW_FILA_PATH = "/BrFlow/relatorio/pesquisar-fila-de-analise-geral?"
BRFLOW_FILA_URL = f"{BRFLOW_ORIGIN}{BRFLOW_FILA_PATH}"
BRFLOW_VER_FILA_PATH = "/BrFlow/relatorio/ver-fila-de-analise-geral/"
BRFLOW_INDEX = f"{BRFLOW_ORIGIN}/BrFlow/index/index"

AUTH_FAIL_HINTS = (
    "senha incorreta",
    "senha inválida",
    "senha invalida",
    "invalid password",
    "authentication failed",
    "unable to sign in",
    "não foi possível entrar",
    "nao foi possivel entrar",
    "credenciais inválidas",
    "credenciais invalidas",
)


class CancelledError(ControleSlaError):
    """Usuário cancelou a conexão em andamento."""


def _looks_like_auth_failure(message: str) -> bool:
    text = (message or "").lower()
    return any(h in text for h in AUTH_FAIL_HINTS)


def _ensure_automacoes():
    try:
        import app  # noqa: F401
    except ModuleNotFoundError as exc:
        raise ControleSlaError(
            "Pacote de automações não instalado. Execute: pip install -e ../automacoes"
        ) from exc


def _check_cancel(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled and is_cancelled():
        raise CancelledError("Conexão cancelada pelo usuário.")


class BrflowBrowserSession:
    """Mantém o Chrome autenticado e consulta a fila no contexto do browser."""

    def __init__(self, driver: Any):
        self._driver = driver
        self._lock = threading.RLock()

    @property
    def driver(self) -> Any:
        return self._driver

    def close(self) -> None:
        with self._lock:
            drv = self._driver
            self._driver = None
        if drv is None:
            return
        try:
            drv.quit()
        except Exception:
            log.debug("Falha ao fechar Chrome BrFlow", exc_info=True)

    def fetch_fila(self) -> list[dict[str, Any]]:
        with self._lock:
            driver = self._driver
            if driver is None:
                raise SessionExpiredError("Sessão BrFlow encerrada.")
            _prepare_fila_context(driver)
            return _probe_fila_in_browser(driver, allow_session_retry=False)


def _friendly_browser_error(exc: BaseException, *, driver: Any | None = None) -> str:
    """Mensagem curta para UI — sem stacktrace do chromedriver."""
    name = type(exc).__name__
    raw = str(exc) or ""
    if "Stacktrace:" in raw:
        raw = raw.split("Stacktrace:", 1)[0]
    raw = raw.replace("Message:", "").strip()
    raw = " ".join(raw.split())
    detail = ""
    if driver is not None:
        try:
            detail = f" (url={ (driver.current_url or '')[:120] })"
        except Exception:
            detail = ""
    if name in {"TimeoutException", "NoSuchElementException", "WebDriverException", "InvalidSessionIdException"}:
        return (
            "Timeout ao concluir o SSO do BrFlow após o Okta. "
            "O login Okta funcionou, mas a sessão do BrFlow não abriu. "
            "Uma janela do Chrome deve abrir durante a conexão — não feche até concluir."
            f"{detail}"
        )
    if isinstance(exc, SessionExpiredError) and raw:
        return raw[:280] + detail
    if not raw or raw.lower() in {"message", "message:"}:
        return f"Falha no navegador automatizado ({name}). Tente novamente.{detail}"
    if len(raw) > 280:
        return raw[:277] + "..."
    return raw + detail


def _resolve_headless(requested: bool | None = None) -> bool:
    """Robôs Okta/BrFlow usam janela visível por padrão — headless quebra o SSO com frequência."""
    import os

    if requested is not None:
        return bool(requested)
    raw = (os.getenv("CONTROLE_SLA_HEADLESS") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _switch_to_brflow_window(driver) -> None:
    for handle in list(driver.window_handles):
        driver.switch_to.window(handle)
        if "brflow.com.br" in (driver.current_url or "").lower():
            return


def _canonicalize_brflow_host(driver) -> None:
    """Força host sem www (mesmo host do HAR / sessão PHP)."""
    url = driver.current_url or ""
    lower = url.lower()
    if "www.brflow.com.br" in lower:
        target = url.replace("https://www.brflow.com.br", BRFLOW_ORIGIN).replace(
            "http://www.brflow.com.br", BRFLOW_ORIGIN
        )
        log.info("BrFlow: normalizando host www → apex (%s)", target[:120])
        driver.get(target)
        time.sleep(1.5)
        _wait_brflow_loading_gone(driver)


def _close_other_windows(driver, keep_handle: str) -> None:
    for handle in list(driver.window_handles):
        if handle == keep_handle:
            continue
        try:
            driver.switch_to.window(handle)
            driver.close()
        except Exception:
            pass
    try:
        driver.switch_to.window(keep_handle)
    except Exception:
        if driver.window_handles:
            driver.switch_to.window(driver.window_handles[-1])


def _wait_new_window(driver, before_handles: list[str], *, timeout: float = 20) -> bool:
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        WebDriverWait(driver, timeout).until(lambda d: len(d.window_handles) > len(before_handles))
        driver.switch_to.window(driver.window_handles[-1])
        return True
    except Exception:
        _switch_to_brflow_window(driver)
        return False


def _wait_brflow_loading_gone(driver, *, timeout: float = 20) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located((By.ID, "sistema-loading")))
    except Exception:
        pass


def _page_looks_like_brflow_login(driver) -> bool:
    url = (driver.current_url or "").lower()
    if "/login" in url:
        return True
    try:
        page = (driver.page_source or "")[:8000].lower()
    except Exception:
        page = ""
    return (
        "login com sso" in page
        or "entrar com sso" in page
        or ("entrar com" in page and "sso" in page)
    )


def _brflow_app_ready(driver) -> bool:
    """True só com UI autenticada (#menu / B_usuario) — evita falso positivo."""
    if _page_looks_like_brflow_login(driver):
        return False
    url = (driver.current_url or "").lower()
    if "brflow" not in url or "/login" in url:
        return False
    try:
        from selenium.webdriver.common.by import By
        from app.config import brflow

        if driver.find_elements(By.XPATH, brflow.B_usuario):
            return True
        if driver.find_elements(By.ID, "menu"):
            return True
    except Exception:
        pass
    return False


def _click_brflow_sso(driver, *, is_cancelled: Callable[[], bool] | None = None) -> bool:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    _check_cancel(is_cancelled)
    before = list(driver.window_handles)
    clicked = False
    for xpath in (
        "//a[contains(., 'Login com SSO')]",
        "//button[contains(., 'Login com SSO')]",
        "//a[contains(translate(., 'sso', 'SSO'), 'SSO')]",
        "//button[contains(translate(., 'sso', 'SSO'), 'SSO')]",
        "//*[contains(text(),'Login com SSO')]",
        "//a[contains(@href,'sso') or contains(@href,'okta')]",
    ):
        try:
            el = driver.find_element(By.XPATH, xpath)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        return False

    time.sleep(1.0)
    _wait_new_window(driver, before, timeout=10)
    try:
        WebDriverWait(driver, 45).until(
            lambda d: _brflow_app_ready(d) or (
                "brflow" in (d.current_url or "").lower() and not _page_looks_like_brflow_login(d)
            )
        )
    except Exception:
        _switch_to_brflow_window(driver)
    _wait_brflow_loading_gone(driver)
    return True


def _open_brflow_from_okta_search(driver, *, is_cancelled: Callable[[], bool] | None = None) -> None:
    """Pesquisa 'brflow' no dashboard Okta e troca para a nova aba (padrão dos bots)."""
    from app.config import okta
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    _check_cancel(is_cancelled)
    wait = WebDriverWait(driver, 30)
    wait.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))
    before = list(driver.window_handles)
    search = driver.find_element(By.ID, okta.O_pesquisar)
    search.clear()
    search.send_keys("brflow")
    search.send_keys(Keys.ENTER)
    if not _wait_new_window(driver, before, timeout=30):
        # Fallback: clicar no card do app se Enter não abriu aba
        for xpath in (
            "//a[contains(@data-se,'app') and contains(translate(., 'BRFLOW', 'brflow'), 'brflow')]",
            "//*[contains(@class,'app-card') and contains(translate(., 'BRFLOW', 'brflow'), 'brflow')]",
            "//a[contains(translate(@aria-label, 'BRFLOW', 'brflow'), 'brflow')]",
        ):
            try:
                before2 = list(driver.window_handles)
                driver.find_element(By.XPATH, xpath).click()
                _wait_new_window(driver, before2, timeout=20)
                break
            except Exception:
                continue
    keep = driver.current_window_handle
    # Mantém só a aba BrFlow ativa (Okta cookies ficam no perfil do Chrome)
    _close_other_windows(driver, keep)
    time.sleep(5.0)
    _canonicalize_brflow_host(driver)
    _wait_brflow_loading_gone(driver, timeout=25)
    _check_cancel(is_cancelled)


def _parse_fila_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ControleSlaError("Resposta BrFlow inválida (não é objeto JSON).")

    typ = str(payload.get("type") or "").strip().lower()
    data = payload.get("data")
    description = str(
        payload.get("description")
        or payload.get("message")
        or payload.get("msg")
        or payload.get("erro")
        or ""
    ).strip()
    redirect_to = str(payload.get("redirectTo") or payload.get("redirect") or "").strip()
    cod = payload.get("cod") if payload.get("cod") is not None else payload.get("code")

    # success e warning com lista = fila utilizável (BrFlow usa warning em vários fluxos)
    if typ in {"success", "warning", "ok", ""}:
        if isinstance(data, list):
            if typ == "warning" and description:
                log.info("BrFlow fila type=warning com data: %s", description[:240])
            return data
        # data nulo com warning sem redirect: ainda pode ser “sem registros”
        if data in (None, "", {}) and typ in {"success", "ok"}:
            return []

    if isinstance(data, list) and not typ:
        return data

    data_preview = ""
    try:
        data_preview = json.dumps(data, ensure_ascii=False)[:300]
    except Exception:
        data_preview = repr(data)[:300]

    log.warning(
        "BrFlow fila payload inesperado: type=%s cod=%s redirect=%s desc=%s keys=%s data=%s",
        typ,
        cod,
        redirect_to[:120],
        description[:240],
        list(payload.keys()),
        data_preview,
    )

    if _looks_like_auth_failure(description):
        raise AuthFailureError(description or "Credenciais inválidas no BrFlow.")

    # LoginException / sessão expirada / redirect
    cod_text = str(cod or "")
    lower = f"{description} {redirect_to} {cod_text}".lower()
    if (
        redirect_to
        or typ in {"error", "erro", "fail", "failed"}
        or "loginexception" in cod_text.lower()
        or "sess" in lower
        or "expirou" in lower
        or "login" in lower
    ):
        raise SessionExpiredError(
            description
            or (f"BrFlow pediu redirecionamento: {redirect_to}" if redirect_to else "")
            or "Sessão BrFlow expirada. Tente conectar novamente."
        )

    raise ControleSlaError(
        description or f"Resposta inesperada do BrFlow (type={typ or '—'}, cod={cod})."
    )


def _open_fila_relatorio_view(driver, *, is_cancelled: Callable[[], bool] | None = None) -> None:
    """
    Abre o relatório como no browser real (HAR):
    1) fica em /BrFlow/index/index
    2) carrega ver-fila-de-analise-geral via menu ou XHR
    3) só então o POST pesquisar funciona (CSRF/contexto da tela)
    """
    from selenium.webdriver.common.by import By

    _check_cancel(is_cancelled)
    _switch_to_brflow_window(driver)
    _canonicalize_brflow_host(driver)

    url = (driver.current_url or "").lower()
    if "/brflow/index" not in url.replace(" ", ""):
        driver.get(BRFLOW_INDEX)
        time.sleep(2.0)
        _wait_brflow_loading_gone(driver)

    if _page_looks_like_brflow_login(driver):
        raise SessionExpiredError("BrFlow na tela de login ao abrir fila de análise.")

    clicked = False
    for xpath in (
        "//a[@data-ascii='fila de analise geral']",
        "//a[contains(@data-ascii,'fila de analise')]",
        "//a[contains(@data-ascii,'fila') and contains(@data-ascii,'analise')]",
        "//a[contains(translate(., 'ÁÂÃÀáâãà', 'AAAAAaaa'), 'Fila de Analise')]",
        "//a[contains(., 'Fila de Análise Geral')]",
        "//a[contains(., 'Fila de Analise Geral')]",
    ):
        try:
            els = driver.find_elements(By.XPATH, xpath)
            if not els:
                continue
            el = els[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.3)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            clicked = True
            log.info("BrFlow: menu Fila de Análise Geral acionado (%s)", xpath)
            break
        except Exception:
            continue

    if not clicked:
        log.info("BrFlow: menu não encontrado — carregando ver-fila via XHR (como o SPA).")
        script = """
        const done = arguments[arguments.length - 1];
        const path = arguments[0];
        const url = path + (path.includes('?') ? '&' : '?') + 'now=' + Date.now();
        fetch(url, {
          method: 'GET',
          headers: {
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'text/plain, */*; q=0.01'
          },
          credentials: 'include'
        })
          .then(async (r) => {
            const text = await r.text();
            // Injeta o HTML parcial no painel principal se existir (igual ao Controles.Tela)
            try {
              const main = document.querySelector('#layout_layout2_panel_main .w2ui-panel-content')
                || document.querySelector('#layout_layout2_panel_main')
                || document.querySelector('#content')
                || document.body;
              if (main && text && text.length > 50) {
                const wrap = document.createElement('div');
                wrap.innerHTML = text;
                // executa scripts inline (csrfId etc.)
                wrap.querySelectorAll('script').forEach((old) => {
                  const s = document.createElement('script');
                  s.text = old.textContent || '';
                  document.body.appendChild(s);
                });
              }
            } catch (e) {}
            done({ ok: r.ok, status: r.status, len: (text || '').length });
          })
          .catch((e) => done({ ok: false, status: 0, len: 0, err: String(e) }));
        """
        driver.set_script_timeout(60)
        raw = driver.execute_async_script(script, BRFLOW_VER_FILA_PATH)
        if not isinstance(raw, dict) or not raw.get("ok"):
            status = (raw or {}).get("status") if isinstance(raw, dict) else None
            raise ControleSlaError(
                f"Falha ao abrir ver-fila-de-analise-geral (status={status})."
            )
        log.info("BrFlow: ver-fila carregado via XHR (bytes=%s)", raw.get("len"))

    time.sleep(1.5)
    _wait_brflow_loading_gone(driver, timeout=25)
    _check_cancel(is_cancelled)


def _prepare_fila_context(driver, *, is_cancelled: Callable[[], bool] | None = None) -> None:
    """Garante host + index + tela do relatório antes do POST da fila."""
    _switch_to_brflow_window(driver)
    _canonicalize_brflow_host(driver)
    if _page_looks_like_brflow_login(driver) or not _brflow_app_ready(driver):
        raise SessionExpiredError("Sessão BrFlow inválida antes de consultar a fila.")
    url = (driver.current_url or "").lower()
    # Nunca navegar para a URL do POST — o HAR mantém referer em index/index
    if "pesquisar-fila" in url:
        driver.get(BRFLOW_INDEX)
        time.sleep(1.5)
        _wait_brflow_loading_gone(driver)
    _open_fila_relatorio_view(driver, is_cancelled=is_cancelled)


def _probe_fila_in_browser(
    driver,
    *,
    allow_redirect_retry: bool = True,
    allow_session_retry: bool = True,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    script = """
    const done = arguments[arguments.length - 1];
    const url = arguments[0];
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01'
      },
      body: 'codTipoFila=2&codCliente=',
      credentials: 'include'
    })
      .then(async (r) => {
        const text = await r.text();
        done({ ok: r.ok, status: r.status, ct: r.headers.get('content-type') || '', text: text.slice(0, 800000) });
      })
      .catch((e) => done({ ok: false, status: 0, ct: '', text: String(e) }));
    """
    driver.set_script_timeout(90)
    try:
        _canonicalize_brflow_host(driver)
        # Sempre path relativo no host atual (apex), como no HAR
        raw = driver.execute_async_script(script, BRFLOW_FILA_PATH)
    except Exception as exc:
        raise ControleSlaError(f"Falha ao consultar a fila no BrFlow: {exc}") from exc

    if not isinstance(raw, dict):
        raise ControleSlaError("Falha ao consultar a fila no BrFlow (browser).")

    text = str(raw.get("text") or "")
    status_code = int(raw.get("status") or 0)
    if status_code in (401, 403):
        raise SessionExpiredError("Sessão BrFlow expirada ao consultar a fila.")
    if text.lstrip().startswith("<!") or text.lstrip().lower().startswith("<html"):
        lower = text[:3000].lower()
        if "okta" in lower or "login" in lower:
            raise SessionExpiredError("Sessão BrFlow redirecionou para login.")
        raise ControleSlaError(
            "BrFlow respondeu HTML em vez do JSON da fila. "
            "Confirme se sua conta tem acesso ao relatório de fila de análise."
        )
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ControleSlaError("Resposta JSON inválida do BrFlow.") from exc

    # Se warning/error com redirect e sem data, segue o redirect e tenta de novo uma vez
    if allow_redirect_retry and isinstance(payload, dict):
        typ = str(payload.get("type") or "").lower()
        data = payload.get("data")
        redirect_to = str(payload.get("redirectTo") or payload.get("redirect") or "").strip()
        if typ == "warning" and redirect_to and not isinstance(data, list):
            target = redirect_to
            if target.startswith("/"):
                target = f"{BRFLOW_ORIGIN}{target}"
            log.info("BrFlow warning com redirectTo — navegando para %s", target)
            try:
                driver.get(target.replace(BRFLOW_ORIGIN_WWW, BRFLOW_ORIGIN))
                time.sleep(1.5)
                if _page_looks_like_brflow_login(driver):
                    _click_brflow_sso(driver, is_cancelled=is_cancelled)
                    time.sleep(2.0)
                _prepare_fila_context(driver, is_cancelled=is_cancelled)
            except SessionExpiredError:
                raise
            except Exception as exc:
                raise SessionExpiredError(f"Falha ao seguir redirect do BrFlow: {exc}") from exc
            return _probe_fila_in_browser(
                driver,
                allow_redirect_retry=False,
                allow_session_retry=allow_session_retry,
                is_cancelled=is_cancelled,
            )

    try:
        return _parse_fila_payload(payload)
    except SessionExpiredError:
        if not allow_session_retry:
            raise
        # 1ª recuperação: reabrir a tela do relatório no host certo (sem voltar ao Okta)
        log.warning("Fila BrFlow: LoginException — reabrindo ver-fila (sem Okta).")
        try:
            _prepare_fila_context(driver, is_cancelled=is_cancelled)
            return _probe_fila_in_browser(
                driver,
                allow_redirect_retry=False,
                allow_session_retry=False,
                is_cancelled=is_cancelled,
            )
        except SessionExpiredError:
            log.warning("Fila BrFlow: ainda sem sessão — recuperando via Okta uma vez.")
            _recover_brflow_app_session(driver, is_cancelled=is_cancelled)
            _prepare_fila_context(driver, is_cancelled=is_cancelled)
            return _probe_fila_in_browser(
                driver,
                allow_redirect_retry=False,
                allow_session_retry=False,
                is_cancelled=is_cancelled,
            )


def _ensure_brflow_app_session(
    driver,
    *,
    is_cancelled: Callable[[], bool] | None = None,
    force_navigate: bool = False,
) -> None:
    """
    Garante BrFlow autenticado.
    Importante: NÃO navegar para o index logo após o tile Okta — isso interrompe o SAML.
    """
    from app.config import brflow
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    _check_cancel(is_cancelled)
    _switch_to_brflow_window(driver)

    url = (driver.current_url or "").lower()
    if force_navigate or "brflow.com.br" not in url:
        driver.get(BRFLOW_INDEX)
        time.sleep(2.0)
        _check_cancel(is_cancelled)
    else:
        _canonicalize_brflow_host(driver)

    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        _check_cancel(is_cancelled)
        _switch_to_brflow_window(driver)
        _wait_brflow_loading_gone(driver, timeout=5)
        if _brflow_app_ready(driver):
            try:
                WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located((By.XPATH, brflow.B_usuario))
                )
            except Exception:
                pass
            return
        if _page_looks_like_brflow_login(driver):
            log.info("BrFlow na tela de login — acionando Login com SSO.")
            if not _click_brflow_sso(driver, is_cancelled=is_cancelled):
                time.sleep(1.5)
            continue
        time.sleep(0.8)

    url_now = ""
    try:
        url_now = driver.current_url or ""
    except Exception:
        pass
    if _page_looks_like_brflow_login(driver):
        raise SessionExpiredError(
            f"BrFlow permanece na tela de login (SSO não concluído). url={url_now[:120]}"
        )
    raise SessionExpiredError(
        f"Não foi possível abrir o BrFlow autenticado após o Okta. url={url_now[:120]}"
    )


def _recover_brflow_app_session(driver, *, is_cancelled: Callable[[], bool] | None = None) -> None:
    """Reabre o BrFlow via pesquisa Okta (padrão dos bots) e completa SSO."""
    _ensure_automacoes()
    from app.config import okta
    from selenium.common.exceptions import TimeoutException

    _check_cancel(is_cancelled)
    log.info("Recuperando sessão BrFlow via dashboard Okta.")
    try:
        # Volta ao dashboard Okta (aba Okta se ainda existir)
        okta_handle = None
        for handle in list(driver.window_handles):
            driver.switch_to.window(handle)
            if "okta.com" in (driver.current_url or "").lower():
                okta_handle = handle
                break
        if okta_handle:
            driver.switch_to.window(okta_handle)
        else:
            driver.get(okta.O_LINK)
            time.sleep(2.0)

        _open_brflow_from_okta_search(driver, is_cancelled=is_cancelled)
        _ensure_brflow_app_session(driver, is_cancelled=is_cancelled, force_navigate=False)
    except TimeoutException as exc:
        raise SessionExpiredError(_friendly_browser_error(exc, driver=driver)) from exc
    except SessionExpiredError:
        raise
    except Exception as exc:
        raise SessionExpiredError(_friendly_browser_error(exc, driver=driver)) from exc


def establish_brflow_session(
    *,
    matricula: str,
    senha: str,
    headless: bool | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_driver: Callable[[Any], None] | None = None,
) -> BrflowBrowserSession:
    """
    Login Okta + BrFlow. Mantém o Chrome aberto (sessão viva).
    max_retries=1 no Okta (anti-lockout).
    Por padrão abre Chrome com janela (headless quebra SSO do BrFlow).
    """
    _ensure_automacoes()
    from app.config import okta, brflow
    from app.infrastructure.selenium_helpers import create_driver, login_okta_resiliente
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    user = (matricula or "").strip()
    password = senha or ""
    if not user or not password:
        raise AuthFailureError("Matrícula e senha são obrigatórias.")

    use_headless = _resolve_headless(headless)
    log.info("Controle SLA: iniciando Chrome headless=%s", use_headless)

    _check_cancel(is_cancelled)
    driver = None
    try:
        driver = create_driver(headless=use_headless)
        if on_driver:
            on_driver(driver)
        _check_cancel(is_cancelled)

        driver.get(okta.O_LINK)
        _check_cancel(is_cancelled)
        try:
            login_okta_resiliente(
                driver,
                user,
                password,
                timeout=25,
                wait_for_post_login=True,
                max_retries=1,
            )
        except Exception as exc:
            if is_cancelled and is_cancelled():
                raise CancelledError("Conexão cancelada pelo usuário.") from exc
            msg = str(exc)
            if _looks_like_auth_failure(msg):
                raise AuthFailureError(
                    "Senha incorreta ou credenciais inválidas. Não será tentado novamente."
                ) from exc
            page = ""
            try:
                page = (driver.page_source or "")[:4000].lower()
            except Exception:
                pass
            if _looks_like_auth_failure(page) or "okta.com" in (driver.current_url or "").lower():
                if "login" in (driver.current_url or "").lower() or "signin" in page:
                    raise AuthFailureError(
                        "Não foi possível autenticar no Okta. Verifique matrícula e senha. "
                        "Não será tentado novamente."
                    ) from exc
            raise AuthFailureError(
                "Falha na autenticação Okta. Verifique os dados e tente novamente manualmente."
            ) from exc

        _check_cancel(is_cancelled)
        _open_brflow_from_okta_search(driver, is_cancelled=is_cancelled)
        _canonicalize_brflow_host(driver)

        try:
            WebDriverWait(driver, 25).until(
                EC.presence_of_element_located((By.XPATH, brflow.B_usuario))
            )
        except Exception:
            log.info("Menu BrFlow ainda não visível — completando SSO se necessário.")
            try:
                _ensure_brflow_app_session(driver, is_cancelled=is_cancelled, force_navigate=False)
            except SessionExpiredError:
                log.warning("BrFlow SSO incompleto — recuperando via Okta.")
                _recover_brflow_app_session(driver, is_cancelled=is_cancelled)

        _wait_brflow_loading_gone(driver, timeout=20)
        _canonicalize_brflow_host(driver)
        _check_cancel(is_cancelled)

        # Fluxo do HAR: index → ver-fila → POST pesquisar (não navegar para a URL do POST)
        _prepare_fila_context(driver, is_cancelled=is_cancelled)
        _probe_fila_in_browser(driver, is_cancelled=is_cancelled)

        _check_cancel(is_cancelled)
        session = BrflowBrowserSession(driver)
        driver = None
        return session
    except (CancelledError, AuthFailureError, SessionExpiredError, ControleSlaError):
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        raise
    except (TimeoutException, WebDriverException) as exc:
        msg = _friendly_browser_error(exc, driver=driver)
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        raise SessionExpiredError(msg) from exc
    except Exception:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        raise


# Compat: poller antigo importava fetch_fila(session requests) — agora usa session.fetch_fila()
def fetch_fila(session: BrflowBrowserSession) -> list[dict[str, Any]]:
    return session.fetch_fila()


_DT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
_DT_BR_RE = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$"
)


def parse_brflow_datetime(raw: Any):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
    text = str(raw).replace("\xa0", " ").replace("\u202f", " ").strip()
    m = _DT_RE.match(text)
    if m:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))

    m_br = _DT_BR_RE.match(text)
    if m_br:
        day, month, year = int(m_br.group(1)), int(m_br.group(2)), int(m_br.group(3))
        hour = int(m_br.group(4) or 0)
        minute = int(m_br.group(5) or 0)
        second = int(m_br.group(6) or 0)
        try:
            dt = datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None
        return dt.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
        return dt
    except ValueError:
        return None
