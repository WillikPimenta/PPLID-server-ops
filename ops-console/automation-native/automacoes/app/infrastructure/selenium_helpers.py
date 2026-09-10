"""Selenium helpers para automação web com retry, visibilidade e tratamento inteligente de erros."""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import logging
import time
import os
import warnings
from datetime import datetime
from pathlib import Path
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    NoSuchElementException,
    TimeoutException,
)
from app.config import HEADLESS_DEFAULT, PLAN_IDF_SERASA_BOTS, okta

LOG = logging.getLogger(__name__)

# Constants for retry and timeout
DEFAULT_TIMEOUT = 30
DEFAULT_ATTEMPTS = 3
DEFAULT_OVERLAY_ID = "canvasloader-container-background"
DEFAULT_ELEMENT_TIMEOUT = 15
DEFAULT_PAGE_LOAD_TIMEOUT = 60
DEFAULT_WINDOW_SIZE = "1200,900"
SCROLL_PAUSE_TIME = 0.3
SCROLL_INCREMENT = 300


def _scroll_element_into_view(driver, element):
    """Faz scroll até o elemento ficar visível."""
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        time.sleep(SCROLL_PAUSE_TIME)
        return True
    except Exception:
        return False


def _ensure_element_visible(driver, element):
    """Garante que o elemento está visível para interação."""
    try:
        # Primeiro, faz scroll para trazer à view
        _scroll_element_into_view(driver, element)
        
        # Aguarda elemento ser clickable (não só visible)
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable(element))
        return True
    except Exception:
        # Fallback: tenta scrollar página inteira
        try:
            driver.execute_script("arguments[0].scrollIntoView(true);", element)
            time.sleep(0.3)
            return True
        except Exception:
            return False


def _find_element_with_alternatives(driver, by, value, timeout=DEFAULT_ELEMENT_TIMEOUT):
    """
    Tenta encontrar elemento com seletor principal, depois tenta alternativas.
    Estratégias: XPath normalizado, ID, Classe, Text, etc.
    """
    selectors = [(by, value)]
    
    # Gera seletores alternativos baseado no tipo
    if by == By.XPATH:
        # Normaliza XPath removendo índices específicos
        normalized = value.replace("[1]", "").replace("[2]", "")
        if normalized != value:
            selectors.append((By.XPATH, normalized))
    
    elif by == By.ID:
        # Tenta por atributo data-* ou name se ID não funcionar
        element_id = value
        selectors.append((By.CSS_SELECTOR, f"[data-id='{element_id}']"))
        selectors.append((By.NAME, element_id))
    
    # Tenta cada seletor
    for selector_by, selector_value in selectors:
        try:
            element = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((selector_by, selector_value))
            )
            return element
        except Exception:
            continue
    
    return None


def wait_for_element(driver, by, value, timeout=DEFAULT_TIMEOUT, ensure_visible=False):
    """
    Aguarda elemento estar presente no DOM com suporte a visibilidade.
    
    Args:
        driver: Webdriver
        by: Tipo de localizador
        value: Valor do localizador
        timeout: Timeout em segundos
        ensure_visible: Se True, aguarda estar visível e scrollável
    
    Returns:
        Element se encontrado, None caso contrário
    """
    try:
        if ensure_visible:
            element = WebDriverWait(driver, timeout).until(
                EC.visibility_of_element_located((by, value))
            )
        else:
            element = _find_element_with_alternatives(driver, by, value, timeout)
        
        if element and ensure_visible:
            _ensure_element_visible(driver, element)
        
        return element
    except Exception:
        return None


def _wait_overlay_invisible(driver, overlay_id: str = DEFAULT_OVERLAY_ID, timeout: int = DEFAULT_ELEMENT_TIMEOUT):
    """Aguarda overlay ficar invisível."""
    try:
        WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located((By.ID, overlay_id)))
    except Exception:
        time.sleep(0.5)


def _js_click_fallback(driver, element):
    """Tenta clicar via JavaScript como fallback."""
    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:
        return False


def _remove_overlay_elements(driver):
    """Remove elementos de overlay que podem bloquear cliques."""
    overlay_selectors = [
        "//div[@id='canvasloader-container-background']",
        "//div[contains(@class, 'overlay')]",
        "//div[contains(@class, 'modal-backdrop')]",
        "//div[contains(@style, 'position: fixed')]",
    ]
    
    for selector in overlay_selectors:
        try:
            elements = driver.find_elements(By.XPATH, selector)
            for el in elements:
                if el.is_displayed():
                    driver.execute_script("arguments[0].style.display='none';", el)
        except Exception:
            pass


