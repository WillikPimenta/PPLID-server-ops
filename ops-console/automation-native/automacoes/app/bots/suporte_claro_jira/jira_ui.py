# -*- coding: utf-8 -*-
"""Automação Selenium: Okta → Jira → Criar Item."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from urllib.parse import urlparse

import pyperclip

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from app.config import okta
from app.infrastructure.selenium_helpers import (
    create_driver,
    login_okta_resiliente,
    take_error_screenshot,
    wait_for_element,
)

from .profiles import JiraCreateProfile, get_profile
from .progress import report_progress, step_label

LOG = logging.getLogger(__name__)

JIRA_SEARCH_TERMS = ("Agile Jira", "jira", "Jira")
JIRA_ROBOT_NAME = "suporte_claro_jira"
CREATE_ISSUE_PATH = "/secure/CreateIssue!default.jspa"

_CREATE_FORM_LOCATORS = (
    (By.ID, "summary"),
    (By.ID, "summary-field"),
    (By.CSS_SELECTOR, "form#issue-create #summary"),
)

_CREATE_ENTRY_LOCATORS = (
    (By.ID, "project-field"),
    (By.CSS_SELECTOR, "form#issue-create"),
    (By.CSS_SELECTOR, "#create-issue-dialog"),
    (By.XPATH, "//h1[contains(normalize-space(),'Criar Item')]"),
    (By.XPATH, "//h2[contains(normalize-space(),'Criar Item')]"),
)

_WIZARD_NEXT_SELECTORS = (
    (By.ID, "issue-create-next"),
    (By.CSS_SELECTOR, "#create-issue-dialog .aui-button-primary"),
    (By.XPATH, "//button[contains(normalize-space(),'Próximo')]"),
    (By.XPATH, "//input[@type='submit' and (@value='Próximo' or @value='Next')]"),
    (By.XPATH, "//a[contains(@class,'aui-button') and contains(normalize-space(),'Próximo')]"),
)


def _log_step(step: str, detail: str = "", *, level: int = logging.INFO) -> None:
    msg = f"[jira-ui] step={step}"
    if detail:
        msg = f"{msg} | {detail}"
    LOG.log(level, msg)


def _error_box(title: str, message: str) -> None:
    _message_box(title, message)


def _keep_browser_open(seconds: int, *, reason: str) -> None:
    if seconds <= 0:
        return
    report_progress("waiting_user", f"{reason} Chrome aberto por {seconds}s para inspeção.")
    time.sleep(seconds)


def _driver_context(driver) -> str:
    try:
        url = driver.current_url or ""
        handles = len(driver.window_handles)
        title = driver.title or ""
        return f"url={url} windows={handles} title={title[:80]}"
    except Exception as exc:
        return f"context_unavailable={exc}"


def _message_box(title: str, message: str) -> None:
    if sys.platform != "win32":
        print(f"\n[{title}] {message}\n")
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
    except Exception:
        print(f"\n[{title}] {message}\n")


def _switch_to_newest_window(driver) -> None:
    handles = driver.window_handles
    if len(handles) > 1:
        driver.switch_to.window(handles[-1])


JIRA_HOST_HINTS = ("jira", "atlassian", "agile.experian.com")


def _is_jira_url(url: str) -> bool:
    lower = (url or "").lower()
    if any(hint in lower for hint in JIRA_HOST_HINTS):
        return True
    # Jira Server Experian: agile.experian.com/secure/Dashboard.jspa (sem "jira" na URL)
    if "experian.com" in lower and "/secure/" in lower:
        return True
    return False


def _is_jira_page(driver) -> bool:
    try:
        if _is_jira_url(driver.current_url or ""):
            return True
        title = (driver.title or "").lower()
        return "jira" in title or ("agile" in title and "pplid" in title)
    except Exception:
        return False


def _focus_jira_window_if_open(driver) -> bool:
    """Se o Jira já estiver aberto em alguma aba, foca nela."""
    for handle in driver.window_handles:
        try:
            driver.switch_to.window(handle)
            if _is_jira_page(driver):
                _log_step("focus_jira", _driver_context(driver))
                return True
        except Exception:
            continue
    return False


def _ensure_jira_window(driver, *, timeout: int = 30) -> None:
    """Garante foco na aba que contém o Jira."""
    if _focus_jira_window_if_open(driver):
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _focus_jira_window_if_open(driver):
            return
        time.sleep(0.5)
    _switch_to_newest_window(driver)
    _log_step("ensure_jira_window", f"fallback newest | {_driver_context(driver)}", level=logging.WARNING)


def _jira_create_issue_url(driver) -> str | None:
    if not _is_jira_page(driver):
        return None
    url = driver.current_url or ""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{CREATE_ISSUE_PATH}"


def _is_full_create_form(driver) -> bool:
    for by, value in _CREATE_FORM_LOCATORS:
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                return True
        except Exception:
            continue
    return False


def _is_create_wizard(driver) -> bool:
    """Assistente inicial: Projeto + Tipo de Item + botão Próximo (sem resumo)."""
    if _is_full_create_form(driver):
        return False
    has_project = False
    for by, value in ((By.ID, "project-field"), (By.XPATH, "//label[contains(normalize-space(),'Projeto')]")):
        try:
            if driver.find_elements(by, value):
                has_project = True
                break
        except Exception:
            continue
    if not has_project:
        return False
    for by, value in _WIZARD_NEXT_SELECTORS:
        try:
            for el in driver.find_elements(by, value):
                if el.is_displayed():
                    return True
        except Exception:
            continue
    return False


def _wait_for_create_entry(driver, *, timeout: int = 25) -> bool:
    """Aguarda tela Criar Item (assistente ou formulário completo)."""
    wait = WebDriverWait(driver, timeout)
    try:
        wait.until(
            EC.any_of(
                *[
                    EC.presence_of_element_located(loc)
                    for loc in _CREATE_ENTRY_LOCATORS + _CREATE_FORM_LOCATORS
                ]
            )
        )
        time.sleep(0.6)
        return True
    except TimeoutException:
        return False


def _wait_for_create_form(driver, *, timeout: int = 25) -> bool:
    """Aguarda formulário completo (campo Resumo visível)."""
    if _is_full_create_form(driver):
        return True
    wait = WebDriverWait(driver, timeout)
    try:
        wait.until(lambda d: _is_full_create_form(d))
        time.sleep(0.35)
        return True
    except TimeoutException:
        return False


def wait_for_jira_ready(driver, *, timeout: int = 35) -> None:
    """Aguarda Jira carregar (URL + barra com botão Criar ou nav)."""
    report_progress("wait_jira_ready", step_label("wait_jira_ready"))
    _ensure_jira_window(driver, timeout=min(timeout, 20))
    wait = WebDriverWait(driver, timeout)
    try:
        wait.until(lambda d: _is_jira_page(d))
    except TimeoutException as exc:
        raise RuntimeError(
            f"Jira não carregou a tempo. {_driver_context(driver)}"
        ) from exc

    try:
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.ID, "create_link")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "nav.aui-header")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "#header")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".aui-header")),
            )
        )
    except TimeoutException:
        _log_step("wait_jira_ready", f"nav/create_link ausente | {_driver_context(driver)}", level=logging.WARNING)

    time.sleep(1.2)
    _log_step("wait_jira_ready", _driver_context(driver))


def open_jira_from_okta(driver, *, timeout: int = 45) -> None:
    """Login já feito — abre app Jira pelo launcher Okta."""
    _log_step("open_jira_from_okta", "buscando app no launcher Okta")

    if _focus_jira_window_if_open(driver):
        report_progress("open_jira", "Jira já estava aberto.", ok=True)
        return

    wait = WebDriverWait(driver, timeout)
    last_error: Exception | None = None

    for term in JIRA_SEARCH_TERMS:
        if _focus_jira_window_if_open(driver):
            report_progress("open_jira", f"Jira detectado (termo {term}).", ok=True)
            return

        try:
            # Só usa busca Okta se ainda estiver no dashboard Okta
            if _is_jira_page(driver):
                report_progress("open_jira", "Jira aberto.", ok=True)
                return

            wait.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))
            search = driver.find_element(By.ID, okta.O_pesquisar)
            search.click()
            search.clear()
            search.send_keys(term)
            time.sleep(0.5)
            search.send_keys(Keys.ENTER)
            time.sleep(5)
            _switch_to_newest_window(driver)

            if _focus_jira_window_if_open(driver):
                _log_step("open_jira_from_okta", f"ok term={term} | {_driver_context(driver)}")
                report_progress("open_jira", f"Jira aberto via Okta ({term}).", ok=True)
                return
        except Exception as exc:
            last_error = exc
            _log_step("open_jira_from_okta", f"falha term={term}: {exc}", level=logging.WARNING)
            if _focus_jira_window_if_open(driver):
                report_progress("open_jira", "Jira detectado após erro na busca Okta.", ok=True)
                return
            continue

    if _focus_jira_window_if_open(driver):
        report_progress("open_jira", "Jira detectado em aba aberta.", ok=True)
        return

    raise RuntimeError(
        "Não foi possível abrir o Jira pelo Okta. "
        f"Tente abrir manualmente. Detalhe: {last_error} | {_driver_context(driver)}"
    )


def _open_create_via_direct_url(driver, *, timeout: int = 30) -> bool:
    report_progress("create_direct_url", step_label("create_direct_url"))
    create_url = _jira_create_issue_url(driver)
    if not create_url:
        _log_step("create_direct_url", "URL Jira indisponível", level=logging.WARNING)
        return False
    _log_step("create_direct_url", f"navegando {create_url}")
    driver.get(create_url)
    if _wait_for_create_entry(driver, timeout=timeout):
        _log_step("create_direct_url", "tela Criar Item detectada")
        return True
    _log_step("create_direct_url", f"formulário não apareceu | {_driver_context(driver)}", level=logging.WARNING)
    return False


def _open_create_via_click(driver, *, timeout_per_selector: int = 10) -> bool:
    report_progress("create_click", step_label("create_click"))
    selectors = [
        (By.ID, "create_link"),
        (By.CSS_SELECTOR, "a#create_link.create-issue"),
        (By.CSS_SELECTOR, "a#create_link"),
        (By.XPATH, "//a[@id='create_link' and contains(@href,'CreateIssue')]"),
        (By.CSS_SELECTOR, "[data-testid='create-issue-button']"),
        (By.XPATH, "//a[contains(@href,'CreateIssue') and contains(@class,'create-issue')]"),
        (By.XPATH, "//a[contains(normalize-space(),'Criar') and contains(@class,'create-issue')]"),
        (By.XPATH, "//button[contains(normalize-space(),'Criar')]"),
    ]
    _log_step("create_click", _driver_context(driver))
    for by, value in selectors:
        label = f"{by}={value}"
        try:
            wait = WebDriverWait(driver, timeout_per_selector)
            el = wait.until(EC.element_to_be_clickable((by, value)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.4)
            el.click()
            _log_step("create_click", f"clicou {label}")
            time.sleep(3)
            if _wait_for_create_entry(driver, timeout=20):
                return True
            _log_step("create_click", f"clique em {label} sem abrir formulário", level=logging.WARNING)
        except TimeoutException:
            _log_step("create_click", f"timeout {label}", level=logging.DEBUG)
            continue
        except Exception as exc:
            _log_step("create_click", f"erro {label}: {exc}", level=logging.DEBUG)
            continue
    return False


def _open_create_via_shortcut(driver) -> bool:
    """Atalho global do Jira Server: tecla 'c'."""
    report_progress("create_shortcut", step_label("create_shortcut"))
    _log_step("create_shortcut", "tentando atalho 'c'")
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.click()
        time.sleep(0.3)
        ActionChains(driver).send_keys("c").perform()
        time.sleep(3)
        if _wait_for_create_entry(driver, timeout=15):
            _log_step("create_shortcut", "tela Criar Item aberta via atalho")
            return True
    except Exception as exc:
        _log_step("create_shortcut", f"falhou: {exc}", level=logging.WARNING)
    return False


def _select_wizard_issue_type(driver, profile: JiraCreateProfile, *, timeout: int = 8) -> None:
    issue_type = profile.issue_type
    if _wizard_issue_type_ready(driver, profile):
        _log_step("create_wizard", f"tipo já selecionado: {issue_type}")
        return

    try:
        if not _fill_single_select_field_if_needed(
            driver,
            "issuetype-field",
            issue_type,
            issue_type,
            timeout=min(timeout, 6),
        ) and _wizard_issue_type_ready(driver, profile):
            return
    except Exception as exc:
        _log_step("create_wizard", f"issuetype-field: {exc}", level=logging.DEBUG)

    xpath_variants = [
        f"//li[contains(@class,'issuetype')]//*[contains(normalize-space(),'{issue_type}')]",
        f"//*[@data-issue-type-name and contains(normalize-space(),'{issue_type}')]",
        f"//button[contains(normalize-space(),'{issue_type}')]",
        f"//label[contains(normalize-space(),'{issue_type}')]",
        f"//span[contains(normalize-space(),'{issue_type}')]/ancestor::li[1]",
    ]
    for xp in xpath_variants:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                if el.is_displayed() and el.is_enabled():
                    el.click()
                    time.sleep(0.15)
                    if _wizard_issue_type_ready(driver, profile):
                        return
        except Exception:
            continue

    deadline = time.time() + min(timeout, 6)
    while time.time() < deadline:
        if _wizard_issue_type_ready(driver, profile):
            return
        time.sleep(0.15)

    try:
        label = driver.find_element(
            By.XPATH,
            "//label[contains(normalize-space(),'Tipo de Item') or contains(normalize-space(),'Issue Type')]",
        )
        field_id = label.get_attribute("for")
        if field_id:
            _fill_single_select_field(
                driver, field_id, issue_type, issue_type, timeout=min(timeout, 6), fast=True
            )
            return
    except Exception as exc:
        _log_step("create_wizard", f"tipo via label: {exc}", level=logging.DEBUG)

    raise RuntimeError(f"Não foi possível selecionar tipo de item «{issue_type}» no assistente Jira.")


def _click_wizard_next(driver, *, timeout: int = 15) -> None:
    last_error: Exception | None = None
    for by, value in _WIZARD_NEXT_SELECTORS:
        label = f"{by}={value}"
        try:
            wait = WebDriverWait(driver, min(timeout, 10))
            el = wait.until(EC.element_to_be_clickable((by, value)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.3)
            el.click()
            _log_step("create_wizard", f"clicou Próximo ({label})")
            time.sleep(0.35)
            return
        except TimeoutException:
            continue
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(
        f"Botão «Próximo» não encontrado no assistente Jira. Detalhe: {last_error} | {_driver_context(driver)}"
    )


def _advance_past_create_wizard(driver, profile: JiraCreateProfile, *, timeout: int = 45) -> None:
    """Assistente Jira 10: Projeto → Tipo de Item → Próximo. Ignora se já estiver no form completo."""
    if _is_full_create_form(driver):
        _log_step("create_wizard", "formulário completo já aberto")
        return

    if not _is_create_wizard(driver):
        if _wait_for_create_entry(driver, timeout=min(timeout, 12)):
            if _is_full_create_form(driver):
                return
        if not _is_create_wizard(driver):
            _log_step("create_wizard", "assistente não detectado — seguindo", level=logging.DEBUG)
            return

    report_progress(
        "create_wizard",
        f"Assistente: projeto {profile.project_search.upper()} → {profile.issue_type}…",
    )
    _log_step("create_wizard", _driver_context(driver))

    if _wizard_ready_for_next(driver, profile):
        _log_step("create_wizard", "projeto e tipo já corretos — indo para Próximo")
        _click_wizard_next(driver, timeout=min(timeout, 10))
        if _wait_for_create_form(driver, timeout=min(timeout, 20)):
            report_progress("create_wizard", "Formulário completo aberto.", ok=True)
            return
        raise RuntimeError(
            "Assistente Jira não avançou para o formulário completo. "
            f"{_driver_context(driver)}"
        )

    if not _wizard_project_ready(driver, profile):
        _fill_single_select_field_if_needed(
            driver,
            "project-field",
            profile.project_search,
            profile.project_option,
            timeout=min(timeout, 8),
            extra_match=("pplid", profile.project_option.split()[0].lower()),
        )

    if not _wizard_issue_type_ready(driver, profile):
        _select_wizard_issue_type(driver, profile, timeout=min(timeout, 8))

    project_val = _get_field_display_value(driver, "project-field")
    type_val = _get_field_display_value(driver, "issuetype-field")
    _log_step(
        "create_wizard",
        f"assistente pronto: projeto={project_val[:60]!r} tipo={type_val[:40]!r}",
    )
    if not _field_text_matches(project_val, profile.project_option, profile.project_search, "pplid"):
        _log_step("create_wizard", "projeto pode estar incorreto — tentando Próximo mesmo assim", level=logging.WARNING)
    if not _field_text_matches(type_val, profile.issue_type):
        _log_step("create_wizard", "tipo pode estar incorreto — tentando Próximo mesmo assim", level=logging.WARNING)

    _click_wizard_next(driver, timeout=min(timeout, 15))

    if not _wait_for_create_form(driver, timeout=timeout):
        raise RuntimeError(
            "Assistente Jira não avançou para o formulário completo. "
            f"{_driver_context(driver)}"
        )
    report_progress("create_wizard", "Formulário completo aberto.", ok=True)


def open_create_issue_form(driver, profile: JiraCreateProfile, *, timeout: int = 45) -> None:
    """
    Abre o formulário Criar Item: URL direta → clique #create_link → atalho c.
    Se abrir o assistente (Projeto/Tipo/Próximo), avança até o formulário completo.
    """
    wait_for_jira_ready(driver, timeout=timeout)

    opened = False
    if _open_create_via_direct_url(driver, timeout=min(timeout, 30)):
        opened = True
    else:
        _ensure_jira_window(driver)
        wait_for_jira_ready(driver, timeout=20)
        if _open_create_via_click(driver):
            opened = True
        elif _open_create_via_shortcut(driver):
            opened = True

    if not opened:
        screenshot = take_error_screenshot(driver, "create_issue_not_found", JIRA_ROBOT_NAME)
        shot_hint = f" Screenshot: {screenshot}" if screenshot else ""
        raise RuntimeError(
            "Não foi possível abrir 'Criar Item' no Jira "
            f"(URL direta, botão #create_link e atalho 'c' falharam). "
            f"{_driver_context(driver)}.{shot_hint}"
        )

    _advance_past_create_wizard(driver, profile, timeout=timeout)
    if not _wait_for_create_form(driver, timeout=timeout):
        raise RuntimeError(
            "Formulário Criar Item não carregou após o assistente. "
            f"{_driver_context(driver)}"
        )


def _reopen_create_form(driver, profile: JiraCreateProfile, *, timeout: int = 30) -> None:
    """Reabre Criar Item após um chamado criado (formalização em lote)."""
    _ensure_jira_window(driver, timeout=min(timeout, 15))
    time.sleep(0.25)
    opened = False
    if _open_create_via_direct_url(driver, timeout=min(timeout, 20)):
        opened = True
    elif _open_create_via_shortcut(driver):
        opened = True
    elif _open_create_via_click(driver, timeout_per_selector=5):
        opened = True
    if not opened:
        raise RuntimeError(
            "Não foi possível reabrir Criar Item no Jira. "
            f"{_driver_context(driver)}"
        )
    _advance_past_create_wizard(driver, profile, timeout=timeout)
    if not _wait_for_create_form(driver, timeout=timeout):
        raise RuntimeError(
            "Formulário Criar Item não carregou ao reabrir. "
            f"{_driver_context(driver)}"
        )


def _create_single_issue_in_session(
    driver,
    profile: JiraCreateProfile,
    summary: str,
    description: str,
    *,
    first_in_batch: bool = False,
    timeout: int = 45,
) -> str | None:
    if first_in_batch:
        open_create_issue_form(driver, profile, timeout=timeout)
    else:
        _reopen_create_form(driver, profile, timeout=timeout)
    fill_create_issue_form(driver, profile, summary, description)
    return submit_create_issue_form(driver, category=profile.category, timeout=timeout)


def _click_select_option(driver, option_text: str, *, timeout: int = 4) -> bool:
    """Clica opção em dropdown AUI/select2."""
    if not option_text:
        return False
    needles = [option_text]
    if any(ch in option_text for ch in "çÇãÃ"):
        needles.append(
            option_text.replace("ç", "c").replace("Ç", "C").replace("ã", "a").replace("Ã", "A")
        )
    xpath_variants: list[str] = []
    for needle in needles:
        xpath_variants.extend(
            [
                f"//li[contains(@class,'aui-list-item')]//*[contains(normalize-space(),'{needle}')]",
                f"//div[contains(@class,'aui-list')]//*[contains(normalize-space(),'{needle}')]",
                f"//*[@role='option' and contains(normalize-space(),'{needle}')]",
                f"//a[contains(normalize-space(),'{needle}')]",
            ]
        )
    for xp in xpath_variants:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                if el.is_displayed() and el.is_enabled():
                    el.click()
                    time.sleep(0.12)
                    return True
        except Exception:
            continue
    wait = WebDriverWait(driver, timeout)
    for xp in xpath_variants:
        try:
            el = wait.until(EC.element_to_be_clickable((By.XPATH, xp)))
            el.click()
            time.sleep(0.12)
            return True
        except TimeoutException:
            continue
    return False


def _category_select_is_filled(driver, category: str) -> bool:
    """True se algum select de Categoria já tem a opção esperada."""
    if not category:
        return True
    for label_text in ("Categoria", "Category"):
        label_xpaths = (
            f"//label[contains(normalize-space(),'{label_text}')]",
            f"//*[contains(@class,'field-label') and contains(normalize-space(),'{label_text}')]",
        )
        for xp in label_xpaths:
            for label in driver.find_elements(By.XPATH, xp):
                if not label.is_displayed():
                    continue
                field_id = (label.get_attribute("for") or "").strip()
                if field_id:
                    try:
                        el = driver.find_element(By.ID, field_id)
                        if el.tag_name.lower() == "select" and _option_text_matches(
                            category, _native_select_current_text(el)
                        ):
                            return True
                    except Exception:
                        pass
                try:
                    container = label.find_element(
                        By.XPATH,
                        "./ancestor::*[contains(@class,'field-group')][1]",
                    )
                    for sel in container.find_elements(By.TAG_NAME, "select"):
                        if _option_text_matches(category, _native_select_current_text(sel)):
                            return True
                except Exception:
                    continue
    for sel in driver.find_elements(By.TAG_NAME, "select"):
        try:
            if not sel.is_displayed():
                continue
            sel_id = (sel.get_attribute("id") or "").lower()
            sel_name = (sel.get_attribute("name") or "").lower()
            if "categ" not in sel_id and "categ" not in sel_name:
                continue
            if _option_text_matches(category, _native_select_current_text(sel)):
                return True
        except Exception:
            continue
    return False


def _get_field_display_value(driver, field_id: str) -> str:
    """Lê valor visível de campo AUI (single ou multi-select)."""
    val = _get_single_select_display_value(driver, field_id)
    if val:
        return val
    try:
        inp = driver.find_element(By.ID, field_id)
        try:
            container = inp.find_element(
                By.XPATH,
                "./ancestor::*[contains(@class,'field-group') or contains(@class,'group')][1]",
            )
            text = (container.text or "").strip()
            if text:
                return text
        except Exception:
            pass
        for attr in ("data-value", "aria-label"):
            v = (inp.get_attribute(attr) or "").strip()
            if v:
                return v
    except Exception:
        pass
    return ""


def _field_already_selected(driver, field_id: str, *hints: str) -> bool:
    current = _get_field_display_value(driver, field_id)
    return bool(current) and _field_text_matches(current, *hints)


def _wizard_project_ready(driver, profile: JiraCreateProfile) -> bool:
    return _field_already_selected(
        driver,
        "project-field",
        profile.project_option,
        profile.project_search,
        "pplid",
    )


def _wizard_issue_type_ready(driver, profile: JiraCreateProfile) -> bool:
    if _field_already_selected(driver, "issuetype-field", profile.issue_type):
        return True
    try:
        for el in driver.find_elements(
            By.CSS_SELECTOR,
            "li.issuetype.selected, .issuetype-field-option.selected, "
            "[data-issue-type-name].selected, .issue-type-option.selected",
        ):
            if el.is_displayed() and _field_text_matches(el.text, profile.issue_type):
                return True
    except Exception:
        pass
    return False


def _wizard_ready_for_next(driver, profile: JiraCreateProfile) -> bool:
    return _wizard_project_ready(driver, profile) and _wizard_issue_type_ready(driver, profile)


def _get_single_select_display_value(driver, field_id: str) -> str:
    """Lê texto visível de um campo single-select AUI do Jira."""
    parts: list[str] = []
    try:
        inp = driver.find_element(By.ID, field_id)
        for attr in ("value", "title"):
            val = (inp.get_attribute(attr) or "").strip()
            if val:
                parts.append(val)
        text = (inp.text or "").strip()
        if text:
            parts.append(text)
        try:
            parent = inp.find_element(By.XPATH, "./ancestor::*[contains(@class,'single-select')][1]")
            parent_text = (parent.text or "").strip()
            if parent_text:
                parts.append(parent_text)
        except Exception:
            pass
    except Exception:
        pass
    if not parts:
        return ""
    return max(parts, key=len)


def _field_text_matches(current: str, *needles: str) -> bool:
    lower = (current or "").lower().replace("\n", " ")
    for needle in needles:
        n = (needle or "").lower().strip()
        if n and n in lower:
            return True
    return False


_NATIVE_SELECT_EMPTY = frozenset({"", "none", "nenhum", "-1", "null"})


def _option_text_matches(option_text: str, candidate: str) -> bool:
    a = (option_text or "").strip().lower()
    b = (candidate or "").strip().lower()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    a_ascii = a.replace("ç", "c").replace("ã", "a")
    b_ascii = b.replace("ç", "c").replace("ã", "a")
    return a_ascii in b_ascii or b_ascii in a_ascii


def _native_select_current_text(select_el) -> str:
    try:
        sel = Select(select_el)
        return (sel.first_selected_option.text or "").strip()
    except Exception:
        return ""


def _native_select_is_empty(select_el) -> bool:
    current = _native_select_current_text(select_el).lower()
    if current in _NATIVE_SELECT_EMPTY:
        return True
    return current.startswith("nenhum") or current.startswith("none")


def _fill_native_select_element(driver, select_el, option_text: str) -> bool:
    """Preenche <select> nativo (clique + opção), sem digitar/pesquisar."""
    if not option_text:
        return False
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", select_el)
        time.sleep(0.1)
        if not _native_select_is_empty(select_el) and _option_text_matches(
            option_text, _native_select_current_text(select_el)
        ):
            return True
        sel = Select(select_el)
        for opt in sel.options:
            label = (opt.text or "").strip()
            val = (opt.get_attribute("value") or "").strip().lower()
            if not label or label.lower() in _NATIVE_SELECT_EMPTY or val in _NATIVE_SELECT_EMPTY:
                continue
            if label.lower().startswith("nenhum") or label.lower().startswith("none"):
                continue
            if _option_text_matches(option_text, label):
                if not opt.is_selected():
                    sel.select_by_visible_text(label)
                    driver.execute_script(
                        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                        select_el,
                    )
                    time.sleep(0.15)
                return True
        for candidate in (option_text, option_text.replace("ç", "c").replace("ã", "a")):
            try:
                sel.select_by_visible_text(candidate)
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                    select_el,
                )
                time.sleep(0.15)
                return True
            except Exception:
                continue
    except Exception as exc:
        _log_step("fill_native_select", str(exc), level=logging.DEBUG)
    return False


def _fill_native_select_by_label(driver, label_substring: str, option_text: str) -> bool:
    """Localiza <select> pelo label (ex.: Categoria → Informação)."""
    label_xpaths = (
        f"//label[contains(normalize-space(),'{label_substring}')]",
        f"//*[contains(@class,'field-label') and contains(normalize-space(),'{label_substring}')]",
    )
    for xp in label_xpaths:
        for label in driver.find_elements(By.XPATH, xp):
            if not label.is_displayed():
                continue
            field_id = (label.get_attribute("for") or "").strip()
            if field_id:
                try:
                    el = driver.find_element(By.ID, field_id)
                    if el.tag_name.lower() == "select" and _fill_native_select_element(driver, el, option_text):
                        return True
                except Exception:
                    pass
            try:
                sel = label.find_element(By.XPATH, "./following::select[1]")
                if _fill_native_select_element(driver, sel, option_text):
                    return True
            except Exception:
                pass
            try:
                container = label.find_element(
                    By.XPATH,
                    "./ancestor::*[contains(@class,'field-group')][1]",
                )
                for sel in container.find_elements(By.TAG_NAME, "select"):
                    if _fill_native_select_element(driver, sel, option_text):
                        return True
            except Exception:
                continue
    try:
        sel = driver.find_element(
            By.XPATH,
            f"//*[contains(@class,'field-group')]"
            f"[.//*[contains(normalize-space(),'{label_substring}')]]//select",
        )
        return _fill_native_select_element(driver, sel, option_text)
    except Exception:
        return False


def _fill_single_select_field(
    driver,
    field_id: str,
    search_text: str,
    option_text: str,
    *,
    timeout: int = 12,
    fast: bool = False,
) -> None:
    wait = WebDriverWait(driver, timeout)
    inp = wait.until(EC.element_to_be_clickable((By.ID, field_id)))
    inp.click()
    pause = 0.08 if fast else 0.15
    type_pause = 0.22 if fast else 0.4
    time.sleep(pause)
    try:
        inp.send_keys(Keys.CONTROL, "a")
        inp.send_keys(Keys.DELETE)
    except Exception:
        pass
    try:
        inp.clear()
    except Exception:
        pass
    inp.send_keys(search_text)
    time.sleep(type_pause)
    if not _click_select_option(driver, option_text, timeout=3 if fast else 4):
        inp.send_keys(Keys.ARROW_DOWN)
        time.sleep(0.05)
        inp.send_keys(Keys.ENTER)
    time.sleep(0.1 if fast else 0.15)


def _fill_single_select_field_if_needed(
    driver,
    field_id: str,
    search_text: str,
    option_text: str,
    *,
    timeout: int = 12,
    extra_match: tuple[str, ...] = (),
    fast: bool = False,
) -> bool:
    """Só pesquisa/preenche se o valor atual não corresponder ao esperado."""
    hints = tuple(h for h in (option_text, search_text, *extra_match) if h)
    if _field_already_selected(driver, field_id, *hints):
        _log_step("fill_select", f"{field_id} já selecionado")
        return False
    current = _get_field_display_value(driver, field_id)
    _log_step("fill_select", f"{field_id} atual={current[:80]!r} → buscar {search_text!r}")
    _fill_single_select_field(
        driver, field_id, search_text, option_text, timeout=timeout, fast=fast
    )
    return True


def _fill_components_if_needed(driver, profile: JiraCreateProfile, *, timeout: int = 5) -> None:
    hints = (
        profile.component_option,
        profile.component_search,
        profile.component_option.replace("ç", "c").replace("ã", "a"),
        "suporte claro",
        "suporte",
    )
    if _field_already_selected(driver, "components-field", *hints):
        _log_step("fill_form", "componente já selecionado")
        return
    if _fill_components_fast(driver, profile, timeout=timeout):
        return
    try:
        _fill_single_select_field_if_needed(
            driver,
            "components-field",
            profile.component_search,
            profile.component_option,
            timeout=timeout,
            extra_match=("suporte", "claro"),
            fast=True,
        )
    except Exception:
        _fill_labeled_select(
            driver,
            "Componente",
            profile.component_search,
            profile.component_option,
            timeout=timeout,
        )


def _fill_components_fast(driver, profile: JiraCreateProfile, *, timeout: int = 3) -> bool:
    """Preenche componente via colar texto — mais rápido que digitar caractere a caractere."""
    option_text = profile.component_option
    if not option_text:
        return False
    try:
        wait = WebDriverWait(driver, timeout)
        inp = wait.until(EC.element_to_be_clickable((By.ID, "components-field")))
        inp.click()
        time.sleep(0.05)
        try:
            inp.send_keys(Keys.CONTROL, "a")
            inp.send_keys(Keys.DELETE)
        except Exception:
            pass
        try:
            pyperclip.copy(option_text)
            inp.send_keys(Keys.CONTROL, "v")
        except Exception:
            inp.send_keys(option_text)
        time.sleep(0.12)
        if _click_select_option(driver, option_text, timeout=2):
            time.sleep(0.05)
            return True
        inp.send_keys(Keys.ARROW_DOWN)
        time.sleep(0.04)
        inp.send_keys(Keys.ENTER)
        time.sleep(0.05)
        return _field_already_selected(
            driver,
            "components-field",
            option_text,
            profile.component_search,
            "suporte claro",
            "suporte",
        )
    except Exception as exc:
        _log_step("fill_components_fast", str(exc), level=logging.DEBUG)
        return False


def _fill_summary(driver, text: str) -> None:
    for field_id in ("summary", "summary-field"):
        el = wait_for_element(driver, By.ID, field_id, timeout=8, ensure_visible=True)
        if el:
            el.clear()
            el.send_keys(text)
            return
    raise RuntimeError("Campo Resumo não encontrado no formulário Jira.")


def _scroll_to_description(driver) -> None:
    try:
        driver.execute_script(
            "var el = document.getElementById('description-wiki-edit') "
            "|| document.getElementById('description') "
            "|| document.querySelector('label[for=\"description\"]');"
            "if (el) el.scrollIntoView({block:'center'});"
        )
        time.sleep(0.15)
    except Exception:
        pass


def _click_description_text_tab(driver) -> bool:
    """Abre aba Text/Texto — textarea fica editável (mais estável que WYSIWYG)."""
    selectors = (
        (By.CSS_SELECTOR, "#description-wiki-edit .wiki-edit-tab-text a"),
        (By.CSS_SELECTOR, "#description-wiki-edit li.wiki-edit-tab-text"),
        (By.XPATH, "//*[@id='description-wiki-edit']//a[normalize-space()='Text' or normalize-space()='Texto']"),
        (By.XPATH, "//*[@id='description-wiki-edit']//*[contains(@class,'wiki-edit-tab-text')]"),
    )
    for by, value in selectors:
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                time.sleep(0.25)
                return True
        except Exception:
            continue
    return False


def _click_description_editable_area(driver) -> bool:
    """Clica na área editável da descrição para dar foco."""
    selectors = (
        (By.CSS_SELECTOR, "textarea#description"),
        (By.CSS_SELECTOR, "textarea[name='description']"),
        (By.CSS_SELECTOR, "#description-wiki-edit .wiki-edit-content"),
        (By.CSS_SELECTOR, "#description-wiki-edit-area"),
        (By.CSS_SELECTOR, "#description-wiki-edit .mce-edit-area"),
        (By.CSS_SELECTOR, "#description-wiki-edit"),
        (By.CSS_SELECTOR, "label[for='description']"),
        (By.XPATH, "//label[contains(normalize-space(),'Descrição') or contains(normalize-space(),'Description')]"),
    )
    for by, value in selectors:
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.1)
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                time.sleep(0.2)
                return True
        except Exception:
            continue
    return False


def _description_textarea_value(driver) -> str:
    for field_id in ("description", "description-field"):
        try:
            ta = driver.find_element(By.ID, field_id)
            val = (ta.get_attribute("value") or "").strip()
            if val:
                return val
        except Exception:
            continue
    try:
        return (
            driver.execute_script(
                "var ta = document.getElementById('description') "
                "|| document.querySelector('textarea[name=\"description\"]');"
                "return ta ? (ta.value || '') : '';"
            )
            or ""
        ).strip()
    except Exception:
        return ""


def _read_tinymce_body_text(driver) -> str:
    driver.switch_to.default_content()
    iframe_selectors = (
        "#description-wiki-edit iframe",
        "iframe[id*='description']",
        "iframe[id*='wysiwyg']",
        "iframe[id*='mce']",
    )
    for css in iframe_selectors:
        try:
            for iframe in driver.find_elements(By.CSS_SELECTOR, css):
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(iframe)
                    body = driver.find_element(By.CSS_SELECTOR, "body, #tinymce")
                    text = (body.text or body.get_attribute("innerText") or "").strip()
                    driver.switch_to.default_content()
                    if text:
                        return text
                except Exception:
                    driver.switch_to.default_content()
        except Exception:
            continue
    driver.switch_to.default_content()
    return ""


def _description_has_content(driver, expected: str, *, min_len: int = 8) -> bool:
    exp = (expected or "").strip()
    if not exp:
        return True
    current = _description_textarea_value(driver) or _read_tinymce_body_text(driver)
    if not current:
        return False
    snippet = exp[: min(len(exp), 40)].lower()
    cur = current.lower()
    return snippet in cur or len(current.strip()) >= min(min_len, max(len(exp) // 3, 5))


def _fill_description_textarea(driver, text: str) -> bool:
    for field_id in ("description", "description-field"):
        try:
            ta = driver.find_element(By.ID, field_id)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", ta)
            try:
                ta.click()
            except Exception:
                driver.execute_script("arguments[0].click();", ta)
            time.sleep(0.15)
            try:
                ta.clear()
            except Exception:
                driver.execute_script("arguments[0].value = '';", ta)
            ta.send_keys(text)
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                ta,
            )
            if _description_has_content(driver, text):
                return True
        except Exception:
            continue
    return False


def _fill_description_tinymce(driver, text: str) -> bool:
    driver.switch_to.default_content()
    iframe_selectors = (
        "#description-wiki-edit iframe",
        "iframe[id*='description']",
        "iframe[id*='wysiwyg']",
        "iframe[id*='mce']",
    )
    for css in iframe_selectors:
        try:
            iframe = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, css))
            )
            driver.switch_to.frame(iframe)
            body = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "body, #tinymce"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", body)
            body.click()
            time.sleep(0.15)
            driver.execute_script(
                "var el = arguments[0], txt = arguments[1];"
                "el.focus();"
                "if (document.execCommand) {"
                "  document.execCommand('selectAll', false, null);"
                "  document.execCommand('insertText', false, txt);"
                "} else {"
                "  el.innerHTML = txt.replace(/\\n/g, '<br>');"
                "}",
                body,
                text,
            )
            try:
                body.send_keys(Keys.CONTROL, "a")
                time.sleep(0.05)
                body.send_keys(text)
            except Exception:
                pass
            driver.switch_to.default_content()
            time.sleep(0.2)
            if _description_has_content(driver, text):
                return True
        except TimeoutException:
            driver.switch_to.default_content()
        except Exception:
            driver.switch_to.default_content()
    return False


def _fill_description_js(driver, text: str) -> bool:
    try:
        driver.execute_script(
            """
            var txt = arguments[0];
            var ta = document.getElementById('description')
                || document.querySelector('textarea[name="description"]');
            if (ta) {
                ta.value = txt;
                ta.dispatchEvent(new Event('input', {bubbles: true}));
                ta.dispatchEvent(new Event('change', {bubbles: true}));
            }
            var editors = [];
            if (window.tinyMCE && window.tinyMCE.editors) editors = window.tinyMCE.editors;
            if (window.tinymce && window.tinymce.editors) editors = window.tinymce.editors;
            for (var i = 0; i < editors.length; i++) {
                try {
                    if (editors[i].id && editors[i].id.indexOf('description') >= 0) {
                        editors[i].setContent(txt.replace(/\\n/g, '<br>'));
                    }
                } catch (e) {}
            }
            if (window.tinyMCE && window.tinyMCE.activeEditor) {
                window.tinyMCE.activeEditor.setContent(txt.replace(/\\n/g, '<br>'));
            }
            """,
            text,
        )
        time.sleep(0.25)
        return _description_has_content(driver, text)
    except Exception as exc:
        _log_step("fill_description", f"js fallback: {exc}", level=logging.DEBUG)
        return False


def _fill_description(driver, text: str) -> None:
    """Seleciona o campo Descrição e preenche (aba Texto, textarea, TinyMCE ou JS)."""
    if not (text or "").strip():
        return
    _log_step("fill_form", f"descrição ({len(text)} chars)")
    _scroll_to_description(driver)
    _click_description_editable_area(driver)
    _click_description_text_tab(driver)
    _click_description_editable_area(driver)

    strategies = (
        _fill_description_textarea,
        _fill_description_tinymce,
        _fill_description_js,
    )
    for attempt, strategy in enumerate(strategies, start=1):
        try:
            if strategy(driver, text):
                _log_step("fill_form", f"descrição ok (estratégia {attempt})")
                return
        except Exception as exc:
            _log_step("fill_description", f"estratégia {attempt}: {exc}", level=logging.DEBUG)
        _click_description_editable_area(driver)

    LOG.warning("Campo Descrição não preenchido — preencha manualmente se necessário.")


def _fill_labeled_select(
    driver,
    label_substring: str,
    search_text: str,
    option_text: str,
    *,
    timeout: int = 12,
) -> bool:
    """Preenche select AUI associado a um label (ex.: Categoria → Informação)."""
    if not option_text:
        return False
    label_xpaths = (
        f"//label[contains(normalize-space(),'{label_substring}')]",
        f"//span[contains(@class,'field-label') and contains(normalize-space(),'{label_substring}')]",
        f"//*[contains(@class,'field-group')]//label[contains(normalize-space(),'{label_substring}')]",
    )
    for xp in label_xpaths:
        try:
            label = driver.find_element(By.XPATH, xp)
            field_id = label.get_attribute("for")
            if field_id:
                _fill_single_select_field_if_needed(
                    driver,
                    field_id,
                    search_text,
                    option_text,
                    timeout=timeout,
                    extra_match=tuple(
                        part
                        for part in (
                            option_text,
                            search_text,
                            option_text.replace("ç", "c").replace("ã", "a"),
                        )
                        if part
                    ),
                )
                return True
            container = label.find_element(
                By.XPATH,
                "./ancestor::*[contains(@class,'field-group') or contains(@class,'field-group')][1]",
            )
            for inp in container.find_elements(By.CSS_SELECTOR, "input[type='text'], select"):
                inp_id = inp.get_attribute("id")
                if inp_id:
                    _fill_single_select_field_if_needed(
                        driver,
                        inp_id,
                        search_text,
                        option_text,
                        timeout=timeout,
                    )
                    return True
        except Exception as exc:
            _log_step("fill_labeled_select", f"{label_substring}: {exc}", level=logging.DEBUG)
            continue
    return False


def _fill_category_field(driver, category: str) -> bool:
    if not category:
        return True
    if _category_select_is_filled(driver, category):
        _log_step("fill_form", f"categoria={category} (já selecionada)")
        return True
    if _fill_native_select_by_label(driver, "Categoria", category):
        _log_step("fill_form", f"categoria={category} (select nativo)")
        return True
    if _fill_native_select_by_label(driver, "Category", category):
        _log_step("fill_form", f"categoria={category} (select nativo EN)")
        return True
    # Fallback: select visível com opção Informação ainda em Nenhum
    for sel in driver.find_elements(By.TAG_NAME, "select"):
        try:
            if not sel.is_displayed():
                continue
            if _native_select_is_empty(sel) and _fill_native_select_element(driver, sel, category):
                _log_step("fill_form", f"categoria={category} (select por opção)")
                return True
        except Exception:
            continue
    search = category[:6] if len(category) > 6 else category
    if _fill_labeled_select(driver, "Categoria", search, category):
        _log_step("fill_form", f"categoria={category} (aui fallback)")
        return True
    LOG.warning("Campo Categoria não preenchido — selecione «%s» manualmente.", category)
    return False


def _try_fill_custom_field_by_label(driver, label_substring: str, value: str) -> None:
    if not value:
        return
    try:
        label = driver.find_element(
            By.XPATH,
            f"//label[contains(normalize-space(),'{label_substring}')]",
        )
        field_id = label.get_attribute("for")
        if field_id:
            _fill_single_select_field(driver, field_id, value, value, timeout=10)
    except Exception as exc:
        LOG.debug("Campo %s não preenchido: %s", label_substring, exc)


def fill_create_issue_form(
    driver,
    profile: JiraCreateProfile,
    summary: str,
    description: str,
) -> None:
    """Preenche modal Criar Item conforme perfil."""
    _log_step("fill_form", f"perfil={profile.key}")
    _advance_past_create_wizard(driver, profile, timeout=30)
    if not _wait_for_create_form(driver, timeout=30):
        raise RuntimeError(f"Formulário Criar Item não visível. {_driver_context(driver)}")

    if _is_create_wizard(driver):
        raise RuntimeError(
            "Ainda no assistente Jira (Projeto/Tipo/Próximo). "
            f"{_driver_context(driver)}"
        )

    # Projeto/tipo já vêm do assistente — só ajusta se necessário.
    try:
        _fill_single_select_field_if_needed(
            driver,
            "project-field",
            profile.project_search,
            profile.project_option,
            timeout=3,
            extra_match=("pplid",),
            fast=True,
        )
    except Exception as exc:
        _log_step("fill_form", f"projeto já definido ou indisponível: {exc}", level=logging.DEBUG)

    try:
        _fill_single_select_field_if_needed(
            driver,
            "issuetype-field",
            profile.issue_type,
            profile.issue_type,
            timeout=3,
            fast=True,
        )
    except Exception as exc:
        _log_step("fill_form", f"tipo já definido ou indisponível: {exc}", level=logging.DEBUG)

    if profile.category:
        _fill_category_field(driver, profile.category)

    _fill_components_if_needed(driver, profile, timeout=3)

    _fill_summary(driver, summary)
    _fill_description(driver, description)
    if description and not _description_has_content(driver, description):
        _log_step("fill_form", "descrição vazia — nova tentativa", level=logging.WARNING)
        _click_description_text_tab(driver)
        _fill_description(driver, description)

    try:
        _fill_single_select_field_if_needed(
            driver,
            "priority-field",
            profile.priority,
            profile.priority,
            timeout=4,
            fast=True,
        )
    except Exception:
        _try_fill_custom_field_by_label(driver, "Prioridade", profile.priority)

    if profile.category and not _category_select_is_filled(driver, profile.category):
        _log_step("fill_form", "categoria ainda vazia — nova tentativa", level=logging.WARNING)
        _fill_category_field(driver, profile.category)

    _log_step("fill_form", "concluído")


_ISSUE_KEY_PATTERN = re.compile(r"([A-Z][A-Z0-9_]+-\d+)", re.I)


def _extract_issue_key_from_url(url: str) -> str | None:
    if not url:
        return None
    lower = url.lower()
    if "/browse/" in lower:
        match = re.search(r"/browse/([^/?#]+)", url, re.I)
        if match:
            return match.group(1).upper()
    match = _ISSUE_KEY_PATTERN.search(url)
    return match.group(1).upper() if match else None


def _extract_issue_key_from_page(driver) -> str | None:
    key = _extract_issue_key_from_url(driver.current_url or "")
    if key:
        return key
    selectors = (
        "#issue_created_key",
        ".issue-created-key",
        ".aui-message-success",
        "#issue-key",
        "[data-issue-key]",
    )
    for sel in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                for attr in ("data-issue-key", "title", "textContent"):
                    val = el.get_attribute(attr)
                    if val:
                        match = _ISSUE_KEY_PATTERN.search(val)
                        if match:
                            return match.group(1).upper()
                text = (el.text or "").strip()
                if text:
                    match = _ISSUE_KEY_PATTERN.search(text)
                    if match:
                        return match.group(1).upper()
        except Exception:
            continue
    return None


def _create_form_error_message(driver) -> str | None:
    selectors = (".aui-message-error", ".error", "#create-issue-dialog .error")
    for sel in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                text = (el.text or "").strip()
                if text:
                    return text[:300]
        except Exception:
            continue
    return None


def _wait_for_issue_created(driver, *, timeout: int = 45) -> str | None:
    """Aguarda confirmação de criação e tenta extrair a chave (ex.: PPLID-123)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        key = _extract_issue_key_from_page(driver)
        if key:
            return key
        url = (driver.current_url or "").lower()
        if "createissue" not in url and ("viewissue" in url or "/browse/" in url):
            key = _extract_issue_key_from_url(driver.current_url or "")
            if key:
                return key
        time.sleep(0.5)
    return None


