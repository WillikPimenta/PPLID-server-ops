# -*- coding: utf-8 -*-
"""Validação e correção de WorkFlow Destino / ICM no painel de edição BRFlow."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.bots.replicacao_aud_planning import (
    eh_fila_documentoscopia_31,
    resolver_filtros_brflow_por_fila,
)
from app.bots.replicacao_d1.selenium.constants import TIMEOUT_DRIVER
from app.bots.replicacao_d1.selenium.filtros_pesquisa import clicar_opcao_select2
from app.bots.replicacao_d1.selenium.select2_utils import (
    normalizar_texto_select2_ui,
    termo_digitacao_select2,
    termos_busca_select2,
    texto_select2_bate,
    ui_select2_vazia,
)
from app.config import brflow

log = logging.getLogger("robots.bot_replicacao_aud_d1")

SetStatusFn = Callable[[str], None]

_XPATH_COMBOBOX_WORKFLOW_PAINEL = (
    '//*[@id="layout_layout2_panel_right"]'
    '//select[@name="codWorkFlowDestino"]'
    '/following-sibling::span[contains(@class,"select2-container")]'
    '//span[contains(@class,"select2-selection")]'
)


def ler_workflow_destino_painel_edicao(driver) -> dict:
    """Lê valor nativo e texto Select2 do WorkFlow Destino no painel de edição."""
    result = driver.execute_script(
        """
        const sel = document.querySelector(arguments[0]);
        if (!sel) return {value: '', ui: ''};
        const container = sel.nextElementSibling;
        const rendered = container
            ? container.querySelector('.select2-selection__rendered')
            : null;
        return {
            value: String(sel.value || ''),
            ui: rendered ? String(rendered.textContent || '').trim() : '',
        };
        """,
        brflow.B_replicacao_workflow_destino_edicao,
    ) or {}
    return {
        "value": str(result.get("value") or ""),
        "ui": normalizar_texto_select2_ui(str(result.get("ui") or "")),
    }


def workflow_destino_painel_confirmado(
    driver,
    valor_cod: str,
    texto_opcao: str = "",
) -> bool:
    """Confirma WorkFlow Destino no painel de edição (select nativo + Select2)."""
    valor_cod = str(valor_cod or "").strip()
    atual = ler_workflow_destino_painel_edicao(driver)
    if atual["value"] != valor_cod:
        return False
    ui = atual["ui"]
    if ui_select2_vazia(ui):
        return False
    if texto_opcao and not texto_select2_bate(ui, texto_opcao):
        return False
    return True


def aguardar_workflow_destino_painel_estavel(
    driver,
    valor_cod: str,
    texto_opcao: str = "",
    *,
    tentativas: int = 3,
    intervalo_seg: float = 0.35,
) -> bool:
    """Confirma WorkFlow Destino em leituras consecutivas no painel de edição."""
    ok_seguidas = 0
    for _ in range(max(1, tentativas)):
        if workflow_destino_painel_confirmado(driver, valor_cod, texto_opcao):
            ok_seguidas += 1
            if ok_seguidas >= 2:
                return True
        else:
            ok_seguidas = 0
        time.sleep(intervalo_seg)
    return workflow_destino_painel_confirmado(driver, valor_cod, texto_opcao)


def definir_workflow_destino_painel_js(
    driver,
    valor_cod: str,
    texto_hint: str = "",
) -> dict:
    """Sincroniza WorkFlow Destino no Select2 do painel de edição via JS/jQuery."""
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
        brflow.B_replicacao_workflow_destino_edicao,
        valor_cod,
        texto_hint,
    ) or {}


def clicar_combobox_workflow_destino_painel(driver, *, timeout: int = TIMEOUT_DRIVER):
    """Abre o Select2 do WorkFlow Destino no painel de edição."""
    elem = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.XPATH, _XPATH_COMBOBOX_WORKFLOW_PAINEL))
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
    try:
        driver.execute_script("arguments[0].click();", elem)
    except Exception:
        elem.click()
    time.sleep(0.15)
    return elem


def selecionar_opcao_select2_painel(
    driver,
    *,
    valor_cod: str,
    texto_opcao: str,
    timeout: int = TIMEOUT_DRIVER,
) -> None:
    """Seleciona opção no dropdown Select2 aberto (painel de edição)."""
    valor_cod = str(valor_cod or "").strip()
    texto_opcao = str(texto_opcao or valor_cod).strip()
    termos = termos_busca_select2(valor_cod, texto_opcao)

    search_input = None
    for by_method, seletor in (
        (By.CSS_SELECTOR, ".select2-container--open input.select2-search__field"),
        (By.CSS_SELECTOR, brflow.B_replicacao_select2_search),
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
        search_input.send_keys(termo_digitacao_select2(valor_cod, texto_opcao))
        time.sleep(0.45)

    def _opcao_visivel():
        seletores = (
            ".select2-container--open .select2-results__option--highlighted",
            ".select2-container--open .select2-results__option",
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
        return None

    option = WebDriverWait(driver, timeout).until(lambda d: _opcao_visivel())

    if search_input is not None:
        try:
            search_input.send_keys(Keys.ENTER)
            time.sleep(0.25)
            if workflow_destino_painel_confirmado(driver, valor_cod, texto_opcao):
                return
        except Exception:
            pass

    if not driver.find_elements(By.CSS_SELECTOR, ".select2-container--open"):
        clicar_combobox_workflow_destino_painel(driver)
        option = WebDriverWait(driver, timeout).until(lambda d: _opcao_visivel())

    clicar_opcao_select2(driver, option)
    time.sleep(0.25)


def aplicar_workflow_destino_painel_edicao(
    driver,
    valor_cod: str,
    texto_opcao: str,
) -> None:
    """Aplica WorkFlow Destino no painel de edição (JS primeiro, fallback UI)."""
    valor_cod = str(valor_cod or "").strip()
    texto_opcao = str(texto_opcao or valor_cod).strip()
    label = "WorkFlow Destino (painel edição)"
    if not valor_cod:
        raise ValueError(f"Valor vazio para {label}")

    if aguardar_workflow_destino_painel_estavel(
        driver, valor_cod, texto_opcao, tentativas=2
    ):
        log.info("[PAINEL] %s já estava em %s (%s)", label, valor_cod, texto_opcao)
        return

    js_result = definir_workflow_destino_painel_js(driver, valor_cod, texto_opcao)
    if aguardar_workflow_destino_painel_estavel(driver, valor_cod, texto_opcao):
        log.info(
            "[PAINEL] %s aplicado via JS | cod=%s | ui=%s",
            label,
            valor_cod,
            ler_workflow_destino_painel_edicao(driver).get("ui"),
        )
        return

    log.warning(
        "[PAINEL] JS não sincronizou %s (%s); tentando UI | js=%s",
        label,
        valor_cod,
        js_result,
    )
    clicar_combobox_workflow_destino_painel(driver)
    selecionar_opcao_select2_painel(
        driver,
        valor_cod=valor_cod,
        texto_opcao=texto_opcao,
    )

    if not aguardar_workflow_destino_painel_estavel(driver, valor_cod, texto_opcao):
        atual = ler_workflow_destino_painel_edicao(driver)
        raise RuntimeError(
            f"{label} não aplicado: esperado={valor_cod} ({texto_opcao}), "
            f"atual={atual.get('value') or '(vazio)'}, ui={atual.get('ui') or '(vazio)'}"
        )
    log.info(
        "[PAINEL] %s aplicado via UI | cod=%s | ui=%s",
        label,
        valor_cod,
        ler_workflow_destino_painel_edicao(driver).get("ui"),
    )


def _ler_icm_auditoria_selecionado(driver) -> bool:
    checkbox = driver.find_element(By.CSS_SELECTOR, brflow.B_replicacao_checkbox_icm_auditoria)
    return checkbox.is_selected()


def _set_icm_auditoria_checkbox(
    driver,
    marcado: bool,
    *,
    set_status: Optional[SetStatusFn] = None,
) -> None:
    """Marca ou desmarca replicarIcmAuditoria no painel de edição."""
    if set_status:
        acao = "Marcando" if marcado else "Desmarcando"
        set_status(f"{acao} replicação ICM auditoria")
    checkbox = WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_replicacao_checkbox_icm_auditoria))
    )
    driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", checkbox)
    selecionado = checkbox.is_selected()
    if selecionado == marcado:
        estado = "marcado" if marcado else "desmarcado"
        log.info("[PAINEL] Checkbox replicarIcmAuditoria já estava %s", estado)
        return
    try:
        driver.execute_script("arguments[0].click();", checkbox)
    except Exception:
        checkbox.click()
    WebDriverWait(driver, 5).until(lambda _: checkbox.is_selected() == marcado)
    log.info(
        "[PAINEL] Checkbox replicarIcmAuditoria %s",
        "marcado" if marcado else "desmarcado",
    )


def desmarcar_replicar_icm_auditoria(driver, *, set_status: Optional[SetStatusFn] = None) -> None:
    _set_icm_auditoria_checkbox(driver, False, set_status=set_status)


def marcar_replicar_icm_auditoria_painel(driver, *, set_status: Optional[SetStatusFn] = None) -> None:
    _set_icm_auditoria_checkbox(driver, True, set_status=set_status)


def validar_e_corrigir_config_fila_painel_edicao(
    driver,
    fila: Optional[str],
    settings: Optional[dict] = None,
    *,
    set_status: Optional[SetStatusFn] = None,
) -> None:
    """Garante WorkFlow Destino e ICM corretos no painel de edição conforme a fila."""
    if not fila:
        log.warning("[PAINEL] Fila não informada; pulando validação do painel de edição")
        return

    settings = settings or {}
    if set_status:
        set_status(f"Validando configuração da fila {fila} no painel")

    filtros = resolver_filtros_brflow_por_fila(fila, settings)
    valor_workflow = str(filtros.get("replicacao_workflow_cod", "")).strip()
    texto_workflow = str(filtros.get("replicacao_workflow_destino", "")).strip()

    log.info(
        "[PAINEL] Validando fila=%s | workflow=%s (%s)",
        fila,
        valor_workflow,
        texto_workflow,
    )

    atual = ler_workflow_destino_painel_edicao(driver)
    log.info("[PAINEL] WorkFlow Destino atual: %s", atual)

    aplicar_workflow_destino_painel_edicao(driver, valor_workflow, texto_workflow)

    if eh_fila_documentoscopia_31(fila):
        marcar_replicar_icm_auditoria_painel(driver, set_status=set_status)
    else:
        desmarcar_replicar_icm_auditoria(driver, set_status=set_status)

    log.info("[PAINEL] Configuração da fila %s confirmada no painel de edição", fila)
