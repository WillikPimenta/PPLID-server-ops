"""Selenium BRFlow: login, navegação e download."""
from __future__ import annotations

import glob
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException

from app.config import *
from app.infrastructure.confer_helpers import click_xpath_com_fallback_js as _click_com_js
from app.infrastructure.selenium_helpers import click_element, login_okta_resiliente, send_keys_to_element
from app.bots.rotina.constants import DOWNLOADS_TEMP_ROTINA
from app.bots.rotina.io import (
    _aguardar_download_completo,
    _get_tz_br,
    _limpar_downloads_temp_inicio,
    _limpar_pasta,
)
from app.bots.rotina.state import _registrar_erro, _set_progress, _set_status

log = logging.getLogger("robots.bot_rotina")

def _fazer_login(drv, matricula, senha, tz_br):
    """
    Faz login no Okta com fallback para dois formulários diferentes.
    
    Args:
        drv: WebDriver do Selenium
        matricula (str): Matrícula do usuário
        senha (str): Senha do usuário
        tz_br: Timezone do Brasil
    
    Raises:
        Exception: Se ambos os formulários falharem
    """
    _set_status("Iniciando autenticação Okta")
    _set_progress(10, "Autenticacao: acessando Okta")

    drv.get(okta.O_LINK)

    try:
        login_okta_resiliente(drv, matricula, senha, timeout=max(TIMEOUT_DRIVER_ROTINA, TIMEOUT_SHORT_ROTINA))
        log.debug("Login Okta resiliente bem-sucedido")
        _set_progress(20, "Autenticacao: validando credenciais")
        return
    except Exception as exc:
        log.error(f"Falha no login Okta resiliente: {exc}")
        raise Exception(f"Falha na autenticação: {exc}")


def _navegar_para_brflow(drv, data_execucao=None):
    """
    Navega até o menu do BRFlow após autenticação.
    
    Args:
        drv: WebDriver do Selenium
        
    Raises:
        TimeoutException: Se elementos não forem encontrados
    """
    wait = WebDriverWait(drv, TIMEOUT_DRIVER_ROTINA)
    wait_short = WebDriverWait(drv, TIMEOUT_SHORT_ROTINA)
    
    _set_status("Pesquisando BRFlow no Okta")
    _set_progress(30, "BRFlow: navegando via Okta")
    
    # Pesquisar BRFlow
    try:
        wait_short.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))
    except TimeoutException:
        log.debug("Timeout curto em O_pesquisar, usando timeout maior")
        wait.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))
    
    send_keys_to_element(drv, "id", okta.O_pesquisar, "brflow")
    drv.find_element(By.ID, okta.O_pesquisar).send_keys(Keys.ENTER)
    
    _set_progress(38, "BRFlow: abrindo nova janela")
    
    # Aguardar nova aba
    try:
        wait_short.until(lambda d: len(d.window_handles) > 1)
    except TimeoutException:
        log.debug("Timeout curto aguardando nova aba, usando timeout maior")
        wait.until(lambda d: len(d.window_handles) > 1)
    
    drv.switch_to.window(drv.window_handles[-1])
    
    _set_progress(45, "BRFlow: carregado, acessando menu de rotinas")
    
    # ========================================
    # ACESSAR SEÇÃO DE ROTINAS
    # ========================================
    
    # Aguardar carregamento e clicar em rotina
    time.sleep(5)
    try:
        wait.until(EC.element_to_be_clickable((By.XPATH, brflow.B_rotina))).click()
    except TimeoutException:
        log.warning("Elemento B_rotina não encontrado, continuando mesmo assim")
    _set_progress(50, "BRFlow: menu de rotinas aberto")
    
    # Inserir data configurada (ou hoje, por padrão)
    data_param = str(data_execucao or "").strip() or str(os.getenv("ROTINA_DATA_EXECUCAO", "")).strip()
    data_rotina = ""
    if data_param:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                data_rotina = datetime.strptime(data_param, fmt).strftime("%d/%m/%Y")
                break
            except ValueError:
                continue
        if not data_rotina:
            log.warning(f"Data de rotina inválida recebida ({data_param}); usando data de hoje")

    if not data_rotina:
        data_rotina = datetime.now(_get_tz_br()).strftime("%d/%m/%Y")

    try:
        data_element = wait.until(EC.presence_of_element_located((By.ID, brflow.B_R_data)))
        try:
            data_element.click()
            data_element.send_keys(Keys.CONTROL, "a")
            data_element.send_keys(Keys.DELETE)
        except Exception:
            log.warning("Não foi possível limpar campo de data com clique e teclas, tentando limpar com JavaScript")
            pass

        try:
            drv.execute_script(
                "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', {bubbles: true})); arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                data_element,
                data_rotina,
            )
        except Exception:
            data_element.send_keys(data_rotina)

        _set_status(f"Data da rotina inserida com sucesso: {data_rotina}")
    except TimeoutException:
        log.error("Elemento 'dataRotinaExecucao' não encontrado")
        _registrar_erro("❌ Erro ao navegar BRFlow: elemento de data não encontrado")
        raise
    
    # Clicar em pesquisar
    try:
        pesquisar_button = wait_short.until(EC.element_to_be_clickable((By.XPATH, brflow.B_R_pesquisar)))
        pesquisar_button.click()
        _set_status("Botão de pesquisa clicado")
    except TimeoutException:
        log.error("Botão de pesquisa não encontrado")
        _registrar_erro("❌ Erro ao navegar BRFlow: botão de pesquisa não encontrado")
        raise
    
    _set_progress(55, "BRFlow: menu pronto para processamento")