def click_element(driver, by, value, timeout=DEFAULT_ELEMENT_TIMEOUT, attempts=DEFAULT_ATTEMPTS, overlay_id=DEFAULT_OVERLAY_ID):
    """
    Clica elemento com múltiplas estratégias de retry.
    
    Estratégias (em ordem):
    1. Wait + Click normal
    2. Remove overlay + Click via JS
    3. Scroll + Click normal
    4. Click via JS com JavaScript
    5. Actions API (como último recurso)
    
    Args:
        driver: Webdriver
        by: Tipo de localizador (By.ID, By.XPATH, etc)
        value: Valor do localizador
        timeout: Timeout para aguardar elemento
        attempts: Número de tentativas
        overlay_id: ID do overlay que pode interceptar clique
    
    Returns:
        Element se sucesso, False se falhou
    """
    last_exception = None
    
    for attempt in range(1, attempts + 1):
        try:
            # Estratégia 1: Encontra elemento com visibilidade
            element = wait_for_element(driver, by, value, timeout=timeout, ensure_visible=True)
            if not element:
                time.sleep(0.2)
                continue
            
            # Tenta click normal
            try:
                element.click()
                LOG.debug(f"✓ Click bem-sucedido em {by}={value} (tentativa {attempt})")
                return element
            except ElementClickInterceptedException:
                # Estratégia 2: Remove overlay e tenta JS click
                _remove_overlay_elements(driver)
                _wait_overlay_invisible(driver, overlay_id, timeout=5)
                
                if _js_click_fallback(driver, element):
                    LOG.debug(f"✓ Click via JS após remover overlay (tentativa {attempt})")
                    return element
            
            except (ElementNotInteractableException, StaleElementReferenceException):
                # Estratégia 3: Scroll e tenta novamente
                _scroll_element_into_view(driver, element)
                time.sleep(SCROLL_PAUSE_TIME)
                try:
                    element.click()
                    LOG.debug(f"✓ Click após scroll (tentativa {attempt})")
                    return element
                except Exception:
                    pass
            
            # Estratégia 4: Click puro via JS
            try:
                driver.execute_script("arguments[0].click();", element)
                LOG.debug(f"✓ Click via JS puro (tentativa {attempt})")
                return element
            except Exception:
                pass
            
            time.sleep(0.3)
        
        except Exception as e:
            last_exception = e
            time.sleep(0.3)
    
    error_msg = f"Falha ao clicar {by}={value} após {attempts} tentativas"
    LOG.error(f"{error_msg} | Última exceção: {last_exception}")
    return False


def send_keys_to_element(driver, by, value, keys, timeout=DEFAULT_ELEMENT_TIMEOUT):
    """
    Envia teclas para elemento com múltiplas tentativas.
    
    Estratégias:
    1. Find + Clear + SendKeys
    2. Focus via JS + SendKeys
    3. Click + SendKeys
    
    Args:
        driver: Webdriver
        by: Tipo de localizador
        value: Valor do localizador
        keys: Teclas a enviar
        timeout: Timeout para aguardar elemento
    
    Returns:
        Element se sucesso, None se falhou
    """
    try:
        element = wait_for_element(driver, by, value, timeout=timeout, ensure_visible=True)
        if not element:
            LOG.error(f"Elemento não encontrado para enviar keys: {value}")
            return None
        
        # Estratégia 1: Clear normal + SendKeys
        try:
            element.clear()
        except Exception:
            # Tenta limpar via JS
            try:
                driver.execute_script("arguments[0].value = '';", element)
            except Exception:
                pass
        
        # Foca no elemento
        try:
            driver.execute_script("arguments[0].focus();", element)
        except Exception:
            pass
        
        time.sleep(0.1)
        element.send_keys(keys)
        LOG.debug(f"✓ Keys enviadas para {by}={value}")
        return element
    
    except Exception as e:
        LOG.error(f"Falha ao enviar keys para {value}: {e}")
        return None


