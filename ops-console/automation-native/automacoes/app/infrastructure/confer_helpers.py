"""
Utilitários compartilhados para automação do sistema Confer.

Usado por bot_production e bot_confer_monitor para evitar duplicação de
navegação Selenium, download e validação de relatórios.
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException

from app.config import okta
from app.infrastructure.selenium_helpers import click_element, send_keys_to_element

log = logging.getLogger("robots.confer_helpers")

LOGIN_MENU_BUTTON_XPATH = "/html/body/app-root/app-login/main/div/div/div/div/div/div/div/button"
CONFER_MENU_ROOT_XPATH = "/html/body/app-root/app-home/main/app-menu"
CONFER_HOME_XPATH = "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[2]/div/div/div[5]/div/div/div/a"


def _noop_status(_msg: str) -> None:
    pass


def _noop_progress(_pct: int, _msg: str = "") -> None:
    pass


def _noop_stop() -> bool:
    return False


def focar_aba_confer(drv, confer_home_xpath: str = CONFER_HOME_XPATH, timeout_sec: int = 20):
    deadline = time.time() + timeout_sec
    ultimo_handle = None

    while time.time() < deadline:
        handles = list(drv.window_handles)
        if not handles:
            time.sleep(0.2)
            continue

        for handle in reversed(handles):
            drv.switch_to.window(handle)
            ultimo_handle = handle
            url = (drv.current_url or "").lower()
            titulo = (drv.title or "").lower()
            if "confer" in url or "confer" in titulo:
                return handle

        for handle in reversed(handles):
            drv.switch_to.window(handle)
            ultimo_handle = handle
            if drv.find_elements(By.XPATH, confer_home_xpath):
                return handle

        time.sleep(0.3)

    if ultimo_handle:
        drv.switch_to.window(ultimo_handle)
        return ultimo_handle

    raise RuntimeError("Nenhuma aba disponível para continuar a automação do Confer")


def fechar_abas_exceto(drv, handle_manter, logger: Optional[logging.Logger] = None):
    logger = logger or log
    handles = list(drv.window_handles)
    for handle in handles:
        if handle == handle_manter:
            continue
        try:
            drv.switch_to.window(handle)
            drv.close()
        except Exception as e:
            logger.warning(f"Falha ao fechar aba {handle}: {e}")

    if handle_manter in drv.window_handles:
        drv.switch_to.window(handle_manter)


def diagnosticar_tela_confer(drv) -> dict:
    """Coleta sinais da tela atual do Confer para decidir se é login ou menu."""
    try:
        url_atual = str(drv.current_url or "").lower()
    except Exception:
        url_atual = ""

    menu_visivel = False
    try:
        for menu in drv.find_elements(By.XPATH, CONFER_MENU_ROOT_XPATH):
            try:
                if menu.is_displayed():
                    menu_visivel = True
                    break
            except Exception:
                continue
    except Exception:
        pass

    botao_login_visivel = False
    try:
        botoes_login = drv.find_elements(By.XPATH, LOGIN_MENU_BUTTON_XPATH)
        for botao in botoes_login:
            try:
                if botao.is_displayed() and botao.is_enabled():
                    botao_login_visivel = True
                    break
            except Exception:
                continue
    except Exception:
        pass

    login_url = ("app-login" in url_atual) or ("/login" in url_atual)

    if not botao_login_visivel:
        esta_login = False
        motivo = "botao_login_invisivel"
    elif menu_visivel:
        esta_login = False
        motivo = "menu_visivel_prioriza_fluxo"
    elif login_url:
        esta_login = True
        motivo = "url_login_detectada"
    else:
        esta_login = True
        motivo = "botao_login_visivel_sem_menu"

    return {
        "url": url_atual,
        "menu_visivel": menu_visivel,
        "botao_login_visivel": botao_login_visivel,
        "login_url": login_url,
        "esta_login": esta_login,
        "motivo": motivo,
    }


def recuperar_menu_confer_se_tela_login(
    drv,
    contexto: str = "",
    *,
    status_fn: Callable[[str], None] = _noop_status,
    timeout_driver: int = 8,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Se o Confer cair na tela de login, clica no botão de retorno ao menu."""
    logger = logger or log
    estado_tela = diagnosticar_tela_confer(drv)
    sufixo_contexto = f" ({contexto})" if contexto else ""
    url_resumida = str(estado_tela.get("url") or "")[:120]
    logger.info(
        "[Confer][LoginCheck]%s url=%s menu_visivel=%s botao_login_visivel=%s login_url=%s decisao_login=%s motivo=%s",
        sufixo_contexto,
        url_resumida,
        estado_tela.get("menu_visivel"),
        estado_tela.get("botao_login_visivel"),
        estado_tela.get("login_url"),
        estado_tela.get("esta_login"),
        estado_tela.get("motivo"),
    )

    if not estado_tela.get("esta_login"):
        return False

    inicio_recuperacao = time.time()
    try:
        botoes = drv.find_elements(By.XPATH, LOGIN_MENU_BUTTON_XPATH)
    except Exception:
        return False

    botao_login = None
    for botao in botoes:
        try:
            if botao.is_displayed() and botao.is_enabled():
                botao_login = botao
                break
        except Exception:
            continue

    if botao_login is None:
        return False

    status_fn(f"Confer na tela de login{sufixo_contexto}. Retornando para o menu...")
    logger.warning(f"Tela de login detectada no Confer{sufixo_contexto}. Iniciando recuperação de sessão.")

    try:
        click_xpath_com_fallback_js(drv, LOGIN_MENU_BUTTON_XPATH)
    except Exception:
        try:
            drv.execute_script("arguments[0].click();", botao_login)
        except Exception as e:
            logger.warning(f"Falha ao clicar no botão de retorno do login do Confer: {e}")
            return False

    try:
        wait = WebDriverWait(drv, max(timeout_driver, 20))
        wait.until(EC.presence_of_element_located((By.XPATH, CONFER_MENU_ROOT_XPATH)))
        tempo_recuperacao = time.time() - inicio_recuperacao
        logger.info(f"Recuperação de sessão do Confer concluída{sufixo_contexto} em {tempo_recuperacao:.2f}s")
        status_fn(f"Sessão do Confer recuperada{sufixo_contexto} em {tempo_recuperacao:.1f}s")
        return True
    except Exception as e:
        tempo_recuperacao = time.time() - inicio_recuperacao
        logger.warning(f"Botão de login clicado, mas menu do Confer não carregou a tempo: {e}")
        logger.warning(f"Tempo de tentativa de recuperação{sufixo_contexto}: {tempo_recuperacao:.2f}s")
        return False


