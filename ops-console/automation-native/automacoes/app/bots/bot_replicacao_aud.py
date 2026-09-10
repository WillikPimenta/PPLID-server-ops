"""Bot de replicação de auditoria no BRFlow.

Fluxo básico:
1) Abre Chrome
2) Faz login no Okta
3) Pesquisa por BRFlow
4) Alterna para a janela BRFlow
5) Acessa o menu "Replicação de Protocolos"
"""

import os
import re
import sys
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException

from app.infrastructure.selenium_helpers import (
    click_element,
    send_keys_to_element,
    take_error_screenshot,
    create_driver,
    login_okta_resiliente,
)
from app.core.common import safe_close_driver, MetricsContext
from app.config import okta, brflow, PASTA_REPLICACAO_AUD_BASE, REPLICACAO_CSV_PLACEHOLDER_LIMPEZA, REPLICACAO_LISTAGEM_ESPERA_SEG, REPLICACAO_UPLOAD_CSV_VAZIO_DEFAULT
from app.bots.replicacao_aud_planning import (
    PlanoReplicacao,
    _normalizar_workflow,
    atualizar_relatorio_excel,
    carregar_estado_execucao,
    carregar_plano_por_run_id,
    csv_protocolos_e_limpeza,
    gerar_plano_replicacao,
    resolver_ultimo_run_id,
    salvar_estado_execucao,
    workflow_nome_brflow,
)

from app.core.bot_runtime import BotRuntime

log = logging.getLogger("robots.bot_replicacao_aud")

_runtime = BotRuntime(mode="replicacao_auditoria")
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
parar_event = _runtime.parar_event
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress
_reset_progress_state = _runtime.reset_progress_state
TIMEOUT_DRIVER = 20


def _get_headless(settings=None) -> bool:
    raw = None
    if isinstance(settings, dict):
        raw = settings.get("headless")
    if raw is None:
        raw = os.getenv("REPLICACAO_HEADLESS") or os.getenv("ROBOT_HEADLESS") or os.getenv("HEADLESS") or "0"
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def _get_credentials(settings=None):
    matricula = ""
    senha = ""
    if isinstance(settings, dict):
        matricula = str(settings.get("matricula", "") or "").strip()
        senha = str(settings.get("senha", "") or "").strip()

    if not matricula:
        matricula = (
            os.getenv("OKTA_USER")
            or os.getenv("ROBOT_USER")
            or os.getenv("NIVEL_USER")
            or os.getenv("MONITOR_USER")
            or ""
        )
    if not senha:
        senha = (
            os.getenv("OKTA_PASS")
            or os.getenv("ROBOT_PASS")
            or os.getenv("NIVEL_PASS")
            or os.getenv("MONITOR_PASS")
            or ""
        )
    return matricula, senha


def _create_chrome_driver(headless: bool = False):
    return create_driver(headless=headless)


def _clicar_com_retry(driver, locator, by=By.XPATH, tentativas=3, pausa=2, descricao="elemento"):
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            elemento = WebDriverWait(driver, TIMEOUT_DRIVER).until(
                EC.element_to_be_clickable((by, locator))
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});",
                elemento,
            )
            try:
                elemento.click()
            except Exception:
                driver.execute_script("arguments[0].click();", elemento)
            return elemento
        except Exception as exc:
            ultimo_erro = exc
            log.warning(f"Falha ao clicar em {descricao} (tentativa {tentativa}/{tentativas}): {exc}")
            time.sleep(pausa)
    raise ultimo_erro


def _fazer_login_okta(driver, matricula: str, senha: str):
    _set_status("Iniciando autenticação Okta")
    _set_progress(10, "Acessando Okta")
    driver.get(okta.O_LINK)
    login_okta_resiliente(driver, matricula, senha, timeout=TIMEOUT_DRIVER)
    _set_progress(40, "Okta autenticado")


def _abrir_brflow(driver):
    _set_status("BRFlow: buscando no Okta")
    _set_progress(45, "Pesquisando BRFlow")
    send_keys_to_element(driver, "id", okta.O_pesquisar, "brflow")
    send_keys_to_element(driver, "id", okta.O_pesquisar, Keys.ENTER)

    WebDriverWait(driver, 30).until(lambda d: len(d.window_handles) > 1)
    driver.switch_to.window(driver.window_handles[-1])
    _set_progress(55, "BRFlow aberto")

    WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located((By.XPATH, brflow.B_menu_replicacao))
    )


def _acessar_menu_replicacao(driver):
    _set_status("Acessando menu de replicação de protocolos")
    _set_progress(60, "Entrando no menu")
    _clicar_com_retry(
        driver,
        brflow.B_menu_replicacao,
        by=By.XPATH,
        descricao="menu Replicação de Protocolos",
    )
    time.sleep(2)
    _set_progress(90, "Menu de replicação acessado")
    _set_status("Menu de replicação de protocolos acessado")


def _selecionar_select2_valor(driver, id_pattern, texto, timeout=15):
    """Seleciona valor em Select2 usando padrão de ID dinâmico."""
    _set_status(f"Selecionando {texto}")
    log.info(f"[SELECT2] Iniciando: pattern={id_pattern}, texto={texto}")
    
    try:
        # Buscar o span renderizado
        xpath_rendered = f"//span[contains(@id, 'select2-{id_pattern}') and contains(@id, '-container')]"
        log.debug(f"[SELECT2] Buscando com XPath: {xpath_rendered}")
        
        container = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, xpath_rendered))
        )
        log.info(f"[SELECT2] Span encontrado: id={container.get_attribute('id')}")
        
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", container)
        
        # Encontrar elemento select2-selection
        try:
            selection_elem = container.find_element(By.XPATH, "./ancestor::span[contains(@class,'select2-selection')]")
            log.info("[SELECT2] select2-selection encontrado (ancestral)")
        except Exception as e:
            log.warning(f"[SELECT2] select2-selection não encontrado: {e}")
            try:
                selection_elem = container.find_element(By.XPATH, "./ancestor::div[contains(@class,'select2-container')]")
                log.info("[SELECT2] select2-container encontrado (fallback)")
            except Exception as e2:
                log.warning(f"[SELECT2] select2-container não encontrado: {e2}")
                selection_elem = container
                log.info("[SELECT2] Usando container direto")
        
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", selection_elem)
        
        try:
            driver.execute_script("arguments[0].click();", selection_elem)
            log.info("[SELECT2] Clicado via JavaScript")
        except Exception as e:
            log.warning(f"[SELECT2] Erro no clique JS: {e}, tentando clique normal")
            selection_elem.click()
            log.info("[SELECT2] Clicado normalmente")
        
        time.sleep(1)
        
        # Capturar screenshot após abrir dropdown
        try:
            take_error_screenshot(driver, "log_screenshots", f"select2_{id_pattern}_apos_clique")
            log.info("[SELECT2] Screenshot capturado após clique")
        except Exception as e:
            log.warning(f"[SELECT2] Erro ao capturar screenshot: {e}")
        
        # Inspecionar estrutura do dropdown
        try:
            dropdown_html = driver.execute_script("""
                // Procurar por elementos open do Select2
                let results = [];
                
                // Procurar .select2-dropdown (aberto)
                let dropdowns = document.querySelectorAll('.select2-dropdown');
                results.push({tipo: '.select2-dropdown', count: dropdowns.length});
                
                // Procurar .select2-results
                let resultsDiv = document.querySelectorAll('.select2-results');
                results.push({tipo: '.select2-results', count: resultsDiv.length});
                
                // Procurar inputs visíveis com classe select2
                let select2Inputs = document.querySelectorAll('input[class*="select2"]');
                results.push({tipo: 'input[class*="select2"]', count: select2Inputs.length});
                
                // Procurar por dropdown aberto
                let openContainer = document.querySelector('.select2-container--open');
                results.push({tipo: '.select2-container--open', found: !!openContainer});
                
                return results;
            """)
            log.info(f"[SELECT2] Análise HTML: {dropdown_html}")
        except Exception as e:
            log.warning(f"[SELECT2] Erro ao analisar HTML: {e}")
        
        # Listar todos os elementos select2 visíveis
        try:
            select2_elements = driver.find_elements(By.CSS_SELECTOR, "[class*='select2']")
            log.info(f"[SELECT2] Total elementos select2: {len(select2_elements)}")
            for idx, elem in enumerate(select2_elements[:20]):
                try:
                    visible = elem.is_displayed()
                    classes = elem.get_attribute('class') or ''
                    tag = elem.tag_name
                    if visible and 'dropdown' in classes.lower():
                        log.info(f"[SELECT2] Elemento visível {idx}: tag={tag}, class={classes[:80]}")
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"[SELECT2] Erro ao listar select2 elements: {e}")
        
        # Aguardar campo de busca com múltiplos seletores
        log.debug(f"[SELECT2] Procurando campo de busca com seletor: {brflow.B_replicacao_select2_search}")
        
        search_input = None
        seletores_alternativas = [
            (By.CSS_SELECTOR, brflow.B_replicacao_select2_search),  # input.select2-search__field
            (By.CSS_SELECTOR, "input.select2-search__field"),
            (By.CSS_SELECTOR, ".select2-search__field"),
            (By.CSS_SELECTOR, "input[type='search']"),
            (By.CSS_SELECTOR, ".select2-dropdown input"),
            (By.XPATH, "//div[contains(@class, 'select2-dropdown')]//input"),
            (By.XPATH, "//input[contains(@class, 'select2-search')]"),
            (By.XPATH, "//input[@type='search']"),
            # Fallback para inputs customizados
            (By.CSS_SELECTOR, "input.form-control.js-input-search"),
            (By.CSS_SELECTOR, "input.js-input-search"),
        ]
        
        for by_method, seletor in seletores_alternativas:
            try:
                log.debug(f"[SELECT2] Tentando seletor: {by_method}={seletor}")
                search_input = WebDriverWait(driver, 3).until(
                    EC.visibility_of_element_located((by_method, seletor))
                )
                log.info(f"[SELECT2] Campo de busca encontrado com: {by_method}={seletor}")
                break
            except TimeoutException:
                log.debug(f"[SELECT2] Não encontrado: {by_method}={seletor}")
                continue
            except Exception as e:
                log.debug(f"[SELECT2] Erro ao procurar {seletor}: {e}")
                continue
        
        if search_input is None:
            log.error("[SELECT2] Nenhum campo de busca encontrado após todas as tentativas")
            
            # Inspecionar container select2 para entender a estrutura
            try:
                container_info = driver.execute_script(f"""
                    let pattern = 'select2-codClienteDestino';
                    let container = document.querySelector('span[id*="' + pattern + '-container"]');
                    if (container) {{
                        return {{
                            containerClass: container.className,
                            containerId: container.id,
                            html: container.innerHTML.substring(0, 500),
                            parentClass: container.parentElement?.className,
                            nextSiblingClass: container.nextElementSibling?.className || 'none',
                        }};
                    }}
                    return 'container not found';
                """)
                log.info(f"[SELECT2] Container info: {container_info}")
            except Exception as e:
                log.error(f"[SELECT2] Erro ao inspecionar container: {e}")
            
            # Listar todos os inputs visíveis na página
            try:
                all_inputs = driver.find_elements(By.TAG_NAME, "input")
                log.info(f"[SELECT2] Total de inputs na página: {len(all_inputs)}")
                for idx, inp in enumerate(all_inputs[:15]):
                    try:
                        visible = inp.is_displayed()
                        input_type = inp.get_attribute('type')
                        input_class = inp.get_attribute('class')
                        input_id = inp.get_attribute('id')
                        if visible:
                            log.info(f"[SELECT2] Input VISÍVEL {idx}: type={input_type}, class={input_class}, id={input_id}")
                    except Exception:
                        pass
            except Exception as e:
                log.error(f"[SELECT2] Erro ao listar inputs: {e}")
            
            raise TimeoutException("Campo de busca do Select2 não encontrado")
        
        log.info("[SELECT2] Campo de busca found e clicável")
        
        try:
            search_input.clear()
        except Exception:
            pass
        
        search_input.send_keys(texto)
        log.info(f"[SELECT2] Texto digitado: {texto}")
        time.sleep(0.5)
        
        # Selecionar opção
        option_xpath = f"{brflow.B_replicacao_select2_option}[normalize-space(string(.))='{texto}']"
        log.debug(f"[SELECT2] Procurando opção: {option_xpath}")
        
        option = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, option_xpath))
        )
        log.info(f"[SELECT2] Opção encontrada: {texto}")
        
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", option)
        
        try:
            driver.execute_script("arguments[0].click();", option)
            log.info("[SELECT2] Opção clicada via JavaScript")
        except Exception as e:
            log.warning(f"[SELECT2] Erro no clique JS da opção: {e}")
            option.click()
            log.info("[SELECT2] Opção clicada normalmente")
        
        time.sleep(0.5)
        
        # Confirmar
        log.debug("[SELECT2] Aguardando confirmação do valor")
        WebDriverWait(driver, timeout).until(
            EC.text_to_be_present_in_element((By.XPATH, xpath_rendered), texto)
        )
        log.info(f"[SELECT2] Seleção confirmada: {texto}")
        
    except TimeoutException as e:
        log.error(f"[SELECT2] TIMEOUT: {e}")
        raise
    except Exception as e:
        log.exception(f"[SELECT2] ERRO: {e}")
        raise



