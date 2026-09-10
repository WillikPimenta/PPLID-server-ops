"""Tarefas Confer: produção, log eventos e sessões."""
from __future__ import annotations

import csv
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)

from app.config import *
from app.infrastructure.confer_helpers import (
    CONFER_HOME_XPATH,
    click_xpath_com_fallback_js as _click_com_js,
    click_xpath_com_fallback_js as _clique_xpath_js,
    fechar_calendario_se_aberto as _fechar_calendario_confer,
    fechar_calendario_se_aberto as _fechar_calendario_confer_rotina,
    focar_aba_confer as _focar_aba_confer,
    recuperar_menu_confer_se_tela_login as _recuperar_menu_confer_se_tela_login_rotina,
    renomear_download_com_sufixo as _renomear_download_com_sufixo_confer,
)
from app.infrastructure.csv_processing import read_csv as cp_read_csv
from app.infrastructure.selenium_helpers import click_element, login_okta_resiliente, send_keys_to_element
from app.bots.rotina.io import (
    _corrigir_texto_mojibake,
    _ler_arquivo_csv_robusto,
    _ler_csv_tratamento,
    _limpar_downloads_temp_inicio,
    _normalizar_nome_coluna,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
    verifica_usuario,
)
from app.bots.rotina.constants import (
    BUSCA_PROTOCOLO_ABA_GERENCIAL,
    BUSCA_PROTOCOLO_COLUNAS_MAP,
    BUSCA_PROTOCOLO_COLUNAS_SAIDA,
    BUSCA_PROTOCOLO_ETAPA_FILTRO,
    DOWNLOADS_TEMP_ROTINA,
)
from app.bots.rotina.selenium_brflow import _baixar_relatorio_retry_rotina
from app.bots.rotina.state import _registrar_erro, _set_progress, _set_status, parar_event
from app.bots.rotina.tasks.monitor import _normalizar_df_monitor_sessoes

log = logging.getLogger("robots.bot_rotina")

_CONFER_LOGIN_BUTTON_XPATH = "/html/body/app-root/app-login/main/div/div/div/div/div/div/div/button"
_CONFER_MENU_ROOT_XPATH = "/html/body/app-root/app-home/main/app-menu"
MAX_RETRY_DOWNLOAD_CONFER_ROTINA = max(1, int(os.getenv("CONFER_DOWNLOAD_RETRY", "4") or "4"))

# ============================================================================
# EXTRAÇÃO DO RELATÓRIO DE PRODUÇÃO DO CONFER
# ============================================================================

