# -*- coding: utf-8 -*-
"""Upload de CSV e salvamento no painel de edição BRFlow (replicação D-1)."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.bots.replicacao_aud_d1_planning import csv_protocolos_e_limpeza
from app.bots.replicacao_d1.domain import WorkflowActionResult
from app.bots.replicacao_d1.selenium.constants import TIMEOUT_DRIVER
from app.bots.replicacao_d1.selenium.painel_edicao import (
    validar_e_corrigir_config_fila_painel_edicao,
)
from app.config import REPLICACAO_CSV_PLACEHOLDER_LIMPEZA, brflow

log = logging.getLogger("robots.bot_replicacao_aud_d1")

SetStatusFn = Callable[[str], None]
RefilterFn = Callable[..., None]
EhFilaDoc31Fn = Callable[[str], bool]
TakeScreenshotFn = Callable[..., None]
SanitizarSufixoFn = Callable[[str], str]


class SaveNotConfirmedError(RuntimeError):
    """O clique ocorreu, mas a UI não confirmou a persistência."""


def verificar_salvamento_replicacao(driver, timeout: int = 10) -> None:
    """Exige retorno à listagem e rejeita mensagens de erro visíveis."""
    mensagens: list[str] = []
    for selector in (
        ".alert-danger",
        ".toast-error",
        ".swal2-icon-error",
        ".w2ui-error",
        ".bootbox .text-danger",
    ):
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    texto = str(getattr(element, "text", "") or "").strip()
                    mensagens.append(texto or selector)
        except Exception:
            continue
    if mensagens:
        raise SaveNotConfirmedError(
            "BRFlow rejeitou o salvamento: " + "; ".join(mensagens)[:300]
        )
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, brflow.B_replicacao_listagem_tbody))
        )
    except TimeoutException as exc:
        raise SaveNotConfirmedError("Listagem BRFlow não retornou após Salvar") from exc


def csv_protocolos_vazio(caminho: Path) -> bool:
    """True se o CSV é de limpeza (0 bytes legado ou só placeholder)."""
    return csv_protocolos_e_limpeza(caminho)


def aguardar_painel_edicao(driver, *, set_status: Optional[SetStatusFn] = None) -> None:
    if set_status:
        set_status("Aguardando painel de edição")
    WebDriverWait(driver, TIMEOUT_DRIVER).until(
        lambda drv: painel_edicao_replicacao_aberto(drv)
    )


def painel_edicao_replicacao_aberto(driver) -> bool:
    """True quando o formulário de edição está visível (não só o slot w2ui lateral)."""
    seletores = (
        brflow.B_replicacao_input_qtd_replicada,
        brflow.B_replicacao_workflow_destino_edicao,
        brflow.B_replicacao_checkbox_protocolos,
    )
    for css in seletores:
        for el in driver.find_elements(By.CSS_SELECTOR, css):
            try:
                if el.is_displayed():
                    return True
            except Exception:
                continue
    return False


def _clicar_elemento_visivel(driver, seletores: tuple) -> bool:
    for by, value in seletores:
        try:
            for el in driver.find_elements(by, value):
                if not el.is_displayed() or not el.is_enabled():
                    continue
                driver.execute_script(
                    "arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});",
                    el,
                )
                try:
                    driver.execute_script("arguments[0].click();", el)
                except Exception:
                    el.click()
                return True
        except Exception:
            continue
    return False


def _forcar_ocultar_painel_edicao_js(driver) -> None:
    driver.execute_script(
        """
        const ids = ['layout_layout2', 'layout'];
        for (const id of ids) {
            const layout = window.w2ui && window.w2ui[id];
            if (layout && typeof layout.hide === 'function') {
                try { layout.hide('right'); } catch (e) {}
            }
        }
        const panel = document.querySelector('#layout_layout2_panel_right');
        if (panel) {
            panel.style.display = 'none';
            panel.classList.remove('w2ui-panel-show');
        }
        """
    )


def tentar_fechar_painel_edicao(driver) -> None:
    """Tenta fechar o painel lateral sem salvar (Cancelar, X, ESC, w2ui)."""
    seletores = (
        (By.XPATH, brflow.B_replicacao_cancelar),
        (By.XPATH, brflow.B_replicacao_cancelar_posicao),
        (
            By.CSS_SELECTOR,
            "#layout_layout2_panel_right .w2ui-panel-close, "
            "#layout_layout2_panel_right button.btn-default",
        ),
        (
            By.XPATH,
            '//*[@id="layout_layout2_panel_right"]//button[normalize-space(string(.))="Voltar"]',
        ),
    )
    if _clicar_elemento_visivel(driver, seletores):
        return
    body = driver.find_element(By.TAG_NAME, "body")
    for _ in range(2):
        body.send_keys(Keys.ESCAPE)
        time.sleep(0.15)
    _forcar_ocultar_painel_edicao_js(driver)


def aguardar_fechamento_painel_edicao(
    driver,
    *,
    set_status: Optional[SetStatusFn] = None,
    permitir_fallback: bool = False,
) -> bool:
    """Aguarda o formulário de edição fechar após salvar ou cancelar."""
    if set_status:
        set_status("Aguardando fechamento do painel de edição")
    listagem = (By.XPATH, brflow.B_replicacao_listagem_tbody)

    def _formulario_fechado(drv):
        if painel_edicao_replicacao_aberto(drv):
            return False
        try:
            return bool(drv.find_elements(*listagem))
        except Exception:
            return True

    try:
        WebDriverWait(driver, TIMEOUT_DRIVER).until(_formulario_fechado)
    except TimeoutException as exc:
        if not permitir_fallback:
            raise SaveNotConfirmedError(
                "Painel de edição permaneceu aberto após Salvar; persistência não confirmada"
            ) from exc
        log.warning("[REPLICACAO] Painel ainda aberto; fechando sem salvar")
        tentar_fechar_painel_edicao(driver)
        WebDriverWait(driver, 8).until(_formulario_fechado)
    WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located(listagem)
    )
    time.sleep(0.25)
    log.info("[REPLICACAO] Painel de edição fechado")
    return True


def marcar_replicar_protocolos_especificos(driver, *, set_status: Optional[SetStatusFn] = None) -> None:
    if set_status:
        set_status("Marcando replicação por protocolos específicos")
    checkbox = WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_replicacao_checkbox_protocolos))
    )
    driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", checkbox)
    if not checkbox.is_selected():
        try:
            driver.execute_script("arguments[0].click();", checkbox)
        except Exception:
            checkbox.click()
        WebDriverWait(driver, 5).until(lambda _: checkbox.is_selected())
        log.info("[REPLICACAO] Checkbox replicarApenasProtocolosEspecificos marcado")
    else:
        log.info("[REPLICACAO] Checkbox replicarApenasProtocolosEspecificos já estava marcado")


def marcar_replicar_icm_auditoria(driver, *, set_status: Optional[SetStatusFn] = None) -> None:
    if set_status:
        set_status("Marcando replicação ICM auditoria")
    checkbox = WebDriverWait(driver, TIMEOUT_DRIVER).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_replicacao_checkbox_icm_auditoria))
    )
    driver.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", checkbox)
    if not checkbox.is_selected():
        try:
            driver.execute_script("arguments[0].click();", checkbox)
        except Exception:
            checkbox.click()
        WebDriverWait(driver, 5).until(lambda _: checkbox.is_selected())
        log.info("[REPLICACAO] Checkbox replicarIcmAuditoria marcado")
    else:
        log.info("[REPLICACAO] Checkbox replicarIcmAuditoria já estava marcado")


def enviar_csv_protocolos(driver, caminho_csv: Path, tentativas: int = 2) -> None:
    caminho = Path(caminho_csv).resolve()
    if not caminho.exists():
        raise FileNotFoundError(f"CSV de protocolos não encontrado: {caminho}")
    csv_vazio = csv_protocolos_vazio(caminho)

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
            try:
                WebDriverWait(driver, 3).until(
                    lambda _: (file_input.get_attribute("value") or "").strip() != ""
                )
            except TimeoutException:
                time.sleep(0.15)
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


def clicar_salvar_replicacao(driver, *, set_status: Optional[SetStatusFn] = None) -> None:
    """Clica em Salvar no painel de edição da replicação."""
    if set_status:
        set_status("Salvando replicação no BRFlow")
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
            return
        except Exception as exc:
            ultimo_erro = exc
    raise ultimo_erro or TimeoutException("Botão Salvar não encontrado no painel de replicação")


def confirmar_modal_replicacao_se_existir(
    driver,
    timeout: int = 10,
    *,
    set_status: Optional[SetStatusFn] = None,
) -> None:
    """Confirma popup w2ui/bootbox/swal após Salvar (botão Sim / Confirmar)."""
    if set_status:
        set_status("Confirmando popup de replicação")
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
                    return
            except Exception:
                continue
        time.sleep(0.15)
    log.warning("[REPLICACAO] Modal de confirmação não encontrado após %ds", timeout)


def configurar_workflow_replicacao(
    driver,
    workflow: str,
    caminho_csv: Path,
    *,
    settings: Optional[dict] = None,
    workflow_brflow: Optional[str] = None,
    fila: Optional[str] = None,
    set_status: Optional[SetStatusFn] = None,
    refilter_after_save: Optional[RefilterFn] = None,
    eh_fila_doc_31: Optional[EhFilaDoc31Fn] = None,
    take_screenshot: Optional[TakeScreenshotFn] = None,
    sanitizar_sufixo: Optional[SanitizarSufixoFn] = None,
) -> WorkflowActionResult:
    """Marca checkbox, envia CSV, salva e confirma no painel de edição."""
    settings = settings or {}
    sufixo_fn = sanitizar_sufixo or (lambda value: value)
    sufixo = sufixo_fn(workflow_brflow or workflow)
    try:
        aguardar_painel_edicao(driver, set_status=set_status)
        validar_e_corrigir_config_fila_painel_edicao(
            driver, fila, settings, set_status=set_status
        )
        marcar_replicar_protocolos_especificos(driver, set_status=set_status)
        enviar_csv_protocolos(driver, caminho_csv)
        clicar_salvar_replicacao(driver, set_status=set_status)
        confirmar_modal_replicacao_se_existir(driver, set_status=set_status)
        aguardar_fechamento_painel_edicao(driver, set_status=set_status)
        verificar_salvamento_replicacao(driver)
        if refilter_after_save:
            refilter_after_save(driver, settings, motivo="após confirmar salvamento")
        if set_status:
            set_status(f"Salvo: {workflow}")
        log.info(
            "[REPLICACAO] Workflow salvo: %s | arquivo=%s",
            workflow,
            caminho_csv.name,
        )
        return WorkflowActionResult(
            status="SALVO_OK",
            resultado="salvo",
            fase_execucao="confirmacao_salvamento",
        )
    except Exception:
        if take_screenshot:
            try:
                take_screenshot(driver, "log_screenshots", f"replicacao_salvar_{sufixo}")
            except Exception:
                log.debug("Falha ao capturar screenshot do salvamento", exc_info=True)
        raise
