"""Monitor de JSON com detecção de alterações de NH e automação web no BrFlow."""
import sys
from pathlib import Path


import os
import time
import json
import logging
import threading
import queue
from pathlib import Path as PathLib
from shutil import which
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import StaleElementReferenceException
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from app.config import (
    STATE_FILE_BRFLOW,
    POLL_INTERVAL, MAX_RETRIES, RETRY_DELAY, DEBOUNCE_SECONDS, HEADLESS,
    WORKER_COUNT, IDLE_CLOSE_SECONDS, FALLBACK_SCAN_INTERVAL,
    DRIVER_FAILURE_THRESHOLD, DRIVER_FAILURE_PAUSE,
    okta, brflow,
)
from app.core.common import ErrorRecoveryHandler, safe_close_driver
from app.infrastructure.selenium_helpers import take_error_screenshot, login_okta_resiliente
from app.infrastructure.teams_notifier import notify

# ---------------------------------------------------------------------------
# Pasta com os JSONs de atualização de NH
# ---------------------------------------------------------------------------
NH_UPDATE_FOLDER = Path(
    os.getenv(
        "NH_UPDATE_FOLDER",
        str(
            Path.home()
            / "OneDrive - EXPERIAN SERVICES CORP"
            / "Planejamento ID&F - Extração de usuários"
            / "NH Update"
        ),
    )
)

# Varredura periódica independente do watchdog (segundos)
JSON_SCAN_INTERVAL = float(os.getenv("NH_JSON_SCAN_INTERVAL", "10"))

# ---------------------------------------------------------------------------
# Credenciais Okta
# ---------------------------------------------------------------------------
OKTA_USER = os.getenv("OKTA_USER") or ""
OKTA_PASS = os.getenv("OKTA_PASS") or ""

log = logging.getLogger("robots.monitor_excel")

if OKTA_USER and not OKTA_PASS:
    log.warning("OKTA_USER configurado mas OKTA_PASS não foi informado pela interface")
    notify(
        "Alterações",
        "aviso",
        "⚠️ Credenciais incompletas",
        "OKTA_PASS não foi informado pela interface",
    )

# ---------------------------------------------------------------------------
# Circuit-breaker para falhas no driver
# ---------------------------------------------------------------------------
_driver_failure_count = 0
_driver_failure_lock = threading.Lock()
_driver_pause_until = 0.0

from app.core.bot_runtime import BotRuntime

_runtime = BotRuntime(mode="excel")
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
parar_event = _runtime.parar_event
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress_with_error_feedback
_reset_progress_state = _runtime.reset_progress_state
_thread = None

# ---------------------------------------------------------------------------
# Chave composta para deduplicação de linhas processadas
# ---------------------------------------------------------------------------
def _make_task_id(record: dict) -> str:
    """Gera chave única a partir de DataRegistro + Gestor + UserLanID."""
    data = _norm_text(record.get("DataRegistro", ""))
    gestor = _norm_text(record.get("Gestor", ""))
    user = _norm_text(record.get("UserLanID", ""))
    return f"{data}|{gestor}|{user}"