def _tratar_producao_confer_df(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica o tratamento da produção_confer conforme regra de negócio."""
    if df is None or df.empty:
        return pd.DataFrame()

    df_proc = df.copy()
    df_proc = df_proc.rename(columns={
        "Data/Hora da Conferência": "datAnalise",
        "Tempo de Análise": "numTempoAnalise",
        "Matrícula do Colaborador": "desMatricula",
        "Etapa": "nomEtapa",
    })

    colunas_obrigatorias = {"datAnalise", "numTempoAnalise", "desMatricula", "nomEtapa"}
    faltantes = [c for c in colunas_obrigatorias if c not in df_proc.columns]
    if faltantes:
        log.warning(f"Produção confer tratada ignorada, colunas ausentes: {faltantes}")
        return pd.DataFrame()

    df_proc["Data"] = (
        df_proc["datAnalise"]
        .astype(str)
        .str.slice(0, 10)
        .str.replace(r"(\d{2})/(\d{2})/(\d{4})", r"\3-\2-\1", regex=True)
    )
    df_proc["Hora"] = df_proc["datAnalise"].astype(str).str.slice(11, 13)

    df_proc["nomCliente"] = "CLARO - FORMALIZAÇÃO"
    df_proc["nomWorkflow"] = "CLARO - CONFER"

    df_proc["matricula"] = (
        df_proc["desMatricula"]
        .astype(str)
        .str.strip()
        .str.upper()
    )
    df_proc = df_proc.drop(columns=["desMatricula"], errors="ignore")

    df_proc["numTempoAnalise"] = pd.to_timedelta(
        df_proc["numTempoAnalise"], errors="coerce"
    ).fillna(pd.Timedelta(seconds=0))

    df_proc = df_proc[df_proc["numTempoAnalise"] > pd.Timedelta(seconds=0)]
    if df_proc.empty:
        return pd.DataFrame()

    df_proc = df_proc.sort_values(by=["matricula", "datAnalise"])

    resultado = (
        df_proc
        .groupby(
            ["Data", "Hora", "matricula", "nomCliente", "nomWorkflow", "nomEtapa"]
        )
        .agg(
            tempoAnalise=("numTempoAnalise", "sum"),
            contagem=("matricula", "count")
        )
        .reset_index()
    )

    resultado["tempoAnalise"] = (
        resultado["tempoAnalise"]
        .dt.total_seconds()
        .astype(int)
    )

    return resultado


def _tratar_busca_protocolo_confer_df(df: pd.DataFrame) -> pd.DataFrame:
    """Filtra Reclassificação e mantém colunas do relatório busca protocolo."""
    if df is None or df.empty:
        return pd.DataFrame()

    df_proc = df.copy()
    if "Etapa" not in df_proc.columns:
        log.warning("Busca protocolo Confer ignorada: coluna Etapa ausente")
        return pd.DataFrame()

    etapa_norm = df_proc["Etapa"].astype(str).str.strip()
    df_proc = df_proc[etapa_norm == BUSCA_PROTOCOLO_ETAPA_FILTRO].copy()
    if df_proc.empty:
        return pd.DataFrame()

    faltantes = [c for c in BUSCA_PROTOCOLO_COLUNAS_MAP if c not in df_proc.columns]
    if faltantes:
        log.warning(f"Busca protocolo Confer ignorada, colunas ausentes: {faltantes}")
        return pd.DataFrame()

    df_proc = df_proc.drop(columns=["Data/Hora do Cadastro"], errors="ignore")
    df_proc = df_proc.rename(columns=BUSCA_PROTOCOLO_COLUNAS_MAP)
    return df_proc[BUSCA_PROTOCOLO_COLUNAS_SAIDA].copy()


def _listar_brutos_confer_mes(yyyymm: str) -> list[Path]:
    """Lista brutos diários do Confer cujo nome contém o prefixo + YYYYMM."""
    arquivos: list[Path] = []
    for ext in (".parquet", ".csv"):
        arquivos.extend(
            PASTA_PRODUCAO_CONFER.glob(f"{PREFIXO_CONF_BRUTO}{yyyymm}*{ext}")
        )
    return sorted(set(arquivos), key=lambda p: p.name)


def _montar_nome_busca_protocolo_mes(yyyymm: str) -> str:
    return f"{PREFIXO_CONFER_BUSCAR_PROTOCOLO_TRATADO}{yyyymm}.csv"


def _montar_nome_busca_protocolo_mes_gerencial(yyyymm: str) -> str:
    return f"{PREFIXO_CONFER_BUSCAR_PROTOCOLO_TRATADO}{yyyymm}.xlsx"


def _consolidar_busca_protocolo_confer_mes(data_d1) -> Path | None:
    """Une brutos do mês, trata Reclassificação e salva CSV (Bots) + XLSX (gerencial)."""
    yyyymm = data_d1.strftime("%Y%m")
    brutos = _listar_brutos_confer_mes(yyyymm)
    if not brutos:
        log.info("Busca protocolo Confer: nenhum bruto encontrado para %s", yyyymm)
        return None

    dfs: list[pd.DataFrame] = []
    for caminho in brutos:
        try:
            df = _ler_csv_tratamento(str(caminho))
            if df is not None and not df.empty:
                dfs.append(df)
        except Exception as exc:
            log.warning("Busca protocolo Confer: falha ao ler %s: %s", caminho.name, exc)

    if not dfs:
        log.info("Busca protocolo Confer: brutos do mês %s sem dados legíveis", yyyymm)
        return None

    df_mes = pd.concat(dfs, ignore_index=True)
    df_tratado = _tratar_busca_protocolo_confer_df(df_mes)
    if df_tratado.empty:
        log.info(
            "Busca protocolo Confer: nenhuma linha Reclassificação no mês %s (%s brutos)",
            yyyymm,
            len(brutos),
        )
        return None

    PASTA_CONFER_BUSCAR_PROTOCOLO_TRATADO.mkdir(parents=True, exist_ok=True)
    PASTA_BUSCA_PROTOCOLOS_GERENCIAL.mkdir(parents=True, exist_ok=True)
    nome_arquivo = _montar_nome_busca_protocolo_mes(yyyymm)
    nome_gerencial = _montar_nome_busca_protocolo_mes_gerencial(yyyymm)
    caminho = PASTA_CONFER_BUSCAR_PROTOCOLO_TRATADO / nome_arquivo
    caminho_gerencial = PASTA_BUSCA_PROTOCOLOS_GERENCIAL / nome_gerencial
    df_tratado.to_csv(caminho, sep=";", index=False, encoding="cp1252")
    df_tratado.to_excel(
        caminho_gerencial,
        index=False,
        engine="openpyxl",
        sheet_name=BUSCA_PROTOCOLO_ABA_GERENCIAL,
    )
    log.info(
        "Busca protocolo Confer mensal salva | mes=%s | brutos=%s | linhas=%s | bots=%s | gerencial=%s",
        yyyymm,
        len(brutos),
        f"{len(df_tratado):,}",
        caminho,
        caminho_gerencial,
    )
    _set_status(
        f"Busca protocolo Confer | {nome_arquivo} + {nome_gerencial} | {len(df_tratado):,} linhas"
    )
    print(f"ROTINA_BRUTO_SAVED|confer_busca|{caminho.resolve()}", flush=True)
    return caminho


def _baixar_relatorio_producao_confer(drv):
    """
    Navega até o Confer via Okta e baixa o relatório de produção do dia,
    salvando o resultado como parquet em DEFAULT_SHAREPOINT_BOTS / 'confer'.
    """
    wait = WebDriverWait(drv, TIMEOUT_DRIVER_ROTINA)
    wait_short = WebDriverWait(drv, TIMEOUT_SHORT_ROTINA)
    pasta_destino = PASTA_PRODUCAO_CONFER
    pasta_destino.mkdir(parents=True, exist_ok=True)
    pasta_destino_tratado = PASTA_PRODUCAO_CONFER_TRATADO
    pasta_destino_tratado.mkdir(parents=True, exist_ok=True)
    pasta_temp = DOWNLOADS_TEMP_ROTINA

    confer_home_xpath = "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[2]/div/div/div[5]/div/div/div/a"

    _set_status("Navegando ao Confer para extração do relatório de produção")
    _set_progress(80, "Confer: acessando sistema")
    try:
        _limpar_downloads_temp_inicio("Extração Confer produção")
        # Retornar ao Okta e buscar pelo Confer
        drv.get(okta.O_LINK)
        matricula = os.getenv("NIVEL_USER") or os.getenv("OKTA_USER")
        senha = os.getenv("NIVEL_PASS") or os.getenv("OKTA_PASS")
        _set_status("Reautenticando no Okta para acessar o Confer")
        login_okta_resiliente(drv, matricula, senha, timeout=max(TIMEOUT_DRIVER_ROTINA, TIMEOUT_SHORT_ROTINA))

        try:
            wait_short.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))
        except TimeoutException:
            wait.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))

        send_keys_to_element(drv, "id", okta.O_pesquisar, "confer")
        drv.find_element(By.ID, okta.O_pesquisar).send_keys(Keys.ENTER)

        # Aguardar nova aba do Confer abrir
        try:
            wait_short.until(lambda d: len(d.window_handles) > 1)
        except TimeoutException:
            wait.until(lambda d: len(d.window_handles) > 1)

        confer_handle = _focar_aba_confer(drv, confer_home_xpath)

        # Fechar abas extras, manter apenas Confer
        for handle in list(drv.window_handles):
            if handle == confer_handle:
                continue
            try:
                drv.switch_to.window(handle)
                drv.close()
            except Exception:
                pass
        drv.switch_to.window(confer_handle)
        _set_progress(82, "Confer: navegando no menu")

        # Aguardar carregamento do menu do Confer
        try:
            wait_short.until(EC.presence_of_element_located((By.XPATH, confer_home_xpath)))
        except TimeoutException:
            wait.until(EC.presence_of_element_located((By.XPATH, confer_home_xpath)))

        # Clicar no menu de Gestão → Relatórios
        _clique_xpath_js(drv, confer_home_xpath)
        try:
            wait_short.until(EC.presence_of_element_located((By.XPATH, "/html/body/app-root/app-home/main/app-gestao/div/div[1]")))
        except TimeoutException:
            wait.until(EC.presence_of_element_located((By.XPATH, "/html/body/app-root/app-home/main/app-gestao/div/div[1]")))
        click_element(drv, "xpath", "/html/body/app-root/app-home/main/app-gestao/div/div[1]")
        time.sleep(1)

        # Preencher datas em d-1 (mesma regra do monitor de eventos)
        data_d1 = _obter_data_base_execucao() - timedelta(days=1)
        data_atual = data_d1.strftime("%d/%m/%Y")
        send_keys_to_element(
            drv, "xpath",
            "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[1]/div[1]/div[2]/div[2]/app-datepicker/div/div/div/input",
            data_atual,
        )
        send_keys_to_element(
            drv, "xpath",
            "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[1]/div[1]/div[2]/div[3]/app-datepicker/div/div/div/input",
            data_atual,
        )
        _fechar_calendario_confer(drv)

        # Selecionar tipo de relatório (radio)
        _clique_xpath_js(drv, "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[1]/div[2]/app-radio/div/div[2]/div/div[1]/div/input")

        # Limpar pasta temp e iniciar download
        _limpar_downloads_temp_inicio("Extração Confer produção")
        botao_download_xpath = "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[2]/div/button/i"
        before_download = {p.name for p in pasta_temp.iterdir()} if pasta_temp.exists() else set()

        _set_status("Aguardando download do relatório de produção Confer")
        _set_progress(84, "Confer: baixando relatorio")
        try:
            arquivo_baixado = baixar_com_retry_seguro_confer(
                drv,
                botao_download_xpath,
                pasta_temp,
                data_d1,
                descricao="download do relatório de produção",
                max_tentativas=min(5, MAX_RETRY_DOWNLOAD_CONFER_ROTINA),
                timeout_por_tentativa=30,
            )
        except TimeoutException:
            arquivo_baixado = None
        click_element(drv, "xpath", "/html/body/app-root/app-home/header/nav/div/div[2]/ul/li[3]/i")
        if not arquivo_baixado:
            log.warning("Relatório de produção Confer: nenhum arquivo detectado no download")
            _set_status("Confer: timeout no download do relatório de produção")
            _registrar_erro("⚠️ Confer sem arquivo: timeout aguardando relatório de produção")
            return False

        # Ler e salvar como parquet
        df = _ler_arquivo_csv_robusto(arquivo_baixado)
        if df is None or df.empty:
            log.warning(f"Relatório de produção Confer: arquivo inválido ou vazio ({Path(arquivo_baixado).name})")
            _set_status("Confer: relatório de produção vazio ou inválido")
            return False

        data_ref = data_d1.strftime("%Y%m%d")
        arquivo_parquet = pasta_destino / f"{PREFIXO_CONF_BRUTO}{data_ref}.parquet"
        df_parquet = _preparar_dataframe_para_parquet(df)
        df_parquet.to_parquet(str(arquivo_parquet), index=False, compression="snappy")
        log.info(f"✅ Produção Confer bruta salva: {arquivo_parquet.name} | Destino: {os.path.abspath(arquivo_parquet)}")

        _consolidar_busca_protocolo_confer_mes(data_d1)

        df_tratado = _tratar_producao_confer_df(df)
        if not df_tratado.empty:
            arquivo_parquet_tratado = pasta_destino_tratado / f"{PREFIXO_CONF_TRATADO}{data_ref}.parquet"
            df_tratado = _preparar_dataframe_para_parquet(df_tratado)
            df_tratado.to_parquet(str(arquivo_parquet_tratado), index=False, compression="snappy")
            log.info(f"✅ Produção Confer tratada salva: {arquivo_parquet_tratado.name} ({len(df_tratado)} linhas) | Destino: {os.path.abspath(arquivo_parquet_tratado)}")

        try:
            os.remove(arquivo_baixado)
        except Exception as exc:
            log.warning(f"Erro ao remover arquivo temporário do Confer: {exc}")

        log.info(f"✓ Relatório de produção Confer salvo: {arquivo_parquet.name} ({len(df)} linhas)")
        _set_progress(86, "Confer: concluido")
        _set_status(f"Confer: relatório de produção salvo ({len(df)} linhas)")
        return True

    except Exception as exc:
        log.error(f"Erro ao baixar relatório de produção do Confer: {exc}", exc_info=True)
        _set_status(f"Erro na extração do Confer: {exc}")
        _registrar_erro(f"❌ Erro Confer produção: {str(exc)[:120]}")
        return False


def _diagnosticar_tela_confer_rotina(drv) -> dict:
    """Coleta sinais da tela atual do Confer para decidir se é login ou menu."""
    try:
        url_atual = str(drv.current_url or "").lower()
    except Exception:
        url_atual = ""

    menu_visivel = False
    try:
        for menu in drv.find_elements(By.XPATH, _CONFER_MENU_ROOT_XPATH):
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
        botoes_login = drv.find_elements(By.XPATH, _CONFER_LOGIN_BUTTON_XPATH)
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

    # Regras de decisão:
    # 1) Sem botão de login visível => não é tela de login.
    # 2) Com menu visível => prioriza menu (não recupera).
    # 3) Botão visível + URL de login => é login.
    # 4) Botão visível e menu não visível => provável login.
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


def _esta_na_tela_login_confer_rotina(drv) -> bool:
    """Confirma se o Confer está realmente na tela de login."""
    return bool(_diagnosticar_tela_confer_rotina(drv).get("esta_login", False))

def _recuperar_menu_confer_se_tela_login_rotina(drv, contexto: str = "") -> bool:
    """Se o Confer cair na tela de login, clica no botão de retorno ao menu."""
    estado_tela = _diagnosticar_tela_confer_rotina(drv)
    sufixo_contexto = f" ({contexto})" if contexto else ""
    url_resumida = str(estado_tela.get("url") or "")[:120]
    log.info(
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
        botoes = drv.find_elements(By.XPATH, _CONFER_LOGIN_BUTTON_XPATH)
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

    _set_status(f"Confer na tela de login{sufixo_contexto}. Retornando para o menu...")
    log.warning(f"Tela de login detectada no Confer{sufixo_contexto}. Iniciando recuperação de sessão.")

    try:
        try:
            botao_login.click()
        except ElementClickInterceptedException:
            drv.execute_script("arguments[0].click();", botao_login)
    except Exception as e:
        log.warning(f"Falha ao clicar no botão de retorno do login do Confer: {e}")
        return False

    try:
        wait = WebDriverWait(drv, max(TIMEOUT_DRIVER_ROTINA, 20))
        wait.until(EC.presence_of_element_located((By.XPATH, _CONFER_MENU_ROOT_XPATH)))
        tempo_recuperacao = time.time() - inicio_recuperacao
        log.info(f"Recuperação de sessão do Confer concluída{sufixo_contexto} em {tempo_recuperacao:.2f}s")
        _set_status(f"Sessão do Confer recuperada{sufixo_contexto} em {tempo_recuperacao:.1f}s")
        return True
    except Exception as e:
        tempo_recuperacao = time.time() - inicio_recuperacao
        log.warning(f"Botão de login clicado, mas menu do Confer não carregou a tempo: {e}")
        log.warning(f"Tempo de tentativa de recuperação{sufixo_contexto}: {tempo_recuperacao:.2f}s")
        return False


def _resolver_arquivo_confer_prod_d1() -> Path | None:
    """Retorna o bruto de produção Confer D-1 (parquet/csv) se existir."""
    data_d1 = (_obter_data_base_execucao() - timedelta(days=1)).strftime("%Y%m%d")
    candidatos = [
        PASTA_PRODUCAO_CONFER / f"{PREFIXO_CONF_BRUTO}{data_d1}.parquet",
        PASTA_PRODUCAO_CONFER / f"{PREFIXO_CONF_BRUTO}{data_d1}.csv",
    ]
    for candidato in candidatos:
        if candidato.is_file():
            return candidato
    return None


def _extrair_matriculas_confer_d1() -> list:
    """Extrai matrículas do arquivo de produção do Confer já baixado para D-1."""

    arquivo_base = _resolver_arquivo_confer_prod_d1()
    if arquivo_base is None:
        arquivos = sorted(
            PASTA_PRODUCAO_CONFER.glob(f"{PREFIXO_CONF_BRUTO}*.parquet"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if arquivos:
            arquivo_base = arquivos[0]

    if arquivo_base is None:
        log.warning("[Confer][Matriculas] Não foi encontrado arquivo base em PASTA_PRODUCAO_CONFER para extrair matrículas")
        return []

    try:
        if str(arquivo_base).lower().endswith(".parquet"):
            df = pd.read_parquet(str(arquivo_base))
        else:
            df = _ler_csv_tratamento(str(arquivo_base))
    except Exception as exc:
        log.warning(f"[Confer][Matriculas] Falha ao ler arquivo base do Confer ({arquivo_base.name}): {exc}")
        return []

    log.info(f"[Confer][Matriculas] Arquivo base de produção: {arquivo_base.name}")
    log.info(f"[Confer][Matriculas] linhas_lidas={len(df)}")

    coluna = None
    for nome in df.columns:
        normalizado = str(nome).strip().lower()
        if normalizado in ("matrícula do colaborador", "matricula do colaborador", "matricula", "matrícula"):
            coluna = nome
            break

    if coluna is None:
        log.warning("[Confer][Matriculas] Coluna de matrícula não encontrada no arquivo base do Confer")
        return []

    matriculas = []
    vistas = set()
    invalidas = 0
    for valor in df[coluna].fillna("").astype(str):
        matricula = valor.strip()
        if not matricula:
            invalidas += 1
            continue
        if matricula in vistas:
            continue
        vistas.add(matricula)
        matriculas.append(matricula)

    log.info(f"[Confer][Matriculas] matriculas_unicas={len(matriculas)}")
    log.info(f"[Confer][Matriculas] matriculas_invalidas_descartadas={invalidas}")
    log.debug(f"[Confer][Matriculas] primeiras_matriculas={matriculas[:10]}")
    return matriculas


def limpar_pasta_download_confer(pasta: Path) -> None:
    """Limpa completamente a pasta temporária específica do CONFER.

    Garante que nenhum arquivo antigo permaneça entre tentativas.
    """
    try:
        pasta = Path(pasta)
        pasta.mkdir(parents=True, exist_ok=True)
        for item in pasta.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                log.warning(f"[CONFER] falha ao remover {item}: {e}")
        log.info(f"[CONFER] pasta limpa: {pasta}")
    except Exception as e:
        log.warning(f"[CONFER] erro ao limpar pasta {pasta}: {e}")


def obter_arquivo_recente_confer(pasta: Path, inicio_download: float):
    """Retorna o arquivo mais recente criado após `inicio_download`, ou None."""
    try:
        pasta = Path(pasta)
        if not pasta.exists():
            return None
        candidatos = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in ('.csv', '.xlsx', '.xls')]
        if not candidatos:
            return None
        mais_recente = max(candidatos, key=lambda f: f.stat().st_ctime)
        criado = mais_recente.stat().st_ctime
        if criado >= (inicio_download - 0.5):
            return mais_recente
    except Exception as e:
        log.warning(f"[CONFER] erro ao obter arquivo recente: {e}")
    return None


def validar_arquivo_confer(df: pd.DataFrame, data_esperada) -> bool:
    """Valida se o DataFrame contém registros da `data_esperada`.

    `data_esperada` pode ser `datetime.date`, `datetime` ou string nos formatos
    YYYYMMDD, YYYY-MM-DD, DD/MM/YYYY.
    """
    try:
        if df is None or df.empty:
            return False

        # Normalizar data esperada
        if hasattr(data_esperada, 'date'):
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
                    target_date = pd.to_datetime(s, dayfirst=True, errors='coerce').date()
                except Exception:
                    return False

        # Detectar colunas candidatas com datas
        col_candidatas = []
        for col in df.columns:
            nome = str(col).strip().lower()
            if any(k in nome for k in ("data", "data/hora", "data hora", "dat", "dt")):
                col_candidatas.append(col)

        # Heurística: se nenhuma coluna tem nome com 'data', tentar parsing rápido
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
        log.warning(f"[CONFER] validação de arquivo falhou: {e}")
        return False


def baixar_com_retry_seguro_confer(
    drv,
    botao_xpath: str,
    pasta_download: Path,
    data_esperada,
    descricao: str = "download do relatório CONFER",
    max_tentativas: int = 5,
    timeout_por_tentativa: int = 30,
    polling: float = 0.5,
    validar_por_dataframe_fn = None,
    on_timeout_retry=None,
    clear_before_attempt: bool = True,
):
    """Fluxo seguro e rápido para downloads do CONFER.

    Regras principais:
    - Limpa a pasta antes de cada tentativa (evita reutilizar arquivos antigos)
    - Timeout por tentativa curto (padrão 20s)
    - Máximo `max_tentativas` tentativas
    - Identifica arquivo mais recente criado após o clique
    - Valida conteúdo via `validar_arquivo_confer` (ou `validar_por_dataframe_fn`)
    Retorna `Path` do arquivo válido ou lança `TimeoutException`.
    """
    ultima_ex = None
    pasta_download = Path(pasta_download)
    for tentativa in range(1, max_tentativas + 1):
        inicio_tentativa = time.time()
        _set_status(f"[CONFER] {descricao} - tentativa {tentativa}/{max_tentativas}")
        log.info(f"[CONFER] Iniciando tentativa {tentativa}/{max_tentativas} para {descricao}")

        # Limpar pasta antes da tentativa (opcional)
        if clear_before_attempt:
            try:
                limpar_pasta_download_confer(pasta_download)
            except Exception as e:
                log.warning(f"[CONFER] falha ao limpar pasta antes da tentativa: {e}")

        # Clicar no botão (fallback JS dentro de _click_com_js)
        try:
            _click_com_js(drv, botao_xpath)
        except Exception as e:
            log.warning(f"[CONFER] falha ao clicar no botão ({descricao}): {e}")

        deadline = time.time() + float(timeout_por_tentativa)
        motivo_falha = "timeout/nenhum arquivo"

        while time.time() < deadline:
            if parar_event.is_set():
                raise TimeoutException("Execução cancelada durante download do CONFER")
            try:
                candidato = obter_arquivo_recente_confer(pasta_download, inicio_tentativa)
                if candidato:
                    nome = str(candidato.name)
                    if nome.endswith('.crdownload'):
                        time.sleep(polling)
                        continue

                    # Verificar estabilidade rápida do tamanho
                    estavel = False
                    try:
                        tamanho_prev = candidato.stat().st_size
                        time.sleep(polling)
                        tamanho_novo = candidato.stat().st_size
                        if tamanho_prev == tamanho_novo and tamanho_novo > 0:
                            estavel = True
                    except Exception:
                        pass

                    if not estavel:
                        time.sleep(polling)
                        continue

                    # Leitura rápida e validação
                    df = None
                    try:
                        df = pd.read_csv(str(candidato), encoding='utf-8-sig', sep=';', engine='python', nrows=500)
                    except Exception:
                        try:
                            df = pd.read_csv(str(candidato), engine='python', nrows=500)
                        except Exception:
                            df = _ler_arquivo_csv_robusto(str(candidato))

                    valid_fn = validar_por_dataframe_fn or validar_arquivo_confer
                    valido = False
                    if df is not None:
                        valido = bool(valid_fn(df, data_esperada))

                    if valido:
                        tempo = time.time() - inicio_tentativa
                        log.info(f"[CONFER] Arquivo válido detectado: {candidato.name} (tentativa {tentativa}) tempo={tempo:.2f}s")
                        _set_status(f"[CONFER] {descricao}: arquivo válido encontrado: {candidato.name}")
                        return Path(candidato)
                    else:
                        motivo_falha = "conteúdo inválido (data)"
                        log.warning(f"[CONFER] Arquivo encontrado, porém inválido para {descricao}: {candidato.name}")
                        try:
                            _remover_arquivo_se_existir_confer(candidato, f"arquivo inválido {descricao}")
                        except Exception:
                            pass
                        break
                time.sleep(polling)
            except Exception as e:
                log.warning(f"[CONFER] erro ao procurar arquivo durante tentativa {tentativa}: {e}")
                time.sleep(polling)

        dur = time.time() - inicio_tentativa
        log.warning(f"[CONFER] tentativa {tentativa} finalizada em {dur:.2f}s - motivo: {motivo_falha}")
        if callable(on_timeout_retry):
            try:
                on_timeout_retry(tentativa, max_tentativas)
            except Exception as e:
                log.warning(f"[CONFER] on_timeout_retry falhou: {e}")
        ultima_ex = TimeoutException(f"Falha no {descricao} (tentativa {tentativa}): {motivo_falha}")
        if tentativa < max_tentativas:
            continue

    log.error(f"[CONFER] {descricao} falhou após {max_tentativas} tentativas - ultimo motivo: {motivo_falha}")
    _registrar_erro(f"❌ Confer download falhou: {descricao} - motivo: {motivo_falha}")
    raise ultima_ex or TimeoutException(f"Falha no {descricao} após {max_tentativas} tentativas")


def _carregar_janelas_confer_prod(arquivo_base: str | Path | None) -> dict:
    """Carrega janela (primeiro/último registro) por matrícula a partir de um CSV/parquet Confer."""
    if arquivo_base is None:
        return {}

    arquivo_base = Path(arquivo_base)
    if not arquivo_base.is_file():
        return {}

    try:
        if str(arquivo_base).lower().endswith(".parquet"):
            df = pd.read_parquet(str(arquivo_base))
        else:
            df = _ler_csv_tratamento(str(arquivo_base))
    except Exception as exc:
        log.warning(f"Falha ao ler confer-prod para janela de sessão ({arquivo_base.name}): {exc}")
        return {}

    if df is None or df.empty:
        return {}

    col_matricula = _identificar_coluna(
        df,
        nomes_preferidos=["matrícula do colaborador", "matricula do colaborador", "matricula", "matrícula", "desMatricula"],
        termos_contidos=["matric"],
    )
    col_datahora = _identificar_coluna(
        df,
        nomes_preferidos=["data/hora da conferência", "data/hora", "data hora", "datahora", "datAnalise"],
        termos_contidos=["data", "hora"],
    )

    if not col_matricula or not col_datahora:
        return {}

    base = df[[col_matricula, col_datahora]].copy()
    base["__matricula_norm"] = (
        base[col_matricula]
        .fillna("")
        .astype(str)
        .str.split(" - ").str[0]
        .str.strip()
        .str.upper()
    )
    base["__dt"] = pd.to_datetime(base[col_datahora], errors="coerce", dayfirst=True)
    base = base[(base["__matricula_norm"] != "") & (base["__dt"].notna())].copy()

    if base.empty:
        return {}

    # Tolerâncias para alinhar log_eventos x confer-prod:
    # - início: login pode ocorrer antes do primeiro registro produtivo
    # - fim: último logout pode ocorrer após o último registro produtivo
    tol_inicio_min = max(0, int(os.getenv("CONFER_JANELA_TOLERANCIA_INICIO_MIN", "1") or "1"))
    tol_fim_min = max(0, int(os.getenv("CONFER_JANELA_TOLERANCIA_FIM_MIN", "1") or "1"))
    tolerancia_inicio = pd.Timedelta(minutes=tol_inicio_min)
    tolerancia_fim = pd.Timedelta(minutes=tol_fim_min)

    resumo = base.groupby("__matricula_norm", sort=False)["__dt"].agg(["min", "max"]).reset_index()
    janelas = {
        str(row["__matricula_norm"]): (
            row["min"] - tolerancia_inicio,
            row["max"] + tolerancia_fim,
        )
        for _, row in resumo.iterrows()
    }
    log.info(
        "Janelas confer-prod carregadas (%s) com tolerância: início=%s min, fim=%s min",
        arquivo_base.name,
        tol_inicio_min,
        tol_fim_min,
    )
    return janelas


def _carregar_janelas_confer_prod_bruto_d1() -> dict:
    """Carrega janela (primeiro/último registro) por matrícula no confer-prod-bruto de D-1."""
    data_d1 = (_obter_data_base_execucao() - timedelta(days=1)).strftime("%Y%m%d")
    candidatos = [
        PASTA_PRODUCAO_CONFER / f"{PREFIXO_CONF_BRUTO}{data_d1}.parquet",
        PASTA_PRODUCAO_CONFER / f"{PREFIXO_CONF_BRUTO}{data_d1}.csv",
    ]

    for candidato in candidatos:
        if candidato.exists():
            return _carregar_janelas_confer_prod(candidato)

    return {}


def _selecionar_matricula_ngx_select_rotina(drv, toggle_xpath: str, input_xpath: str, matricula: str, timeout_sec: int = 8):
    wait = WebDriverWait(drv, timeout_sec)
    input_xpaths = (input_xpath, "//app-log-eventos//ngx-select//input", "//ngx-select//input")

    def _encontrar_input_disponivel():
        for xp in input_xpaths:
            try:
                wait.until(EC.presence_of_element_located((By.XPATH, xp)))
                for candidato in drv.find_elements(By.XPATH, xp):
                    try:
                        if candidato.is_displayed() and candidato.is_enabled():
                            return candidato
                    except StaleElementReferenceException:
                        continue
            except Exception:
                continue
        return None

    ultima_excecao = None
    for tentativa in range(1, 4):
        try:
            _click_com_js(drv, toggle_xpath)
            input_elemento = _encontrar_input_disponivel()
            if input_elemento is None:
                raise TimeoutException("Não foi possível localizar o campo de matrícula no ngx-select")

            try:
                input_elemento.click()
            except Exception:
                drv.execute_script("arguments[0].focus();", input_elemento)

            try:
                input_elemento.send_keys(Keys.CONTROL, "a")
                input_elemento.send_keys(Keys.DELETE)
            except Exception:
                drv.execute_script("arguments[0].value = '';", input_elemento)

            valor_matricula = str(matricula)
            try:
                input_elemento.send_keys(valor_matricula)
            except Exception:
                drv.execute_script(
                    "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
                    input_elemento,
                    valor_matricula,
                )

            time.sleep(0.2)
            input_elemento.send_keys(Keys.ENTER)
            return
        except (TimeoutException, WebDriverException, StaleElementReferenceException) as exc:
            ultima_excecao = exc
            if tentativa < 3:
                time.sleep(0.5)
                continue

    raise ultima_excecao or TimeoutException("Não foi possível preencher a matrícula no ngx-select")


def _gerar_parquet_sessoes_monitor_confer(arquivos_csv: list, data_d1: datetime):
    """Consolida CSVs do Log Eventos em parquet de sessões no padrão Monitor_conferDDMMYYYY."""
    if not arquivos_csv:
        return None

    frames = []
    for arquivo in arquivos_csv:
        try:
            df = _ler_csv_tratamento(str(arquivo))
            if df is None or df.empty:
                continue

            col_evento = next((c for c in df.columns if "evento" in str(c).lower()), None)
            col_datahora = next((c for c in df.columns if "data" in str(c).lower() and "hora" in str(c).lower()), None)
            col_matricula = next(
                (c for c in df.columns if "matric" in str(c).lower()),
                None,
            )

            if not col_evento or not col_datahora:
                continue

            df_local = df.copy()
            df_local["__dt"] = pd.to_datetime(df_local[col_datahora], errors="coerce", dayfirst=True)
            df_local = df_local[df_local["__dt"].notna()].sort_values("__dt")
            if df_local.empty:
                continue

            matricula_valor = ""
            if col_matricula and col_matricula in df_local.columns:
                matricula_valor = str(df_local[col_matricula].iloc[0]).strip()

            inicio_atual = None
            linhas = []
            for _, row in df_local.iterrows():
                evento = str(row[col_evento]).strip().lower()
                if "troca de etapa" in evento:
                    continue

                if "logout" in evento:
                    if inicio_atual is not None:
                        dt_inicio = inicio_atual["__dt"]
                    else:
                        dt_inicio = row["__dt"]

                    dt_fim = row["__dt"]
                    linhas.append(
                        {
                            "Data": dt_inicio.strftime("%Y-%m-%d"),
                            "Hora": str(int(dt_inicio.hour)),
                            "matricula": matricula_valor,
                            "Data do Evento": dt_inicio.strftime("%Y-%m-%d %H:%M:%S"),
                            "Evento": "Autenticação com sucesso",
                            "Data segundo evento": dt_fim.strftime("%Y-%m-%d %H:%M:%S"),
                            "Segundo evento": "Logout",
                        }
                    )
                    inicio_atual = None
                elif inicio_atual is None:
                    inicio_atual = row

            if linhas:
                frames.append(pd.DataFrame(linhas))
        except Exception as exc:
            log.warning(f"Falha no tratamento de sessões para {Path(arquivo).name}: {exc}")

    if not frames:
        return None

    consolidado = pd.concat(frames, ignore_index=True)
    consolidado = consolidado.sort_values(by=["matricula", "Data do Evento"], kind="stable")
    consolidado_parquet = _preparar_dataframe_para_parquet(consolidado)

    PASTA_MONITOR_CONFER_TRATADO.mkdir(parents=True, exist_ok=True)
    nome_saida = f"Monitor_confer{data_d1.strftime('%d%m%Y')}.parquet"
    caminho_saida = PASTA_MONITOR_CONFER_TRATADO / nome_saida
    consolidado_parquet.to_parquet(str(caminho_saida), index=False, compression="snappy")
    log.info(f"✅ Monitor Confer consolidado salvo | Destino: {os.path.abspath(caminho_saida)}")
    return caminho_saida


def _normalizar_sufixo_nome_confer(valor: str) -> str:
    texto = str(valor or "").strip()
    permitidos = [ch for ch in texto if ch.isalnum() or ch in ("-", "_")]
    return "".join(permitidos) if permitidos else "item"


def _renomear_download_com_sufixo_confer(caminho: Path, sufixo: str) -> Path:
    sufixo_ok = _normalizar_sufixo_nome_confer(sufixo)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    novo_nome = f"relatorio_{sufixo_ok}_{timestamp}.csv"
    novo_caminho = caminho.parent / novo_nome
    contador = 1
    while novo_caminho.exists():
        novo_caminho = caminho.parent / f"relatorio_{sufixo_ok}_{timestamp}_{contador}.csv"
        contador += 1
    try:
        caminho.rename(novo_caminho)
        return novo_caminho
    except Exception as e:
        log.warning(f"Falha ao renomear {caminho.name}: {e}")
        return caminho


def _remover_arquivo_se_existir_confer(caminho: Path, descricao: str = "arquivo temporário") -> bool:
    if not caminho:
        return False
    try:
        caminho = Path(caminho)
        if not caminho.exists() or not caminho.is_file():
            return False
        caminho.unlink()
        log.info(f"{descricao.capitalize()} removido: {caminho.name}")
        return True
    except Exception as e:
        log.warning(f"Falha ao remover {descricao} {Path(caminho).name}: {e}")
        return False


def _limpar_arquivos_temporarios_confer(caminhos, preservar=None):
    preservar_resolvidos = set()
    for caminho in preservar or []:
        try:
            preservar_resolvidos.add(str(Path(caminho).resolve()))
        except Exception:
            continue
    vistos = set()
    for caminho in caminhos or []:
        if not caminho:
            continue
        path_obj = Path(caminho)
        try:
            path_resolvido = str(path_obj.resolve())
        except Exception:
            path_resolvido = str(path_obj)
        if path_resolvido in vistos or path_resolvido in preservar_resolvidos:
            continue
        vistos.add(path_resolvido)
        _remover_arquivo_se_existir_confer(path_obj, "arquivo temporário")


def _identificar_coluna(df, nomes_preferidos=None, termos_contidos=None):
    nomes_preferidos = [_normalizar_nome_coluna(n).lower() for n in (nomes_preferidos or [])]
    termos_contidos = [_normalizar_nome_coluna(t).lower() for t in (termos_contidos or [])]
    for col in df.columns:
        col_norm = _normalizar_nome_coluna(col).lower()
        if col_norm in nomes_preferidos:
            return col
    for col in df.columns:
        col_norm = _normalizar_nome_coluna(col).lower()
        if any(term in col_norm for term in termos_contidos):
            return col
    return None


def _formatar_datetime_tabela(valor) -> str:
    if pd.isna(valor):
        return ""
    if isinstance(valor, pd.Timestamp):
        return valor.strftime("%Y-%m-%d %H:%M:%S")
    return str(valor or "").strip()


def _formatar_data_base_tabela(valor) -> str:
    if pd.isna(valor):
        return ""
    if isinstance(valor, pd.Timestamp):
        return valor.strftime("%Y-%m-%d")
    return str(valor or "").strip()


def _normalizar_nome_agente(valor: str) -> str:
    """Normaliza nome do colaborador para match Relatórios ↔ Log Eventos."""
    texto = _corrigir_texto_mojibake(str(valor or "")).strip()
    if not texto:
        return ""
    texto = re.sub(r"\s+", " ", texto)
    try:
        texto = texto.encode("ascii", "ignore").decode("ascii")
    except Exception:
        pass
    return texto.casefold().strip()


def _carregar_mapa_nome_matricula_confer(csv_prod: str | Path | None) -> dict[str, str]:
    """Monta mapa nome_normalizado → matrícula a partir do Relatórios Confer do ciclo."""
    if csv_prod is None:
        return {}

    caminho = Path(csv_prod)
    if not caminho.is_file():
        return {}

    try:
        if str(caminho).lower().endswith(".parquet"):
            df = pd.read_parquet(str(caminho))
        else:
            df = _ler_csv_tratamento(str(caminho))
    except Exception as exc:
        log.warning(
            "Falha ao ler Relatórios Confer para mapa nome→matrícula (%s): %s",
            caminho.name,
            exc,
        )
        return {}

    if df is None or df.empty:
        return {}

    df = df.copy()
    df.columns = [_normalizar_nome_coluna(c) for c in df.columns]

    col_nome = _identificar_coluna(
        df,
        nomes_preferidos=["nome do colaborador", "nome colaborador", "nome", "colaborador"],
        termos_contidos=["nome"],
    )
    col_matricula = _identificar_coluna(
        df,
        nomes_preferidos=[
            "matrícula do colaborador",
            "matricula do colaborador",
            "matricula",
            "matrícula",
            "desMatricula",
        ],
        termos_contidos=["matric"],
    )
    if not col_nome or not col_matricula:
        log.warning(
            "Mapa nome→matrícula Confer: colunas ausentes (nome=%s, matricula=%s)",
            col_nome,
            col_matricula,
        )
        return {}

    mapa: dict[str, str] = {}
    conflitos = 0
    for _, row in df.iterrows():
        nome_norm = _normalizar_nome_agente(row.get(col_nome, ""))
        mat_raw = str(row.get(col_matricula, "") or "").strip()
        mat_raw = mat_raw.split(" - ")[0].strip()
        if not nome_norm or not mat_raw:
            continue
        if not verifica_usuario(mat_raw):
            continue
        mat_norm = re.sub(r"[^A-Za-z0-9]", "", mat_raw).lower().strip()
        if nome_norm in mapa and mapa[nome_norm] != mat_norm:
            conflitos += 1
            continue
        mapa.setdefault(nome_norm, mat_norm)

    if conflitos:
        log.warning(
            "Mapa nome→matricula Confer: %s nome(s) com matrículas conflitantes ignorados",
            conflitos,
        )
    log.info("Mapa nome→matricula Confer carregado: %s entradas (%s)", len(mapa), caminho.name)
    return mapa


def _resolver_matricula_por_nome(
    matricula_candidata: str,
    nome_linha: str,
    mapa_nome_matricula: dict[str, str],
) -> str:
    """Prefere matrícula válida; senão resolve pelo nome (ou nome no campo matrícula)."""
    mat = str(matricula_candidata or "").strip()
    if mat and verifica_usuario(mat):
        return re.sub(r"[^A-Za-z0-9]", "", mat).lower().strip()

    if not mapa_nome_matricula:
        return mat

    for candidato in (nome_linha, mat):
        chave = _normalizar_nome_agente(candidato)
        if chave and chave in mapa_nome_matricula:
            return mapa_nome_matricula[chave]

    return mat


def _gerar_csv_sessoes_por_evento(
    caminho_csv: Path,
    matricula: str = None,
    csv_prod_confer: str | Path | None = None,
) -> Path:

    df = cp_read_csv(
        caminho_csv,
        dtype=str,
        encodings=("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1"),
    )
    df.columns = [_normalizar_nome_coluna(col) for col in df.columns]
    log.info(f"[Confer][Sessoes] CSV {caminho_csv.name} | linhas_brutas={len(df)}")
    if len(df.columns) > 0:
        log.info(f"[Confer][Sessoes] colunas: {list(df.columns)}")
    if df.empty:
        log.warning(f"[Confer][CSV] CSV vazio para processamento de sessões: {caminho_csv.name}")
        return caminho_csv

    col_evento = _identificar_coluna(df, nomes_preferidos=["evento"], termos_contidos=["evento"])
    if col_evento is None:
        log.warning(f"[Confer][Sessoes] Coluna 'Evento' não encontrada em {caminho_csv.name}")
        return caminho_csv

    if col_evento in df.columns:
        df[col_evento] = df[col_evento].apply(_corrigir_texto_mojibake)

    col_nome = _identificar_coluna(
        df,
        nomes_preferidos=["nome do colaborador", "nome colaborador", "colaborador", "usuario", "usuário"],
        termos_contidos=["colaborador", "usu", "nome"],
    )
    col_matricula = _identificar_coluna(
        df,
        nomes_preferidos=["matrícula do colaborador", "matricula do colaborador", "matricula", "matrícula"],
        termos_contidos=["matric"],
    )

    col_datahora = _identificar_coluna(
        df,
        nomes_preferidos=["data/hora", "data hora", "datahora", "data e hora"],
        termos_contidos=["data", "hora"],
    )
    log.info(f"[Confer][Sessoes] col_evento={col_evento} | col_matricula={col_matricula} | col_nome={col_nome} | col_datahora={col_datahora}")

    mapa_nome_matricula = (
        _carregar_mapa_nome_matricula_confer(csv_prod_confer) if csv_prod_confer else {}
    )

    datas_invalidas = 0
    if col_datahora is not None:
        datas_invalidas = df[col_datahora].isna().sum() + (df[col_datahora] == '').sum()
    log.info(f"[Confer][Sessoes] datas_invalidas={datas_invalidas}")

    df_proc = df.copy()
    df_proc["__ordem"] = range(len(df_proc))
    if col_datahora is not None:
        df_proc["__dt"] = pd.to_datetime(df_proc[col_datahora], errors="coerce", dayfirst=True)
        df_proc = df_proc.sort_values(by=["__dt", "__ordem"], na_position="last")
    else:
        df_proc["__dt"] = pd.NaT
        df_proc = df_proc.sort_values(by=["__ordem"])

    if len(df_proc) == 0:
        log.warning(f"[Confer][Sessoes] CSV sem linhas para processar sessões: {caminho_csv.name}")
        return caminho_csv

    def _montar_linha_sessao(
        sessao_id: int,
        row_inicio=None,
        row_logout=None,
        renomear_inicio_autenticacao: bool = False,
        forcar_evento_logout: bool = False,
        status_pendente: bool = False,
    ):
        tem_inicio = row_inicio is not None
        tem_logout = row_logout is not None
        row_inicio = row_inicio if tem_inicio else {}
        row_logout = row_logout if tem_logout else {}
        matricula_candidata = str(
            matricula
            or (row_inicio.get(col_matricula, "") if col_matricula else "")
            or (row_logout.get(col_matricula, "") if col_matricula else "")
            or ""
        ).strip()
        nome_linha = ""
        if col_nome:
            nome_linha = str(row_inicio.get(col_nome) or row_logout.get(col_nome) or "").strip()

        if matricula and verifica_usuario(matricula):
            matricula_linha = re.sub(r"[^A-Za-z0-9]", "", str(matricula)).lower().strip()
        else:
            matricula_linha = _resolver_matricula_por_nome(
                matricula_candidata,
                nome_linha,
                mapa_nome_matricula,
            )

        dt_inicio = row_inicio.get("__dt") if isinstance(row_inicio, pd.Series) else pd.NaT
        dt_logout = row_logout.get("__dt") if isinstance(row_logout, pd.Series) else pd.NaT
        tempo_logado = ""
        if pd.notna(dt_inicio) and pd.notna(dt_logout) and dt_logout >= dt_inicio:
            tempo_logado = _formatar_timedelta_hhmmss(dt_logout - dt_inicio)

        evento_inicio = str(row_inicio.get(col_evento, "")).strip()
        if renomear_inicio_autenticacao and tem_inicio:
            evento_inicio = "Autenticação com sucesso"

        evento_logout = str(row_logout.get(col_evento, "")).strip()
        evento_logout = "Logout" if tem_logout else ""

        dt_evento = dt_inicio if pd.notna(dt_inicio) else pd.to_datetime(row_inicio.get(col_datahora, ""), errors="coerce", dayfirst=True)
        dt_segundo_evento = dt_logout if pd.notna(dt_logout) else pd.to_datetime(row_logout.get(col_datahora, ""), errors="coerce", dayfirst=True)

        hora = ""
        if pd.notna(dt_evento):
            hora = str(int(dt_evento.hour))

        return {
            "Data": _formatar_data_base_tabela(dt_evento),
            "Hora": hora,
            "matricula": matricula_linha,
            "Data do Evento": _formatar_datetime_tabela(dt_evento),
            "Evento": evento_inicio,
            "Data segundo evento": _formatar_datetime_tabela(dt_segundo_evento),
            "Segundo evento": evento_logout,
        }

    linhas_sessoes = []
    sessao_id = 1


    if col_nome is not None:
        df_proc["__usuario_key"] = df_proc[col_nome].fillna("").astype(str).str.strip()
    else:
        df_proc["__usuario_key"] = ""

    default_usuario = str(matricula or "usuario").strip() or "usuario"
    df_proc["__usuario_key"] = df_proc["__usuario_key"].replace("", default_usuario)
    log.info(f"[Confer][Sessoes] Agrupamento atual: __usuario_key baseado em nome do colaborador")

    eventos_ignorados_troca_etapa = 0
    logouts_consecutivos_ignorados = 0
    grupos_formados = 0
    sessoes_por_grupo = []

    for usuario_key, grupo in df_proc.groupby("__usuario_key", sort=False):
        grupos_formados += 1
        inicio_atual = None
        ultimo_evento = None
        ultimo_evento_foi_logout = False
        sessoes_geradas = 0

        for _, row in grupo.iterrows():
            evento = str(row[col_evento]).strip()
            evento_norm = evento.lower()

            if "troca de etapa" in evento_norm:
                eventos_ignorados_troca_etapa += 1
                log.debug(f"[Confer][Sessoes] Evento ignorado (Troca de Etapa): {evento}")
                continue

            ultimo_evento = row
            eh_logout = "logout" in evento_norm

            if eh_logout:
                if ultimo_evento_foi_logout:
                    logouts_consecutivos_ignorados += 1
                    log.debug(f"[Confer][Sessoes] Logout consecutivo ignorado para usuário {usuario_key}")
                    continue
                linhas_sessoes.append(
                    _montar_linha_sessao(
                        sessao_id,
                        row_inicio=inicio_atual,
                        row_logout=row,
                        renomear_inicio_autenticacao=True,
                    )
                )
                sessoes_geradas += 1
                sessao_id += 1
                inicio_atual = None
                ultimo_evento_foi_logout = True
                continue

            if inicio_atual is None:
                inicio_atual = row
            ultimo_evento_foi_logout = False

        if inicio_atual is not None:
            linhas_sessoes.append(
                _montar_linha_sessao(
                    sessao_id,
                    row_inicio=inicio_atual,
                    row_logout=ultimo_evento,
                    renomear_inicio_autenticacao=True,
                    forcar_evento_logout=False,
                    status_pendente=True,
                )
            )
            sessoes_geradas += 1
            sessao_id += 1

        log.info(f"[Confer][Sessoes] Grupo={usuario_key} | eventos={len(grupo)} | sessoes={sessoes_geradas} | pendente={inicio_atual is not None}")
        sessoes_por_grupo.append(sessoes_geradas)

    log.info(f"[Confer][Sessoes] eventos_ignorados_troca_etapa={eventos_ignorados_troca_etapa}")
    log.info(f"[Confer][Sessoes] logouts_consecutivos_ignorados={logouts_consecutivos_ignorados}")
    log.info(f"[Confer][Sessoes] grupos_formados={grupos_formados}")
    log.info(f"[Confer][Sessoes] Total de sessões geradas={sum(sessoes_por_grupo)}")

    df_evt = pd.DataFrame(
        linhas_sessoes,
        columns=[
            "Data",
            "Hora",
            "matricula",
            "Data do Evento",
            "Evento",
            "Data segundo evento",
            "Segundo evento",
        ],
    )

    # Regra adicional: se usuário ultrapassar 6h logado, restringe sessões à janela
    # do confer-prod (primeiro e último registro da matrícula no dia).
    if not df_evt.empty:
        limite_logado = pd.Timedelta(hours=6)
        if csv_prod_confer is not None:
            janelas_confer = _carregar_janelas_confer_prod(csv_prod_confer)
        else:
            janelas_confer = _carregar_janelas_confer_prod_bruto_d1()

        if janelas_confer:
            df_evt["__inicio"] = pd.to_datetime(df_evt["Data do Evento"], errors="coerce")
            df_evt["__fim"] = pd.to_datetime(df_evt["Data segundo evento"], errors="coerce")
            df_evt["__matricula_norm"] = (
                df_evt["matricula"]
                .fillna("")
                .astype(str)
                .str.split(" - ").str[0]
                .str.strip()
                .str.upper()
            )

            grupos_filtrados = []
            total_sessoes_descartadas = 0

            for matricula_norm, grupo in df_evt.groupby("__matricula_norm", sort=False, dropna=False):
                janela = janelas_confer.get(str(matricula_norm or ""))
                grupo_ajustado = grupo.copy()

                possui_logout_faltante = (
                    grupo_ajustado["Segundo evento"].fillna("").astype(str).str.strip().str.lower() != "logout"
                )

                # Se não houver logout explícito, usa o último horário do confer-prod-bruto como logout.
                if janela and bool(possui_logout_faltante.any()):
                    _, fim_janela = janela
                    grupo_ajustado.loc[possui_logout_faltante, "__fim"] = fim_janela
                    grupo_ajustado.loc[possui_logout_faltante, "Data segundo evento"] = _formatar_datetime_tabela(fim_janela)
                    grupo_ajustado.loc[possui_logout_faltante, "Segundo evento"] = "Logout"

                duracoes = (grupo_ajustado["__fim"] - grupo_ajustado["__inicio"]).fillna(pd.Timedelta(0))
                duracoes = duracoes.where(duracoes > pd.Timedelta(0), pd.Timedelta(0))
                total_logado = duracoes.sum()

                deve_aplicar_filtro_janela = total_logado > limite_logado or bool(possui_logout_faltante.any())

                if not janela:
                    if deve_aplicar_filtro_janela:
                        log.warning(
                            f"Sessões sem logout ou acima de 6h para {matricula_norm}, mas sem janela no confer-prod-bruto D-1."
                        )
                    grupos_filtrados.append(grupo_ajustado)
                    continue

                if not deve_aplicar_filtro_janela:
                    grupos_filtrados.append(grupo_ajustado)
                    continue

                inicio_janela, fim_janela = janela
                
                # Ajustar sessões para ficar dentro da janela, em vez de descartar
                grupo_ajustado_final = grupo_ajustado.copy()
                
                for idx, row in grupo_ajustado.iterrows():
                    dt_inicio = row["__inicio"]
                    dt_fim = row["__fim"]
                    
                    if pd.isna(dt_inicio) or pd.isna(dt_fim):
                        continue
                    
                    # Se sessão não intercepta a janela, descarta
                    if dt_fim < inicio_janela or dt_inicio > fim_janela:
                        grupo_ajustado_final = grupo_ajustado_final.drop(idx)
                        continue
                    
                    # Sessão intercepta janela - ajustar limites
                    novo_inicio = max(dt_inicio, inicio_janela)
                    novo_fim = min(dt_fim, fim_janela)
                    
                    # Atualizar na cópia
                    grupo_ajustado_final.loc[idx, "__inicio"] = novo_inicio
                    grupo_ajustado_final.loc[idx, "__fim"] = novo_fim
                    grupo_ajustado_final.loc[idx, "Data do Evento"] = _formatar_datetime_tabela(novo_inicio)
                    grupo_ajustado_final.loc[idx, "Data segundo evento"] = _formatar_datetime_tabela(novo_fim)
                
                total_sessoes_descartadas += max(0, len(grupo_ajustado) - len(grupo_ajustado_final))
                grupos_filtrados.append(grupo_ajustado_final)

                log.info(
                    f"Filtro 6h aplicado para {matricula_norm}: "
                    f"janela {inicio_janela} -> {fim_janela}, "
                    f"sessões mantidas {len(grupo_ajustado_final)}/{len(grupo_ajustado)}"
                )

            if grupos_filtrados:
                df_evt = pd.concat(grupos_filtrados, ignore_index=True)
            else:
                df_evt = df_evt.iloc[0:0].copy()

            if total_sessoes_descartadas > 0:
                log.info(f"Filtro 6h: {total_sessoes_descartadas} sessão(ões) descartada(s) por janela do confer-prod-bruto")

            df_evt = df_evt.drop(columns=["__inicio", "__fim", "__matricula_norm"], errors="ignore")

    arquivo_saida = caminho_csv.with_name(f"{caminho_csv.stem}_sessoes.csv")
    df_evt.to_csv(
        arquivo_saida,
        index=False,
        encoding="utf-8-sig",
        sep=";",
        quotechar='"',
        quoting=csv.QUOTE_ALL,
    )
    log.info(f"CSV de sessões gerado: {arquivo_saida.name} ({len(df_evt)} linhas)")
    return arquivo_saida


def _formatar_timedelta_hhmmss(valor_timedelta: pd.Timedelta) -> str:
	if pd.isna(valor_timedelta):
		return "00:00:00"
	total_segundos = int(max(0, valor_timedelta.total_seconds()))
	horas = total_segundos // 3600
	minutos = (total_segundos % 3600) // 60
	segundos = total_segundos % 60
	return f"{horas:02d}:{minutos:02d}:{segundos:02d}"


def _calcular_resumo_tempos_sessoes(df_sessoes: pd.DataFrame) -> pd.DataFrame:
	if df_sessoes.empty:
		return pd.DataFrame(columns=["matricula_ref", "tempo_logado", "tempo_deslogado", "sessoes"]) 

	col_matricula = _identificar_coluna(df_sessoes, nomes_preferidos=["matricula_ref"], termos_contidos=["matric"])
	col_tipo_evento = _identificar_coluna(df_sessoes, nomes_preferidos=["tipo_evento"], termos_contidos=["tipo_evento"])
	col_datahora = _identificar_coluna(
		df_sessoes,
		nomes_preferidos=["data do evento", "data/hora", "data hora", "datahora", "data e hora"],
		termos_contidos=["data", "hora"],
	)

	if not col_matricula or not col_tipo_evento or not col_datahora:
		log.warning("Não foi possível calcular resumo de tempos: colunas obrigatórias ausentes")
		return pd.DataFrame(columns=["matricula_ref", "tempo_logado", "tempo_deslogado", "sessoes"]) 

	df = df_sessoes.copy()
	df["__matricula"] = df[col_matricula].fillna("").astype(str).str.strip()
	df = df[df["__matricula"] != ""].copy()
	if df.empty:
		return pd.DataFrame(columns=["matricula_ref", "tempo_logado", "tempo_deslogado", "sessoes"]) 

	df["__tipo"] = df[col_tipo_evento].fillna("").astype(str).str.strip().str.lower()
	df["__dt"] = pd.to_datetime(df[col_datahora], errors="coerce", dayfirst=True)
	df = df.dropna(subset=["__dt"]).copy()
	if df.empty:
		return pd.DataFrame(columns=["matricula_ref", "tempo_logado", "tempo_deslogado", "sessoes"]) 

	df = df.sort_values(by=["__matricula", "__dt"]).copy()

	linhas_resumo = []
	for matricula, grupo in df.groupby("__matricula", sort=True):
		tempo_logado = pd.Timedelta(0)
		tempo_deslogado = pd.Timedelta(0)
		ultimo_logout = None
		inicio_atual = None
		sessoes = 0

		for _, row in grupo.iterrows():
			tipo = str(row["__tipo"])
			dt = row["__dt"]

			eh_inicio = "inicio" in tipo
			eh_logout = "logout" in tipo

			if eh_inicio:
				sessoes += 1
				if ultimo_logout is not None and dt >= ultimo_logout:
					tempo_deslogado += (dt - ultimo_logout)
				inicio_atual = dt

			if eh_logout:
				if inicio_atual is not None and dt >= inicio_atual:
					tempo_logado += (dt - inicio_atual)
				elif ultimo_logout is None:
					sessoes = max(1, sessoes)
				ultimo_logout = dt
				inicio_atual = None

		linhas_resumo.append({
			"matricula_ref": matricula,
			"tempo_logado": _formatar_timedelta_hhmmss(tempo_logado),
			"tempo_deslogado": _formatar_timedelta_hhmmss(tempo_deslogado),
			"sessoes": int(max(1, sessoes)) if len(grupo) > 0 else 0,
		})

	return pd.DataFrame(linhas_resumo)




def _consolidar_csv_sessoes_confer_d1(caminhos_sessoes: list, data_d1) -> Path:
    """Consolida CSVs de sessões em parquet final em PASTA_MONITOR_CONFER_TRATADO."""
    arquivos_validos = [Path(p) for p in caminhos_sessoes if p and Path(p).exists()]
    log.info(f"[Confer][Consolidacao] Arquivos recebidos para consolidar: {len(arquivos_validos)}")
    if not arquivos_validos:
        log.warning("[Confer][Consolidacao] Nenhum arquivo de sessão válido para consolidar")
        return None

    frames = []
    for arquivo in arquivos_validos:
        try:
            df = _ler_csv_tratamento(str(arquivo))
            linhas = len(df) if df is not None else 0
            log.info(f"[Confer][Consolidacao] {arquivo.name} -> {linhas} linhas")
            if df is not None and not df.empty:
                frames.append(df)
            else:
                log.warning(f"[Confer][Consolidacao] Arquivo ignorado por estar vazio ou inválido: {arquivo.name}")
        except Exception as exc:
            log.warning(f"[Confer][Consolidacao] Falha ao ler CSV de sessões {arquivo.name}: {exc}")

    if not frames:
        log.warning("[Confer][Consolidacao] Nenhum dado consolidado, todos arquivos vazios ou inválidos")
        return None

    consolidado = pd.concat(frames, ignore_index=True)
    log.info(f"[Confer][Consolidacao] linhas_consolidadas_antes_salvar={len(consolidado)}")
    consolidado = _normalizar_df_monitor_sessoes(consolidado)
    log.info(f"[Confer][Consolidacao] linhas_apos_normalizacao={len(consolidado)}")
    colunas_ord = [c for c in ["Usuário", "Data do Evento"] if c in consolidado.columns]
    if colunas_ord:
        consolidado = consolidado.sort_values(by=colunas_ord, kind="stable").reset_index(drop=True)

    PASTA_MONITOR_CONFER_TRATADO.mkdir(parents=True, exist_ok=True)
    nome_saida = f"{PREFIXO_MONITOR_CONFER_TRATADO}{data_d1.strftime('%d%m%Y')}.parquet"
    caminho_saida = PASTA_MONITOR_CONFER_TRATADO / nome_saida
    df_parquet = _preparar_dataframe_para_parquet(consolidado)
    df_parquet.to_parquet(str(caminho_saida), index=False, compression="snappy")
    log.info(f"[Confer][Consolidacao] parquet_final={caminho_saida.name} | linhas={len(consolidado)} | caminho={os.path.abspath(caminho_saida)}")
    return caminho_saida


def log_eventos(drv, matriculas=None, settings=None):
    """Baixa Log Eventos D-1 em um único download (data + Pesquisar, sem loop por matrícula).

    Resolve matrícula cruzando nome com o Relatórios Confer D-1 (fonte do busca protocolo).
    O parâmetro ``matriculas`` é ignorado (mantido só por compatibilidade de assinatura).
    """
    wait = WebDriverWait(drv, TIMEOUT_DRIVER_ROTINA)
    wait_short = WebDriverWait(drv, TIMEOUT_SHORT_ROTINA)
    data_d1 = _obter_data_base_execucao() - timedelta(days=1)
    data_d1_fmt = data_d1.strftime("%d/%m/%Y")

    menu_log_eventos_xpath = (
        "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[2]/div/div/div[10]/div/div/div/a"
    )
    data_log_eventos_xpath = (
        "/html/body/app-root/app-home/main/app-gestao/app-log-eventos/div/div/div/div[1]/div[1]"
        "/app-datepicker/div/div/div/input"
    )
    radio_todos_xpath = (
        "/html/body/app-root/app-home/main/app-gestao/app-log-eventos/div/div/div/div[2]/div"
        "/app-radio/div/div[2]/div/div[1]/div/input"
    )
    botao_pesquisar_xpath = (
        "/html/body/app-root/app-home/main/app-gestao/app-log-eventos/div/div/div/div[3]/div/button"
    )

    def _abrir_pagina_log_eventos_d1():
        try:
            wait_short.until(EC.element_to_be_clickable((By.XPATH, menu_log_eventos_xpath)))
        except TimeoutException:
            wait.until(EC.element_to_be_clickable((By.XPATH, menu_log_eventos_xpath)))
        click_element(drv, "xpath", menu_log_eventos_xpath)

        try:
            wait_short.until(EC.presence_of_element_located((By.XPATH, data_log_eventos_xpath)))
        except TimeoutException:
            wait.until(EC.presence_of_element_located((By.XPATH, data_log_eventos_xpath)))

        _click_com_js(drv, radio_todos_xpath)
        send_keys_to_element(drv, "xpath", data_log_eventos_xpath, data_d1_fmt)
        _fechar_calendario_confer_rotina(drv)

    def _recuperar_e_reabrir_log_eventos(contexto: str):
        try:
            recuperou = _recuperar_menu_confer_se_tela_login_rotina(drv, contexto=contexto)
            if recuperou:
                log.info(f"[Confer][LoginRecovery] Sessão recuperada ({contexto})")
        except Exception as exc:
            log.warning(f"[Confer][LoginRecovery] Falha ao tentar recuperar sessão ({contexto}): {exc}")
        _abrir_pagina_log_eventos_d1()

    pasta_download = Path(PASTA_MONITOR_CONFER_TMP)
    pasta_download.mkdir(parents=True, exist_ok=True)
    arquivos_temporarios = []

    _set_status(f"Log Eventos D-1 — download do dia {data_d1_fmt} (sem loop por matrícula)")
    _set_progress(0, "Confer monitor: baixando Log Eventos do dia")
    _recuperar_menu_confer_se_tela_login_rotina(drv, contexto="início do Log Eventos D-1")
    _abrir_pagina_log_eventos_d1()

    def _rearmar_fluxo_retry(_tentativa_atual, _total_tentativas):
        _recuperar_e_reabrir_log_eventos("retry do download Log Eventos do dia")

    try:
        novo_csv = baixar_com_retry_seguro_confer(
            drv,
            botao_pesquisar_xpath,
            pasta_download,
            data_d1,
            descricao="download do Log Eventos do dia",
            max_tentativas=min(4, MAX_RETRY_DOWNLOAD_CONFER_ROTINA),
            timeout_por_tentativa=60,
            on_timeout_retry=_rearmar_fluxo_retry,
            clear_before_attempt=True,
        )
    except Exception as exc:
        log.error(f"[Confer][LogEventos] Falha no download do dia: {exc}", exc_info=True)
        _set_status(f"Log Eventos D-1: falha no download — {exc}")
        _registrar_erro(f"❌ Log Eventos: falha no download do dia — {str(exc)[:120]}")
        return []

    if not novo_csv:
        log.warning("[Confer][LogEventos] Nenhum arquivo retornado no download do dia")
        _set_status("Log Eventos D-1: nenhum arquivo baixado")
        return []

    log.info(f"[Confer][CSV] Arquivo bruto {novo_csv.name} baixado para Log Eventos D-1")
    novo_csv = _renomear_download_com_sufixo_confer(novo_csv, "log_eventos_dia")
    arquivos_temporarios.append(novo_csv)

    csv_prod_confer = _resolver_arquivo_confer_prod_d1()
    if csv_prod_confer:
        log.info(f"[Confer][LogEventos] Relatórios Confer D-1 para matrícula: {csv_prod_confer.name}")
    else:
        log.warning(
            "[Confer][LogEventos] confer-prod-bruto D-1 ausente — sessões sem mapa nome→matrícula"
        )

    try:
        csv_sessoes = _gerar_csv_sessoes_por_evento(
            novo_csv,
            matricula=None,
            csv_prod_confer=csv_prod_confer,
        )
    except Exception as exc:
        log.error(f"[Confer][LogEventos] Falha ao gerar sessões: {exc}", exc_info=True)
        _limpar_arquivos_temporarios_confer(arquivos_temporarios)
        _registrar_erro(f"❌ Log Eventos: falha ao gerar sessões — {str(exc)[:120]}")
        return []

    if Path(csv_sessoes) != Path(novo_csv):
        arquivos_temporarios.append(csv_sessoes)
    try:
        sessoes_geradas = sum(1 for _ in open(csv_sessoes, encoding="utf-8", errors="ignore")) - 1
    except Exception:
        sessoes_geradas = "erro"
    log.info(
        f"[Confer][Sessoes] Arquivo de sessões gerado: {Path(csv_sessoes).name} "
        f"com {sessoes_geradas} sessões"
    )

    consolidado = _consolidar_csv_sessoes_confer_d1([str(csv_sessoes)], data_d1)
    if consolidado:
        _limpar_arquivos_temporarios_confer(arquivos_temporarios, preservar=[consolidado])
        _set_status(f"Monitor Confer D-1 consolidado: {Path(consolidado).name}")
        return [str(consolidado)]

    _limpar_arquivos_temporarios_confer(arquivos_temporarios, preservar=[csv_sessoes])
    return [str(csv_sessoes)]
