# -*- coding: utf-8 -*-
"""Filtros de pesquisa BRFlow (Cliente e WorkFlow Destino) — replicação D-1."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.bots.replicacao_d1.selenium.constants import (
    REPLICACAO_FILTRO_WORKFLOW_CARREGAMENTO_SEG,
    TIMEOUT_DRIVER,
)
from app.bots.replicacao_d1.selenium.filters import css_select_filtro_pesquisa
from app.bots.replicacao_d1.selenium.select2_utils import (
    normalizar_texto_select2_ui,
    termo_digitacao_select2,
    termos_busca_select2,
    texto_select2_bate,
    ui_select2_vazia,
)
from app.bots.replicacao_d1.settings import filtrar_workflow_destino_habilitado
from app.config import brflow

log = logging.getLogger("robots.bot_replicacao_aud_d1")

SetStatusFn = Callable[[str], None]
TakeScreenshotFn = Callable[..., None]


def ler_valor_select_filtro_pesquisa(driver, select_name: str) -> str:
    css = css_select_filtro_pesquisa(select_name)
    return str(
        driver.execute_script(
            "const sel = document.querySelector(arguments[0]); return sel ? String(sel.value || '') : '';",
            css,
        )
        or ""
    )


def ler_texto_select2_filtro_pesquisa(driver, select_name: str) -> str:
    css = css_select_filtro_pesquisa(select_name)
    return normalizar_texto_select2_ui(
        str(
            driver.execute_script(
                """
                const sel = document.querySelector(arguments[0]);
                if (!sel) return '';
                const container = sel.nextElementSibling;
                if (!container) return '';
                const rendered = container.querySelector('.select2-selection__rendered');
                return rendered ? String(rendered.textContent || '').trim() : '';
                """,
                css,
            )
            or ""
        )
    )


def filtro_select2_confirmado(
    driver,
    select_name: str,
    valor_cod: str,
    texto_opcao: str = "",
) -> bool:
    """Confirma valor no <select> nativo e texto renderizado no Select2."""
    valor_cod = str(valor_cod or "").strip()
    if ler_valor_select_filtro_pesquisa(driver, select_name) != valor_cod:
        return False
    ui = ler_texto_select2_filtro_pesquisa(driver, select_name)
    if ui_select2_vazia(ui):
        return False
    if texto_opcao and not texto_select2_bate(ui, texto_opcao):
        return False
    return True


def aguardar_filtro_select2_estavel(
    driver,
    select_name: str,
    valor_cod: str,
    texto_opcao: str = "",
    *,
    tentativas: int = 3,
    intervalo_seg: float = 0.35,
) -> bool:
    """Confirma o filtro em leituras consecutivas (evita falso positivo antes do reset do Select2)."""
    ok_seguidas = 0
    for _ in range(max(1, tentativas)):
        if filtro_select2_confirmado(driver, select_name, valor_cod, texto_opcao):
            ok_seguidas += 1
            if ok_seguidas >= 2:
                return True
        else:
            ok_seguidas = 0
        time.sleep(intervalo_seg)
    return filtro_select2_confirmado(driver, select_name, valor_cod, texto_opcao)


def definir_select_filtro_js(
    driver,
    select_name: str,
    valor_cod: str,
    texto_hint: str = "",
) -> dict:
    """Define valor no <select> de pesquisa e sincroniza Select2 via jQuery."""
    css = css_select_filtro_pesquisa(select_name)
    return driver.execute_script(
        """
        const sel = document.querySelector(arguments[0]);
        const valor = String(arguments[1] || '');
        const hint = String(arguments[2] || '');
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
        const container = sel.nextElementSibling;
        const rendered = container ? container.querySelector('.select2-selection__rendered') : null;
        return {
            ok: String(sel.value) === String(opt.value),
            value: String(sel.value || ''),
            text: String(opt.text || '').trim(),
            ui: rendered ? String(rendered.textContent || '').trim() : '',
        };
        """,
        css,
        valor_cod,
        texto_hint,
    ) or {}


def definir_workflow_destino_js(
    driver,
    valor_cod: str,
    texto_hint: str = "",
) -> dict:
    """Sincroniza WorkFlow Destino no Select2 (dropdown dependente do cliente)."""
    css = brflow.B_replicacao_workflow_destino_pesquisa
    return driver.execute_script(
        """
        const sel = document.querySelector(arguments[0]);
        const valor = String(arguments[1] || '');
        const hint = String(arguments[2] || '');
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
        const texto = String(opt.text || '').trim();
        Array.from(sel.options || []).forEach((o) => {
            o.selected = String(o.value) === String(opt.value);
        });
        sel.value = opt.value;
        sel.dispatchEvent(new Event('input', { bubbles: true }));
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        if (typeof jQuery !== 'undefined') {
            const $sel = jQuery(sel);
            $sel.val(opt.value).trigger('change').trigger('change.select2');
            const data = { id: opt.value, text: texto };
            try {
                if ($sel.data('select2')) {
                    $sel.trigger({ type: 'select2:select', params: { data } });
                }
            } catch (e) {}
        }
        const container = sel.nextElementSibling;
        const rendered = container ? container.querySelector('.select2-selection__rendered') : null;
        if (rendered) {
            const clear = rendered.querySelector('.select2-selection__clear');
            rendered.textContent = '';
            if (clear) rendered.appendChild(clear);
            rendered.appendChild(document.createTextNode(texto));
            rendered.setAttribute('title', texto);
        }
        const uiTxt = rendered ? String(rendered.textContent || '').trim() : '';
        return {
            ok: String(sel.value) === String(opt.value) && uiTxt.toLowerCase() !== 'selecione',
            value: String(sel.value || ''),
            text: texto,
            ui: uiTxt,
        };
        """,
        css,
        valor_cod,
        texto_hint,
    ) or {}


def clicar_combobox_select2(driver, select_name: str, *, timeout: int = TIMEOUT_DRIVER):
    if select_name == "codClienteDestino":
        xpath = (
            "//select[contains(@class,'clienteDestinoPesquisa')][@name='codClienteDestino']"
            "/following-sibling::span[contains(@class,'select2-container')]"
            "//span[contains(@class,'select2-selection')]"
        )
    elif select_name == "codWorkFlowDestino":
        xpath = (
            "//select[contains(@class,'workflowDestinoPesquisa')][@name='codWorkFlowDestino']"
            "/following-sibling::span[contains(@class,'select2-container')]"
            "//span[contains(@class,'select2-selection')]"
        )
    elem = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
    try:
        driver.execute_script("arguments[0].click();", elem)
    except Exception:
        elem.click()
    time.sleep(0.15)
    return elem


def aguardar_formulario_filtros_replicacao(driver, timeout: int = 30) -> None:
    """Aguarda o formulário de pesquisa (selects Pesquisa) estar no DOM."""
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, brflow.B_replicacao_cliente_destino_pesquisa)
        )
    )
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, brflow.B_replicacao_workflow_destino_pesquisa)
        )
    )


def clicar_opcao_select2(driver, option) -> None:
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


def selecionar_opcao_select2_aberta(
    driver,
    *,
    valor_cod: str,
    texto_opcao: str,
    select_name: str = "codWorkFlowDestino",
    timeout: int = TIMEOUT_DRIVER,
) -> None:
    """Seleciona opção no dropdown Select2 aberto (Enter ou clique do mouse)."""
    valor_cod = str(valor_cod or "").strip()
    texto_opcao = str(texto_opcao or valor_cod).strip()
    termos = termos_busca_select2(valor_cod, texto_opcao)

    search_input = None
    for by_method, seletor in (
        (By.CSS_SELECTOR, ".select2-container--open input.select2-search__field"),
        (By.CSS_SELECTOR, brflow.B_replicacao_select2_search),
        (By.CSS_SELECTOR, "input.select2-search__field"),
    ):
        try:
            search_input = WebDriverWait(driver, 5).until(
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
        busca = termo_digitacao_select2(valor_cod, texto_opcao)
        search_input.send_keys(busca)
        log.info("[FILTROS] Select2 busca digitada: %s", busca)
        time.sleep(0.45)

    def _opcao_visivel():
        seletores = (
            ".select2-container--open .select2-results__option--highlighted",
            ".select2-container--open .select2-results__option",
            ".select2-results__option--highlighted",
            ".select2-results__option",
        )
        vistos = []
        for css in seletores:
            for opt in driver.find_elements(By.CSS_SELECTOR, css):
                if opt in vistos:
                    continue
                vistos.append(opt)
                classe = opt.get_attribute("class") or ""
                try:
                    if not opt.is_displayed():
                        continue
                except StaleElementReferenceException:
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
                    try:
                        if not opt.is_displayed():
                            continue
                    except StaleElementReferenceException:
                        continue
                    if "select2-results__option--disabled" in classe:
                        continue
                    return opt
        return None

    option = WebDriverWait(driver, timeout).until(lambda d: _opcao_visivel())
    log.info(
        "[FILTROS] Select2 opção encontrada: %s",
        (option.text or "").strip()[:80],
    )

    # 1) Enter confirma a opção destacada no filtro (comportamento nativo do Select2)
    if search_input is not None:
        try:
            search_input.send_keys(Keys.ENTER)
            time.sleep(0.25)
            if filtro_select2_confirmado(driver, select_name, valor_cod, texto_opcao):
                log.info("[FILTROS] Select2 confirmado via ENTER")
                return
        except Exception as exc:
            log.debug("[FILTROS] ENTER no Select2 falhou: %s", exc)

    # 2) Clique na opção (reabre dropdown se o ENTER fechou sem selecionar)
    if not driver.find_elements(By.CSS_SELECTOR, ".select2-container--open"):
        clicar_combobox_select2(driver, select_name)
        try:
            search_input = WebDriverWait(driver, 3).until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, ".select2-container--open input.select2-search__field")
                )
            )
            search_input.clear()
            search_input.send_keys(termo_digitacao_select2(valor_cod, texto_opcao))
            time.sleep(0.35)
        except TimeoutException:
            search_input = None
        option = WebDriverWait(driver, timeout).until(lambda d: _opcao_visivel())

    clicar_opcao_select2(driver, option)
    time.sleep(0.25)
    if filtro_select2_confirmado(driver, select_name, valor_cod, texto_opcao):
        log.info("[FILTROS] Select2 confirmado via clique")
        return

    # 3) Último recurso: ENTER novamente no campo de busca aberto
    try:
        campo = driver.find_element(
            By.CSS_SELECTOR, ".select2-container--open input.select2-search__field"
        )
        campo.send_keys(Keys.ENTER)
        time.sleep(0.25)
    except Exception:
        pass


def aguardar_opcoes_workflow_destino(
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
            const sel = document.querySelector(arguments[1]);
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
            brflow.B_replicacao_workflow_destino_pesquisa,
        )

    WebDriverWait(driver, timeout).until(_workflow_pronto)
    info = driver.execute_script(
        """
        const sel = document.querySelector(arguments[0]);
        if (!sel) return {ok: false};
        const opts = Array.from(sel.options || [])
            .filter((o) => String(o.value || '').trim() !== '')
            .map((o) => ({value: String(o.value), text: String(o.text || '').trim()}));
        return {ok: true, total: opts.length, amostra: opts.slice(0, 3)};
        """,
        brflow.B_replicacao_workflow_destino_pesquisa,
    )
    log.info("[FILTROS] WorkFlow Destino carregado: %s", info)


def aplicar_filtro_select2_replicacao(
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

    if aguardar_filtro_select2_estavel(driver, select_name, valor_cod, texto_opcao, tentativas=2):
        log.info("[FILTROS] %s já estava em %s", label, valor_cod)
        return

    js_result = definir_select_filtro_js(driver, select_name, valor_cod, texto_opcao)
    if aguardar_filtro_select2_estavel(driver, select_name, valor_cod, texto_opcao):
        log.info(
            "[FILTROS] %s aplicado via JS | cod=%s | texto_ui=%s",
            label,
            valor_cod,
            ler_texto_select2_filtro_pesquisa(driver, select_name),
        )
        return

    log.warning(
        "[FILTROS] JS não sincronizou %s (%s): %s; tentando UI",
        label,
        select_name,
        js_result,
    )
    clicar_combobox_select2(driver, select_name)
    selecionar_opcao_select2_aberta(
        driver,
        valor_cod=valor_cod,
        texto_opcao=texto_opcao,
        select_name=select_name,
    )

    if not aguardar_filtro_select2_estavel(driver, select_name, valor_cod, texto_opcao):
        nativo = ler_valor_select_filtro_pesquisa(driver, select_name)
        ui = ler_texto_select2_filtro_pesquisa(driver, select_name)
        raise RuntimeError(
            f"{label} não aplicado: esperado={valor_cod}, nativo={nativo or '(vazio)'}, ui={ui or '(vazio)'}"
        )
    log.info(
        "[FILTROS] %s aplicado via UI | cod=%s | texto_ui=%s",
        label,
        valor_cod,
        ler_texto_select2_filtro_pesquisa(driver, select_name),
    )


def abrir_dropdown_workflow_destino(driver, *, timeout: int = TIMEOUT_DRIVER) -> None:
    """Abre o Select2 do WorkFlow Destino (clique ou API select2)."""
    try:
        clicar_combobox_select2(driver, "codWorkFlowDestino", timeout=timeout)
        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".select2-container--open"))
        )
        return
    except TimeoutException:
        log.warning("[FILTROS] Clique no WorkFlow Destino não abriu dropdown; tentando select2('open')")
    driver.execute_script(
        """
        const sel = document.querySelector(arguments[0]);
        if (sel && typeof jQuery !== 'undefined') {
            jQuery(sel).select2('open');
        }
        """,
        brflow.B_replicacao_workflow_destino_pesquisa,
    )
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".select2-container--open"))
    )


def aplicar_filtro_workflow_destino_replicacao(
    driver,
    *,
    valor_cod: str,
    texto_opcao: str,
) -> None:
    """WorkFlow Destino: JS no select de pesquisa; fallback UI com Enter/clique."""
    valor_cod = str(valor_cod or "").strip()
    texto_opcao = str(texto_opcao or "").strip()
    label = "WorkFlow Destino"
    if not valor_cod:
        raise ValueError(f"Valor vazio para filtro {label}")

    if aguardar_filtro_select2_estavel(
        driver, "codWorkFlowDestino", valor_cod, texto_opcao, tentativas=2
    ):
        log.info("[FILTROS] %s já estava em %s", label, valor_cod)
        return

    js_result = definir_workflow_destino_js(driver, valor_cod, texto_opcao)
    if aguardar_filtro_select2_estavel(driver, "codWorkFlowDestino", valor_cod, texto_opcao):
        log.info(
            "[FILTROS] %s aplicado via JS | cod=%s | texto_ui=%s",
            label,
            valor_cod,
            ler_texto_select2_filtro_pesquisa(driver, "codWorkFlowDestino"),
        )
        return

    log.info(
        "[FILTROS] Selecionando %s via UI | cod=%s | texto=%s | js=%s",
        label,
        valor_cod,
        texto_opcao or valor_cod,
        js_result,
    )
    abrir_dropdown_workflow_destino(driver)
    selecionar_opcao_select2_aberta(
        driver,
        valor_cod=valor_cod,
        texto_opcao=texto_opcao,
        select_name="codWorkFlowDestino",
    )

    if not aguardar_filtro_select2_estavel(driver, "codWorkFlowDestino", valor_cod, texto_opcao):
        nativo = ler_valor_select_filtro_pesquisa(driver, "codWorkFlowDestino")
        ui = ler_texto_select2_filtro_pesquisa(driver, "codWorkFlowDestino")
        raise RuntimeError(
            f"{label} não aplicado: esperado={valor_cod}, nativo={nativo or '(vazio)'}, ui={ui or '(vazio)'}"
        )
    log.info(
        "[FILTROS] %s aplicado via UI | cod=%s | texto_ui=%s",
        label,
        valor_cod,
        ler_texto_select2_filtro_pesquisa(driver, "codWorkFlowDestino"),
    )


def limpar_filtro_workflow_destino_pesquisa(driver) -> dict:
    """Remove filtro WorkFlow Destino da pesquisa (busca ampla por cliente)."""
    return driver.execute_script(
        """
        const sel = document.querySelector(arguments[0]);
        if (!sel) return {ok: false, reason: 'select ausente'};
        const opts = Array.from(sel.options || []);
        let empty = opts.find((o) => !String(o.value || '').trim());
        if (!empty && opts.length) {
            empty = opts[0];
        }
        if (empty) {
            sel.value = empty.value;
            Array.from(sel.options || []).forEach((o) => {
                o.selected = String(o.value) === String(empty.value);
            });
        } else {
            sel.selectedIndex = -1;
            sel.value = '';
        }
        sel.dispatchEvent(new Event('input', { bubbles: true }));
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        if (typeof jQuery !== 'undefined') {
            const $sel = jQuery(sel);
            $sel.val(sel.value || null).trigger('change').trigger('change.select2');
            try {
                if ($sel.data('select2')) {
                    $sel.select2('val', sel.value || null);
                }
            } catch (e) {}
        }
        const container = sel.nextElementSibling;
        const rendered = container ? container.querySelector('.select2-selection__rendered') : null;
        const ui = rendered ? String(rendered.textContent || '').trim() : '';
        return {
            ok: true,
            value: String(sel.value || ''),
            ui,
        };
        """,
        brflow.B_replicacao_workflow_destino_pesquisa,
    ) or {}


def workflow_destino_pesquisa_vazio(driver) -> bool:
    ui = ler_texto_select2_filtro_pesquisa(driver, "codWorkFlowDestino")
    nativo = ler_valor_select_filtro_pesquisa(driver, "codWorkFlowDestino")
    if not str(nativo or "").strip():
        return True
    return ui_select2_vazia(ui)


def validar_filtros_replicacao_antes_pesquisar(
    driver,
    settings: Optional[dict] = None,
) -> dict:
    """Garante Cliente/WorkFlow Destino aplicados na UI antes de Pesquisar."""
    settings = settings or {}
    valor_cliente = str(settings.get("replicacao_cliente_cod", "751")).strip() or "751"
    texto_cliente = str(settings.get("replicacao_cliente_destino", "") or "").strip()
    filtrar_destino = filtrar_workflow_destino_habilitado(settings)
    valor_workflow = str(settings.get("replicacao_workflow_cod", "17047")).strip() or "17047"
    texto_workflow = str(settings.get("replicacao_workflow_destino", "") or "").strip()

    def _snapshot():
        return {
            "cliente": ler_valor_select_filtro_pesquisa(driver, "codClienteDestino"),
            "cliente_ui": ler_texto_select2_filtro_pesquisa(driver, "codClienteDestino"),
            "workflow": ler_valor_select_filtro_pesquisa(driver, "codWorkFlowDestino"),
            "workflow_ui": ler_texto_select2_filtro_pesquisa(driver, "codWorkFlowDestino"),
        }

    conferencia = _snapshot()
    log.info("[FILTROS] Conferência pré-Pesquisar: %s", conferencia)

    if not aguardar_filtro_select2_estavel(
        driver, "codClienteDestino", valor_cliente, texto_cliente, tentativas=2
    ):
        conferencia = _snapshot()
        raise RuntimeError(
            f"Cliente Destino não confirmado antes de Pesquisar: esperado={valor_cliente} "
            f"({texto_cliente}), atual={conferencia.get('cliente')}, "
            f"ui={conferencia.get('cliente_ui')}"
        )
    if not filtrar_destino:
        if not workflow_destino_pesquisa_vazio(driver):
            conferencia = _snapshot()
            raise RuntimeError(
                "WorkFlow Destino deveria estar vazio na busca ampla: "
                f"atual={conferencia.get('workflow')}, ui={conferencia.get('workflow_ui')}"
            )
        return _snapshot()
    if not aguardar_filtro_select2_estavel(
        driver, "codWorkFlowDestino", valor_workflow, texto_workflow, tentativas=3
    ):
        conferencia = _snapshot()
        raise RuntimeError(
            f"WorkFlow Destino não confirmado antes de Pesquisar: esperado={valor_workflow} "
            f"({texto_workflow}), atual={conferencia.get('workflow')}, "
            f"ui={conferencia.get('workflow_ui')}"
        )
    return _snapshot()


def preencher_replicacao_filtros(
    driver,
    settings: Optional[dict] = None,
    *,
    set_status: Optional[SetStatusFn] = None,
    take_screenshot: Optional[TakeScreenshotFn] = None,
):
    """Preenche Cliente Destino e, se habilitado, WorkFlow Destino da pesquisa."""
    settings = settings or {}
    if set_status:
        set_status("Preenchendo filtros de replicação")

    log.info("[FILTROS] Iniciando preenchimento de filtros")

    aguardar_formulario_filtros_replicacao(driver)

    valor_cliente = str(settings.get("replicacao_cliente_cod", "751")).strip() or "751"
    texto_cliente = str(settings.get("replicacao_cliente_destino", "") or "").strip()
    filtrar_destino = filtrar_workflow_destino_habilitado(settings)
    valor_workflow = str(settings.get("replicacao_workflow_cod", "17047")).strip() or "17047"
    texto_workflow = str(settings.get("replicacao_workflow_destino", "") or "").strip()

    try:
        aplicar_filtro_select2_replicacao(
            driver,
            select_name="codClienteDestino",
            valor_cod=valor_cliente,
            texto_opcao=texto_cliente,
            label="Cliente Destino",
        )
        if not filtrar_destino:
            limpar_filtro_workflow_destino_pesquisa(driver)
            time.sleep(0.3)
            if not workflow_destino_pesquisa_vazio(driver):
                raise RuntimeError(
                    "WorkFlow Destino não foi limpo para busca ampla: "
                    f"ui={ler_texto_select2_filtro_pesquisa(driver, 'codWorkFlowDestino')}"
                )
            log.info("[FILTROS] Busca ampla: WorkFlow Destino sem filtro (apenas cliente)")
            return

        aguardar_opcoes_workflow_destino(driver, valor_workflow)
        time.sleep(0.3)
        aplicar_filtro_workflow_destino_replicacao(
            driver,
            valor_cod=valor_workflow,
            texto_opcao=texto_workflow,
        )
        conferencia = {
            "cliente": ler_valor_select_filtro_pesquisa(driver, "codClienteDestino"),
            "cliente_ui": ler_texto_select2_filtro_pesquisa(driver, "codClienteDestino"),
            "workflow": ler_valor_select_filtro_pesquisa(driver, "codWorkFlowDestino"),
            "workflow_ui": ler_texto_select2_filtro_pesquisa(driver, "codWorkFlowDestino"),
        }
        log.info("[FILTROS] Conferência final: %s", conferencia)
        if not aguardar_filtro_select2_estavel(
            driver, "codWorkFlowDestino", valor_workflow, texto_workflow
        ):
            conferencia = {
                "cliente": ler_valor_select_filtro_pesquisa(driver, "codClienteDestino"),
                "cliente_ui": ler_texto_select2_filtro_pesquisa(driver, "codClienteDestino"),
                "workflow": ler_valor_select_filtro_pesquisa(driver, "codWorkFlowDestino"),
                "workflow_ui": ler_texto_select2_filtro_pesquisa(driver, "codWorkFlowDestino"),
            }
            raise RuntimeError(
                f"WorkFlow Destino não confirmado na UI: esperado={valor_workflow} "
                f"({texto_workflow}), atual={conferencia.get('workflow')}, "
                f"ui={conferencia.get('workflow_ui')}"
            )
    except Exception as e:
        log.error("[FILTROS] Erro ao preencher filtros de replicação: %s", e)
        if take_screenshot:
            take_screenshot(driver, "log_screenshots", "filtro_replicacao_erro")
        raise