def _norm_text(value) -> str:
    """Normaliza valores textuais, tratando None/null como vazio."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() == "null":
        return ""
    return text


# ---------------------------------------------------------------------------
# Automação BrFlow
# ---------------------------------------------------------------------------
def _fechar_abas_exceto(driver, handle_manter):
    handles = list(driver.window_handles)
    for handle in handles:
        if handle == handle_manter:
            continue
        try:
            driver.switch_to.window(handle)
            driver.close()
        except Exception as e:
            log.warning("Falha ao fechar aba %s: %s", handle, e)

    try:
        if handle_manter in driver.window_handles:
            driver.switch_to.window(handle_manter)
    except Exception:
        pass


def _handle_inactivity_if_present(driver) -> bool:
    """
    Verifica se o BrFlow exibiu mensagem de inatividade ou sessão expirada.

    Estratégias de detecção:
      1. Redirecionamento para a página de login do Okta (URL mudou).
      2. Modal/dialog visível com texto relacionado a inatividade ou sessão expirada.
      3. Sobreposição de tela de login do próprio BrFlow (formulário de usuário/senha visível).

    Retorna True se inatividade foi detectada (re-login necessário), False caso contrário.
    """
    _INACTIVITY_KEYWORDS = (
        "inativi",
        "sessão expirou",
        "sua sessão",
        "session expired",
        "session timeout",
        "faça login",
        "fazer login",
        "entrar novamente",
        "precisa autenticar",
        "autenticação necessária",
        "token expirado",
    )
    try:
        # Não verifica inatividade enquanto o loading estiver visível: evita
        # falsos positivos causados pelo overlay de carregamento do BrFlow.
        try:
            loading_el = driver.find_element(By.ID, "sistema-loading")
            style = loading_el.get_attribute("style") or ""
            if loading_el.is_displayed() and "display: none" not in style:
                log.debug("Loading visível — verificação de inatividade adiada.")
                return False
        except Exception:
            pass

        # 1) URL mudou para login Okta
        current_url = (driver.current_url or "").lower()
        if "okta.com/login" in current_url or (
            "okta.com" in current_url and "brflow" not in current_url
        ):
            log.info("Redirecionamento para Okta detectado – sessão BrFlow expirada.")
            return True

        # 2) Formulário de login do BrFlow visível (tela de fundo do sistema expirou)
        try:
            login_form = driver.find_element(By.XPATH, "//form[.//input[@type='password']]")
            if login_form.is_displayed():
                log.info("Formulário de login BrFlow visível – sessão expirada.")
                return True
        except Exception:
            pass

        # 3) Modal/overlay com texto de inatividade
        # Varre todos os elementos visíveis que contenham as palavras-chave
        for kw in _INACTIVITY_KEYWORDS:
            try:
                xpath = (
                    "//*["
                    "contains(translate(normalize-space(text()),"
                    "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÂÊÎÔÛÃÕÇ',"
                    "'abcdefghijklmnopqrstuvwxyzáéíóúâêîôûãõç')"
                    f", '{kw}')"
                    "]"
                )
                candidates = driver.find_elements(By.XPATH, xpath)
                for el in candidates:
                    try:
                        if not el.is_displayed():
                            continue
                        snippet = (el.text or "")[:120]
                        log.info("Detectada mensagem de inatividade BrFlow: '%s'", snippet)

                        # Tenta clicar em botão de dismissal dentro (ou próximo a) este elemento
                        _dismiss_modal(driver)
                        return True
                    except Exception:
                        pass
            except Exception:
                pass

    except Exception as e:
        log.debug("Verificação de inatividade BrFlow: %s", e)

    return False


def _dismiss_modal(driver) -> None:
    """Tenta fechar modais de alerta/confirmação clicando em botões de OK/Fechar."""
    _BTN_TEXTS = ("ok", "fechar", "close", "sim", "yes", "confirmar", "confirm", "entrar", "login")
    for btn_text in _BTN_TEXTS:
        try:
            xpath = (
                "//button["
                "contains(translate(normalize-space(.),"
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                "'abcdefghijklmnopqrstuvwxyz')"
                f", '{btn_text}')"
                "]"
            )
            btns = driver.find_elements(By.XPATH, xpath)
            for btn in btns:
                try:
                    if btn.is_displayed() and btn.is_enabled():
                        btn.click()
                        time.sleep(0.8)
                        log.debug("Modal dispensado via botão '%s'.", btn_text)
                        return
                except Exception:
                    pass
        except Exception:
            pass

    # Fallback: tenta pressionar ESC para fechar modal
    try:
        from selenium.webdriver.common.keys import Keys as _Keys
        driver.find_element(By.TAG_NAME, "body").send_keys(_Keys.ESCAPE)
        time.sleep(0.5)
    except Exception:
        pass


def _prepare_brflow_user_page(driver) -> bool:
    """Garante sessão logada e tela de Usuários do BrFlow pronta para uso."""
    main_wait = WebDriverWait(driver, 60)

    # Sessão já pronta: verifica inatividade ANTES de qualquer ação.
    if getattr(driver, "brflow_ready", False):
        try:
            if getattr(driver, "brflow_window", None) in driver.window_handles:
                driver.switch_to.window(driver.brflow_window)
                _fechar_abas_exceto(driver, driver.brflow_window)
        except Exception:
            pass

        # Detecta modal de inatividade antes de tentar usar a tela
        if _handle_inactivity_if_present(driver):
            log.info("Sessão BrFlow inativa detectada antes de nova alteração. Re-login necessário.")
            driver.brflow_ready = False
            # Cai no bloco de re-login abaixo
        else:
            try:
                WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, brflow.B_U_user)))
                return True
            except Exception:
                # Campo não encontrado; verifica novamente inatividade e tenta re-navegação.
                if _handle_inactivity_if_present(driver):
                    log.info("Inatividade detectada após falha no campo de busca. Re-login necessário.")
                    driver.brflow_ready = False
                else:
                    # Tenta re-navegar dentro do BrFlow sem repetir login Okta.
                    log.info("Campo matrícula não acessível. Tentando re-navegação interna no BrFlow.")
                    try:
                        if getattr(driver, "brflow_window", None) in driver.window_handles:
                            driver.switch_to.window(driver.brflow_window)
                            _fechar_abas_exceto(driver, driver.brflow_window)
                        usuario_btn = WebDriverWait(driver, 15).until(
                            EC.element_to_be_clickable((By.XPATH, brflow.B_usuario))
                        )
                        usuario_btn.click()
                        WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, brflow.B_U_perfil)))
                        driver.find_element(By.XPATH, brflow.B_U_perfil).click()
                        driver.find_element(By.XPATH, brflow.B_U_status).click()
                        WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, brflow.B_U_user)))
                        log.info("Re-navegação interna BrFlow bem-sucedida.")
                        return True
                    except Exception as nav_err:
                        log.warning("Re-navegação interna falhou (%s). Será feito re-login completo.", nav_err)
                        driver.brflow_ready = False

    def _wait_no_loading(timeout=15):
        try:
            WebDriverWait(driver, timeout).until(
                EC.invisibility_of_element_located((By.ID, "sistema-loading"))
            )
        except Exception:
            try:
                el = driver.find_element(By.ID, "sistema-loading")
                WebDriverWait(driver, 3).until(
                    lambda d: el.get_attribute("style")
                    and "display: none" in el.get_attribute("style")
                )
            except Exception:
                pass

    def safe_click(el, timeout_click=15, attempts=3):
        for _ in range(attempts):
            try:
                _wait_no_loading(timeout=5)
                if isinstance(el, tuple):
                    WebDriverWait(driver, timeout_click).until(EC.element_to_be_clickable(el))
                we = driver.find_element(*el) if isinstance(el, tuple) else el
                try:
                    we.click()
                    return True
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", we)
                        return True
                    except Exception:
                        time.sleep(0.5)
            except Exception:
                time.sleep(0.5)
        return False

    # Sessão ainda não preparada: faz login e navegação completa uma única vez.
    try:
        driver.get(okta.O_LINK)
        login_okta_resiliente(driver, OKTA_USER, OKTA_PASS, timeout=20)
        driver.okta_logged = True
        main_wait.until(EC.presence_of_element_located((By.ID, okta.O_pesquisar)))
        driver.find_element(By.ID, okta.O_pesquisar).send_keys("brflow")
        driver.find_element(By.ID, okta.O_pesquisar).send_keys(Keys.ENTER)
        WebDriverWait(driver, 20).until(lambda d: len(d.window_handles) > 1)
        driver.brflow_window = driver.window_handles[-1]
        driver.switch_to.window(driver.brflow_window)
        _fechar_abas_exceto(driver, driver.brflow_window)

        usuario_btn = main_wait.until(EC.element_to_be_clickable((By.XPATH, brflow.B_usuario)))
        safe_click(usuario_btn)
        main_wait.until(EC.element_to_be_clickable((By.XPATH, brflow.B_U_perfil)))
        safe_click((By.XPATH, brflow.B_U_perfil))
        safe_click((By.XPATH, brflow.B_U_status))
        main_wait.until(EC.element_to_be_clickable((By.XPATH, brflow.B_U_user)))

        driver.brflow_ready = True
        return True
    except Exception as e:
        driver.brflow_ready = False
        log.error("Falha ao preparar sessão BrFlow: %s", e)
        return False


def alter_user_with_driver(driver, usuario: str, para_nivel) -> bool:
    """Altera o nível hierárquico de *usuario* para *para_nivel* no BrFlow."""
    main_wait = WebDriverWait(driver, 60)

    if not _prepare_brflow_user_page(driver):
        return False

    try:
        def _wait_no_loading(timeout=15):
            try:
                WebDriverWait(driver, timeout).until(
                    EC.invisibility_of_element_located((By.ID, "sistema-loading"))
                )
            except Exception:
                pass

        def _find_clickable(locator, timeout=30):
            return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))

        def _click_fresh(locator, timeout=30, retries=4):
            last_error = None
            for _ in range(retries):
                try:
                    element = _find_clickable(locator, timeout=timeout)
                    try:
                        element.click()
                    except Exception:
                        element = driver.find_element(*locator)
                        driver.execute_script("arguments[0].click();", element)
                    return
                except StaleElementReferenceException as stale_err:
                    last_error = stale_err
                    time.sleep(0.2)
                except Exception as err:
                    last_error = err
                    time.sleep(0.2)
            if last_error:
                raise last_error

        def _fill_fresh(locator, value, timeout=30, retries=4):
            """Limpa e preenche campo; retorna o elemento após o send_keys."""
            last_error = None
            for _ in range(retries):
                try:
                    element = _find_clickable(locator, timeout=timeout)
                    # .clear() é a forma mais confiável em campos autocomplete
                    try:
                        element.clear()
                    except Exception:
                        pass
                    # Seleciona tudo como fallback (campos que bloqueiam clear)
                    try:
                        element.send_keys(Keys.CONTROL + "a")
                    except Exception:
                        pass
                    element.send_keys(str(value))
                    return element
                except StaleElementReferenceException as stale_err:
                    last_error = stale_err
                    time.sleep(0.2)
                except Exception as err:
                    last_error = err
                    time.sleep(0.2)
            if last_error:
                raise last_error
            return None

        # Daqui em diante cada alteração começa pelo campo de matrícula.
        _fill_fresh((By.XPATH, brflow.B_U_user), str(usuario), timeout=30, retries=5)

        previous_edit = None
        try:
            previous_edit = driver.find_element(By.XPATH, brflow.B_U_editar)
        except Exception:
            previous_edit = None

        _click_fresh((By.XPATH, brflow.B_U_pesquisar), timeout=30, retries=5)

        _wait_no_loading(timeout=20)
        if previous_edit is not None:
            try:
                WebDriverWait(driver, 5).until(EC.staleness_of(previous_edit))
            except Exception:
                pass

        _click_fresh((By.XPATH, brflow.B_U_editar), timeout=30, retries=5)

        _click_fresh((By.XPATH, brflow.B_U_alterarspan), timeout=30, retries=5)

        # --- Preenche campo NH com verificação para evitar troca de valores ---
        nh_locator = (By.XPATH, brflow.B_U_alterar)
        nh_field = None
        for _nh_attempt in range(5):
            nh_field = _fill_fresh(nh_locator, str(para_nivel), timeout=30, retries=3)
            # Aguarda autocomplete processar
            time.sleep(2)
            # Re-adquire elemento fresco e verifica o valor real presente no campo
            try:
                nh_field = _find_clickable(nh_locator, timeout=10)
                actual_val = driver.execute_script(
                    "return arguments[0].value || arguments[0].textContent || '';",
                    nh_field,
                )
                if str(para_nivel).lower()[:8] in str(actual_val).lower():
                    log.debug(
                        "Campo NH correto: '%s' (esperado: '%s')", actual_val, para_nivel
                    )
                    break
                log.warning(
                    "Campo NH contém '%s' mas esperado '%s' — relimpando (tentativa %d/5).",
                    actual_val,
                    para_nivel,
                    _nh_attempt + 1,
                )
            except Exception:
                break
        if nh_field is None:
            nh_field = _find_clickable(nh_locator, timeout=20)
        nh_field.send_keys(Keys.ENTER)

        _click_fresh((By.XPATH, brflow.B_U_salvar), timeout=30, retries=5)

        # Aguarda a página estabilizar após salvar.
        # O BrFlow pode ter dois ciclos de loading: um ao salvar e outro ao
        # retornar para a tela de listagem de usuários.
        _wait_no_loading(timeout=20)
        time.sleep(1.5)  # pequena pausa para BrFlow completar a navegação de retorno
        _wait_no_loading(timeout=15)  # segundo ciclo de loading pós-navegação
        try:
            WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, brflow.B_U_user)))
        except Exception:
            # Campo de busca não apareceu — tenta re-navegação interna antes de
            # exigir novo login completo.
            log.info("Campo matrícula não acessível após salvar. Tentando re-navegação interna.")
            try:
                usuario_btn = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable((By.XPATH, brflow.B_usuario))
                )
                usuario_btn.click()
                WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, brflow.B_U_perfil)))
                driver.find_element(By.XPATH, brflow.B_U_perfil).click()
                driver.find_element(By.XPATH, brflow.B_U_status).click()
                WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, brflow.B_U_user)))
                log.info("Re-navegação interna após salvar bem-sucedida.")
            except Exception as nav_err:
                log.warning(
                    "Re-navegação pós-salvar falhou (%s). Próxima tarefa fará re-login.", nav_err
                )
                driver.brflow_ready = False
        return True

    except Exception as e:
        driver.brflow_ready = False
        log.error("Erro ao processar usuario='%s': %s", usuario, e)
        return False


# ---------------------------------------------------------------------------
# Persistência de linhas já processadas
# ---------------------------------------------------------------------------
def _load_processed() -> dict:
    """Carrega o mapa {task_id -> timestamp} do arquivo de estado."""
    try:
        if STATE_FILE_BRFLOW.exists():
            with open(STATE_FILE_BRFLOW, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
                if isinstance(data, list):
                    return {str(i): "" for i in data}
    except Exception:
        pass
    return {}


def _save_processed(processed: dict) -> None:
    """Salva o mapa de processados de forma atômica."""
    try:
        STATE_FILE_BRFLOW.parent.mkdir(parents=True, exist_ok=True)
        serial = {str(k): str(v) for k, v in processed.items()}
        tmp = STATE_FILE_BRFLOW.with_name(STATE_FILE_BRFLOW.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(serial, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        try:
            tmp.replace(STATE_FILE_BRFLOW)
        except Exception:
            with open(STATE_FILE_BRFLOW, "w", encoding="utf-8") as f:
                json.dump(serial, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Leitura dos JSONs da pasta NH Update
# ---------------------------------------------------------------------------
def read_tasks_from_json_folder() -> list:
    """
    Varre todos os arquivos .json em NH_UPDATE_FOLDER e extrai tarefas.

    Cada registro deve conter:
        CurrentActivityTitle – nível hierárquico de destino
        DataRegistro         – timestamp ISO do registro  ─┐
        Gestor               – LAN ID do gestor            ├─ chave composta
        UserLanID            – LAN ID do usuário a alterar ─┘

    Retorna lista de dicts com chaves: id, usuario, para_nivel, titulo, fonte.
    """
    if not NH_UPDATE_FOLDER.exists():
        log.warning("Pasta NH Update não encontrada: %s", NH_UPDATE_FOLDER)
        return []

    tasks: list = []
    json_files = sorted(
        [
            file_path
            for file_path in NH_UPDATE_FOLDER.iterdir()
            if file_path.is_file() and file_path.suffix.lower() == ".json"
        ],
        key=lambda file_path: file_path.name.lower(),
    )

    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                records = json.load(f)
        except Exception as exc:
            # Pode ocorrer durante gravação/sincronização; varredura periódica tentará novamente.
            log.warning("Falha ao ler %s (será tentado novamente): %s", json_file.name, exc)
            continue

        if not isinstance(records, list):
            records = [records]

        for rec in records:
            usuario = _norm_text(rec.get("UserLanID", ""))
            current_activity = _norm_text(rec.get("CurrentActivity", ""))
            titulo = _norm_text(rec.get("CurrentActivityTitle", ""))
            para_nivel = titulo
            data_registro = _norm_text(rec.get("DataRegistro", ""))

            if not usuario or not current_activity or not para_nivel:
                log.debug(
                    "Registro ignorado (sem UserLanID, CurrentActivity ou CurrentActivityTitle): %s",
                    rec,
                )
                continue

            task_id = _make_task_id(rec)
            tasks.append(
                {
                    "id": task_id,
                    "usuario": usuario,
                    "para_nivel": str(para_nivel),
                    "titulo": titulo,
                    "data_registro": data_registro,
                    "fonte": json_file.name,
                }
            )

    return tasks


# ---------------------------------------------------------------------------
# Watchdog – detecta novos/alterados JSONs na pasta NH Update
# ---------------------------------------------------------------------------
class JsonFolderEventHandler(FileSystemEventHandler):
    """Observa a pasta NH Update e dispara reprocessamento ao detectar JSONs."""

    def __init__(self, trigger_fn, debounce_seconds: float = DEBOUNCE_SECONDS):
        super().__init__()
        self.trigger_fn = trigger_fn
        self.debounce_seconds = debounce_seconds
        self._last: float = 0.0

    def on_modified(self, event):
        self._handle(event)

    def on_created(self, event):
        self._handle(event)

    def on_moved(self, event):
        self._handle(event)

    def _handle(self, event):
        try:
            if getattr(event, "is_directory", False):
                return
            src = getattr(event, "src_path", None) or getattr(event, "dest_path", None)
            if not src or not str(src).lower().endswith(".json"):
                return

            now = time.time()
            if now - self._last < self.debounce_seconds:
                return
            self._last = now

            result = self.trigger_fn()
            if result:
                if isinstance(result, int):
                    found_count = result
                else:
                    try:
                        found_count = len(result)
                    except Exception:
                        found_count = 1
                log.info("Encontradas %d novas tarefas via watchdog", found_count)
        except Exception:
            log.exception("Erro no handler watchdog")


# ---------------------------------------------------------------------------
# Criação do WebDriver
# ---------------------------------------------------------------------------
def create_driver():
    """Cria ChromeDriver com opções de economia de recursos."""
    options = webdriver.ChromeOptions()
    headless_raw = (os.getenv("EXCEL_HEADLESS") or os.getenv("ROBOT_HEADLESS") or os.getenv("HEADLESS") or "").strip().lower()
    if headless_raw:
        headless_flag = headless_raw in ("1", "true", "yes", "on")
    else:
        headless_flag = HEADLESS
    if headless_flag:
        try:
            options.add_argument("--headless=new")
        except Exception:
            options.add_argument("--headless")

    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--mute-audio")
    options.add_argument("--window-size=1200,900")

    def _is_valid_driver_binary(path_value: str) -> bool:
        try:
            p = PathLib(path_value)
            return p.is_file() and p.suffix.lower() == ".exe"
        except Exception:
            return False

    def _resolve_driver_from_webdriver_manager():
        try:
            from webdriver_manager.chrome import ChromeDriverManager
        except Exception as import_err:
            log.warning("webdriver-manager não disponível: %s", import_err)
            return None

        try:
            raw_path = ChromeDriverManager().install()
        except Exception as install_err:
            log.warning("Falha no webdriver-manager: %s", install_err)
            return None

        if _is_valid_driver_binary(raw_path):
            return raw_path

        # Alguns releases retornam caminho de arquivo auxiliar; procura chromedriver.exe no diretório.
        try:
            root_dir = PathLib(raw_path).parent if PathLib(raw_path).suffix else PathLib(raw_path)
            for candidate in root_dir.rglob("chromedriver.exe"):
                if _is_valid_driver_binary(str(candidate)):
                    return str(candidate)
        except Exception:
            pass

        log.warning("webdriver-manager não retornou um chromedriver.exe válido: %s", raw_path)
        return None

    # 1) Tenta Selenium Manager (Selenium 4.6+): melhor opção em Windows
    try:
        driver = webdriver.Chrome(options=options)
    except Exception as selenium_manager_error:
        log.warning(
            "Falha no Selenium Manager: %s. Tentando webdriver-manager/CHROMEDRIVER_PATH/PATH…",
            selenium_manager_error,
        )

        # 2) Tenta resolver automaticamente com webdriver-manager
        managed_driver = _resolve_driver_from_webdriver_manager()
        if managed_driver:
            try:
                driver = webdriver.Chrome(service=Service(managed_driver), options=options)
            except Exception as managed_error:
                log.warning("Falha ao iniciar driver do webdriver-manager (%s): %s", managed_driver, managed_error)
                managed_driver = None

        if 'driver' in locals():
            try:
                driver.set_page_load_timeout(30)
            except Exception:
                pass
            driver.okta_logged = False
            return driver

        # 3) Permite caminho explícito via variável de ambiente
        custom_driver = (os.getenv("CHROMEDRIVER_PATH") or "").strip()
        resolved_driver = None
        if custom_driver:
            if _is_valid_driver_binary(custom_driver):
                resolved_driver = custom_driver
            else:
                log.warning("CHROMEDRIVER_PATH informado, mas inválido: %s", custom_driver)

        # 4) Busca no PATH se não veio caminho explícito
        if not resolved_driver:
            resolved_driver = which("chromedriver")

        if resolved_driver and not _is_valid_driver_binary(resolved_driver):
            log.warning("Executável encontrado no PATH não parece ser chromedriver.exe válido: %s", resolved_driver)
            resolved_driver = None

        if not resolved_driver:
            raise RuntimeError(
                "Não foi possível criar o WebDriver. Configure CHROMEDRIVER_PATH "
                "ou adicione chromedriver ao PATH."
            )

        try:
            driver = webdriver.Chrome(service=Service(resolved_driver), options=options)
        except Exception as path_error:
            raise RuntimeError(
                f"Falha ao iniciar chromedriver em '{resolved_driver}': {path_error}"
            ) from path_error

    try:
        driver.set_page_load_timeout(30)
    except Exception:
        pass
    driver.okta_logged = False
    driver.brflow_ready = False
    return driver


# ---------------------------------------------------------------------------
# Worker – executa alterações em threads paralelas
# ---------------------------------------------------------------------------
def worker_loop(
    task_queue: queue.Queue,
    processed_set: dict,
    processed_lock: threading.Lock,
    queued_set: set,
    queued_lock: threading.Lock,
    active_users: set,
    active_users_lock: threading.Lock,
    stop_event,
    worker_idx: int,
):
    global _driver_failure_count, _driver_pause_until

    driver = None
    last_used = 0.0
    error_handler = ErrorRecoveryHandler(max_errors=5, retry_delay=2)

    try:
        while not stop_event.is_set():
            try:
                task = task_queue.get(timeout=POLL_INTERVAL)
            except queue.Empty:
                # Mantém driver aberto para continuar alterações sem novo login.
                continue

            task_id = task["id"]
            usuario = task["usuario"]
            para_nivel = task["para_nivel"]
            titulo = task.get("titulo", "")
            fonte = task.get("fonte", "")

            _set_status(f"Monitor NH: processando {usuario} para NH {para_nivel} ({titulo})")

            # Verifica se já foi processado (chave composta já está no estado)
            with processed_lock:
                if processed_set.get(task_id) is not None:
                    with queued_lock:
                        queued_set.discard(task_id)
                    task_queue.task_done()
                    time.sleep(0.1)
                    continue

            # Garante que a mesma matrícula não seja processada em paralelo.
            with active_users_lock:
                if usuario in active_users:
                    task_queue.put(task)
                    task_queue.task_done()
                    time.sleep(0.2)
                    continue
                active_users.add(usuario)

            # Circuit-breaker
            if driver is None:
                now_t = time.time()
                if now_t < _driver_pause_until:
                    rem = int(_driver_pause_until - now_t)
                    _set_status(f"Monitor NH: aguardando {rem}s para recuperacao")
                    with active_users_lock:
                        active_users.discard(usuario)
                    time.sleep(min(RETRY_DELAY, max(1, rem)))
                    continue

                try:
                    driver = create_driver()
                    with _driver_failure_lock:
                        _driver_failure_count = 0
                except Exception:
                    log.error("Falha ao criar driver")
                    notify(
                        "Alterações",
                        "erro",
                        "❌ Falha ao criar driver",
                        "Não foi possível inicializar Selenium WebDriver",
                    )
                    take_error_screenshot(None, "create_driver", "monitor_excel")
                    with _driver_failure_lock:
                        _driver_failure_count += 1
                        if _driver_failure_count >= DRIVER_FAILURE_THRESHOLD:
                            _driver_pause_until = time.time() + DRIVER_FAILURE_PAUSE
                            log.error(
                                "Pausando drivers por %ds (falhas consecutivas)",
                                DRIVER_FAILURE_PAUSE,
                            )
                    with queued_lock:
                        queued_set.discard(task_id)
                    task_queue.task_done()
                    with active_users_lock:
                        active_users.discard(usuario)
                    if not error_handler.handle_error(None, "Falha ao criar driver", _set_status, log):
                        notify(
                            "Alterações",
                            "erro",
                            "❌ Monitor finalizado por erros",
                            "Limite máximo de falhas foi atingido.",
                        )
                        stop_event.set()
                        return
                    time.sleep(RETRY_DELAY)
                    continue

            # Tentativas de alteração
            success = False
            for attempt in range(MAX_RETRIES + 1):
                try:
                    success = alter_user_with_driver(driver, usuario, para_nivel)
                    if success:
                        error_handler.reset()
                        break
                except Exception as exc:
                    log.error("Tentativa %d falhou para %s: %s", attempt + 1, usuario, exc)
                    take_error_screenshot(driver, f"tentativa_{attempt + 1}", "monitor_excel")
                    safe_close_driver(driver)
                    driver = None
                    try:
                        driver = create_driver()
                        with _driver_failure_lock:
                            _driver_failure_count = 0
                    except Exception:
                        with _driver_failure_lock:
                            _driver_failure_count += 1
                            if _driver_failure_count >= DRIVER_FAILURE_THRESHOLD:
                                _driver_pause_until = time.time() + DRIVER_FAILURE_PAUSE
                                notify(
                                    "Alterações",
                                    "aviso",
                                    "⚠️ Muitos erros de driver",
                                    f"Pausa de {DRIVER_FAILURE_PAUSE}s ativada.",
                                )
                        if not error_handler.handle_error(
                            None, "Falha recriar driver", _set_status, log
                        ):
                            log.error("Máximo de erros atingido. Encerrando worker %d.", worker_idx)
                        break

            if success:
                with processed_lock:
                    processed_set[task_id] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    _save_processed(processed_set)
                log.info("Processado: %s → NH %s [%s]", usuario, para_nivel, fonte)
                notify(
                    "Alterações",
                    "sucesso",
                    "✅ Usuário processado",
                    f"Usuário: {usuario}\nNH: {para_nivel}\nAtividade: {titulo}",
                )
                _set_progress(100, f"Monitor NH: {usuario} concluido")
            else:
                msg = f"Falha: {usuario} após {MAX_RETRIES} tentativas"
                log.error(msg)
                notify(
                    "Alterações",
                    "erro",
                    "❌ Falha no processamento",
                    f"{msg}\nNH desejado: {para_nivel}",
                )
                _set_progress(0, f"Monitor NH: {usuario} falha")

            with queued_lock:
                queued_set.discard(task_id)
            task_queue.task_done()
            with active_users_lock:
                active_users.discard(usuario)
            last_used = time.time()

    except Exception:
        log.error("Erro fatal no worker %d", worker_idx)
        notify(
            "Alterações",
            "erro",
            "❌ Erro fatal no worker",
            "Uma thread worker encontrou uma exceção não capturada.",
        )
    finally:
        safe_close_driver(driver)


# ---------------------------------------------------------------------------
# Loop principal do monitor
# ---------------------------------------------------------------------------
def _monitor_loop(settings=None, intervalo=POLL_INTERVAL, stop_event=None):
    stop_event = stop_event or threading.Event()
    processed = _load_processed()
    processed_lock = threading.Lock()
    task_q: queue.Queue = queue.Queue()
    queued_set: set = set()
    queued_lock = threading.Lock()
    active_users: set = set()
    active_users_lock = threading.Lock()
    def scan_and_enqueue():
        tasks = read_tasks_from_json_folder()
        tasks.sort(key=lambda t: (t.get("data_registro", ""), t.get("usuario", ""), t.get("id", "")))
        enqueued = 0
        with processed_lock, queued_lock:
            for t in tasks:
                tid = t["id"]
                if processed.get(tid) is None and tid not in queued_set:
                    queued_set.add(tid)
                    task_q.put(t)
                    enqueued += 1
        if enqueued:
            log.info("Enfileiradas %d tarefas", enqueued)
            notify(
                "Alterações",
                "info",
                "ℹ️ Tarefas enfileiradas",
                f"{enqueued} alteração(ões) de NH para processar",
            )
            _set_status(f"Monitor NH: {enqueued} tarefas enfileiradas")
            return enqueued
        return []

    # Iniciar workers
    effective_worker_count = WORKER_COUNT if WORKER_COUNT >= 2 else 2
    if WORKER_COUNT < 2:
        log.warning("WORKER_COUNT=%s ajustado para 2 (mínimo solicitado para agilidade).", WORKER_COUNT)

    workers = []
    for i in range(effective_worker_count):
        t = threading.Thread(
            target=worker_loop,
            args=(
                task_q,
                processed,
                processed_lock,
                queued_set,
                queued_lock,
                active_users,
                active_users_lock,
                stop_event,
                i,
            ),
            daemon=True,
        )
        t.start()
        workers.append(t)

    # Watchdog na pasta NH Update
    observer = Observer()
    handler = JsonFolderEventHandler(scan_and_enqueue, debounce_seconds=DEBOUNCE_SECONDS)
    watch_folder = str(NH_UPDATE_FOLDER) if NH_UPDATE_FOLDER.exists() else str(Path.home())
    observer.schedule(handler, watch_folder, recursive=False)
    observer.start()

    _set_status(f"Monitor NH: iniciado em {NH_UPDATE_FOLDER}")
    notify(
        "Alterações",
        "info",
        "ℹ️ Monitor NH iniciado",
        f"Monitorando: {NH_UPDATE_FOLDER}\nWorkers: {effective_worker_count}",
    )

    # Varredura inicial (processa JSONs já existentes na pasta)
    try:
        scan_and_enqueue()
    except Exception:
        pass

    scan_interval = max(1.0, JSON_SCAN_INTERVAL)
    last_scan = 0.0
    try:
        while not stop_event.is_set():
            time.sleep(1)
            now = time.time()
            if now - last_scan >= scan_interval:
                try:
                    scan_and_enqueue()
                except Exception:
                    pass
                last_scan = now
    except Exception:
        pass
    finally:
        stop_event.set()
        observer.stop()
        observer.join(timeout=5)
        task_q.join()
        for w in workers:
            w.join(timeout=5)
        _save_processed(processed)
        _set_status("Monitor NH: finalizado")
        notify("Alterações", "info", "ℹ️ Monitor NH finalizado", "Monitor foi parado normalmente.")


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def start(settings=None, intervalo=POLL_INTERVAL, tempo_max=None):
    global _thread, parar_event
    parar_event.clear()
    _reset_progress_state()
    if _thread and _thread.is_alive():
        return _thread
    _thread = threading.Thread(
        target=_monitor_loop,
        kwargs={
            "settings": settings or {},
            "intervalo": intervalo,
            "stop_event": parar_event,
        },
        daemon=True,
    )
    _thread.start()
    return _thread


def stop(timeout: int = 5):
    global _thread, parar_event
    parar_event.set()
    if _thread:
        _thread.join(timeout=timeout)
    _thread = None


if __name__ == "__main__":
    start()
    try:
        while not parar_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop()