def _find_first_existing(driver, by_value_pairs):
    """Retorna o primeiro elemento existente/interagível dentre vários localizadores."""
    for by, value in by_value_pairs:
        try:
            elements = driver.find_elements(by, value)
            for element in elements:
                try:
                    if element and element.is_displayed() and element.is_enabled():
                        return element
                except Exception:
                    if element:
                        return element
        except Exception:
            continue
    return None


def _find_in_shadow_dom(driver, css_selectors):
    """Procura elemento em shadow DOM usando CSS selectors."""
    if not css_selectors:
        return None

    js = """
        const selectors = arguments[0] || [];
        const seen = new Set();
        const walk = (root) => {
            if (!root || seen.has(root)) return null;
            seen.add(root);
            for (const sel of selectors) {
                try {
                    const found = root.querySelector(sel);
                    if (found) return found;
                } catch (_) {}
            }
            const nodes = root.querySelectorAll('*');
            for (const node of nodes) {
                if (node && node.shadowRoot) {
                    const inside = walk(node.shadowRoot);
                    if (inside) return inside;
                }
            }
            return null;
        };
        return walk(document);
    """

    try:
        return driver.execute_script(js, css_selectors)
    except Exception:
        return None


def _find_element_any_context(driver, locators, shadow_css_selectors=None):
    """Procura elemento no documento principal, shadow DOM e iframes."""
    driver.switch_to.default_content()

    element = _find_first_existing(driver, locators)
    if not element:
        element = _find_in_shadow_dom(driver, shadow_css_selectors)
    if element:
        return element, "main"

    frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
    for idx, frame in enumerate(frames):
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            element = _find_first_existing(driver, locators)
            if not element:
                element = _find_in_shadow_dom(driver, shadow_css_selectors)
            if element:
                return element, f"iframe[{idx}]"
        except Exception:
            continue

    driver.switch_to.default_content()
    return None, "none"


def _wait_for_any_context(driver, locators, shadow_css_selectors=None, timeout=DEFAULT_TIMEOUT, poll=0.5):
    """Aguarda até localizar um elemento em qualquer contexto."""
    deadline = time.time() + timeout
    last_context = "none"
    while time.time() < deadline:
        try:
            element, last_context = _find_element_any_context(driver, locators, shadow_css_selectors)
            if element:
                return element, last_context
        except Exception:
            pass
        time.sleep(poll)
    return None, last_context


def _clear_and_type(driver, element, value):
    """Limpa e preenche um campo de forma resiliente."""
    try:
        element.clear()
    except Exception:
        try:
            driver.execute_script("arguments[0].value = '';", element)
        except Exception:
            pass

    try:
        driver.execute_script("arguments[0].focus();", element)
    except Exception:
        pass

    time.sleep(0.1)
    element.send_keys(value)


def _click_web_element(driver, element):
    """Tenta clicar em WebElement com fallback JS."""
    try:
        element.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False


def _okta_user_locators():
    return [
        (By.ID, getattr(okta, "O_usuario", "")),
        (By.ID, getattr(okta, "O_usuario2", "")),
        (By.ID, "okta-signin-username"),
        (By.NAME, "username"),
        (By.NAME, "identifier"),
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[autocomplete='username']"),
        (By.CSS_SELECTOR, "input[name='loginfmt']"),
    ]


def _okta_user_css_selectors():
    return [
        "#okta-signin-username",
        "input[name='username']",
        "input[name='identifier']",
        "input[autocomplete='username']",
        "input[type='email']",
        "input[id^='input'][type='text']",
    ]


def _okta_password_locators():
    return [
        (By.ID, getattr(okta, "O_senha", "")),
        (By.ID, getattr(okta, "O_senha2", "")),
        (By.ID, "okta-signin-password"),
        (By.NAME, "password"),
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.CSS_SELECTOR, "input[autocomplete='current-password']"),
    ]


def _okta_password_css_selectors():
    return [
        "#okta-signin-password",
        "input[name='password']",
        "input[type='password']",
        "input[autocomplete='current-password']",
    ]