def fechar_calendario_se_aberto(drv, timeout_sec: int = 4):
    calendario_css = "div.dp-calendar-wrapper"
    body_css = "body"
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        wrappers = drv.find_elements(By.CSS_SELECTOR, calendario_css)
        visiveis = [w for w in wrappers if w.is_displayed()]
        if not visiveis:
            return

        try:
            drv.find_element(By.CSS_SELECTOR, body_css).send_keys(Keys.ESCAPE)
        except Exception:
            pass

        try:
            drv.execute_script("document.body.click();")
        except Exception:
            pass

        time.sleep(0.2)


def click_xpath_com_fallback_js(drv, xpath: str):
    try:
        click_element(drv, "xpath", xpath)
    except ElementClickInterceptedException:
        elemento = drv.find_element(By.XPATH, xpath)
        drv.execute_script("arguments[0].click();", elemento)
    except Exception:
        elemento = drv.find_element(By.XPATH, xpath)
        drv.execute_script("arguments[0].click();", elemento)


def aguardar_arquivo_estavel(caminho: Path, timeout_sec: int = 30) -> bool:
    deadline = time.time() + timeout_sec
    tamanho_anterior = -1
    contador_estavel = 0

    while time.time() < deadline:
        if not caminho.exists():
            time.sleep(0.5)
            continue

        tamanho_atual = caminho.stat().st_size
        if tamanho_atual > 0 and tamanho_atual == tamanho_anterior:
            contador_estavel += 1
            if contador_estavel >= 3:
                return True
        else:
            contador_estavel = 0

        tamanho_anterior = tamanho_atual
        time.sleep(0.5)

    return False


def normalizar_sufixo_nome(valor: str) -> str:
    texto = str(valor or "").strip()
    permitidos = []
    for ch in texto:
        if ch.isalnum() or ch in ("-", "_"):
            permitidos.append(ch)
    if not permitidos:
        return "item"
    return "".join(permitidos)


def renomear_download_com_sufixo(caminho: Path, sufixo: str, logger: Optional[logging.Logger] = None) -> Path:
    logger = logger or log
    sufixo_ok = normalizar_sufixo_nome(sufixo)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    novo_nome = f"{sufixo_ok}.csv"
    novo_caminho = caminho.parent / novo_nome

    contador = 1
    while novo_caminho.exists():
        novo_caminho = caminho.parent / f"relatorio_{sufixo_ok}_{timestamp}_{contador}.csv"
        contador += 1

    try:
        caminho.rename(novo_caminho)
        return novo_caminho
    except Exception as e:
        logger.warning(f"Falha ao renomear arquivo {caminho.name}: {e}")
        return caminho