TIMEOUT_DRIVER = 20
REPLICACAO_FILTRO_WORKFLOW_CARREGAMENTO_SEG = 25


def _normalizar_texto_select2_ui(texto_ui: str) -> str:
    """Remove prefixo do botão limpar (×) e espaços do texto renderizado do Select2."""
    t = (texto_ui or "").strip()
    while t and t[0] in ("\u00d7", "×", "\uf0d7"):
        t = t[1:].strip()
    return t


def _ui_select2_vazia(texto_ui: str) -> bool:
    t = _normalizar_texto_select2_ui(texto_ui).lower()
    if not t:
        return True
    return t == "selecione" or t.startswith("adicione ")


def _termos_busca_select2(valor_cod: str, texto_opcao: str) -> list[str]:
    valor_cod = str(valor_cod or "").strip()
    texto_opcao = str(texto_opcao or "").strip()
    termos: list[str] = []
    if texto_opcao:
        termos.append(texto_opcao)
        if " - " in texto_opcao:
            termos.append(texto_opcao.split(" - ", 1)[0].strip())
    if valor_cod:
        termos.append(valor_cod)
    return [t for t in dict.fromkeys(termos) if t]


def _termo_digitacao_select2(valor_cod: str, texto_opcao: str) -> str:
    """Termo curto para digitar no Select2 (ex.: 'G Auditoria' em vez do label completo)."""
    termos = _termos_busca_select2(valor_cod, texto_opcao)
    nao_digit = [t for t in termos if not str(t).isdigit()]
    if nao_digit:
        return min(nao_digit, key=len)
    return termos[0] if termos else str(valor_cod or "")


def _texto_select2_bate(texto_ui: str, texto_opcao: str) -> bool:
    ui_l = _normalizar_texto_select2_ui(texto_ui).lower()
    if not ui_l or _ui_select2_vazia(ui_l):
        return False
    if not texto_opcao:
        return True
    hints = [texto_opcao.lower()]
    if " - " in texto_opcao:
        hints.append(texto_opcao.split(" - ", 1)[0].strip().lower())
    return any(h and (h in ui_l or ui_l in h) for h in hints)


def _filtro_select2_confirmado(
    driver,
    select_name: str,
    valor_cod: str,
    texto_opcao: str = "",
) -> bool:
    """Confirma valor no <select> nativo e texto renderizado no Select2."""
    valor_cod = str(valor_cod or "").strip()
    if _ler_valor_select_brflow(driver, select_name) != valor_cod:
        return False
    ui = _normalizar_texto_select2_ui(_ler_texto_select2_brflow(driver, select_name))
    if _ui_select2_vazia(ui):
        return False
    if texto_opcao and not _texto_select2_bate(ui, texto_opcao):
        return False
    return True


def _definir_select_filtro_js(
    driver,
    select_name: str,
    valor_cod: str,
    texto_hint: str = "",
) -> dict:
    """Define valor no <select> e sincroniza Select2 via jQuery (caminho rápido)."""
    return driver.execute_script(
        """
        const name = arguments[0];
        const valor = String(arguments[1] || '');
        const hint = String(arguments[2] || '');
        const sel = document.querySelector('select[name="' + name + '"]');
        if (!sel) return {ok: false, reason: 'select ausente'};
        const opts = Array.from(sel.options || []).filter(
            (o) => String(o.value || '').trim() !== ''
        );
        let opt = opts.find((o) => String(o.value) === valor);
        if (!opt && hint) {
            const h = hint.toLowerCase();
            opt = opts.find((o) => {
                const t = String(o.text || '').trim().toLowerCase();
                return t === h || t.includes(h) || h.includes(t);
            });
        }
        if (!opt) {
            return {ok: false, reason: 'opcao ausente', total: opts.length, valor, hint};
        }
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        if (typeof jQuery !== 'undefined') {
            const $sel = jQuery(sel);
            $sel.val(opt.value).trigger('change');
            $sel.trigger('change.select2');
            try {
                if ($sel.data('select2')) {
                    $sel.select2('val', opt.value);
                }
            } catch (e) {}
        }
        const ui = document.querySelector(
            'span[id*="select2-' + name + '"][id$="-container"]'
        );
        return {
            ok: String(sel.value) === String(opt.value),
            value: String(sel.value || ''),
            text: String(opt.text || '').trim(),
            ui: ui ? String(ui.textContent || '').trim() : '',
        };
        """,
        select_name,
        valor_cod,
        texto_hint,
    ) or {}


def _definir_workflow_destino_js(
    driver,
    valor_cod: str,
    texto_hint: str = "",
) -> dict:
    """Sincroniza WorkFlow Destino no Select2 (dropdown dependente do cliente)."""
    return driver.execute_script(
        """
        const valor = String(arguments[0] || '');
        const hint = String(arguments[1] || '');
        const sel = document.querySelector('select[name="codWorkFlowDestino"]');
        if (!sel) return {ok: false, reason: 'select ausente'};
        const opts = Array.from(sel.options || []).filter(
            (o) => String(o.value || '').trim() !== ''
        );
        let opt = opts.find((o) => String(o.value) === valor);
        if (!opt && hint) {
            const h = hint.toLowerCase();
            opt = opts.find((o) => {
                const t = String(o.text || '').trim().toLowerCase();
                return t === h || t.includes(h) || h.includes(t);
            });
        }
        if (!opt) {
            return {ok: false, reason: 'opcao ausente', total: opts.length, valor, hint};
        }
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        if (typeof jQuery !== 'undefined') {
            const $sel = jQuery(sel);
            $sel.val(opt.value).trigger('change').trigger('change.select2');
            try {
                if ($sel.data('select2')) {
                    $sel.select2('val', opt.value);
                }
            } catch (e) {}
            const data = { id: opt.value, text: String(opt.text || '').trim() };
            $sel.trigger({ type: 'select2:select', params: { data } });
        }
        const ui = document.querySelector(
            'span[id*="select2-codWorkFlowDestino"][id$="-container"]'
        );
        const rendered = ui ? ui.querySelector('.select2-selection__rendered') : null;
        const texto = String(opt.text || '').trim();
        if (rendered && (!rendered.textContent || rendered.textContent.trim().toLowerCase() === 'selecione')) {
            rendered.textContent = texto;
            rendered.setAttribute('title', texto);
        }
        const uiTxt = ui ? String(ui.textContent || '').trim() : '';
        return {
            ok: String(sel.value) === String(opt.value) && uiTxt.toLowerCase() !== 'selecione',
            value: String(sel.value || ''),
            text: texto,
            ui: uiTxt,
        };
        """,
        valor_cod,
        texto_hint,
    ) or {}