def _extrair_sufixo_brbr4467(descricao_filtro: str) -> str | None:
    for sufixo in ("1 Dia atrás", "2 Dias atrás", "3 Dias atrás"):
        if sufixo in descricao_filtro:
            return sufixo
    return None


def _log_descricoes_painel(drv, limite: int = 8) -> None:
    try:
        candidatos = drv.find_elements(By.XPATH, "//tr[td[2]]")
        textos = []
        for row in candidatos[:limite]:
            try:
                textos.append(row.find_element(By.XPATH, "./td[2]").text.strip())
            except Exception:
                pass
        if textos:
            log.warning("Descrições visíveis no painel BRFlow (td[2]): %s", textos)
    except Exception:
        pass


def _encontrar_linhas_rotina(drv, descricao_filtro: str, correspondencia_exata: bool = False) -> list:
    """Localiza linhas da tabela de rotinas automáticas pelo texto da coluna de descrição."""
    rows = []
    if correspondencia_exata:
        try:
            rows = drv.find_elements(By.XPATH, f"//tr[normalize-space(td[2])='{descricao_filtro}']")
        except Exception:
            rows = []

        if not rows:
            try:
                candidatos = drv.find_elements(By.XPATH, "//tr[td[2]]")
                rows = [
                    row for row in candidatos
                    if row.find_element(By.XPATH, "./td[2]").text.strip() == descricao_filtro
                ]
            except Exception:
                rows = []
    else:
        try:
            rows = drv.find_elements(By.XPATH, f"//tr[contains(td[2], '{descricao_filtro}')]")
        except Exception:
            rows = []

        if not rows:
            sufixo = _extrair_sufixo_brbr4467(descricao_filtro)
            if sufixo:
                try:
                    rows = drv.find_elements(By.XPATH, f"//tr[contains(td[2], '{sufixo}')]")
                except Exception:
                    rows = []

    if not rows:
        log.warning("Nenhuma rotina encontrada com descrição: %s", descricao_filtro)
        _log_descricoes_painel(drv)

    return rows