def limpar_pasta_download_confer(pasta: Path, logger: Optional[logging.Logger] = None) -> None:
    """Limpa a pasta temporária utilizada para downloads do CONFER."""
    logger = logger or log
    try:
        pasta = Path(pasta)
        pasta.mkdir(parents=True, exist_ok=True)
        for item in pasta.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink(missing_ok=True)
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
            except Exception as e:
                logger.warning(f"[CONFER] falha ao remover {item}: {e}")
        logger.info(f"[CONFER] pasta limpa: {pasta}")
    except Exception as e:
        logger.warning(f"[CONFER] erro ao limpar pasta {pasta}: {e}")


def obter_arquivo_recente_confer(pasta: Path, inicio_download: float, logger: Optional[logging.Logger] = None):
    """Retorna o arquivo mais recente criado após `inicio_download`, ou None."""
    logger = logger or log
    try:
        pasta = Path(pasta)
        if not pasta.exists():
            return None
        candidatos = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xls")]
        if not candidatos:
            return None
        mais_recente = max(candidatos, key=lambda f: f.stat().st_ctime)
        criado = mais_recente.stat().st_ctime
        if criado >= (inicio_download - 0.5):
            return mais_recente
    except Exception as e:
        logger.warning(f"[CONFER] erro ao obter arquivo recente: {e}")
    return None


def validar_arquivo_confer(df: pd.DataFrame, data_esperada, logger: Optional[logging.Logger] = None) -> bool:
    """Valida se o DataFrame contém registros da `data_esperada`."""
    logger = logger or log
    try:
        if df is None or df.empty:
            return False

        if hasattr(data_esperada, "date"):
            target_date = data_esperada.date()
        else:
            s = str(data_esperada or "").strip()
            target_date = None
            for fmt in ("%Y%m%d", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    target_date = datetime.strptime(s, fmt).date()
                    break
                except Exception:
                    continue
            if target_date is None:
                try:
                    target_date = pd.to_datetime(s, dayfirst=True, errors="coerce").date()
                except Exception:
                    return False

        col_candidatas = []
        for col in df.columns:
            nome = str(col).strip().lower()
            if any(k in nome for k in ("data", "data/hora", "data hora", "dat", "dt")):
                col_candidatas.append(col)

        if not col_candidatas:
            for col in df.columns:
                try:
                    serie = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
                    if serie.notna().any():
                        col_candidatas.append(col)
                except Exception:
                    continue

        for col in col_candidatas:
            try:
                serie = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
                if serie.notna().any():
                    datas = serie.dt.date
                    if (datas == target_date).any():
                        return True
            except Exception:
                continue

        return False
    except Exception as e:
        logger.warning(f"[CONFER] validação de arquivo falhou: {e}")
        return False


def navegar_busca_confer(
    drv,
    *,
    status_fn: Callable[[str], None] = _noop_status,
    progress_fn: Callable[[int, str], None] = _noop_progress,
    timeout_driver: int = 8,
    confer_home_xpath: str = CONFER_HOME_XPATH,
):
    wait = WebDriverWait(drv, timeout_driver)
    wait_short = WebDriverWait(drv, 6)

    status_fn("Pesquisando 'confer' no Okta")
    progress_fn(60, "Confer: buscando aplicacao")
    try:
        wait_short.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))
    except TimeoutException:
        wait.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))

    send_keys_to_element(drv, "id", okta.O_pesquisar, "confer")
    drv.find_element(By.ID, okta.O_pesquisar).send_keys(Keys.ENTER)

    progress_fn(75, "Confer: abrindo sistema")
    confer_handle = focar_aba_confer(drv, confer_home_xpath)
    fechar_abas_exceto(drv, confer_handle)
    progress_fn(90, "Confer: aguardando carregamento")
    try:
        wait_short.until(EC.presence_of_element_located((By.XPATH, confer_home_xpath)))
    except TimeoutException:
        wait.until(EC.presence_of_element_located((By.XPATH, confer_home_xpath)))