def _ler_valor_select_brflow(driver, select_name: str) -> str:
    return str(
        driver.execute_script(
            """
            const sel = document.querySelector('select[name="' + arguments[0] + '"]');
            return sel ? String(sel.value || '') : '';
            """,
            select_name,
        )
        or ""
    )


def _ler_texto_select2_brflow(driver, select_name: str) -> str:
    return str(
        driver.execute_script(
            """
            const rendered = document.querySelector(
                'span[id*="select2-' + arguments[0] + '"][id$="-container"]'
            );
            return rendered ? String(rendered.textContent || '').trim() : '';
            """,
            select_name,
        )
        or ""
    )


def _clicar_combobox_select2(driver, select_name: str, *, timeout: int = TIMEOUT_DRIVER):
    xpath = (
        f"//select[@name='{select_name}']/following-sibling::span"
        f"[contains(@class,'select2-container')]//span[contains(@class,'select2-selection')]"
    )
    elem = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
    try:
        driver.execute_script("arguments[0].click();", elem)
    except Exception:
        elem.click()
    time.sleep(0.15)
    return elem


def _clicar_opcao_select2(driver, option) -> None:
    """Clica opção do Select2 com eventos de mouse reais (JS click puro costuma falhar)."""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", option)
    try:
        option.click()
        return
    except Exception:
        pass
    driver.execute_script(
        """
        const el = arguments[0];
        ['mouseover', 'mousedown', 'mouseup', 'click'].forEach((type) => {
            el.dispatchEvent(new MouseEvent(type, {
                bubbles: true,
                cancelable: true,
                view: window,
            }));
        });
        """,
        option,
    )


def _selecionar_opcao_select2_aberta(
    driver,
    *,
    valor_cod: str,
    texto_opcao: str,
    timeout: int = TIMEOUT_DRIVER,
) -> None:
    """Seleciona opção no dropdown Select2 aberto (Enter ou clique do mouse)."""
    valor_cod = str(valor_cod or "").strip()
    texto_opcao = str(texto_opcao or valor_cod).strip()
    termos = _termos_busca_select2(valor_cod, texto_opcao)
    select_name = "codWorkFlowDestino"

    search_input = None
    for by_method, seletor in (
        (By.CSS_SELECTOR, ".select2-container--open input.select2-search__field"),
        (By.CSS_SELECTOR, brflow.B_replicacao_select2_search),
        (By.CSS_SELECTOR, "input.select2-search__field"),
    ):
        try:
            search_input = WebDriverWait(driver, 2).until(
                EC.visibility_of_element_located((by_method, seletor))
            )
            break
        except TimeoutException:
            continue

    if search_input is not None:
        try:
            search_input.clear()
        except Exception:
            pass
        busca = _termo_digitacao_select2(valor_cod, texto_opcao)
        search_input.send_keys(busca)
        log.info("[FILTROS] Select2 busca digitada: %s", busca)
        time.sleep(0.45)

    def _opcao_visivel():
        for opt in driver.find_elements(By.CSS_SELECTOR, ".select2-container--open .select2-results__option"):
            classe = opt.get_attribute("class") or ""
            if not opt.is_displayed():
                continue
            if "select2-results__option--disabled" in classe:
                continue
            if "loading-results" in classe or "message" in classe:
                continue
            oid = opt.get_attribute("id") or ""
            if valor_cod and (oid.endswith(f"-{valor_cod}") or f"-{valor_cod}" in oid):
                return opt
            txt = (opt.text or "").strip()
            for termo in termos:
                termo_l = termo.lower()
                txt_l = txt.lower()
                if txt_l == termo_l or termo_l in txt_l or txt_l in termo_l:
                    return opt
        for termo in termos:
            xpath_exato = (
                f"{brflow.B_replicacao_select2_option}"
                f"[normalize-space(string(.))='{termo}']"
            )
            for xpath in (
                xpath_exato,
                f"{brflow.B_replicacao_select2_option}[contains(normalize-space(.), '{termo}')]",
            ):
                for opt in driver.find_elements(By.XPATH, xpath):
                    classe = opt.get_attribute("class") or ""
                    if not opt.is_displayed():
                        continue
                    if "select2-results__option--disabled" in classe:
                        continue
                    return opt
        return None

    option = WebDriverWait(driver, timeout).until(lambda d: _opcao_visivel())
    log.info("[FILTROS] Select2 opção encontrada: %s", (option.text or "").strip()[:80])

    if search_input is not None:
        try:
            search_input.send_keys(Keys.ENTER)
            time.sleep(0.25)
            if _filtro_select2_confirmado(driver, select_name, valor_cod, texto_opcao):
                log.info("[FILTROS] Select2 confirmado via ENTER")
                return
        except Exception as exc:
            log.debug("[FILTROS] ENTER no Select2 falhou: %s", exc)

    if not driver.find_elements(By.CSS_SELECTOR, ".select2-container--open"):
        _clicar_combobox_select2(driver, select_name)
        try:
            search_input = WebDriverWait(driver, 3).until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, ".select2-container--open input.select2-search__field")
                )
            )
            search_input.clear()
            search_input.send_keys(_termo_digitacao_select2(valor_cod, texto_opcao))
            time.sleep(0.35)
        except TimeoutException:
            search_input = None
        option = WebDriverWait(driver, timeout).until(lambda d: _opcao_visivel())

    _clicar_opcao_select2(driver, option)
    time.sleep(0.25)
    if _filtro_select2_confirmado(driver, select_name, valor_cod, texto_opcao):
        log.info("[FILTROS] Select2 confirmado via clique")
        return

    try:
        campo = driver.find_element(
            By.CSS_SELECTOR, ".select2-container--open input.select2-search__field"
        )
        campo.send_keys(Keys.ENTER)
        time.sleep(0.25)
    except Exception:
        pass


def _aguardar_opcoes_workflow_destino(
    driver,
    valor_cod: str,
    *,
    timeout: int = REPLICACAO_FILTRO_WORKFLOW_CARREGAMENTO_SEG,
) -> None:
    """Aguarda o WorkFlow Destino carregar após a escolha do cliente."""
    valor_cod = str(valor_cod or "").strip()
    log.info(
        "[FILTROS] Aguardando opções do WorkFlow Destino (até %ds, cod=%s)",
        timeout,
        valor_cod,
    )

    def _workflow_pronto(drv):
        return drv.execute_script(
            """
            const valor = String(arguments[0] || '');
            const sel = document.querySelector('select[name="codWorkFlowDestino"]');
            if (!sel || sel.disabled) return false;
            const container = sel.nextElementSibling;
            if (container && container.classList.contains('select2-container--disabled')) {
                return false;
            }
            const opts = Array.from(sel.options || []).filter(
                (o) => String(o.value || '').trim() !== ''
            );
            if (!opts.length) return false;
            if (valor) {
                return opts.some((o) => String(o.value) === valor);
            }
            return true;
            """,
            valor_cod,
        )

    WebDriverWait(driver, timeout).until(_workflow_pronto)
    info = driver.execute_script(
        """
        const sel = document.querySelector('select[name="codWorkFlowDestino"]');
        if (!sel) return {ok: false};
        const opts = Array.from(sel.options || [])
            .filter((o) => String(o.value || '').trim() !== '')
            .map((o) => ({value: String(o.value), text: String(o.text || '').trim()}));
        return {ok: true, total: opts.length, amostra: opts.slice(0, 3)};
        """
    )
    log.info("[FILTROS] WorkFlow Destino carregado: %s", info)


def _aplicar_filtro_select2_replicacao(
    driver,
    *,
    select_name: str,
    valor_cod: str,
    texto_opcao: str,
    label: str,
) -> None:
    """Seleciona filtro: JS+jQuery primeiro; fallback UI no combobox Select2."""
    valor_cod = str(valor_cod or "").strip()
    texto_opcao = str(texto_opcao or valor_cod).strip()
    if not valor_cod:
        raise ValueError(f"Valor vazio para filtro {label}")

    if _filtro_select2_confirmado(driver, select_name, valor_cod, texto_opcao):
        log.info("[FILTROS] %s já estava em %s", label, valor_cod)
        return

    js_result = _definir_select_filtro_js(driver, select_name, valor_cod, texto_opcao)
    if _filtro_select2_confirmado(driver, select_name, valor_cod, texto_opcao):
        log.info(
            "[FILTROS] %s aplicado via JS | cod=%s | texto_ui=%s",
            label,
            valor_cod,
            _ler_texto_select2_brflow(driver, select_name),
        )
        return

    log.warning(
        "[FILTROS] JS não sincronizou %s (%s): %s; tentando UI",
        label,
        select_name,
        js_result,
    )
    _clicar_combobox_select2(driver, select_name)
    _selecionar_opcao_select2_aberta(
        driver,
        valor_cod=valor_cod,
        texto_opcao=texto_opcao,
    )

    if not _filtro_select2_confirmado(driver, select_name, valor_cod, texto_opcao):
        nativo = _ler_valor_select_brflow(driver, select_name)
        ui = _ler_texto_select2_brflow(driver, select_name)
        raise RuntimeError(
            f"{label} não aplicado: esperado={valor_cod}, nativo={nativo or '(vazio)'}, ui={ui or '(vazio)'}"
        )
    log.info(
        "[FILTROS] %s aplicado via UI | cod=%s | texto_ui=%s",
        label,
        valor_cod,
        _ler_texto_select2_brflow(driver, select_name),
    )


def _abrir_dropdown_workflow_destino(driver, *, timeout: int = TIMEOUT_DRIVER) -> None:
    """Abre o Select2 do WorkFlow Destino (clique ou API select2)."""
    try:
        _clicar_combobox_select2(driver, "codWorkFlowDestino", timeout=timeout)
        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".select2-container--open"))
        )
        return
    except TimeoutException:
        log.warning("[FILTROS] Clique no WorkFlow Destino não abriu dropdown; tentando select2('open')")
    driver.execute_script(
        """
        if (typeof jQuery !== 'undefined') {
            jQuery('select[name="codWorkFlowDestino"]').select2('open');
        }
        """
    )
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".select2-container--open"))
    )


