"""Interação UI Select2 + Pesquisar na tela Prioridade NH x WF."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger("robots.prioridades_nh")

# XPaths informados pelo usuário (suffixo select2 é dinâmico → usamos contains)
XPATH_NH_SELECT2_SPAN = (
    '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div[3]/span/span[1]/span'
    ' | //*[@id="layout_layout2_panel_main"]//form//span[contains(@id,"select2-codNivelHierarquico")'
    ' and contains(@id,"-container")]'
    ' | //span[contains(@id,"select2-codNivelHierarquico") and contains(@id,"-container")]'
)
XPATH_NH_RESULTS_LI = (
    '//ul[starts-with(@id,"select2-codNivelHierarquico-") and contains(@id,"-results")]'
    '/li[contains(@class,"select2-results__option")]'
)
XPATH_PESQUISAR_BTN = (
    '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div[7]/button'
    ' | //*[@id="layout_layout2_panel_main"]//form//button[contains(@class,"btn-primary")'
    ' and (contains(.,"Pesquisar") or contains(@class,"btn-sutmit-pesquisar")'
    ' or contains(@class,"btn-submit-pesquisar"))]'
)
XPATH_RESULT_TABLE = (
    '//*[@id="layout_layout2_panel_main"]/div[4]/div/div[3]'
    ' | //*[@id="layout_layout2_panel_main"]//table[contains(@class,"table")'
    ' or contains(@class,"listagem")]'
)


def _wait_loading_gone(driver, *, timeout: float = 20) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.ID, "sistema-loading"))
        )
    except Exception:
        pass


def _install_fetch_interceptor(driver) -> None:
    """Captura o JSON de pesquisar-prioridade-nivel-hierarquico no próximo fetch."""
    driver.execute_script(
        """
        if (!window.__pplidPrioridadeHooked) {
          window.__pplidPrioridadeHooked = true;
          window.__pplidPrioridadeJson = null;
          const orig = window.fetch.bind(window);
          window.fetch = async function(input, init) {
            const url = (typeof input === 'string') ? input : (input && input.url) || '';
            const res = await orig(input, init);
            try {
              if (String(url).indexOf('pesquisar-prioridade-nivel-hierarquico') !== -1) {
                const text = await res.clone().text();
                window.__pplidPrioridadeJson = text;
              }
            } catch (e) {}
            return res;
          };
        } else {
          window.__pplidPrioridadeJson = null;
        }
        """
    )


def _read_intercepted_json(driver, *, timeout: float = 20) -> list[dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = driver.execute_script("return window.__pplidPrioridadeJson;")
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                time.sleep(0.3)
                continue
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
            if isinstance(payload, dict) and payload.get("type"):
                # success com data vazio
                return []
        time.sleep(0.25)
    return []


def _open_nh_select2(driver) -> None:
    # Prefer container select2-codNivelHierarquico
    candidates = [
        (By.XPATH, XPATH_NH_SELECT2_SPAN),
        (
            By.XPATH,
            '//span[contains(@id,"select2-codNivelHierarquico") and contains(@id,"-container")]',
        ),
        (
            By.CSS_SELECTOR,
            'span[id*="select2-codNivelHierarquico"][id$="-container"]',
        ),
        (
            By.XPATH,
            '//*[@id="layout_layout2_panel_main"]//form//span[contains(@class,"select2-selection")]',
        ),
    ]
    el = None
    last_err = None
    for by, sel in candidates:
        try:
            el = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((by, sel)))
            if el:
                break
        except Exception as exc:
            last_err = exc
            continue
    if el is None:
        raise RuntimeError(f"Select2 Nível Hierárquico não encontrado ({last_err})")

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.2)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    time.sleep(0.5)


def _type_nh_search(driver, nome: str) -> None:
    search = None
    for by, sel in (
        (By.CSS_SELECTOR, "input.select2-search__field"),
        (By.CSS_SELECTOR, ".select2-container--open input.select2-search__field"),
        (By.XPATH, "//span[contains(@class,'select2-search')]//input"),
        (By.XPATH, "//input[contains(@class,'select2-search__field')]"),
    ):
        try:
            search = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((by, sel)))
            if search:
                break
        except Exception:
            continue
    if search is None:
        raise RuntimeError("Campo de busca Select2 do NH não apareceu")

    search.clear()
    search.send_keys(Keys.CONTROL, "a")
    search.send_keys(Keys.BACKSPACE)
    search.send_keys(nome)
    time.sleep(0.8)


def _click_nh_result(driver, nome: str) -> None:
    """Clica no li do resultado Select2 (id dinâmico select2-codNivelHierarquico-XX-results)."""
    WebDriverWait(driver, 12).until(
        EC.presence_of_element_located((By.XPATH, XPATH_NH_RESULTS_LI))
    )
    options = driver.find_elements(By.XPATH, XPATH_NH_RESULTS_LI)
    if not options:
        options = driver.find_elements(
            By.CSS_SELECTOR,
            "ul.select2-results__options li.select2-results__option",
        )
    if not options:
        raise RuntimeError(f"Nenhum resultado Select2 para NH '{nome}'")

    target = None
    nome_l = nome.casefold().strip()
    for opt in options:
        txt = (opt.text or "").strip()
        if not txt or "carregando" in txt.casefold() or "nenhum" in txt.casefold():
            continue
        if nome_l in txt.casefold() or txt.casefold() in nome_l:
            target = opt
            break
    if target is None:
        # primeiro resultado útil
        for opt in options:
            txt = (opt.text or "").strip()
            if txt and "carregando" not in txt.casefold():
                target = opt
                break
    if target is None:
        raise RuntimeError(f"Sem opção selecionável no Select2 para '{nome}'")

    log.info("Select2 NH: escolhendo '%s'", (target.text or "")[:80])
    try:
        target.click()
    except Exception:
        driver.execute_script("arguments[0].click();", target)
    time.sleep(0.4)


def selecionar_nh_na_ui(driver, nome: str) -> None:
    """Preenche o Select2 de Nível Hierárquico com o nome do NH e escolhe o resultado."""
    _open_nh_select2(driver)
    _type_nh_search(driver, nome)
    _click_nh_result(driver, nome)
    _wait_loading_gone(driver, timeout=10)


def clicar_pesquisar(driver) -> None:
    btn = None
    for by, sel in (
        (By.XPATH, XPATH_PESQUISAR_BTN),
        (
            By.XPATH,
            '//*[@id="layout_layout2_panel_main"]//button[contains(.,"Pesquisar")]',
        ),
        (By.CSS_SELECTOR, "button.btn-primary.btn-sutmit-pesquisar"),
        (By.CSS_SELECTOR, "button.btn-primary"),
    ):
        try:
            els = driver.find_elements(by, sel)
            for el in els:
                label = (el.text or "").strip().casefold()
                if "pesquisar" in label or not label:
                    btn = el
                    break
            if btn:
                break
        except Exception:
            continue
    if btn is None:
        raise RuntimeError("Botão Pesquisar não encontrado")
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    time.sleep(0.2)
    try:
        btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", btn)
    _wait_loading_gone(driver, timeout=30)


def _selected_cod_nivel(driver) -> int | None:
    try:
        val = driver.execute_script(
            """
            const sel = document.querySelector('select[name="codNivelHierarquico"], #codNivelHierarquico');
            if (!sel) return null;
            return sel.value || null;
            """
        )
        if val in (None, "", "0"):
            return None
        return int(val)
    except Exception:
        return None


def pesquisar_nh_via_ui(driver, nome: str) -> list[dict[str, Any]]:
    """
    Fluxo visual:
      1) Select2 NH → digitar → clicar li do resultado
      2) Clicar Pesquisar
      3) Ler JSON interceptado (tabela também aparece na UI)
    """
    from app.bots.prioridades_nh.brflow_fetch import pesquisar_prioridade

    _install_fetch_interceptor(driver)
    selecionar_nh_na_ui(driver, nome)
    clicar_pesquisar(driver)

    # Aguarda tabela / loading
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, XPATH_RESULT_TABLE))
        )
    except Exception:
        pass
    time.sleep(0.5)

    rows = _read_intercepted_json(driver, timeout=15)
    if rows:
        return rows

    # Fallback: POST direto com o código selecionado no select
    cod = _selected_cod_nivel(driver)
    if cod is None:
        raise RuntimeError(
            f"Pesquisa UI de '{nome}' não retornou JSON e o select NH ficou vazio"
        )
    log.warning("Interceptor vazio — fallback fetch API cod=%s", cod)
    return pesquisar_prioridade(driver, cod)