def baixar_com_retry_seguro_confer(
    drv,
    botao_xpath: str,
    pasta_download: Path,
    data_esperada,
    descricao: str = "download do relatório CONFER",
    max_tentativas: int = 4,
    timeout_por_tentativa: int = 20,
    polling: float = 0.5,
    validar_por_dataframe_fn=None,
    on_timeout_retry=None,
    clear_before_attempt: bool = True,
    *,
    status_fn: Callable[[str], None] = _noop_status,
    stop_check: Callable[[], bool] = _noop_stop,
    ler_csv_fn=None,
    logger: Optional[logging.Logger] = None,
):
    """Fluxo seguro para downloads do CONFER com validação de conteúdo."""
    logger = logger or log
    ultima_ex = None
    pasta_download = Path(pasta_download)
    motivo_falha = "timeout/nenhum arquivo"

    for tentativa in range(1, max_tentativas + 1):
        inicio_tentativa = time.time()
        status_fn(f"[CONFER] {descricao} - tentativa {tentativa}/{max_tentativas}")
        logger.info(f"[CONFER] Iniciando tentativa {tentativa}/{max_tentativas} para {descricao}")

        if clear_before_attempt:
            try:
                limpar_pasta_download_confer(pasta_download, logger=logger)
            except Exception as e:
                logger.warning(f"[CONFER] falha ao limpar pasta antes da tentativa: {e}")

        try:
            click_xpath_com_fallback_js(drv, botao_xpath)
        except Exception as e:
            logger.warning(f"[CONFER] falha ao clicar no botão ({descricao}): {e}")

        deadline = time.time() + float(timeout_por_tentativa)
        motivo_falha = "timeout/nenhum arquivo"

        while time.time() < deadline:
            if stop_check():
                raise TimeoutException("Execução cancelada durante download do CONFER")
            try:
                candidato = obter_arquivo_recente_confer(pasta_download, inicio_tentativa, logger=logger)
                if candidato:
                    nome = str(candidato.name)
                    if nome.endswith(".crdownload"):
                        time.sleep(polling)
                        continue

                    try:
                        if not aguardar_arquivo_estavel(candidato, timeout_sec=30):
                            time.sleep(polling)
                            continue
                    except Exception:
                        time.sleep(polling)
                        continue

                    df = None
                    if callable(ler_csv_fn):
                        try:
                            df, _ = ler_csv_fn(str(candidato))
                            if df is not None and len(df) > 500:
                                df = df.head(500)
                        except Exception:
                            df = None
                    if df is None:
                        try:
                            df = pd.read_csv(str(candidato), encoding="utf-8-sig", sep=";", engine="python", nrows=500)
                        except Exception:
                            try:
                                df = pd.read_csv(str(candidato), engine="python", nrows=500)
                            except Exception:
                                df = None

                    valid_fn = validar_por_dataframe_fn or validar_arquivo_confer
                    valido = False
                    if df is not None:
                        try:
                            valido = bool(valid_fn(df, data_esperada))
                        except Exception:
                            valido = False

                    if valido:
                        tempo = time.time() - inicio_tentativa
                        logger.info(
                            f"[CONFER] Arquivo válido detectado: {candidato.name} "
                            f"(tentativa {tentativa}) tempo={tempo:.2f}s"
                        )
                        status_fn(f"[CONFER] {descricao}: arquivo válido encontrado: {candidato.name}")
                        return Path(candidato)

                    motivo_falha = "conteúdo inválido (data)"
                    logger.warning(f"[CONFER] Arquivo encontrado, porém inválido para {descricao}: {candidato.name}")
                    try:
                        candidato.unlink(missing_ok=True)
                    except Exception:
                        pass
                    break
                time.sleep(polling)
            except Exception as e:
                logger.warning(f"[CONFER] erro ao procurar arquivo durante tentativa {tentativa}: {e}")
                time.sleep(polling)

        dur = time.time() - inicio_tentativa
        logger.warning(f"[CONFER] tentativa {tentativa} finalizada em {dur:.2f}s - motivo: {motivo_falha}")
        if callable(on_timeout_retry):
            try:
                on_timeout_retry(tentativa, max_tentativas)
            except Exception as e:
                logger.warning(f"[CONFER] on_timeout_retry falhou: {e}")
        ultima_ex = TimeoutException(f"Falha no {descricao} (tentativa {tentativa}): {motivo_falha}")
        if tentativa < max_tentativas:
            continue

    logger.error(f"[CONFER] {descricao} falhou após {max_tentativas} tentativas - ultimo motivo: {motivo_falha}")
    raise ultima_ex or TimeoutException(f"Falha no {descricao} após {max_tentativas} tentativas")