def _aplicar_filtro_workflow_destino_replicacao(
    driver,
    *,
    valor_cod: str,
    texto_opcao: str,
) -> None:
    """WorkFlow Destino: seleção sempre pela UI (Select2 dependente do cliente)."""
    valor_cod = str(valor_cod or "").strip()
    texto_opcao = str(texto_opcao or "").strip()
    label = "WorkFlow Destino"
    if not valor_cod:
        raise ValueError(f"Valor vazio para filtro {label}")

    if _filtro_select2_confirmado(driver, "codWorkFlowDestino", valor_cod, texto_opcao):
        log.info("[FILTROS] %s já estava em %s", label, valor_cod)
        return

    log.info(
        "[FILTROS] Selecionando %s via UI | cod=%s | texto=%s",
        label,
        valor_cod,
        texto_opcao or valor_cod,
    )
    _abrir_dropdown_workflow_destino(driver)
    _selecionar_opcao_select2_aberta(
        driver,
        valor_cod=valor_cod,
        texto_opcao=texto_opcao,
    )

    if not _filtro_select2_confirmado(driver, "codWorkFlowDestino", valor_cod, texto_opcao):
        nativo = _ler_valor_select_brflow(driver, "codWorkFlowDestino")
        ui = _ler_texto_select2_brflow(driver, "codWorkFlowDestino")
        raise RuntimeError(
            f"{label} não aplicado: esperado={valor_cod}, nativo={nativo or '(vazio)'}, ui={ui or '(vazio)'}"
        )
    log.info(
        "[FILTROS] %s aplicado via UI | cod=%s | texto_ui=%s",
        label,
        valor_cod,
        _ler_texto_select2_brflow(driver, "codWorkFlowDestino"),
    )


def _validar_filtros_replicacao_antes_pesquisar(
    driver,
    settings: Optional[dict] = None,
) -> dict:
    """Garante Cliente/WorkFlow Destino aplicados na UI antes de Pesquisar."""
    settings = settings or {}
    valor_cliente = str(settings.get("replicacao_cliente_cod", "751")).strip() or "751"
    texto_cliente = str(settings.get("replicacao_cliente_destino", "") or "").strip()
    valor_workflow = str(settings.get("replicacao_workflow_cod", "17047")).strip() or "17047"
    texto_workflow = str(settings.get("replicacao_workflow_destino", "") or "").strip()

    conferencia = driver.execute_script(
        """
        return {
            cliente: document.querySelector('select[name="codClienteDestino"]')?.value || '',
            cliente_ui: (document.querySelector(
                'span[id*="select2-codClienteDestino"][id$="-container"]'
            )?.textContent || '').trim(),
            workflow: document.querySelector('select[name="codWorkFlowDestino"]')?.value || '',
            workflow_ui: (document.querySelector(
                'span[id*="select2-codWorkFlowDestino"][id$="-container"]'
            )?.textContent || '').trim(),
        };
        """
    )
    log.info("[FILTROS] Conferência pré-Pesquisar: %s", conferencia)

    if not _filtro_select2_confirmado(
        driver, "codClienteDestino", valor_cliente, texto_cliente
    ):
        raise RuntimeError(
            f"Cliente Destino não confirmado antes de Pesquisar: esperado={valor_cliente} "
            f"({texto_cliente}), atual={conferencia.get('cliente')}, "
            f"ui={conferencia.get('cliente_ui')}"
        )
    if not _filtro_select2_confirmado(
        driver, "codWorkFlowDestino", valor_workflow, texto_workflow
    ):
        raise RuntimeError(
            f"WorkFlow Destino não confirmado antes de Pesquisar: esperado={valor_workflow} "
            f"({texto_workflow}), atual={conferencia.get('workflow')}, "
            f"ui={conferencia.get('workflow_ui')}"
        )
    return conferencia


def _preencher_replicacao_filtros(driver, settings: Optional[dict] = None):
    """Preenche Cliente Destino, aguarda WorkFlow e seleciona WorkFlow Destino."""
    settings = settings or {}
    _set_status("Preenchendo filtros de replicação")

    log.info("[FILTROS] Iniciando preenchimento de filtros")

    valor_cliente = str(settings.get("replicacao_cliente_cod", "751")).strip() or "751"
    texto_cliente = str(settings.get("replicacao_cliente_destino", "") or "").strip()
    valor_workflow = str(settings.get("replicacao_workflow_cod", "17047")).strip() or "17047"
    texto_workflow = str(settings.get("replicacao_workflow_destino", "") or "").strip()

    try:
        _aplicar_filtro_select2_replicacao(
            driver,
            select_name="codClienteDestino",
            valor_cod=valor_cliente,
            texto_opcao=texto_cliente,
            label="Cliente Destino",
        )
        _aguardar_opcoes_workflow_destino(driver, valor_workflow)
        time.sleep(0.3)
        _aplicar_filtro_workflow_destino_replicacao(
            driver,
            valor_cod=valor_workflow,
            texto_opcao=texto_workflow,
        )
        conferencia = driver.execute_script(
            """
            return {
                cliente: document.querySelector('select[name="codClienteDestino"]')?.value || '',
                cliente_ui: (document.querySelector(
                    'span[id*="select2-codClienteDestino"][id$="-container"]'
                )?.textContent || '').trim(),
                workflow: document.querySelector('select[name="codWorkFlowDestino"]')?.value || '',
                workflow_ui: (document.querySelector(
                    'span[id*="select2-codWorkFlowDestino"][id$="-container"]'
                )?.textContent || '').trim(),
            };
            """
        )
        log.info("[FILTROS] Conferência final: %s", conferencia)
        if not _filtro_select2_confirmado(
            driver, "codWorkFlowDestino", valor_workflow, texto_workflow
        ):
            raise RuntimeError(
                f"WorkFlow Destino não confirmado na UI: esperado={valor_workflow} "
                f"({texto_workflow}), atual={conferencia.get('workflow')}, "
                f"ui={conferencia.get('workflow_ui')}"
            )
    except Exception as e:
        log.error("[FILTROS] Erro ao preencher filtros de replicação: %s", e)
        take_error_screenshot(driver, "log_screenshots", "filtro_replicacao_erro")
        raise


def _selecionar_select2_valor_alt(driver, elem, valor):
    """Seleciona valor em dropdown customizado ou Select2 não-padrão."""
    log.info(f"[SELECT2_ALT] Iniciando seleção de: {valor}")
    
    try:
        # Clicar no elemento para abrir dropdown
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", elem)
        
        try:
            driver.execute_script("arguments[0].click();", elem)
            log.info("[SELECT2_ALT] Clicado via JavaScript")
        except:
            elem.click()
            log.info("[SELECT2_ALT] Clicado normalmente")
        
        time.sleep(1)
        
        # Procurar por dropdown customizado
        # Pode estar dentro de um modal, popover ou list
        dropdown_xpath = f"""
        //ul[contains(@class, 'dropdown-menu')]//a[contains(text(), '{valor}')] |
        //ul[contains(@class, 'select')]//li[contains(text(), '{valor}')] |
        //div[contains(@class, 'dropdown')]//a[contains(text(), '{valor}')] |
        //div[contains(@class, 'popover')]//button[contains(text(), '{valor}')] |
        //*[contains(@class, 'option') and contains(text(), '{valor}')] |
        //li[contains(@class, 'select2-results__option') and contains(text(), '{valor}')]
        """
        
        opcao = WebDriverWait(driver, TIMEOUT_DRIVER).until(
            EC.element_to_be_clickable((By.XPATH, dropdown_xpath))
        )
        log.info(f"[SELECT2_ALT] Opção encontrada: {valor}")
        
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", opcao)
        driver.execute_script("arguments[0].click();", opcao)
        log.info(f"[SELECT2_ALT] Opção clicada: {valor}")
        
        time.sleep(0.5)
    except Exception as e:
        log.error(f"[SELECT2_ALT] Erro: {e}")
        raise


_ATTR_CELULA_WORKFLOW_ORIGEM = (
    "data-html-nom_workflow_origem",
    "data-html-nom-workflow-origem",
)


def _extrair_texto_celula_replicacao(td, attr_names=None, driver=None) -> str:
    """Lê texto visível ou valor em data-html-* / title (BRFlow costuma usar atributos)."""
    attr_names = attr_names or _ATTR_CELULA_WORKFLOW_ORIGEM
    texto = (td.text or "").strip()
    if texto:
        return texto
    for attr in attr_names:
        valor = (td.get_attribute(attr) or "").strip()
        if valor:
            return valor
    title = (td.get_attribute("title") or "").strip()
    if title:
        return title
    if driver is not None:
        try:
            inner = driver.execute_script(
                "return (arguments[0].innerText || arguments[0].textContent || '').trim();",
                td,
            )
            if inner:
                return str(inner).strip()
        except Exception:
            pass
    return ""


def _ler_total_itens_paginador(driver) -> Optional[int]:
    """Extrai total de itens do rodapé (ex.: '4 itens - Pág. 1 de 1')."""
    try:
        bloco = driver.find_element(
            By.XPATH,
            '//*[@id="layout_layout2_panel_main"]/div[4]/div/div[2]/div[3]',
        )
        texto = (bloco.text or "").strip()
        match = re.search(r"(\d+)\s*itens", texto, re.IGNORECASE)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None


def _obter_tbody_listagem_replicacao(driver):
    """Retorna tbody com mais linhas (evita pegar tbody vazio quando há duplicatas)."""
    tbodies = driver.find_elements(By.XPATH, brflow.B_replicacao_listagem_tbody)
    melhor = None
    melhor_n = 0
    for tb in tbodies:
        try:
            n = len(tb.find_elements(By.TAG_NAME, "tr"))
        except StaleElementReferenceException:
            continue
        if n > melhor_n:
            melhor_n = n
            melhor = tb
    if melhor is not None:
        return melhor, melhor_n
    return None, 0


