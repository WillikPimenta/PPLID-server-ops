# -*- coding: utf-8 -*-
"""Painel BRFlow: replicação por quantidade diária configurada no workflow."""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.bots.replicacao_d1.selenium.constants import TIMEOUT_DRIVER
from app.bots.replicacao_d1.domain import WorkflowActionResult
from app.bots.replicacao_d1.selenium.painel_edicao import (
    validar_e_corrigir_config_fila_painel_edicao,
)
from app.bots.replicacao_d1.selenium.workflow_upload import (
    aguardar_fechamento_painel_edicao,
    aguardar_painel_edicao,
    clicar_salvar_replicacao,
    confirmar_modal_replicacao_se_existir,
    tentar_fechar_painel_edicao,
    verificar_salvamento_replicacao,
)
from app.config import brflow

log = logging.getLogger("robots.bot_replicacao_aud_d1")

SetStatusFn = Callable[[str], None]
TakeScreenshotFn = Callable[..., None]
AbrirEdicaoFn = Callable[[], None]
SanitizarSufixoFn = Callable[[str], str]

STATUS_SALVO_OK = "SALVO_OK"
STATUS_SEM_ALTERACAO = "SEM_ALTERACAO"


def confirmar_qtd_replicada_pos_save(
    driver,
    qtd_alvo: int,
    *,
    abrir_edicao: AbrirEdicaoFn,
    set_status: Optional[SetStatusFn] = None,
) -> int:
    """Reabre o workflow e confirma a quantidade efetivamente persistida."""
    if set_status:
        set_status(f"Confirmando quantidade salva no BRFlow: {int(qtd_alvo)}")
    abrir_edicao()
    aguardar_painel_edicao(driver, set_status=set_status)
    qtd_encontrada = ler_qtd_replicada_painel(driver)
    fechar_painel_edicao_sem_salvar(driver, set_status=set_status)
    if qtd_encontrada is None:
        from app.bots.replicacao_d1.selenium.workflow_upload import SaveNotConfirmedError

        raise SaveNotConfirmedError("Quantidade não pôde ser relida após Salvar")
    if int(qtd_encontrada) != int(qtd_alvo):
        from app.bots.replicacao_d1.selenium.workflow_upload import SaveNotConfirmedError

        raise SaveNotConfirmedError(
            f"Quantidade persistida diverge do alvo: alvo={int(qtd_alvo)}, encontrada={int(qtd_encontrada)}"
        )
    return int(qtd_encontrada)


def _parse_qtd_brflow(texto: str) -> Optional[int]:
    texto = (texto or "").strip()
    if not texto:
        return None
    match = re.search(r"-?\d+", texto.replace(".", "").replace(",", ""))
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def ler_qtd_replicada_painel(driver) -> Optional[int]:
    """Lê valor atual do campo qtdReplicada no painel de edição."""
    try:
        elemento = WebDriverWait(driver, TIMEOUT_DRIVER).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, brflow.B_replicacao_input_qtd_replicada)
            )
        )
        valor = elemento.get_attribute("value")
        if valor is None:
            valor = driver.execute_script("return arguments[0].value;", elemento)
        return _parse_qtd_brflow(str(valor or ""))
    except TimeoutException:
        log.warning("[PAINEL_QTD] Campo qtdReplicada não encontrado")
        return None


def preencher_qtd_replicada_painel(driver, valor: int, *, set_status: Optional[SetStatusFn] = None) -> None:
    if set_status:
        set_status(f"Definindo quantidade replicada: {valor}")
    elemento = WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, brflow.B_replicacao_input_qtd_replicada)
        )
    )
    driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", elemento)
    elemento.clear()
    elemento.send_keys(str(int(valor)))