def _baixar_arquivos_rotina(drv, descricao_filtro, pasta_destino, correspondencia_exata: bool = False):

    #baixar os arquivos de rotina filtrando pela descrição e movendo para a pasta destino

    tipo_busca = "exata" if correspondencia_exata else "parcial"
    _limpar_downloads_temp_inicio(f"Extração BRFlow ({descricao_filtro})")
    _set_status(f"Procurando rotinas com descrição ({tipo_busca}): {descricao_filtro}")
    log.info("BRFlow: buscando rotina (%s): %s", tipo_busca, descricao_filtro)
    Path(pasta_destino).mkdir(parents=True, exist_ok=True)
    time.sleep(1)
    for arquivo in glob.glob(os.path.join(pasta_destino, "*")):
        try:
            os.remove(arquivo)
        except Exception:
            pass
    arquivos_baixados = []
    try:
        rows = _encontrar_linhas_rotina(drv, descricao_filtro, correspondencia_exata=correspondencia_exata)

        if not rows:
            return []
        
        log.info("BRFlow: encontradas %d linha(s) para '%s'", len(rows), descricao_filtro)
        _set_status(f"Encontradas {len(rows)} linha(s) com a descrição")
        for idx, row in enumerate(rows):
            try:
                download_button = None
                try:
                    download_button = row.find_element(By.XPATH, ".//button | .//a[contains(@class, 'download')] | .//i[contains(@class, 'download')]/..")
                except Exception:
                    pass
                if not download_button:
                    try:
                        download_button = row.find_elements(By.XPATH, ".//button | .//a")[-1]  # Último elemento clicável
                    except Exception:
                        pass
                
                if download_button:
                    drv.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", download_button)
                    time.sleep(0.5)
                    
                    _set_progress(min(64, 58 + (idx * 2)), f"Baixando arquivo {idx + 1}/4")
                    try:
                        download_button.click()
                    except Exception:
                        drv.execute_script("arguments[0].click();", download_button)
                    
                    _set_status(f"Download {idx + 1} iniciado, aguardando conclusão...")

                    arquivo_completo = _aguardar_download_completo(pasta_destino, timeout=TIMEOUT_DOWNLOAD_ROTINA)
                    
                    if arquivo_completo:
                        arquivos_baixados.append(arquivo_completo)
                        _set_status(f"Arquivo {idx + 1} completamente baixado: {os.path.basename(arquivo_completo)}")
                    else:
                        log.warning(f"Timeout esperando arquivo {idx + 1}")
                        _set_status(f"Aviso: Timeout esperando arquivo {idx + 1}")
                else:
                    log.debug(f"Botão de download não encontrado na linha {idx + 1}")
            
            except Exception as e:
                log.error(f"Erro ao baixar arquivo {idx + 1}: {e}")
        _set_status(f"Verificando se todos os {len(arquivos_baixados)} arquivos foram completamente baixados...")
        time.sleep(5)
        for arquivo in arquivos_baixados:
            if os.path.exists(arquivo):
                tamanho = os.path.getsize(arquivo)
                log.debug(f"Arquivo validado: {os.path.basename(arquivo)} ({tamanho} bytes)")
        
        _set_status(f"Todos os {len(arquivos_baixados)} arquivos prontos para combinação")
        return arquivos_baixados
    
    except Exception as e:
        log.error(f"Erro ao procurar rotinas: {e}")
        return []


def _baixar_relatorio_retry_rotina(
    drv,
    botao_xpath: str,
    before_download,
    pasta_download: Path,
    descricao: str = "download do relatório",
    max_tentativas: int = 4,
    on_timeout_retry=None,
    on_pos_click_retry=None,
) -> Path:
    """Clica no botão e aguarda download, repetindo em caso de timeout."""
    ultima_excecao = None
    before = set(before_download or [])
    for tentativa in range(1, max_tentativas + 1):
        # _limpar_downloads_temp_inicio(f"{descricao} (tentativa {tentativa}/{max_tentativas})")
        if tentativa > 1:
            log.warning(f"Retry {tentativa}/{max_tentativas} para {descricao}: reapertando botão após timeout")
            _set_status(f"Timeout ao baixar ({descricao}). Nova tentativa {tentativa}/{max_tentativas}...")
            if callable(on_timeout_retry):
                try:
                    on_timeout_retry(tentativa, max_tentativas)
                except Exception as e:
                    log.warning(f"Falha ao preparar retry de {descricao}: {e}")
        _click_com_js(drv, botao_xpath)
        try:
            arquivo = _aguardar_download_completo(str(pasta_download), timeout=180)
            if arquivo is None:
                raise TimeoutException(f"Arquivo não detectado em {pasta_download}")
            return Path(arquivo)
        except TimeoutException as e:
            ultima_excecao = e
            log.warning(f"Timeout no {descricao} (tentativa {tentativa}/{max_tentativas}).")
            if callable(on_pos_click_retry):
                try:
                    caiu_login = bool(on_pos_click_retry(tentativa, max_tentativas))
                    if caiu_login:
                        log.warning(
                            f"Tela de login detectada após timeout de {descricao} "
                            f"(tentativa {tentativa}/{max_tentativas})"
                        )
                except Exception as err:
                    log.warning(f"Falha ao validar tela de login após timeout de {descricao}: {err}")
            if tentativa < max_tentativas:
                time.sleep(1)
    raise TimeoutException(
        f"Falha no {descricao} após {max_tentativas} tentativas reapertando o botão"
    ) from ultima_excecao