def _contar_linhas_listagem_replicacao(driver) -> int:
    """Conta linhas visíveis na listagem (tbody com mais tr ou células workflow)."""
    try:
        _, n_tr = _obter_tbody_listagem_replicacao(driver)
        if n_tr > 0:
            return n_tr
        n_celulas = len(
            driver.find_elements(By.CSS_SELECTOR, brflow.B_replicacao_td_workflow_origem)
        )
        if n_celulas > 0:
            return n_celulas
        return n_tr
    except StaleElementReferenceException:
        return -1
    except Exception:
        return 0


def _listagem_pronta_para_ler(
    ultima_contagem: int,
    contagem: int,
    repeticoes: int,
    polls_estaveis: int,
    min_linhas: int,
    alvo_linhas: Optional[int] = None,
    polls_estavel_sem_alvo: int = 8,
) -> tuple[int, int, bool]:
    """Calcula próximo estado da espera da listagem (extraído para testes)."""
    if contagem < 0:
        return ultima_contagem, 0, False
    if contagem > ultima_contagem and ultima_contagem >= 0:
        return contagem, 0, False
    if contagem == ultima_contagem and contagem >= min_linhas:
        repeticoes += 1
        if alvo_linhas is None or contagem >= alvo_linhas:
            pronta = repeticoes >= polls_estaveis
        elif contagem > 0:
            pronta = repeticoes >= polls_estavel_sem_alvo
        else:
            pronta = False
        return contagem, repeticoes, pronta
    return contagem, 0, False


def _aguardar_listagem_replicacao_carregada(
    driver,
    timeout: float = 45,
    polls_estaveis: int = 2,
    intervalo_seg: float = 0.25,
    min_linhas: int = 1,
    usar_alvo_paginador: bool = True,
    polls_estavel_sem_alvo: int = 8,
) -> int:
    """
    Aguarda a listagem BRFlow terminar de carregar após Pesquisar ou mudança de paginação.
    Não estabiliza enquanto a contagem ainda sobe; paginador é referência, não bloqueio rígido.
    """
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.XPATH, brflow.B_replicacao_listagem_tbody))
    )
    inicio = time.time()
    ultima_contagem = -1
    repeticoes = 0
    total_paginador = _ler_total_itens_paginador(driver) if usar_alvo_paginador else None
    alvo_linhas = min(total_paginador, 1000) if total_paginador else None
    if total_paginador is not None:
        log.debug(
            "[LISTAGEM] Paginador indica %d item(ns); alvo=%d linha(s) (flexível se divergir)",
            total_paginador,
            alvo_linhas,
        )

    while time.time() - inicio < timeout:
        contagem = _contar_linhas_listagem_replicacao(driver)
        if contagem < 0:
            repeticoes = 0
            time.sleep(intervalo_seg)
            continue

        ultima_contagem, repeticoes, pronta = _listagem_pronta_para_ler(
            ultima_contagem,
            contagem,
            repeticoes,
            polls_estaveis,
            min_linhas,
            alvo_linhas,
            polls_estavel_sem_alvo,
        )
        if pronta:
            if alvo_linhas and contagem < alvo_linhas:
                log.info(
                    "[LISTAGEM] Carregada com %d linha(s) após %.1fs "
                    "(paginador=%d; contagem diverge do rodapé)",
                    contagem,
                    time.time() - inicio,
                    total_paginador,
                )
            else:
                log.info(
                    "[LISTAGEM] Carregada com %d linha(s) após %.1fs%s",
                    contagem,
                    time.time() - inicio,
                    f" (paginador={total_paginador})" if total_paginador else "",
                )
            return contagem

        time.sleep(intervalo_seg)

    contagem = max(_contar_linhas_listagem_replicacao(driver), 0)
    log.warning(
        "[LISTAGEM] Timeout (%ds) aguardando estabilização; seguindo com %d linha(s)%s",
        int(timeout),
        contagem,
        f" (paginador={total_paginador})" if total_paginador else "",
    )
    return contagem


def _linha_deve_ocultar_por_status_titulo(titulo: Optional[str]) -> bool:
    """Espelha a regra do filtro JS: remover linha se title !== 'Ativo'."""
    return (titulo or "").strip() != "Ativo"


def _pausar_listagem_replicacao(segundos: float, motivo: str) -> None:
    log.info("[LISTAGEM] Aguardando %.0fs (%s)", segundos, motivo)
    _set_status(f"Aguardando listagem ({motivo})")
    time.sleep(segundos)


def _filtrar_listagem_apenas_ativos_js(driver) -> dict:
    """Remove tr.js-tpl-linha sem .btn-status[title=Ativo]. Retorna contadores."""
    script = """
    let removidas = 0, mantidas = 0;
    document.querySelectorAll("tr.js-tpl-linha").forEach(tr => {
        const status = tr.querySelector(".btn-status");
        if (!status || status.getAttribute("title") !== "Ativo") {
            tr.remove();
            removidas++;
        } else {
            mantidas++;
        }
    });
    return { removidas: removidas, mantidas: mantidas, total: removidas + mantidas };
    """
    resultado = driver.execute_script(script)
    if not isinstance(resultado, dict):
        resultado = {"removidas": 0, "mantidas": 0, "total": 0}
    log.info(
        "[LISTAGEM] Filtro ativos | mantidas=%s | removidas=%s | total=%s",
        resultado.get("mantidas", 0),
        resultado.get("removidas", 0),
        resultado.get("total", 0),
    )
    return resultado


def _refiltrar_listagem_ativos_apos_salvar(
    driver,
    settings: Optional[dict] = None,
    motivo: str = "após salvar",
) -> None:
    """Aguarda listagem estável e remove linhas inativas (pós confirmar / próximo workflow)."""
    settings = settings or {}
    if not bool(settings.get("replicacao_apenas_ativos", True)):
        return
    espera = float(settings.get("replicacao_listagem_espera_seg", REPLICACAO_LISTAGEM_ESPERA_SEG))
    _set_status(f"Preparando listagem ({motivo})")
    _aguardar_listagem_replicacao_carregada(
        driver,
        timeout=15,
        usar_alvo_paginador=False,
        min_linhas=1,
    )
    _filtrar_listagem_apenas_ativos_js(driver)
    _pausar_listagem_replicacao(espera, motivo)


def _logar_diagnostico_listagem_replicacao(driver) -> None:
    """Loga contagem de linhas quando Selenium não enxerga linhas visíveis."""
    try:
        total = len(driver.find_elements(By.CSS_SELECTOR, brflow.B_replicacao_linha_listagem))
    except Exception:
        total = -1
    visiveis = len(_iterar_linhas_listagem_replicacao(driver))
    log.warning(
        "[TABELA] Diagnóstico listagem | tr.js-tpl-linha=%s | visíveis=%s",
        total,
        visiveis,
    )


def _preparar_listagem_pos_paginacao(driver, settings: Optional[dict] = None) -> None:
    """Timers fixos + filtro JS de ativos após paginação 1000."""
    settings = settings or {}
    apenas_ativos = bool(settings.get("replicacao_apenas_ativos", True))
    espera = float(settings.get("replicacao_listagem_espera_seg", REPLICACAO_LISTAGEM_ESPERA_SEG))

    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_replicacao_linha_listagem))
    )

    _pausar_listagem_replicacao(espera, "após paginação 1000")

    if apenas_ativos:
        _filtrar_listagem_apenas_ativos_js(driver)
        _pausar_listagem_replicacao(espera, "após filtro ativos")


def _forcar_paginacao_js(driver, novo_valor=1000):
    try:
        log.info(f"[PAGINACAO_JS] Alterando paginação para {novo_valor}")

        tbody_antes = None
        try:
            tbody_antes = driver.find_element(By.XPATH, brflow.B_replicacao_listagem_tbody)
        except Exception:
            pass

        botao_dropdown = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((
                By.XPATH,
                '//*[@id="layout_layout2_panel_main"]/div[4]/div/div[2]/div[3]/div/div[1]/span/div/div/div/button[4]'
            ))
        )
        driver.execute_script("arguments[0].click();", botao_dropdown)

        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((
                By.XPATH,
                f'//span[@data-nrtamanho="{novo_valor}"] | //span[@data-nrtamanho="250"]',
            ))
        )

        modo = driver.execute_script(f"""
            let alvo = document.querySelector('span[data-nrtamanho="{novo_valor}"]');
            if (alvo) {{
                let li = alvo.closest('li');
                if (li) {{
                    li.click();
                    return 'direto';
                }}
            }}

            let span = document.querySelector('span[data-nrtamanho="250"]');
            if (!span) return null;

            span.setAttribute('data-nrtamanho', '{novo_valor}');
            span.innerText = '{novo_valor}';

            let li = span.closest('li');
            if (li) {{
                li.setAttribute('data-paginador-tamanho-pagina', '{novo_valor}');
                li.click();
                return 'ajuste_250';
            }}

            return null;
        """)

        if modo is None:
            raise Exception(f"Opção de paginação {novo_valor} não encontrada no dropdown")

        log.info(f"[PAGINACAO_JS] Paginação {novo_valor} selecionada ({modo})")

        if tbody_antes is not None:
            try:
                WebDriverWait(driver, 15).until(EC.staleness_of(tbody_antes))
            except TimeoutException:
                log.debug("[PAGINACAO_JS] tbody não ficou stale após mudança de página")

    except Exception as e:
        log.error(f"[PAGINACAO_JS] Erro ao alterar paginação: {e}")
        raise

def _xpath_escape_texto(texto: str) -> str:
    """Escapa texto para uso seguro em literais XPath entre aspas duplas."""
    if '"' not in str(texto):
        return f'"{texto}"'
    if "'" not in str(texto):
        return f"'{texto}'"
    partes = str(texto).split('"')
    return "concat(" + ', \'"\', '.join(f'"{p}"' for p in partes) + ")"


