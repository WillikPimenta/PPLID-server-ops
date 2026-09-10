# -*- coding: utf-8 -*-
"""Navegação Okta → BRFlow → menu de replicação."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.config import brflow, okta
from app.infrastructure.selenium_helpers import create_driver, login_okta_resiliente, send_keys_to_element
from app.bots.replicacao_d1.selenium.constants import TIMEOUT_DRIVER

log = logging.getLogger("robots.replicacao_d1.navigation")

StatusFn = Callable[..., None]
ProgressFn = Callable[..., None]
WaitFiltersFn = Callable[..., None]


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def create_chrome_driver(headless: bool = False):
    return create_driver(headless=headless)


def clicar_com_retry(
    driver,
    locator,
    *,
    by=By.XPATH,
    tentativas: int = 3,
    pausa: int = 2,
    descricao: str = "elemento",
):
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
            log.warning(
                "Falha ao clicar em %s (tentativa %d/%d): %s",
                descricao,
                tentativa,
                tentativas,
                exc,
            )
            time.sleep(pausa)
    raise ultimo_erro


def fazer_login_okta(
    driver,
    matricula: str,
    senha: str,
    *,
    set_status: StatusFn | None = None,
    set_progress: ProgressFn | None = None,
) -> None:
    set_status = set_status or _noop
    set_progress = set_progress or _noop
    set_status("Iniciando autenticação Okta")
    set_progress(18, "Acessando Okta")
    driver.get(okta.O_LINK)
    login_okta_resiliente(driver, matricula, senha, timeout=TIMEOUT_DRIVER)
    set_progress(22, "Okta autenticado")


def abrir_brflow(
    driver,
    *,
    set_status: StatusFn | None = None,
    set_progress: ProgressFn | None = None,
) -> None:
    set_status = set_status or _noop
    set_progress = set_progress or _noop
    set_status("BRFlow: buscando no Okta")
    set_progress(25, "Pesquisando BRFlow")
    send_keys_to_element(driver, "id", okta.O_pesquisar, "brflow")
    send_keys_to_element(driver, "id", okta.O_pesquisar, Keys.ENTER)

    WebDriverWait(driver, 30).until(lambda d: len(d.window_handles) > 1)
    driver.switch_to.window(driver.window_handles[-1])
    set_progress(30, "BRFlow aberto")

    WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located((By.XPATH, brflow.B_menu_replicacao))
    )


def acessar_menu_replicacao(
    driver,
    *,
    set_status: StatusFn | None = None,
    set_progress: ProgressFn | None = None,
    wait_filters: WaitFiltersFn | None = None,
) -> None:
    set_status = set_status or _noop
    set_progress = set_progress or _noop
    set_status("Acessando menu de replicação de protocolos")
    set_progress(32, "Entrando no menu")
    clicar_com_retry(
        driver,
        brflow.B_menu_replicacao,
        by=By.XPATH,
        descricao="menu Replicação de Protocolos",
    )
    if wait_filters:
        wait_filters(driver)
    set_progress(35, "Menu de replicação acessado")
    set_status("Menu de replicação de protocolos acessado")