def _okta_next_locators():
    return [
        (By.ID, "idp-discovery-submit"),
        (By.XPATH, getattr(okta, "O_proximo", "")),
        (By.XPATH, getattr(okta, "O_proximo2", "")),
        (By.XPATH, "//button[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÃÂÉÊÍÓÔÕÚÇ', 'abcdefghijklmnopqrstuvwxyzáàãâéêíóôõúç'), 'próximo') ]"),
        (By.XPATH, "//button[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'proximo') ]"),
        (By.XPATH, "//input[@type='submit' and contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÃÂÉÊÍÓÔÕÚÇ', 'abcdefghijklmnopqrstuvwxyzáàãâéêíóôõúç'), 'próximo') ]"),
        (By.XPATH, "//input[@type='submit' and contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'proximo') ]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "input[type='submit']"),
    ]


def _okta_next_css_selectors():
    return [
        "#idp-discovery-submit",
        "button[type='submit']",
        "input[type='submit']",
        "input.button-primary",
        "button.button-primary",
    ]


def _okta_verify_locators():
    return [
        (By.ID, "okta-signin-submit"),
        (By.XPATH, getattr(okta, "O_verificar", "")),
        (By.XPATH, getattr(okta, "O_verificar2", "")),
        (By.XPATH, getattr(okta, "O_entrar", "")),
        (By.XPATH, getattr(okta, "O_entrar2", "")),
        (By.XPATH, "//button[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÃÂÉÊÍÓÔÕÚÇ', 'abcdefghijklmnopqrstuvwxyzáàãâéêíóôõúç'), 'verificar') ]"),
        (By.XPATH, "//input[@type='submit' and contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÃÂÉÊÍÓÔÕÚÇ', 'abcdefghijklmnopqrstuvwxyzáàãâéêíóôõúç'), 'verificar') ]"),
        (By.XPATH, "//button[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'verify') ]"),
        (By.XPATH, "//button[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in') ]"),
        (By.XPATH, "//input[@type='submit' and contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'verify') ]"),
        (By.XPATH, "//input[@type='submit' and contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in') ]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "input[type='submit']"),
    ]


def _okta_verify_css_selectors():
    return [
        "#okta-signin-submit",
        "button[type='submit']",
        "input[type='submit']",
        "input.button-primary",
        "button.button-primary",
    ]


def esta_na_tela_okta(driver):
    """Retorna True se o driver está atualmente em uma tela de login do Okta."""
    try:
        current_url = driver.current_url or ""
        # URL de login Okta (não de app pós-login)
        if "okta.com/login" in current_url or "okta.com/signin" in current_url:
            return True
        # Verifica se campo de usuário está presente (sessão expirou e voltou ao login)
        user_input, _ = _wait_for_any_context(
            driver,
            _okta_user_locators(),
            _okta_user_css_selectors(),
            timeout=2,
            poll=0.3,
        )
        return user_input is not None
    except Exception:
        return False


def relogin_se_necessario(driver, matricula, senha, timeout=20, max_retries=3):
    """
    Verifica se o driver voltou para a tela de login do Okta (sessão expirada/
    inatividade) e refaz o login automaticamente se necessário.

    Retorna True se o relogin foi realizado, False se não era necessário.
    Lança RuntimeError se não conseguir recuperar após max_retries tentativas.
    """
    if not esta_na_tela_okta(driver):
        return False

    logging.warning("[Okta] Sessão expirada ou tela de login detectada — iniciando relogin automático.")
    try:
        driver.get(okta.O_LINK)
    except Exception:
        pass

    login_okta_resiliente(driver, matricula, senha, timeout=timeout, max_retries=max_retries, wait_for_post_login=True)
    logging.info("[Okta] Relogin concluído com sucesso.")
    return True


def login_okta_resiliente(driver, matricula, senha, timeout=20, wait_for_post_login=True, max_retries=3):
    """
    Executa login no Okta usando seletores estáveis e fallbacks dinâmicos.

    Em caso de falha (timeout, página travada, sessão expirada), recarrega a
    página de login e tenta novamente até max_retries vezes.
    """
    if not matricula or not senha:
        raise ValueError("Matrícula e senha são obrigatórias para login Okta")

    last_error = None
    for tentativa in range(1, max_retries + 1):
        try:
            if tentativa > 1:
                logging.warning(f"[Okta] Tentativa {tentativa}/{max_retries} — recarregando página de login.")
                try:
                    driver.get(okta.O_LINK)
                except Exception:
                    pass
                time.sleep(2)

            wait = WebDriverWait(driver, timeout)
            wait.until(lambda d: d.execute_script("return document.readyState") in ("interactive", "complete"))

            user_input, user_ctx = _wait_for_any_context(
                driver,
                _okta_user_locators(),
                _okta_user_css_selectors(),
                timeout=timeout,
            )
            if not user_input:
                raise TimeoutException(f"Campo de usuário do Okta não encontrado (contexto: {user_ctx})")

            _clear_and_type(driver, user_input, matricula)

            pass_input, _ = _wait_for_any_context(
                driver,
                _okta_password_locators(),
                _okta_password_css_selectors(),
                timeout=1.5,
                poll=0.25,
            )
            if not pass_input:
                next_button, _ = _wait_for_any_context(
                    driver,
                    _okta_next_locators(),
                    _okta_next_css_selectors(),
                    timeout=5,
                    poll=0.25,
                )
                if next_button:
                    if not _click_web_element(driver, next_button):
                        user_input.send_keys(Keys.ENTER)
                else:
                    user_input.send_keys(Keys.ENTER)

                pass_input, pass_ctx = _wait_for_any_context(
                    driver,
                    _okta_password_locators(),
                    _okta_password_css_selectors(),
                    timeout=timeout,
                )
                if not pass_input:
                    raise TimeoutException(f"Campo de senha do Okta não encontrado após etapa da matrícula (contexto: {pass_ctx})")

            _clear_and_type(driver, pass_input, senha)

            verify_button, _ = _wait_for_any_context(
                driver,
                _okta_verify_locators(),
                _okta_verify_css_selectors(),
                timeout=5,
                poll=0.25,
            )
            if verify_button:
                if not _click_web_element(driver, verify_button):
                    pass_input.send_keys(Keys.ENTER)
            else:
                pass_input.send_keys(Keys.ENTER)

            if wait_for_post_login:
                wait.until(
                    EC.any_of(
                        EC.presence_of_element_located((By.ID, okta.O_pesquisar)),
                        EC.url_contains("okta.com/app"),
                        EC.url_contains("brflow.com.br"),
                    )
                )

            if tentativa > 1:
                logging.info(f"[Okta] Login bem-sucedido na tentativa {tentativa}/{max_retries}.")
            return True

        except Exception as e:
            last_error = e
            logging.warning(f"[Okta] Falha na tentativa {tentativa}/{max_retries}: {e}")
            if tentativa < max_retries:
                time.sleep(3)

    raise TimeoutException(
        f"[Okta] Login falhou após {max_retries} tentativas. Último erro: {last_error}"
    )


def _configure_driver_options(
    headless: bool,
    download_dir: str = None,
    ignore_certificate_errors: bool = False,
) -> webdriver.ChromeOptions:
    """Configura opções do Chrome com argumentos padronizados para performance."""
    opts = webdriver.ChromeOptions()
    
    # Modo headless (mais rápido)
    if headless:
        opts.add_argument("--headless=new")
    else:
        # Garante janela visível quando a validação Okta pede navegador aberto.
        opts.add_argument("--start-maximized")
    
    # Argumentos de PERFORMANCE (reduz startup de ~8s para ~4-5s)
    perf_args = [
        "--disable-gpu",                      # Desativa GPU (não precisa em headless)
        "--no-sandbox",                       # Desativa sandbox (mais rápido)
        "--disable-dev-shm-usage",            # Usa /tmp em vez de /dev/shm
        "--disable-extensions",               # Sem extensões
        "--disable-plugins",                  # Sem plugins
        "--disable-images",                   # Sem imagens (opcional - reduz ~500ms)
        "--disable-popup-blocking",           # Sem bloqueio de popups
        "--disable-default-apps",             # Sem apps padrão
        "--disable-preconnect",               # Sem pré-conexão
        "--disable-background-networking",    # Sem background networking
        "--no-first-run",                     # Sem configuração inicial
        "--no-default-browser-check",         # Sem check de navegador padrão
        "--disable-backgrounding-occluded-windows",
        "--disable-breakpad",                 # Sem crash reporting
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-sync",                     # Sem sincronização (crítico!)
        "--enable-automation",
    ]
    for arg in perf_args:
        opts.add_argument(arg)

    if ignore_certificate_errors:
        opts.add_argument("--ignore-certificate-errors")
        opts.add_argument("--ignore-ssl-errors")
        opts.add_argument("--allow-insecure-localhost")
        opts.set_capability("acceptInsecureCerts", True)
    
    opts.add_argument(f"--window-size={DEFAULT_WINDOW_SIZE}")
    
    # Desativa notificações de crash e outras popups
    opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    
    # Download directory (caminho absoluto — Chrome ignora path relativo em headless)
    if download_dir:
        abs_dir = str(Path(download_dir).expanduser().resolve())
        prefs = {
            "download.default_directory": abs_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": False,
            "profile.default_content_settings.popups": 0,
            "plugins.always_open_pdf_externally": True,
        }
        opts.add_experimental_option("prefs", prefs)
    
    return opts


def _get_python_arch():
    """Detecta arquitetura do Python (32 ou 64 bits)."""
    import struct
    return 64 if struct.calcsize("P") == 8 else 32


def _validate_chromedriver(driver_path):
    """Valida se chromedriver é compatível com a arquitetura do Python."""
    import subprocess
    import struct
    try:
        result = subprocess.run([driver_path, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return True
    except Exception:
        pass
    return False


def _resolve_chromedriver_executable(driver_path: str) -> str:
    """Resolve o executável real do chromedriver quando o manager retorna um arquivo auxiliar."""
    if not driver_path:
        return driver_path

    path = Path(driver_path)
    if path.is_file() and path.name.lower() in ("chromedriver", "chromedriver.exe"):
        return str(path)

    candidates = []

    if path.is_dir():
        candidates.extend(path.glob("**/chromedriver.exe"))
        candidates.extend(path.glob("**/chromedriver"))
    else:
        parent = path.parent
        candidates.extend(parent.glob("**/chromedriver.exe"))
        candidates.extend(parent.glob("**/chromedriver"))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return driver_path


def _cleanup_chromedriver_cache():
    """Remove cache do webdriver-manager para forçar novo download."""
    try:
        from pathlib import Path
        cache_dir = Path.home() / ".wdm"
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir)
    except Exception:
        pass


def create_driver(headless=HEADLESS_DEFAULT, download_dir=None, ignore_certificate_errors: bool = False):
    """
    Cria webdriver Chrome com configurações padronizadas.
    
    Args:
        headless: Se True, roda sem interface
        download_dir: Diretório para downloads
        ignore_certificate_errors: Se True, ignora erros de certificado SSL/TLS
    
    Returns:
        Webdriver Chrome configurado
    
    Raises:
        Exception: Se não conseguir criar driver após todas tentativas
    """
    opts = _configure_driver_options(
        headless,
        download_dir,
        ignore_certificate_errors=ignore_certificate_errors,
    )
    python_arch = _get_python_arch()
    
    # Strategy 1: Try PATH first (fastest - skips download checks)
    try:
        drv = webdriver.Chrome(options=opts)
        drv.set_page_load_timeout(DEFAULT_PAGE_LOAD_TIMEOUT)
        LOG.info("✓ Driver criado com sucesso via PATH")
        return drv
    except Exception as e:
        LOG.debug(f"PATH strategy falhou: {e}")
    
    # Strategy 2: webdriver-manager (com validação)
    try:
        os.environ['WDM_SSL_VERIFY'] = '0'

        try:
            import urllib3
            warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

        driver_path = ChromeDriverManager().install()
        driver_path = _resolve_chromedriver_executable(driver_path)
        
        if _validate_chromedriver(driver_path):
            service = Service(driver_path)
            drv = webdriver.Chrome(service=service, options=opts)
            drv.set_page_load_timeout(DEFAULT_PAGE_LOAD_TIMEOUT)
            LOG.info("✓ Driver criado com sucesso via webdriver-manager")
            return drv
        else:
            LOG.warning(f"Chromedriver inválido após download: {driver_path}")
            _cleanup_chromedriver_cache()
    except OSError as e:
        if "193" in str(e) or "Win32" in str(e):
            LOG.error(f"OSError 193: Mismatch de arquitetura")
            _cleanup_chromedriver_cache()
    except Exception as e:
        LOG.debug(f"webdriver-manager strategy falhou: {e}")
    
    # Strategy 3: Search common paths (fallback)
    try:
        import shutil
        from pathlib import Path
        
        common_paths = [
            Path.home() / ".wdm" / "drivers" / "chromedriver" / "win32",
            Path.home() / ".wdm" / "drivers" / "chromedriver" / "win64",
            Path("C:\\Program Files\\Google\\Chrome\\Application"),
            Path("C:\\Program Files (x86)\\Google\\Chrome\\Application"),
        ]
        
        for search_dir in common_paths:
            if search_dir.exists():
                for chromedriver in search_dir.glob("**/chromedriver*"):
                    if chromedriver.suffix in ("", ".exe"):
                        if _validate_chromedriver(str(chromedriver)):
                            service = Service(str(chromedriver))
                            drv = webdriver.Chrome(service=service, options=opts)
                            drv.set_page_load_timeout(DEFAULT_PAGE_LOAD_TIMEOUT)
                            LOG.info(f"✓ Driver criado com sucesso")
                            return drv
    except Exception as e:
        LOG.debug(f"Common paths strategy falhou: {e}")
    
    error_msg = f"Não foi possível criar WebDriver\nArquitetura: {python_arch}-bit"
    LOG.error(error_msg)
    raise RuntimeError(error_msg)


def close_browser(driver):
    """Fecha navegador de forma segura."""
    if driver:
        try:
            driver.quit()
        except Exception:
            pass


def take_error_screenshot(driver, error_name, robot_name=""):
    """
    Captura screenshot ao ocorrer erro com diagnóstico.
    
    Args:
        driver: Webdriver
        error_name: Nome do erro ou descrição
        robot_name: Nome do robô (nivel_h, monitor, monitor_excel)
    
    Returns:
        Path do arquivo salvo ou None se falha
    """
    if not driver:
        return None
    
    try:
        # Cria diretório de erros no caminho centralizado de logs
        error_dir = PLAN_IDF_SERASA_BOTS / "logs" / "error_screenshots"
        error_dir.mkdir(parents=True, exist_ok=True)
        
        # Gera nome do arquivo com timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        robot_prefix = f"{robot_name}_" if robot_name else ""
        filename = error_dir / f"{robot_prefix}erro_{timestamp}_{error_name[:30]}.png"
        
        # Captura screenshot
        driver.save_screenshot(str(filename))
        LOG.error(f"Screenshot salvo: {filename}")
        
        # Tenta salvar HTML da página para análise
        try:
            html_file = error_dir / f"{robot_prefix}debug_{timestamp}.html"
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            LOG.error(f"Debug HTML salvo: {html_file}")
        except Exception:
            pass
        
        return filename
    except Exception as e:
        LOG.error(f"Falha ao capturar screenshot: {e}")
        return None


def diagnose_element_issues(driver, by, value):
    """
    Diagnóstico detalhado de por que um elemento não pode ser encontrado/clicado.
    
    Args:
        driver: Webdriver
        by: Tipo de localizador
        value: Valor do localizador
    
    Returns:
        Dicionário com diagnóstico
    """
    diagnosis = {
        "locator": f"{by}={value}",
        "element_found": False,
        "element_visible": False,
        "element_clickable": False,
        "overlays_detected": False,
        "page_ready": True,
        "issues": []
    }
    
    try:
        # Verifica se elemento existe
        try:
            element = driver.find_element(by, value)
            diagnosis["element_found"] = True
            
            # Verifica se é visível
            if element.is_displayed():
                diagnosis["element_visible"] = True
            else:
                diagnosis["issues"].append("Elemento não está visível (display: none)")
            
            # Verifica se é clickable
            try:
                WebDriverWait(driver, 2).until(EC.element_to_be_clickable(element))
                diagnosis["element_clickable"] = True
            except TimeoutException:
                diagnosis["issues"].append("Elemento não é clickable")
        
        except NoSuchElementException:
            diagnosis["issues"].append(f"Elemento não encontrado com {by}={value}")
        
        # Detecta overlays
        overlays = driver.find_elements(By.XPATH, "//*[contains(@class, 'overlay') or contains(@class, 'modal')]")
        if overlays:
            for overlay in overlays:
                if overlay.is_displayed():
                    diagnosis["overlays_detected"] = True
                    diagnosis["issues"].append(f"Overlay detectado: {overlay.tag_name} classe={overlay.get_attribute('class')}")
        
        # Verifica se página está completamente carregada
        try:
            doc_ready = driver.execute_script("return document.readyState") == "complete"
            if not doc_ready:
                diagnosis["page_ready"] = False
                diagnosis["issues"].append("Página não completamente carregada")
        except Exception:
            pass
    
    except Exception as e:
        diagnosis["issues"].append(f"Erro ao diagnosticar: {str(e)}")
    
    if diagnosis["issues"]:
        LOG.warning(f"Diagnóstico para {value}: {diagnosis['issues']}")
    
    return diagnosis