def _sanitizar_sufixo_screenshot(nome: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", str(nome).strip())[:80] or "workflow"


def _aguardar_painel_edicao(driver):
    _set_status("Aguardando painel de edição")
    WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, brflow.B_replicacao_painel_edicao))
    )
    time.sleep(0.5)


def _marcar_replicar_protocolos_especificos(driver):
    _set_status("Marcando replicação por protocolos específicos")
    checkbox = WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_replicacao_checkbox_protocolos))
    )
    driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", checkbox)
    if not checkbox.is_selected():
        try:
            driver.execute_script("arguments[0].click();", checkbox)
        except Exception:
            checkbox.click()
        log.info("[REPLICACAO] Checkbox replicarApenasProtocolosEspecificos marcado")
    else:
        log.info("[REPLICACAO] Checkbox replicarApenasProtocolosEspecificos já estava marcado")
    time.sleep(0.5)


def _csv_protocolos_vazio(caminho: Path) -> bool:
    """True se o CSV é de limpeza (0 bytes legado ou só placeholder)."""
    return csv_protocolos_e_limpeza(caminho)


def _upload_csv_vazio_habilitado(settings=None) -> bool:
    settings = settings or {}
    if "replicacao_upload_csv_vazio" in settings:
        return bool(settings["replicacao_upload_csv_vazio"])
    raw = os.getenv("REPLICACAO_UPLOAD_CSV_VAZIO", "")
    if raw.strip():
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return REPLICACAO_UPLOAD_CSV_VAZIO_DEFAULT


def _enviar_csv_protocolos(driver, caminho_csv: Path, tentativas: int = 2):
    caminho = Path(caminho_csv).resolve()
    if not caminho.exists():
        raise FileNotFoundError(f"CSV de protocolos não encontrado: {caminho}")
    csv_vazio = _csv_protocolos_vazio(caminho)

    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            file_input = WebDriverWait(driver, TIMEOUT_DRIVER).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_replicacao_input_arquivo_csv))
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});",
                file_input,
            )
            file_input.send_keys(str(caminho))
            if csv_vazio:
                log.info(
                    "[REPLICACAO] CSV limpeza (placeholder %s) enviado: %s (tentativa %d/%d)",
                    REPLICACAO_CSV_PLACEHOLDER_LIMPEZA,
                    caminho.name,
                    tentativa,
                    tentativas,
                )
            else:
                log.info(
                    "[REPLICACAO] CSV enviado: %s (tentativa %d/%d)",
                    caminho.name,
                    tentativa,
                    tentativas,
                )
            time.sleep(0.5)
            return
        except Exception as exc:
            ultimo_erro = exc
            log.warning(
                "[REPLICACAO] Falha ao enviar CSV (tentativa %d/%d): %s",
                tentativa,
                tentativas,
                exc,
            )
            time.sleep(1)
    raise ultimo_erro


def _classificar_situacao_texto(texto: str) -> str:
    """Classifica situação da linha BRFlow: ATIVO, INATIVO ou DESCONHECIDO."""
    bruto = str(texto or "").strip()
    if not bruto:
        return "DESCONHECIDO"
    t = bruto.casefold()
    if "inativ" in t:
        return "INATIVO"
    if t in ("n", "nao", "não", "0", "false", "no"):
        return "INATIVO"
    if "ativ" in t:
        return "ATIVO"
    if t in ("s", "sim", "1", "true", "yes"):
        return "ATIVO"
    return "DESCONHECIDO"


def _extrair_texto_situacao_linha(linha) -> str:
    """Texto de situação: .btn-status title, data-html-* ou vazio."""
    try:
        btn = linha.find_element(By.CSS_SELECTOR, brflow.B_replicacao_btn_status)
        titulo = (btn.get_attribute("title") or "").strip()
        if titulo:
            return titulo
    except Exception:
        pass
    partes: list[str] = []
    for attr in brflow.B_replicacao_td_situacao_attrs:
        for td in linha.find_elements(By.CSS_SELECTOR, f"td[{attr}]"):
            txt = (td.text or "").strip()
            if txt:
                partes.append(txt)
    if partes:
        return " | ".join(partes)
    return ""


def _linha_listagem_visivel(tr) -> bool:
    try:
        return tr.is_displayed()
    except Exception:
        return False


def _iterar_linhas_listagem_replicacao(driver):
    """Itera linhas visíveis da tabela (tr.js-tpl-linha após filtro JS)."""
    trs = driver.find_elements(By.CSS_SELECTOR, brflow.B_replicacao_linha_listagem)
    if trs:
        return [tr for tr in trs if _linha_listagem_visivel(tr)]
    tbody, n_tr = _obter_tbody_listagem_replicacao(driver)
    if tbody is not None and n_tr > 0:
        return [tr for tr in tbody.find_elements(By.TAG_NAME, "tr") if _linha_listagem_visivel(tr)]
    return [
        tr
        for tr in driver.find_elements(
            By.XPATH,
            "//tr[.//td[@data-html-nom_workflow_origem]]",
        )
        if _linha_listagem_visivel(tr)
    ]


def _classificar_linha_replicacao(linha, indice: int, driver=None) -> Optional[dict]:
    """Extrai workflow + situação de uma linha da listagem BRFlow."""
    if not _linha_listagem_visivel(linha):
        return None
    wf_tds = linha.find_elements(By.CSS_SELECTOR, brflow.B_replicacao_td_workflow_origem)
    if not wf_tds:
        return None
    workflow = _extrair_texto_celula_replicacao(wf_tds[0], driver=driver)
    if not workflow:
        return None
    situacao = _classificar_situacao_texto(_extrair_texto_situacao_linha(linha))
    return {
        "workflow": workflow,
        "_wf_key": _normalizar_workflow(workflow),
        "situacao": situacao,
        "indice_linha": indice,
    }


def _ler_linhas_replicacao(driver) -> list:
    """Lê todas as linhas da listagem (inclui duplicatas ativo/inativo)."""
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.XPATH, brflow.B_replicacao_listagem_tbody))
    )
    trs = _iterar_linhas_listagem_replicacao(driver)
    if not trs:
        log.warning("[LISTAGEM] Nenhuma linha com Workflow Origem encontrada")
        return []
    linhas: list = []
    for idx, tr in enumerate(trs, start=1):
        try:
            item = _classificar_linha_replicacao(tr, idx, driver=driver)
            if item:
                linhas.append(item)
        except Exception as exc:
            log.debug("Listagem BRFlow | linha %d ignorada: %s", idx, exc)
    if linhas:
        log.debug(
            "Listagem BRFlow | amostra linha 1: workflow=%s situacao=%s",
            linhas[0]["workflow"],
            linhas[0]["situacao"],
        )
    return linhas


def _resumir_linhas_por_workflow(linhas: list) -> dict:
    """Agrega duplicatas por workflow normalizado."""
    resumo: dict = {}
    for item in linhas:
        key = item["_wf_key"]
        if key not in resumo:
            resumo[key] = {
                "workflow": item["workflow"],
                "tem_ativo": False,
                "tem_inativo": False,
                "linhas_ativas": 0,
                "linhas_inativas": 0,
            }
        bloco = resumo[key]
        if item["situacao"] == "ATIVO":
            bloco["tem_ativo"] = True
            bloco["linhas_ativas"] += 1
        elif item["situacao"] == "INATIVO":
            bloco["tem_inativo"] = True
            bloco["linhas_inativas"] += 1
    return resumo


def _workflow_tem_linha_ativa_brflow(resumo: dict, workflow: str) -> bool:
    key = _normalizar_workflow(workflow)
    return bool(resumo.get(key, {}).get("tem_ativo"))


def _workflow_ausente_listagem_brflow(resumo: dict, workflow: str) -> bool:
    return _normalizar_workflow(workflow) not in resumo


def _eh_workflow_apenas_inativo_brflow(resumo: dict, workflow: str) -> bool:
    key = _normalizar_workflow(workflow)
    if key not in resumo:
        return False
    bloco = resumo[key]
    return bool(bloco["tem_inativo"] and not bloco["tem_ativo"])


def _logar_workflow_ausente_diagnostico(
    workflow_config: str,
    workflow_brflow: str,
    linhas_br: list,
    resumo_br: dict,
) -> None:
    """Loga contexto quando workflow do plano não aparece no resumo da listagem."""
    wf_key = _normalizar_workflow(workflow_brflow)
    log.warning(
        "[PESQUISAR] Diagnóstico ausente | config=%s | brflow=%s | linhas_lidas=%d | workflows_resumo=%d",
        workflow_config,
        workflow_brflow,
        len(linhas_br),
        len(resumo_br),
    )
    parciais: list[str] = []
    for item in linhas_br:
        nome = item.get("workflow", "")
        chave = item.get("_wf_key", "")
        if not nome:
            continue
        if wf_key and (wf_key in chave or chave in wf_key):
            parciais.append(nome)
            continue
        alvo = workflow_brflow or workflow_config
        if alvo.casefold() in nome.casefold() or nome.casefold() in alvo.casefold():
            parciais.append(nome)
    if parciais:
        unicos = list(dict.fromkeys(parciais))[:5]
        log.warning("[PESQUISAR] Match parcial na listagem: %s", unicos)
    log.warning(
        "[PESQUISAR] Verifique Default.xlsx aba Workflow d1: coluna 'Workflow - selenium' "
        "deve coincidir com Workflow Origem no BRFlow"
    )


_STATUS_WORKFLOW_SKIP_RETOMADA = frozenset({"UPLOAD_OK", "SALVO_OK", "INATIVO"})