def submit_create_issue_form(
    driver,
    *,
    timeout: int = 45,
    category: str | None = None,
) -> str | None:
    """Clica em Criar para submeter o formulário. Retorna chave do chamado se detectada."""
    report_progress("submit_issue", step_label("submit_issue"))
    _log_step("submit_issue", _driver_context(driver))

    selectors = [
        (By.ID, "create-issue-submit"),
        (By.CSS_SELECTOR, "input#create-issue-submit"),
        (By.ID, "issue-create-submit"),
        (By.CSS_SELECTOR, "input#issue-create-submit"),
        (By.CSS_SELECTOR, "#issue-create input[type='submit']"),
        (By.CSS_SELECTOR, "form#issue-create input[type='submit']"),
        (By.XPATH, "//input[@type='submit' and (@value='Criar' or @value='Create')]"),
        (By.XPATH, "//button[@type='submit' and (normalize-space()='Criar' or normalize-space()='Create')]"),
        (By.CSS_SELECTOR, "#create-issue-dialog input[type='submit']"),
        (By.CSS_SELECTOR, "#create-issue-dialog .aui-button-primary"),
        (By.XPATH, "//div[contains(@class,'buttons')]//input[@type='submit']"),
        (By.XPATH, "//footer//input[@type='submit']"),
    ]

    last_error: Exception | None = None
    for by, value in selectors:
        label = f"{by}={value}"
        try:
            wait = WebDriverWait(driver, min(timeout, 15))
            el = wait.until(EC.element_to_be_clickable((by, value)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.4)
            el.click()
            _log_step("submit_issue", f"clicou {label}")
            time.sleep(2)

            issue_key = _wait_for_issue_created(driver, timeout=timeout)
            if issue_key:
                _log_step("submit_issue", f"chamado criado: {issue_key}")
                return issue_key

            url = (driver.current_url or "").lower()
            if "createissue" in url:
                err = _create_form_error_message(driver)
                if err and category and (
                    "categoria" in err.lower() or "must enter a value" in err.lower()
                ):
                    _log_step("submit_issue", "erro categoria — tentando preencher novamente", level=logging.WARNING)
                    if _fill_category_field(driver, category):
                        time.sleep(0.3)
                        continue
                if err:
                    raise RuntimeError(f"Jira rejeitou a criação: {err} | {_driver_context(driver)}")
                raise RuntimeError(
                    "Clicou em Criar, mas o chamado não foi confirmado a tempo. "
                    f"{_driver_context(driver)}"
                )

            _log_step("submit_issue", "submetido (chave não detectada)", level=logging.WARNING)
            return None
        except TimeoutException:
            continue
        except RuntimeError:
            raise
        except Exception as exc:
            last_error = exc
            _log_step("submit_issue", f"erro {label}: {exc}", level=logging.DEBUG)
            continue

    screenshot = take_error_screenshot(driver, "submit_issue_not_found", JIRA_ROBOT_NAME)
    shot_hint = f" Screenshot: {screenshot}" if screenshot else ""
    raise RuntimeError(
        "Não foi possível clicar em Criar no formulário Jira. "
        f"Detalhe: {last_error} | {_driver_context(driver)}.{shot_hint}"
    )


def run_jira_batch_flow(
    matricula: str,
    senha: str,
    *,
    profile_key: str,
    items: list[dict],
    headless: bool = False,
) -> dict:
    """Lote: 1 login Okta → N chamados em sequência."""
    profile = get_profile(profile_key)
    batch_items = [dict(item or {}) for item in (items or []) if item]
    total = len(batch_items)
    if total == 0:
        return {"ok": False, "message": "Nenhuma demanda no lote.", "step": "error", "batch": True}

    driver = None
    current_step = "init"
    keep_open_sec = int(os.environ.get("SUPORTE_CLARO_JIRA_KEEP_OPEN_SEC", "120") or "120")
    results: list[dict] = []
    succeeded = 0
    failed = 0

    try:
        report_progress("init", "Abrindo Chrome…", batch_total=total)
        driver = create_driver(headless=headless)
        try:
            driver.maximize_window()
        except Exception:
            pass

        current_step = "okta_login"
        report_progress(current_step, f"Abrindo {okta.O_LINK}", batch_total=total)
        driver.get(okta.O_LINK)
        time.sleep(1.5)
        login_okta_resiliente(driver, matricula, senha, timeout=25, wait_for_post_login=True)
        report_progress(current_step, "Login Okta concluído.", ok=True, batch_total=total)

        current_step = "open_jira"
        open_jira_from_okta(driver)
        report_progress(current_step, "Jira aberto.", ok=True, batch_total=total)

        for index, item in enumerate(batch_items, start=1):
            registro_id = item.get("registro_id")
            protocolo = str(item.get("protocolo") or f"#{registro_id}" or index)
            summary = str(item.get("summary") or "").strip()
            description = str(item.get("description") or "").strip()
            label = f"{index}/{total} — {protocolo}"
            current_step = "batch_item"
            report_progress(
                "batch_item",
                label,
                batch_index=index,
                batch_total=total,
                batch_items=results,
                batch_current_registro_id=registro_id,
            )
            try:
                issue_key = _create_single_issue_in_session(
                    driver,
                    profile,
                    summary,
                    description,
                    first_in_batch=(index == 1),
                )
                entry = {
                    "ok": True,
                    "registro_id": registro_id,
                    "protocolo": protocolo,
                    "issue_key": issue_key,
                }
                results.append(entry)
                succeeded += 1
                report_progress(
                    "batch_item",
                    f"{label} — {issue_key or 'criado'}",
                    batch_index=index,
                    batch_total=total,
                    batch_items=results,
                    batch_current_registro_id=registro_id,
                    ok=True,
                )
            except Exception as exc:
                LOG.exception("Falha item lote Jira %s", protocolo)
                screenshot = take_error_screenshot(driver, f"batch_{index}", JIRA_ROBOT_NAME)
                err_msg = str(exc)
                if screenshot:
                    err_msg = f"{err_msg}\nScreenshot: {screenshot}"
                entry = {
                    "ok": False,
                    "registro_id": registro_id,
                    "protocolo": protocolo,
                    "error": err_msg[:500],
                }
                results.append(entry)
                failed += 1
                report_progress(
                    "batch_item",
                    f"{label} — falhou",
                    detail=err_msg[:500],
                    batch_index=index,
                    batch_total=total,
                    batch_items=results,
                    batch_current_registro_id=registro_id,
                    ok=False,
                )
                continue

        current_step = "batch_done"
        done_msg = f"Lote concluído: {succeeded}/{total} chamado(s) criado(s)."
        if failed:
            done_msg = f"{done_msg} {failed} falha(s)."
        _message_box(
            "Suporte Claro — Jira (lote)",
            (
                f"{done_msg}\n\n"
                f"Perfil: {profile.label}\n\n"
                f"O Chrome ficará aberto por {keep_open_sec} segundos."
            ),
        )
        _keep_browser_open(keep_open_sec, reason="Confira os chamados criados no Jira.")
        report_progress(
            "batch_done",
            done_msg,
            ok=True,
            batch_index=total,
            batch_total=total,
            batch_items=results,
        )
        return {
            "ok": True,
            "batch": True,
            "message": done_msg,
            "profile": profile.key,
            "step": current_step,
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "items": results,
        }
    except Exception as exc:
        LOG.exception("Falha automação Jira lote step=%s", current_step)
        screenshot = take_error_screenshot(driver, current_step, JIRA_ROBOT_NAME) if driver else None
        detail = str(exc)
        ctx = _driver_context(driver) if driver else ""
        if ctx:
            detail = f"{detail}\n\nContexto: {ctx}"
        if screenshot:
            detail = f"{detail}\n\nScreenshot: {screenshot}"
        report_progress(
            "error",
            f"Falhou em: {step_label(current_step)}",
            detail=detail[:500],
            ok=False,
            batch_total=total,
            batch_items=results,
        )
        _error_box(
            "Suporte Claro — Erro Jira (lote)",
            (
                f"Etapa: {step_label(current_step)} ({current_step})\n\n"
                f"{detail}\n\n"
                f"Concluídos antes da falha: {succeeded}/{total}.\n\n"
                f"O Chrome ficará aberto por {keep_open_sec}s."
            ),
        )
        _keep_browser_open(keep_open_sec, reason="Inspecione o erro no Chrome.")
        return {
            "ok": False,
            "batch": True,
            "message": detail,
            "profile": profile_key,
            "step": current_step,
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "items": results,
        }
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_jira_create_flow(
    matricula: str,
    senha: str,
    *,
    profile_key: str,
    summary: str,
    description: str,
    headless: bool = False,
) -> dict:
    """Fluxo completo: Okta → Jira → preencher Criar Item."""
    profile = get_profile(profile_key)
    driver = None
    current_step = "init"
    keep_open_sec = int(os.environ.get("SUPORTE_CLARO_JIRA_KEEP_OPEN_SEC", "120") or "120")
    try:
        report_progress("init", "Abrindo Chrome…")
        driver = create_driver(headless=headless)
        try:
            driver.maximize_window()
        except Exception:
            pass

        current_step = "okta_login"
        report_progress(current_step, f"Abrindo {okta.O_LINK}")
        driver.get(okta.O_LINK)
        time.sleep(1.5)
        login_okta_resiliente(driver, matricula, senha, timeout=25, wait_for_post_login=True)
        report_progress(current_step, "Login Okta concluído.", ok=True)

        current_step = "open_jira"
        open_jira_from_okta(driver)
        report_progress(current_step, "Jira aberto.", ok=True)

        current_step = "open_create_form"
        open_create_issue_form(driver, profile)
        report_progress(current_step, "Formulário Criar Item aberto.", ok=True)

        current_step = "fill_form"
        fill_create_issue_form(driver, profile, summary, description)
        report_progress(current_step, "Campos preenchidos.", ok=True)

        current_step = "submit_issue"
        issue_key = submit_create_issue_form(driver, category=profile.category)
        if issue_key:
            report_progress(current_step, f"Chamado {issue_key} criado no Jira.", ok=True)
        else:
            report_progress(current_step, "Formulário enviado no Jira.", ok=True)

        current_step = "done"
        if issue_key:
            done_msg = (
                f"Chamado {issue_key} criado e vinculado automaticamente à demanda no portal."
            )
        else:
            done_msg = (
                "Chamado criado no Jira. O portal tentará vincular o código automaticamente."
            )
        report_progress("waiting_user", done_msg)
        _message_box(
            "Suporte Claro — Jira",
            (
                f"Chamado criado no Jira ({profile.label}).\n\n"
                + (f"Código: {issue_key}\n\n" if issue_key else "")
                + (
                    "O código foi vinculado automaticamente à demanda no portal.\n\n"
                    if issue_key
                    else "Aguarde o portal vincular o código à demanda.\n\n"
                )
                + f"O Chrome ficará aberto por {keep_open_sec} segundos."
            ),
        )
        _keep_browser_open(
            keep_open_sec,
            reason="Confira o chamado criado no Jira.",
        )
        report_progress("done", "Automação concluída.", ok=True)
        return {
            "ok": True,
            "message": done_msg,
            "profile": profile.key,
            "step": current_step,
            "issue_key": issue_key,
        }
    except Exception as exc:
        LOG.exception("Falha automação Jira Suporte Claro step=%s", current_step)
        screenshot = take_error_screenshot(driver, current_step, JIRA_ROBOT_NAME) if driver else None
        detail = str(exc)
        ctx = _driver_context(driver) if driver else ""
        if ctx:
            detail = f"{detail}\n\nContexto: {ctx}"
        if screenshot:
            detail = f"{detail}\n\nScreenshot: {screenshot}"
        report_progress("error", f"Falhou em: {step_label(current_step)}", detail=detail[:500], ok=False)
        _error_box(
            "Suporte Claro — Erro Jira",
            (
                f"Etapa: {step_label(current_step)} ({current_step})\n\n"
                f"{detail}\n\n"
                f"O Chrome ficará aberto por {keep_open_sec}s para você inspecionar."
            ),
        )
        _keep_browser_open(keep_open_sec, reason="Inspecione o erro no Chrome.")
        return {
            "ok": False,
            "message": detail,
            "profile": profile_key,
            "step": current_step,
        }
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