def desmarcar_replicar_protocolos_especificos(
    driver,
    *,
    set_status: Optional[SetStatusFn] = None,
) -> bool:
    """Garante modo quantidade no painel e informa se houve alteração."""
    if set_status:
        set_status("Desmarcando replicação por protocolos específicos")
    checkbox = WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, brflow.B_replicacao_checkbox_protocolos)
        )
    )
    driver.execute_script(
        "arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});",
        checkbox,
    )
    if not checkbox.is_selected():
        log.info("[PAINEL_QTD] Checkbox de protocolos específicos já estava desmarcado")
        return False
    try:
        driver.execute_script("arguments[0].click();", checkbox)
    except Exception:
        checkbox.click()
    WebDriverWait(driver, 5).until(lambda _: not checkbox.is_selected())
    log.info("[PAINEL_QTD] Checkbox de protocolos específicos desmarcado")
    return True


def fechar_painel_edicao_sem_salvar(driver, *, set_status: Optional[SetStatusFn] = None) -> None:
    if set_status:
        set_status("Fechando painel (sem alteração)")
    tentar_fechar_painel_edicao(driver)
    aguardar_fechamento_painel_edicao(
        driver, set_status=set_status, permitir_fallback=True
    )


def configurar_workflow_qtd_replicada(
    driver,
    workflow: str,
    qtd_calculada: int,
    *,
    settings: Optional[dict] = None,
    workflow_brflow: Optional[str] = None,
    fila: Optional[str] = None,
    set_status: Optional[SetStatusFn] = None,
    take_screenshot: Optional[TakeScreenshotFn] = None,
    sanitizar_sufixo: Optional[SanitizarSufixoFn] = None,
) -> WorkflowActionResult:
    """Valida destino, compara qtd e salva apenas se diferente. Retorna status."""
    settings = settings or {}
    sufixo_fn = sanitizar_sufixo or (lambda value: value)
    sufixo = sufixo_fn(workflow_brflow or workflow)
    qtd_alvo = max(0, int(qtd_calculada or 0))
    try:
        aguardar_painel_edicao(driver, set_status=set_status)
        validar_e_corrigir_config_fila_painel_edicao(
            driver, fila, settings, set_status=set_status
        )
        checkbox_alterado = desmarcar_replicar_protocolos_especificos(
            driver,
            set_status=set_status,
        )
        qtd_atual = ler_qtd_replicada_painel(driver)
        log.info(
            "[PAINEL_QTD] %s | modo=qtd | calculada=%s | brflow=%s | checkbox_alterado=%s | csv=false",
            workflow,
            qtd_alvo,
            qtd_atual,
            checkbox_alterado,
        )
        if not checkbox_alterado and qtd_atual is not None and qtd_atual == qtd_alvo:
            fechar_painel_edicao_sem_salvar(driver, set_status=set_status)
            if set_status:
                set_status(f"Sem alteração: {workflow} (qtd={qtd_alvo})")
            return WorkflowActionResult(
                status=STATUS_SEM_ALTERACAO,
                resultado="sem_alteracao",
                motivo_codigo="QTD_JA_CONFIGURADA",
                motivo_resumo=f"Quantidade {qtd_alvo} já estava configurada no BRFlow",
                fase_execucao="comparacao_quantidade",
                quantidade_alvo=qtd_alvo,
                quantidade_encontrada=qtd_atual,
            )

        preencher_qtd_replicada_painel(driver, qtd_alvo, set_status=set_status)
        clicar_salvar_replicacao(driver, set_status=set_status)
        confirmar_modal_replicacao_se_existir(driver, set_status=set_status)
        aguardar_fechamento_painel_edicao(driver, set_status=set_status)
        verificar_salvamento_replicacao(driver)
        if set_status:
            set_status(f"Salvo (qtd): {workflow}")
        log.info("[PAINEL_QTD] Workflow salvo | %s | qtd=%s", workflow, qtd_alvo)
        return WorkflowActionResult(
            status=STATUS_SALVO_OK,
            resultado="salvo",
            fase_execucao="confirmacao_salvamento",
            quantidade_alvo=qtd_alvo,
            quantidade_encontrada=None,
        )
    except Exception:
        if take_screenshot:
            try:
                take_screenshot(driver, "log_screenshots", f"replicacao_qtd_{sufixo}")
            except Exception:
                log.debug("Falha ao capturar screenshot de quantidade", exc_info=True)
        raise