def _clicar_salvar_replicacao(driver):
    """Clica em Salvar no painel de edição da replicação."""
    _set_status("Salvando replicação no BRFlow")
    seletores = (
        (By.XPATH, brflow.B_replicacao_salvar),
        (By.XPATH, brflow.B_replicacao_salvar_posicao),
        (By.XPATH, brflow.B_U_salvar),
    )
    ultimo_erro = None
    for by, value in seletores:
        try:
            botao = WebDriverWait(driver, TIMEOUT_DRIVER).until(
                EC.element_to_be_clickable((by, value))
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});",
                botao,
            )
            try:
                driver.execute_script("arguments[0].click();", botao)
            except Exception:
                botao.click()
            log.info("[REPLICACAO] Salvar clicado")
            time.sleep(0.5)
            return
        except Exception as exc:
            ultimo_erro = exc
    raise ultimo_erro or TimeoutException("Botão Salvar não encontrado no painel de replicação")


def _confirmar_modal_replicacao_se_existir(driver, timeout: int = 10):
    """Confirma popup w2ui/bootbox/swal após Salvar (botão Sim / Confirmar)."""
    _set_status("Confirmando popup de replicação")
    seletores = (
        (By.CSS_SELECTOR, brflow.B_replicacao_confirmar_w2ui_sim),
        (By.XPATH, brflow.B_replicacao_confirmar_w2ui_popup),
        (By.ID, "Yes"),
        (By.XPATH, brflow.B_replicacao_confirmar_modal),
        (
            By.XPATH,
            "//div[contains(@class,'bootbox') and contains(@class,'in')]"
            "//button[contains(@class,'btn-primary')]",
        ),
        (
            By.XPATH,
            "//div[contains(@class,'swal2-container') and not(contains(@style,'display: none'))]"
            "//button[contains(@class,'swal2-confirm')]",
        ),
    )
    fim = time.time() + timeout
    while time.time() < fim:
        for by, value in seletores:
            try:
                botoes = driver.find_elements(by, value)
                for botao in botoes:
                    if not botao.is_displayed() or not botao.is_enabled():
                        continue
                    driver.execute_script("arguments[0].click();", botao)
                    log.info("[REPLICACAO] Popup de confirmação aceito (Sim)")
                    time.sleep(0.5)
                    return
            except Exception:
                continue
        time.sleep(0.25)
    log.warning("[REPLICACAO] Modal de confirmação não encontrado após %ds", timeout)


def _aguardar_fechamento_painel_edicao(driver):
    """Aguarda o painel lateral fechar após salvar."""
    _set_status("Aguardando fechamento do painel de edição")
    painel = (By.CSS_SELECTOR, brflow.B_replicacao_painel_edicao)
    listagem = (By.XPATH, brflow.B_replicacao_listagem_tbody)

    def _painel_fechado(drv):
        try:
            elementos = drv.find_elements(*painel)
            if not elementos:
                return True
            for el in elementos:
                if el.is_displayed():
                    return False
            return True
        except Exception:
            return True

    WebDriverWait(driver, TIMEOUT_DRIVER).until(_painel_fechado)
    WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located(listagem)
    )
    time.sleep(0.5)
    log.info("[REPLICACAO] Painel de edição fechado")


def _configurar_workflow_replicacao(
    driver,
    workflow: str,
    caminho_csv: Path,
    settings: Optional[dict] = None,
    workflow_brflow: Optional[str] = None,
):
    """Marca checkbox, envia CSV, salva e confirma no painel de edição."""
    settings = settings or {}
    sufixo = _sanitizar_sufixo_screenshot(workflow_brflow or workflow)
    try:
        _aguardar_painel_edicao(driver)
        _marcar_replicar_protocolos_especificos(driver)
        _enviar_csv_protocolos(driver, caminho_csv)
        _clicar_salvar_replicacao(driver)
        _confirmar_modal_replicacao_se_existir(driver)
        _aguardar_fechamento_painel_edicao(driver)
        _refiltrar_listagem_ativos_apos_salvar(driver, settings, motivo="após confirmar salvamento")
        _set_status(f"Salvo: {workflow}")
        log.info(
            "[REPLICACAO] Workflow salvo: %s | arquivo=%s",
            workflow,
            caminho_csv.name,
        )
    except Exception:
        take_error_screenshot(driver, "log_screenshots", f"replicacao_salvar_{sufixo}")
        raise


def _clicar_editar_por_workflow(
    driver,
    workflow_alvo,
    apenas_ativo: bool = False,
    settings: Optional[dict] = None,
):
    try:
        log.info(
            "[TABELA] Procurando workflow: %s (apenas_ativo=%s)",
            workflow_alvo,
            apenas_ativo,
        )

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, brflow.B_replicacao_listagem_tbody))
        )
        wf_key = _normalizar_workflow(workflow_alvo)

        if apenas_ativo:
            trs = _iterar_linhas_listagem_replicacao(driver)
            if not trs:
                _refiltrar_listagem_ativos_apos_salvar(
                    driver, settings, motivo="retry antes de editar"
                )
                trs = _iterar_linhas_listagem_replicacao(driver)
            if not trs:
                _logar_diagnostico_listagem_replicacao(driver)
                raise TimeoutException(
                    f"Listagem sem linhas ativas após salvar para workflow: {workflow_alvo}; "
                    "verifique btn-status na tela BRFlow"
                )
            for tr in trs:
                item = _classificar_linha_replicacao(tr, 0, driver=driver)
                if not item or item["_wf_key"] != wf_key:
                    continue
                botao_editar = tr.find_element(
                    By.XPATH,
                    './/span[contains(@class,"glyphicon-pencil")]',
                )
                driver.execute_script("arguments[0].click();", botao_editar)
                log.info("[TABELA] Clique em editar (linha visível): %s", workflow_alvo)
                return
            raise TimeoutException(
                f"Nenhuma linha visível encontrada para workflow: {workflow_alvo}"
            )

        texto_xpath = _xpath_escape_texto(workflow_alvo)
        xpath_linha = f"""
        //tr[
            .//td[@data-html-nom_workflow_origem
            and (
                contains(normalize-space(), {texto_xpath})
                or contains(@data-html-nom_workflow_origem, {texto_xpath})
            )]
        ]
        """
        linha = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, xpath_linha))
        )
        botao_editar = linha.find_element(
            By.XPATH,
            './/span[contains(@class,"glyphicon-pencil")]',
        )
        driver.execute_script("arguments[0].click();", botao_editar)
        log.info(f"[TABELA] Clique em editar realizado para: {workflow_alvo}")

    except Exception as e:
        log.error(f"[TABELA] Erro ao clicar no editar: {e}")
        raise

def _normalizar_settings_replicacao(settings=None) -> dict:
    """Mescla settings com variáveis de ambiente da replicação."""
    cfg = dict(settings or {})
    if os.getenv("REPLICACAO_APENAS_PLANEJAMENTO", "").strip() in ("1", "true", "True", "yes"):
        cfg.setdefault("apenas_planejamento", True)
    run_env = os.getenv("REPLICACAO_RUN_ID", "").strip()
    if run_env and "run_id" not in cfg:
        cfg["run_id"] = run_env
    return cfg


def _atualizar_estado_workflow(
    estado: dict,
    workflow: str,
    status: str,
    csv_path: Optional[Path] = None,
    workflow_brflow: Optional[str] = None,
) -> None:
    if not estado:
        return
    workflows = estado.setdefault("workflows", {})
    entry = workflows.setdefault(workflow, {})
    entry["status"] = status
    if csv_path is not None:
        entry["csv"] = str(csv_path)
    if workflow_brflow:
        entry["workflow_brflow"] = workflow_brflow
    entry["atualizado_em"] = datetime.now().isoformat(timespec="seconds")


def _clicar_pesquisar_replicacao(
    driver,
    plano: PlanoReplicacao,
    estado: Optional[dict] = None,
    settings: Optional[dict] = None,
):
    """Pesquisa, abre edição e anexa CSV de protocolos para cada workflow do plano."""
    settings = settings or {}
    workflows = plano.workflows
    if not workflows:
        log.warning("[PESQUISAR] Nenhum workflow com protocolos no plano de replicação")
        return

    forcar = bool(settings.get("forcar_reexecucao", False))
    apenas_pendentes = bool(settings.get("apenas_pendentes", True))
    apenas_ativos = bool(settings.get("replicacao_apenas_ativos", True))

    _validar_filtros_replicacao_antes_pesquisar(driver, settings)
    PESQUISAR = '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/fieldset/div[2]/button'
    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, PESQUISAR)))
    click_element(driver, by=By.XPATH, value=PESQUISAR)
    log.info("[PESQUISAR] Botão Pesquisar clicado com sucesso")
    _aguardar_listagem_replicacao_carregada(
        driver,
        timeout=30,
        usar_alvo_paginador=False,
    )
    _forcar_paginacao_js(driver, 1000)
    _preparar_listagem_pos_paginacao(driver, settings)

    resumo_br: dict = {}
    if apenas_ativos:
        linhas_br = _ler_linhas_replicacao(driver)
        resumo_br = _resumir_linhas_por_workflow(linhas_br)
        duplicatas = sum(
            1
            for item in resumo_br.values()
            if item["linhas_ativas"] + item["linhas_inativas"] > 1
        )
        ativos = sum(1 for item in resumo_br.values() if item["tem_ativo"])
        inativos = sum(1 for item in resumo_br.values() if item["tem_inativo"] and not item["tem_ativo"])
        log.info(
            "Listagem BRFlow | %d linhas | %d workflow(s) | ativos=%d | só inativo=%d | duplicatas=%d",
            len(linhas_br),
            len(resumo_br),
            ativos,
            inativos,
            duplicatas,
        )

    total = len(workflows)
    for indice, workflow in enumerate(workflows, start=1):
        if parar_event.is_set():
            log.info("[PESQUISAR] Parada solicitada; interrompendo loop de workflows")
            break

        if estado:
            info = estado.get("workflows", {}).get(workflow, {})
            status_atual = str(info.get("status", "PENDENTE"))
            if status_atual in _STATUS_WORKFLOW_SKIP_RETOMADA and apenas_pendentes and not forcar:
                log.info("[PESQUISAR] Workflow %s já processado (%s); pulando", workflow, status_atual)
                continue

        csv_path = plano.csv_paths.get(workflow)
        if not csv_path or not Path(csv_path).exists():
            log.warning("[PESQUISAR] CSV ausente para %s; pulando", workflow)
            plano.warnings.append(f"{workflow}: CSV não encontrado para upload na UI")
            _atualizar_estado_workflow(estado, workflow, "PULADO", csv_path)
            continue
        csv_vazio = _csv_protocolos_vazio(Path(csv_path))
        if csv_vazio:
            if _upload_csv_vazio_habilitado(settings):
                log.info(
                    "[PESQUISAR] CSV limpeza (placeholder %s) para %s — enviando no BRFlow",
                    REPLICACAO_CSV_PLACEHOLDER_LIMPEZA,
                    workflow,
                )
            else:
                log.warning(
                    "[PESQUISAR] CSV limpeza (placeholder %s) para %s; pulando upload",
                    REPLICACAO_CSV_PLACEHOLDER_LIMPEZA,
                    workflow,
                )
                _atualizar_estado_workflow(estado, workflow, "PULADO", Path(csv_path))
                continue

        wf_brflow = workflow_nome_brflow(plano, workflow)
        if apenas_ativos:
            if _workflow_ausente_listagem_brflow(resumo_br, wf_brflow):
                msg = (
                    f"{workflow} (BRFlow: {wf_brflow}): ausente na listagem após Pesquisar"
                )
                log.warning("[PESQUISAR] %s", msg)
                _logar_workflow_ausente_diagnostico(workflow, wf_brflow, linhas_br, resumo_br)
                plano.warnings.append(msg)
                _atualizar_estado_workflow(
                    estado, workflow, "PULADO", Path(csv_path), workflow_brflow=wf_brflow
                )
                if estado and plano.estado_execucao_path:
                    salvar_estado_execucao(estado, plano.estado_execucao_path)
                continue
            if _eh_workflow_apenas_inativo_brflow(resumo_br, wf_brflow):
                msg = f"{workflow} (BRFlow: {wf_brflow}): apenas linha(s) inativa(s)"
                log.warning("[PESQUISAR] %s", msg)
                plano.warnings.append(msg)
                _atualizar_estado_workflow(
                    estado, workflow, "INATIVO", Path(csv_path), workflow_brflow=wf_brflow
                )
                if estado and plano.estado_execucao_path:
                    salvar_estado_execucao(estado, plano.estado_execucao_path)
                continue
            wf_key = _normalizar_workflow(wf_brflow)
            if resumo_br.get(wf_key, {}).get("linhas_ativas", 0) > 1:
                log.warning(
                    "[PESQUISAR] %s (BRFlow: %s): %d linhas ATIVO; editando a primeira",
                    workflow,
                    wf_brflow,
                    resumo_br[wf_key]["linhas_ativas"],
                )

        _set_progress(0, f"Workflow {indice}/{total}: {workflow}")
        _set_status(f"Editando workflow ({indice}/{total}): {wf_brflow}")
        log.info(
            "[PESQUISAR] Abrindo edição | config=%s | brflow=%s",
            workflow,
            wf_brflow,
        )
        try:
            if apenas_ativos:
                _refiltrar_listagem_ativos_apos_salvar(
                    driver, settings, motivo="antes de abrir edição"
                )
            _clicar_editar_por_workflow(
                driver, wf_brflow, apenas_ativo=apenas_ativos, settings=settings
            )
            _configurar_workflow_replicacao(
                driver, workflow, Path(csv_path), settings=settings, workflow_brflow=wf_brflow
            )
            _atualizar_estado_workflow(
                estado, workflow, "SALVO_OK", Path(csv_path), workflow_brflow=wf_brflow
            )
            if estado and plano.estado_execucao_path:
                salvar_estado_execucao(estado, plano.estado_execucao_path)
        except Exception:
            _atualizar_estado_workflow(
                estado, workflow, "ERRO", Path(csv_path), workflow_brflow=wf_brflow
            )
            if estado and plano.estado_execucao_path:
                salvar_estado_execucao(estado, plano.estado_execucao_path)
            take_error_screenshot(driver, "log_screenshots", f"replicacao_wf_{indice}")
            log.exception("[PESQUISAR] Falha no workflow %s; continuando", workflow)
        time.sleep(0.3)


def _resolver_plano(settings: dict) -> tuple[PlanoReplicacao, Optional[dict]]:
    """Gera plano novo ou carrega execução existente pelo run_id."""
    run_id = str(settings.get("run_id", "") or "").strip()
    gerar_novo = bool(settings.get("gerar_novo_plano", False))

    if not run_id:
        run_id = resolver_ultimo_run_id() or ""

    if run_id and not gerar_novo and carregar_estado_execucao(run_id):
        log.info("Carregando plano existente run_id=%s", run_id)
        plano = carregar_plano_por_run_id(run_id, settings)
        estado = carregar_estado_execucao(run_id)
        return plano, estado

    if run_id and not gerar_novo:
        settings = {**settings, "run_id": run_id}

    plano = gerar_plano_replicacao(settings=settings)
    estado = carregar_estado_execucao(plano.run_id)
    return plano, estado


def _executar_planejamento(settings=None) -> PlanoReplicacao:
    """Etapa 1: gera CSVs de protocolos por workflow (sem abrir navegador)."""
    settings = _normalizar_settings_replicacao(settings)
    _set_status("Planejamento: volumetria + Default.xlsx + Categoria + parquet D-1")
    _set_progress(5, "Gerando plano de replicação")
    gerar_novo = bool(settings.get("gerar_novo_plano", False))
    run_id = str(settings.get("run_id", "") or "").strip()
    if run_id and not gerar_novo and carregar_estado_execucao(run_id):
        plano = carregar_plano_por_run_id(run_id, settings)
    else:
        plano = gerar_plano_replicacao(settings=settings)
    for workflow, path in plano.csv_paths.items():
        qtd = len(plano.protocolos_por_workflow.get(workflow, []))
        log.info("Plano | %s | %d protocolos | CSV: %s", workflow, qtd, path)
    for aviso in plano.warnings:
        log.warning("Plano | %s", aviso)
    if plano.workflows_sem_registro:
        log.warning(
            "Plano | %d workflow(s) sem D-1 (CSV fallback vazio): %s",
            len(plano.workflows_sem_registro),
            ", ".join(plano.workflows_sem_registro),
        )
    if plano.relatorio_excel_path:
        log.info("Plano | relatório Excel: %s", plano.relatorio_excel_path)
    if plano.estado_execucao_path:
        log.info("Plano | estado: %s", plano.estado_execucao_path)
    log.info(
        "Plano | run_id=%s | base=%s | protocolos=%s | resumo=%s",
        plano.run_id,
        PASTA_REPLICACAO_AUD_BASE,
        plano.pasta_protocolos,
        plano.pasta_resumo,
    )
    _set_progress(15, f"Plano gerado: {len(plano.workflows)} workflow(s)")
    _set_status(
        f"run_id={plano.run_id} | protocolos: {plano.pasta_protocolos.name} | "
        f"resumo: {plano.pasta_resumo.name}"
    )
    return plano


def executar_bot_replicacao(settings=None):
    _reset_progress_state()
    _set_status("Replicação de Auditoria: iniciando")
    _set_progress(0, "Inicializando")
    settings = _normalizar_settings_replicacao(settings)

    if settings.get("apenas_planejamento"):
        try:
            plano = _executar_planejamento(settings)
            log.info(
                "Planejamento concluído (sem BRFlow) | run_id=%s | protocolos=%s",
                plano.run_id,
                plano.pasta_protocolos,
            )
            _set_progress(100, "Planejamento concluído (sem BRFlow)")
            _set_status(f"Planejamento OK | run_id={plano.run_id}")
        except Exception as exc:
            log.exception("Falha no planejamento da replicação")
            _set_status(f"Erro no planejamento: {exc}")
        return

    drv = None
    estado = None
    plano = None
    try:
        plano, estado = _resolver_plano(settings)
    except Exception as exc:
        log.exception("Falha no planejamento da replicação")
        _set_status(f"Erro no planejamento: {exc}")
        return

    headless = _get_headless(settings)
    matricula, senha = _get_credentials(settings)
    drv = _create_chrome_driver(headless=headless)

    with MetricsContext("bot_replicacao_aud") as exec_ctx:
        try:
            if not matricula or not senha:
                _set_status("Credenciais Okta não fornecidas")
                log.warning("Credenciais Okta não encontradas em settings ou variáveis de ambiente")

            _fazer_login_okta(drv, matricula, senha)
            _abrir_brflow(drv)
            _acessar_menu_replicacao(drv)
            _preencher_replicacao_filtros(drv, settings=settings)
            _clicar_pesquisar_replicacao(drv, plano, estado=estado, settings=settings)
            _set_progress(100, "Fluxo concluído")
            _set_status("Replicação de Auditoria: execução concluída")
        except (TimeoutException, WebDriverException):
            log.exception("Erro Selenium durante execução do bot de replicação")
            take_error_screenshot(drv, "log_screenshots", "bot_replicacao_aud_selenium_erro")
        except Exception:
            log.exception("Erro inesperado no bot de replicação")
            take_error_screenshot(drv, "log_screenshots", "bot_replicacao_aud_erro")
        finally:
            if plano and estado:
                try:
                    estado_atual = carregar_estado_execucao(plano.run_id) or estado
                    atualizar_relatorio_excel(plano, estado_atual)
                    log.info("Relatório Excel atualizado: %s", plano.relatorio_excel_path)
                except Exception:
                    log.exception("Falha ao atualizar relatório Excel após Selenium")
            safe_close_driver(drv)

def start(settings=None):
    thread = threading.Thread(target=executar_bot_replicacao, args=(settings,), daemon=True)
    thread.start()
    return thread


def stop():
    parar_event.set()


def main():
    executar_bot_replicacao()


if __name__ == "__main__":
    main()

