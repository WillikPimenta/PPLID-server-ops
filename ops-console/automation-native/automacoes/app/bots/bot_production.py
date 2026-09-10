"""
Bot de Produção - Extração de relatórios de produtividade do BRFlow.

Este módulo automatiza a extração de relatórios de produtividade do sistema BRFlow,
baixando arquivos CSV com dados da meta configurada e armazenando-os em SharePoint.
"""

import sys
from pathlib import Path

import shutil
import os
import time
import tempfile
import logging
import threading
import gc
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from datetime import date, datetime, time as dt_time, timedelta, timezone
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
import csv
import json

try:
    from openpyxl import load_workbook
    from openpyxl.worksheet.table import Table, TableStyleInfo
except ImportError:
    load_workbook = None
    Table = None
    TableStyleInfo = None

from app.infrastructure.selenium_helpers import (
    click_element,
    send_keys_to_element,
    create_driver,
    take_error_screenshot,
    login_okta_resiliente,
    _wait_overlay_invisible,
)

from app.config import *
from app.infrastructure.csv_processing import read_csv as cp_read_csv, write_xlsx_from_df as cp_write_xlsx, wait_for_new_file as cp_wait_for_new_file
from app.core.credentials import get_credentials
from app.core.bot_runtime import BotRuntime
from app.core.common import ErrorRecoveryHandler, safe_close_driver, MetricsContext
from app.infrastructure.confer_helpers import (
    baixar_com_retry_seguro_confer,
    validar_arquivo_confer as _validar_arquivo_confer,
    click_xpath_com_fallback_js as _click_xpath_com_fallback_js,
    fechar_abas_exceto as _fechar_abas_exceto,
    fechar_calendario_se_aberto as _fechar_calendario_se_aberto,
    focar_aba_confer as _focar_aba_confer,
    navegar_busca_confer as _navegar_busca_confer_impl,
    recuperar_menu_confer_se_tela_login as _recuperar_menu_confer_se_tela_login_impl,
    renomear_download_com_sufixo as _renomear_download_com_sufixo,
)

from app.infrastructure.teams_notifier import notify
from app.infrastructure.teams_adaptive_card import build_status_card
from app.infrastructure.bot_sync_drop import (
    DOMAIN_PRODUTIVIDADE,
    append_marker_to_robot_log,
    write_sync_drop,
)

# Logger centralizado - usa o sistema configurado via configure_logging()
log = logging.getLogger("robots.bot_production")

_runtime = BotRuntime(mode="production")
parar_event = _runtime.parar_event
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress
_reset_progress_state = _runtime.reset_progress_state
_begin_cycle = _runtime.begin_cycle


def _emit_production_detalhado_saved(path: Path, *, row_count: int, label: str = "") -> None:
    """Emite marcador de sync para o hook Django (stdout + fallbacks).

    O XLSX já foi gravado. Se o ``print`` falhar (pipe quebrado / Errno 22),
    ainda gravamos drop file + linha no log AppData — o robot_manager drena
    no poll de status e enfileira o sync de produtividade H/H.
    """
    resolved = path.resolve()
    line = f"PRODUCTION_DETALHADO_SAVED|{resolved}"
    printed = False
    try:
        print(line, flush=True)
        printed = True
    except OSError as exc:
        log.warning(
            "Falha ao emitir PRODUCTION_DETALHADO_SAVED via stdout: %s | path=%s",
            exc,
            resolved,
        )

    drop = write_sync_drop(
        DOMAIN_PRODUTIVIDADE,
        resolved,
        extra={"row_count": int(row_count), "label": label or ""},
    )
    logged = append_marker_to_robot_log("production", line)

    if not printed:
        _set_status(
            "Aviso: stdout do sync falhou; usando fallback sync_drop"
            + (" + log" if logged else "")
            + (f" ({drop.name})" if drop else "")
        )
    suffix = f" ({row_count} linhas)"
    _set_status(f"Detalhado salvo{label}: {resolved}{suffix}")
# Constantes de configuração
MAX_ERROS_EXTRACAO = 5
ESPERA_ENTRE_TENTATIVAS = 10  # segundos
TIMEOUT_DRIVER = 8  # Reduzido de 12s (mais agressivo nas esperas)
TIMEOUT_WAIT_FILE = 320
TIMEOUT_FILE_STABLE = 30
DEFAULT_WORKERS_PRODUCTION = 2
DOWNLOADS_TEMP_BASE = PLAN_IDF_SERASA_BOTS / "downloads_temp"
DOWNLOADS_TEMP_PRODUCTION = DOWNLOADS_TEMP_BASE / "production"
# Timing instrumentation
_timing_start = {}

# Confer constants
MAX_RETRY_DOWNLOAD_CONFER = max(1, int(os.getenv("CONFER_DOWNLOAD_RETRY", "4") or "4"))

_SISTEMA_LABEL = {"confer": "Confer", "brflow": "BRFlow"}

# Demandas exibidas na notificação Teams de produção
TASK_CONFER = "confer"
TASK_BRFLOW = "brflow"
TASK_PLANILHA = "planilha"
TASK_MONITOR = "monitor"
TASK_LOG_EVENTOS = "log_eventos"
TASK_GED_IRREGULARIDADE = "ged_irregularidade"

_DEMANDAS_TABELA = [
    ("confer", "Confer", [TASK_CONFER]),
    ("brflow", "BRFlow", [TASK_BRFLOW]),
    ("monitor", "Monitor", [TASK_MONITOR]),
    ("log_eventos", "Log Eventos", [TASK_LOG_EVENTOS]),
    ("planilha", "Planilha", [TASK_PLANILHA]),
    ("ged_irregularidade", "GED Irregularidade", [TASK_GED_IRREGULARIDADE]),
]
_TAREFAS_PRODUCAO = [tid for _, _, tasks in _DEMANDAS_TABELA for tid in tasks]

_OBS_HUMANIZADA = {
    "sem dados": "Relatório sem dados",
    "sem arquivo": "Arquivo não encontrado",
    "csv vazio": "CSV sem linhas de dados",
    "download falhou": "Falha no download",
    "falha na união": "Falha ao unir os relatórios",
    "falha ao salvar": "Falha ao salvar planilha",
    "erro": "Falha na execução",
    "ok": "Concluída",
}

_exec_summary = {
    "erros": [],
    "task_status": {},
    "ciclo": 0,
    "notificado": False,
}


def _reset_exec_summary():
    _exec_summary["erros"] = []
    _exec_summary["task_status"] = {}
    _exec_summary["ciclo"] = 0
    _exec_summary["notificado"] = False


def _inicializar_status_producao(settings=None):
    s = settings or {}
    task_status = {}
    disabled = {
        TASK_CONFER: not _production_flag(s, "executar_confer", True),
        TASK_BRFLOW: not _production_flag(s, "executar_brflow", True),
        TASK_MONITOR: not _production_flag(s, "executar_brflow", True)
        or not _production_flag(s, "baixar_monitor_com_producao", True),
        TASK_LOG_EVENTOS: not _production_flag(s, "executar_confer", True)
        or not _production_flag(s, "baixar_log_eventos_com_producao", True),
        TASK_GED_IRREGULARIDADE: not _production_flag(s, "executar_ged_irregularidade", True),
    }
    for task_id in _TAREFAS_PRODUCAO:
        if disabled.get(task_id):
            task_status[task_id] = {"estado": "nao_executado", "obs": "desabilitado"}
        else:
            task_status[task_id] = {"estado": "pendente", "obs": ""}
    _exec_summary["task_status"] = task_status


def _production_flag(settings: dict | None, key: str, default: bool = True) -> bool:
    s = settings or {}
    if key in s:
        return bool(s[key])
    return default


def _registrar_status_producao(task_id: str, ok: bool, obs: str = ""):
    if task_id not in _exec_summary.get("task_status", {}):
        return
    _exec_summary["task_status"][task_id] = {
        "estado": "ok" if ok else "erro",
        "obs": str(obs or ("OK" if ok else "erro"))[:80],
    }


def _registrar_erro_producao(mensagem: str):
    _exec_summary["erros"].append(str(mensagem))


def _finalizar_status_pendentes():
    for task_id, info in _exec_summary.get("task_status", {}).items():
        if info.get("estado") == "pendente":
            info["estado"] = "nao_executado"
            info["obs"] = ""


def _extra_obs_rules_producao(valor: str):
    lower = valor.lower()
    if "sem dados" in lower:
        return "Relatório sem dados"
    if "vazio" in lower:
        return "Relatório vazio"
    if lower.startswith("erro selenium"):
        return "Erro de automação (Selenium)"
    if "falha em ambos" in lower:
        return "Confer e BRFlow falharam"
    return None


def _build_adaptive_card_producao(erros: list = None) -> dict:
    ciclo = _exec_summary.get("ciclo", 0)
    agora = datetime.now().strftime("%d/%m/%Y às %H:%M")
    return build_status_card(
        titulo_card="Produção h/h — status das extrações",
        subtitulo=f"Ciclo {ciclo} · Atualizado em {agora}",
        demandas_tabela=_DEMANDAS_TABELA,
        task_status=_exec_summary["task_status"],
        erros=erros,
        obs_map=_OBS_HUMANIZADA,
        extra_obs_rules=_extra_obs_rules_producao,
    )


def _enviar_notificacao_ciclo():
    if _exec_summary.get("notificado"):
        return

    _finalizar_status_pendentes()
    erros = list(_exec_summary.get("erros", []))
    task_status = _exec_summary["task_status"]

    concluidas = sum(1 for t in task_status.values() if t.get("estado") == "ok")
    com_falha = sum(1 for t in task_status.values() if t.get("estado") == "erro")
    ciclo = _exec_summary.get("ciclo", 0)
    run_id = f"PROD-{ciclo}-{datetime.now().strftime('%Y%m%d-%H%M')}"

    if com_falha == 0 and concluidas > 0 and not erros:
        status = "sucesso"
        titulo = "Produção — ciclo concluído"
    elif concluidas > 0:
        status = "alerta"
        titulo = "Produção — ciclo com pendências"
    else:
        status = "erro"
        titulo = "Produção — ciclo com pendências"

    adaptive_card = _build_adaptive_card_producao(erros=erros or None)
    try:
        notify("Produção", status, titulo, adaptive_card=adaptive_card, run_id=run_id)
    except Exception as exc:
        log.warning(f"Falha ao enviar notificação Teams de produção: {exc}")
    _exec_summary["notificado"] = True


class FalhaAmbosSistemasDownload(RuntimeError):
    """Ambos Confer e BRFlow falharam no download."""

    def __init__(self, confer_erro=None, brflow_erro=None):
        self.confer_erro = confer_erro
        self.brflow_erro = brflow_erro
        msg = (
            f"Falha em AMBOS os sistemas - "
            f"Confer: {confer_erro or 'Sem dados'} | "
            f"BRFlow: {brflow_erro or 'Sem dados'}"
        )
        super().__init__(msg)


def _truncar_erro_log(msg, max_len=200):
    if msg is None:
        return None
    texto = str(msg)
    return texto[:max_len] if len(texto) > max_len else texto


def _resolve_headless() -> bool:
    raw = (
        os.getenv("PRODUCTION_HEADLESS")
        or os.getenv("ROBOT_HEADLESS")
        or os.getenv("HEADLESS")
        or ""
    ).strip().lower()
    if raw:
        return raw in ("1", "true", "yes", "on")
    return HEADLESS_DEFAULT


def _navegar_busca_confer(drv):
    _navegar_busca_confer_impl(
        drv,
        status_fn=_set_status,
        progress_fn=_set_progress,
        timeout_driver=TIMEOUT_DRIVER,
    )


def _recuperar_menu_confer_se_tela_login(drv, contexto: str = "") -> bool:
    return _recuperar_menu_confer_se_tela_login_impl(
        drv,
        contexto,
        status_fn=_set_status,
        timeout_driver=TIMEOUT_DRIVER,
        logger=log,
    )

def _timing_start_step(step_name):
    """Inicia cronômetro para uma etapa."""
    _timing_start[step_name] = time.time()


def _timing_end_step(step_name):
    """Encerra cronômetro e registra duração."""
    if step_name in _timing_start:
        elapsed = time.time() - _timing_start[step_name]
        log.info(f"⏱ {step_name}: {elapsed:.2f}s")
        del _timing_start[step_name]
        return elapsed
    return 0

def _get_tz_br():
    """
    Retorna timezone de Brasília.
    Usa zoneinfo quando disponível (Python 3.9+), senão offset fixo -03:00.
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Sao_Paulo")
    except Exception:
        return timezone(timedelta(hours=-3))


def _periodo_analise_hxh(data_dia: datetime, agora_br: datetime) -> tuple[str, str]:
    """Janela BRFlow por dia operacional: (D-1) 23:00 → agora (hoje) ou D 23:00 (fechado).

    Retorna strings no formato ``%d/%m/%Y %H:%M`` para datAnaliseInicial/Final.
    """
    dia = data_dia.date() if isinstance(data_dia, datetime) else data_dia
    agora_dia = agora_br.date() if isinstance(agora_br, datetime) else agora_br
    tz = getattr(agora_br, "tzinfo", None) or getattr(data_dia, "tzinfo", None)

    inicio_dt = datetime.combine(dia - timedelta(days=1), dt_time(23, 0), tzinfo=tz)
    if dia == agora_dia:
        fim_dt = agora_br.replace(second=0, microsecond=0)
    else:
        fim_dt = datetime.combine(dia, dt_time(23, 0), tzinfo=tz)

    fmt = "%d/%m/%Y %H:%M"
    return inicio_dt.strftime(fmt), fim_dt.strftime(fmt)


def _bounds_janela_operacional_hxh(
    target_date: date,
    agora_br: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Limites datetime (naive) da janela operacional: [D-1 23:00, fim).

    ``fim`` = agora (hoje) ou D 23:00 (dia fechado). O limite superior é exclusivo
    quando fecha em D 23:00 (hora 23 de D já pertence ao turno seguinte).
    """
    tz = _get_tz_br()
    if agora_br is None:
        agora_br = datetime.now(tz)
    elif agora_br.tzinfo is not None:
        agora_br = agora_br.astimezone(tz)

    inicio = datetime.combine(target_date - timedelta(days=1), dt_time(23, 0))
    if target_date == agora_br.date():
        # Inclusivo até o minuto atual: usa fim exclusivo = agora + 1 min
        fim = (agora_br.replace(second=0, microsecond=0) + timedelta(minutes=1)).replace(tzinfo=None)
    else:
        fim = datetime.combine(target_date, dt_time(23, 0))
    return inicio, fim


def _resolver_tempo_espera_minutos(settings=None, intervalo=None) -> int:
    """Tempo de espera entre ciclos em minutos (mínimo 5)."""
    raw = None
    if isinstance(settings, dict) and settings.get("tempo_espera_minutos") is not None:
        raw = settings.get("tempo_espera_minutos")
    elif intervalo is not None:
        # Compat: intervalo legado em segundos
        try:
            raw = max(5, int(round(float(intervalo) / 60.0)))
        except (TypeError, ValueError):
            raw = None
    if raw is None:
        env_raw = (os.getenv("PRODUCTION_TEMPO_ESPERA_MINUTOS") or "").strip()
        raw = env_raw or 60
    try:
        minutos = int(raw)
    except (TypeError, ValueError):
        minutos = 60
    return max(5, min(180, minutos))


def _resolver_dias_download_brflow(settings=None) -> int:
    """Quantidade de dias da produção BRFlow baixados em cada ciclo."""
    raw = None
    if isinstance(settings, dict) and settings.get("dias_download_brflow") is not None:
        raw = settings.get("dias_download_brflow")
    if raw is None:
        raw = (os.getenv("PRODUCTION_DIAS_DOWNLOAD_BRFLOW") or "").strip() or 3
    try:
        dias = int(raw)
    except (TypeError, ValueError):
        dias = 3
    return max(1, min(31, dias))


def _sleep_tempo_espera(minutos: int, log_interval_long=30, log_interval_short=1):
    """
    Aguarda `minutos` até o próximo ciclo.
    Faz sleeps em blocos para não travar o loop e respeita o parar_event.
    """
    minutos = max(5, int(minutos))
    tz = _get_tz_br()
    target = datetime.now(tz) + timedelta(minutes=minutos)
    _set_status(
        f"Aguardando {minutos} min · próximo às {target.strftime('%H:%M')}"
    )

    while not parar_event.is_set():
        restante = (target - datetime.now(tz)).total_seconds()
        if restante <= 0:
            break
        tempo_sleep = min(log_interval_long if restante > 10 else log_interval_short, restante)
        time.sleep(tempo_sleep)


def _detectar_sep(caminho_csv, default=','):
    """
    Detecta o separador do CSV usando csv.Sniffer.
    Fallback heurístico: conta ; vs , se Sniffer falhar.
    
    Args:
        caminho_csv: Caminho do arquivo CSV
        default: Separador padrão se detecção falhar
    
    Returns:
        Separador detectado ou padrão
    """
    try:
        with open(caminho_csv, 'r', encoding='utf-8', errors='ignore') as f:
            amostra = f.read(4096)
            if not amostra:
                return default
            try:
                dialect = csv.Sniffer().sniff(amostra)
                return dialect.delimiter
            except Exception:
                return ';' if amostra.count(';') > amostra.count(',') else ','
    except Exception as e:
        log.warning(f"Erro ao detectar separador: {e}. Usando padrão: {default}")
        return default


def _ler_csv_robusto(caminho, sep=None):
    """
    Lê CSV tentando múltiplos separadores e encodings.
    Prioriza: utf-8-sig > utf-8 > latin1 > cp1252
    
    Args:
        caminho: Caminho do arquivo CSV
        sep: Separador pré-definido (opcional)
    
    Returns:
        Tupla (DataFrame, separador_efetivo)
    """
    _sep = sep or _detectar_sep(caminho)
    encodings = ["utf-8-sig", "utf-8", "latin1", "cp1252"]
    
    for enc in encodings:
        try:
            df = pd.read_csv(
                caminho, sep=_sep, engine='python',
                on_bad_lines='skip', dtype=str, encoding=enc
            )
            if df.columns.duplicated().any():
                df = df.loc[:, ~df.columns.duplicated()]
                log.warning(f"CSV lido com encoding={enc} continha colunas duplicadas; duplicatas descartadas")
            log.debug(f"CSV lido com sucesso: encoding={enc}, sep='{_sep}', linhas={len(df)}")
            return df, _sep
        except Exception as e:
            log.debug(f"Falha ao ler com encoding {enc}: {type(e).__name__}")
            continue
    
    try:
        df = pd.read_csv(caminho, sep=_sep, engine='python', on_bad_lines='skip', dtype=str)
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated()]
            log.warning("CSV lido com encoding padrão continha colunas duplicadas; duplicatas descartadas")
        log.debug(f"CSV lido com encoding padrão: linhas={len(df)}")
        return df, _sep
    except Exception as e:
        log.error(f"Falha final ao ler CSV {caminho}: {e}")
        #notify("Produção", "erro", "❌ Falha ao ler arquivo CSV", f"Arquivo: {Path(caminho).name}\nTodos os encodings falharam")
        return pd.DataFrame(), _sep


def _validar_arquivo_confer_producao(df, data_esperada):
    """
    Valida relatório Confer no bot de produção.

    A tela já filtra por data inicial/final; aceita arquivo com estrutura
    esperada e linhas de dados mesmo quando a checagem estrita de data falha
    (ex.: separador/encoding diferente na leitura rápida).
    """
    if df is None or df.empty:
        return False

    colunas_lower = {str(c).strip().lower() for c in df.columns}
    tem_matricula = any(
        nome in colunas_lower
        for nome in (
            "matrícula do colaborador",
            "matricula do colaborador",
            "matrícula",
            "matricula",
        )
    )
    tem_data = any(
        "data" in nome
        for nome in colunas_lower
    )

    if not (tem_matricula and tem_data):
        return False

    try:
        if _validar_arquivo_confer(df, data_esperada, logger=log):
            return True
    except Exception:
        pass

    return len(df) > 0


def _garantir_csv_com_dados(caminho, sistema: str) -> Path:
    """Garante que o CSV baixado existe e contém ao menos uma linha de dados."""
    path = Path(caminho)
    label = _SISTEMA_LABEL.get(str(sistema).lower(), str(sistema))

    if not path.exists():
        raise RuntimeError(f"Relatório {label} não encontrado após download")

    if path.stat().st_size == 0:
        raise RuntimeError(f"Relatório {label} vazio (0 bytes): {path.name}")

    df, _ = _ler_csv_robusto(str(path))
    if df.empty:
        raise RuntimeError(f"Relatório {label} sem linhas de dados: {path.name}")

    return path


def _registrar_falha_sistema(sistema: str, mensagem: str, confer_erro, brflow_erro):
    """Atualiza estado de erro por sistema e retorna tupla (confer_erro, brflow_erro)."""
    label = _SISTEMA_LABEL.get(sistema, sistema)
    log.warning(f"Sistema {label} sem dados utilizáveis: {mensagem}")
    if sistema == "confer":
        return mensagem, brflow_erro
    return confer_erro, mensagem


EXPECTED_COLUNAS_PRODUCAO = [
    "Matrícula",
    "Data de Análise",
    "Hora",
    "Workflow",
    "Etapa",
    "Tempo Total",
    "Total de Análise",
    "Media_Tempo_por_Analise",
    "Meta",
    "Percentual",
]

_COLUMN_NORMALIZACAO_PRODUCAO = {
    "matrícula": "Matrícula",
    "matricula": "Matrícula",
    "matrícula do colaborador": "Matrícula",
    "matricula do colaborador": "Matrícula",
    "data de análise": "Data de Análise",
    "data de analise": "Data de Análise",
    "data/hora da conferência": "Data de Análise",
    "data hora da conferência": "Data de Análise",
    "workflow": "Workflow",
    "ilha": "Workflow",
    "etapa": "Etapa",
    "tempo total": "Tempo Total",
    "tempo de análise": "Tempo Total",
    "total de análise": "Total de Análise",
    "total de analise": "Total de Análise",
}

# Caminho para a planilha de metas
METAS_EXCEL_PATH = PASTA_CONFIG / "config_prod.xlsx"
METAS_SHEET_NAME = "Metas_Etapa"
HC_SHEET_NAME = "HC"


def _carregar_leader_por_matricula():
    try:
        df_hc = pd.read_excel(METAS_EXCEL_PATH, sheet_name=HC_SHEET_NAME, dtype=str)
        df_hc = df_hc.rename(columns=lambda c: str(c).strip())
        if not {"Agent: UserLanID", "FinalDate", "Leader: UserLanID"}.issubset(set(df_hc.columns)):
            return {}

        df_hc["Agent: UserLanID"] = df_hc["Agent: UserLanID"].astype(str).str.strip()
        df_hc["Leader: UserLanID"] = df_hc["Leader: UserLanID"].astype(str).str.strip()
        df_hc["FinalDate"] = df_hc["FinalDate"].apply(lambda v: str(v).strip() if pd.notna(v) else "")

        valid = df_hc[df_hc["FinalDate"].isin(["", "nan", "none", "null"])]
        return dict(zip(valid["Agent: UserLanID"], valid["Leader: UserLanID"]))
    except Exception as e:
        log.warning(f"Não foi possível ler aba HC de {METAS_EXCEL_PATH}: {e}")
        return {}


def _formatar_timedelta_hhmmss(valor_timedelta):
    if pd.isna(valor_timedelta):
        return "0:00:00"
    try:
        total_segundos = int(max(0, valor_timedelta.total_seconds()))
        horas = total_segundos // 3600
        minutos = (total_segundos % 3600) // 60
        segundos = total_segundos % 60
        return f"{horas}:{minutos:02d}:{segundos:02d}"
    except Exception:
        return str(valor_timedelta)

def _numero_robusto(valor):
    """
    Converte números em formato BR/US sem multiplicar por 10.
    
    Exemplos:
    123.0     -> 123.0
    123,0     -> 123.0
    1.234     -> 1234.0
    1.234,56  -> 1234.56
    1234.56   -> 1234.56
    """
    if pd.isna(valor):
        return 0.0

    texto = str(valor).strip()

    if texto == "":
        return 0.0

    # Remove espaços
    texto = texto.replace(" ", "")

    try:
        # Tem ponto e vírgula: padrão BR com milhar e decimal
        # Ex: 1.234,56
        if "." in texto and "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
            return float(texto)

        # Tem só vírgula: decimal BR
        # Ex: 123,0
        if "," in texto:
            texto = texto.replace(",", ".")
            return float(texto)

        # Tem só ponto
        if "." in texto:
            partes = texto.split(".")

            # Caso 1.234 / 12.345 / 123.456 = milhar
            if len(partes) == 2 and len(partes[1]) == 3 and partes[0].isdigit() and partes[1].isdigit():
                return float(texto.replace(".", ""))

            # Caso 123.0 / 1234.56 = decimal normal
            return float(texto)

        return float(texto)

    except Exception:
        return 0.0
    
def _parsear_tempo(valor, assume_horas=False):
    if pd.isna(valor):
        return pd.Timedelta(0)

    texto = str(valor).strip()
    if texto == "":
        return pd.Timedelta(0)

    try:
        if ":" in texto:
            parsed = pd.to_timedelta(texto, errors="coerce")
            if pd.notna(parsed):
                return parsed

        texto_normalizado = texto.replace(".", "").replace(",", ".")
        numerico = float(texto_normalizado)
        if assume_horas:
            return pd.to_timedelta(numerico, unit="h")
        return pd.to_timedelta(numerico, unit="s")
    except Exception:
        return pd.Timedelta(0)


def _extrair_hora(data_str):
    if pd.isna(data_str):
        return -1

    try:
        dt = pd.to_datetime(str(data_str).strip(), dayfirst=True, errors="coerce")
        return int(dt.hour) if pd.notna(dt) else -1
    except Exception:
        return -1


def _map_hour_slot(hora):
    try:
        h = int(hora)
        if 0 <= h <= 23:
            return h
        return None
    except Exception:
        return None

def _limpar_pasta_temporaria(pasta: Path, recriar=True):
    """
    Limpa arquivos e subpastas dentro da pasta temporária de produção.
    Proteção: só permite limpar caminhos dentro de DOWNLOADS_TEMP_BASE.
    """
    pasta = Path(pasta)

    try:
        pasta_abs = pasta.resolve()
        base_abs = DOWNLOADS_TEMP_BASE.resolve()

        # Proteção contra apagar pasta errada
        if base_abs not in pasta_abs.parents and pasta_abs != base_abs:
            raise RuntimeError(f"Bloqueado: tentativa de limpar pasta fora da área temporária: {pasta_abs}")

        if not pasta.exists():
            if recriar:
                pasta.mkdir(parents=True, exist_ok=True)
            return

        for item in pasta.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink(missing_ok=True)
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
            except Exception as e:
                log.warning(f"Falha ao remover item temporário {item}: {e}")

        if recriar:
            pasta.mkdir(parents=True, exist_ok=True)

        log.info(f"Pasta temporária limpa: {pasta}")

    except Exception as e:
        log.warning(f"Falha ao limpar pasta temporária {pasta}: {e}")

def _to_numeric_total_analise(valor):
    if pd.isna(valor):
        return 0.0
    if isinstance(valor, pd.Timedelta):
        return valor.total_seconds()
    texto = str(valor).strip()
    if texto == "":
        return 0.0
    if ":" in texto:
        td = _parsear_tempo(texto, assume_horas=False)
        return td.total_seconds() if pd.notna(td) else 0.0
    try:
        return float(texto)
    except Exception:
        return 0.0


def _series_tempo_total_para_segundos(serie: pd.Series | None) -> pd.Series:
    """Converte Tempo Total (HH:MM:SS / número / Timedelta) para segundos — vetorizado."""
    if serie is None:
        return pd.Series(dtype="float64")
    s = serie.copy()
    out = pd.Series(0.0, index=s.index, dtype="float64")
    mask_na = s.isna()
    if mask_na.all():
        return out

    # Timedelta nativo
    if pd.api.types.is_timedelta64_dtype(s):
        secs = s.dt.total_seconds().fillna(0.0)
        out.loc[~mask_na] = secs.loc[~mask_na]
        return out

    as_str = s.astype(str).str.strip()
    mask_empty = mask_na | as_str.isin(["", "nan", "None", "NaT"])
    mask_clock = (~mask_empty) & as_str.str.contains(":", regex=False)
    if mask_clock.any():
        td = pd.to_timedelta(as_str.loc[mask_clock], errors="coerce")
        out.loc[mask_clock] = td.dt.total_seconds().fillna(0.0)

    mask_num = (~mask_empty) & (~mask_clock)
    if mask_num.any():
        nums = pd.to_numeric(
            as_str.loc[mask_num].str.replace(",", ".", regex=False),
            errors="coerce",
        ).fillna(0.0)
        out.loc[mask_num] = nums
    return out


def _series_total_analise_para_numero(serie: pd.Series | None) -> pd.Series:
    """Converte Total de Análise para float — vetorizado."""
    if serie is None:
        return pd.Series(dtype="float64")
    s = serie.copy()
    out = pd.Series(0.0, index=s.index, dtype="float64")
    mask_na = s.isna()
    if mask_na.all():
        return out
    if pd.api.types.is_timedelta64_dtype(s):
        out.loc[~mask_na] = s.dt.total_seconds().fillna(0.0).loc[~mask_na]
        return out
    as_str = s.astype(str).str.strip()
    mask_empty = mask_na | as_str.isin(["", "nan", "None", "NaT"])
    mask_clock = (~mask_empty) & as_str.str.contains(":", regex=False)
    if mask_clock.any():
        td = pd.to_timedelta(as_str.loc[mask_clock], errors="coerce")
        out.loc[mask_clock] = td.dt.total_seconds().fillna(0.0)
    mask_num = (~mask_empty) & (~mask_clock)
    if mask_num.any():
        nums = pd.to_numeric(
            as_str.loc[mask_num].str.replace(",", ".", regex=False),
            errors="coerce",
        ).fillna(0.0)
        out.loc[mask_num] = nums
    return out


def _processar_dataframe_producao(df: pd.DataFrame, target_date: date | None = None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=EXPECTED_COLUNAS_PRODUCAO)

    renamed = {}
    for col in df.columns:
        chave = str(col).strip().lower()
        renamed[col] = _COLUMN_NORMALIZACAO_PRODUCAO.get(chave, None) or str(col).strip()

    df = df.rename(columns=renamed)
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()]
        log.warning("DataFrame de produção continha colunas duplicadas após normalização; duplicatas descartadas")
    df = df[[col for col in EXPECTED_COLUNAS_PRODUCAO if col in df.columns]].copy()
    if "Leader: UserLanID" not in df.columns:
        df["Leader: UserLanID"] = ""

    if target_date is None:
        tz_br = _get_tz_br()
        target_date = datetime.now(tz_br).date()

    if "Data de Análise" in df.columns:
        df["Data de Análise"] = pd.to_datetime(
            df["Data de Análise"],
            dayfirst=True,
            errors="coerce"
        )

    # Janela operacional 23h (D-1) → agora / 23h (D) — não cortar madrugada do turno.
    inicio, fim = _bounds_janela_operacional_hxh(target_date)
    ts = df["Data de Análise"]
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_localize(None)
    df = df[(ts >= inicio) & (ts < fim)].copy()

    log.info(
        f"Filtrado janela operacional {inicio.strftime('%d/%m/%Y %H:%M')} → "
        f"{fim.strftime('%d/%m/%Y %H:%M')} (dia ref {target_date}): {len(df)} linhas restantes"
    )
    df["matricula_valida"] = (
    df["Matrícula"]
    .astype(str)
    .str.match(r"^[A-Za-z]\d{5}[A-Za-z]$", na=False)
    )

    df = df[df["matricula_valida"]].copy()
    df.drop(columns="matricula_valida", inplace=True)   

    for col in EXPECTED_COLUNAS_PRODUCAO:
        if col not in df.columns:
            if col == "Hora":
                df[col] = -1
            elif col in ["Media_Tempo_por_Analise", "Meta"]:
                df[col] = ""
            else:
                df[col] = ""

    if "Data de Análise" in df.columns:
        # Vetorizado: apply célula-a-célula em ~180k linhas do BRFlow trava o ciclo (>1h).
        hora_vals = df["Data de Análise"].dt.hour
        df["Hora"] = hora_vals.fillna(-1).astype(int)
        df.loc[hora_vals.isna(), "Hora"] = -1
        df["Data de Análise"] = df["Data de Análise"].dt.date
    else:
        df["Hora"] = -1

    col_tempo = df.get("Tempo Total")
    if isinstance(col_tempo, pd.DataFrame):
        col_tempo = col_tempo.iloc[:, 0]

    col_total = df.get("Total de Análise")
    if isinstance(col_total, pd.DataFrame):
        col_total = col_total.iloc[:, 0]

    df["Tempo Total"] = _series_tempo_total_para_segundos(col_tempo)
    df["Total de Análise"] = _series_total_analise_para_numero(col_total)

    df_agrupado = (
        df.groupby(["Data de Análise","Matrícula", "Hora", "Workflow","Etapa"], sort=False, dropna=False, as_index=False)
        .agg({
            "Tempo Total": "sum",
            "Total de Análise": "sum",
        })
    )

    # Calcular média: Tempo Total em segundos / Total de Análise
    def calcular_media(row):
        tempo_total = row["Tempo Total"]
        total_analise = row["Total de Análise"]
        if pd.isna(total_analise) or total_analise == 0:
            return 0
        if isinstance(tempo_total, pd.Timedelta):
            tempo_segundos = tempo_total.total_seconds()
        else:
            tempo_segundos = float(tempo_total)
        if isinstance(total_analise, pd.Timedelta):
            analise_count = total_analise.total_seconds()
        else:
            analise_count = float(total_analise)
        if analise_count == 0:
            return 0
        return int(round(tempo_segundos / analise_count))

    df_agrupado["Media_Tempo_por_Analise"] = df_agrupado.apply(calcular_media, axis=1)

    # Ler planilha de metas e fazer merge
    try:
        df_metas = pd.read_excel(METAS_EXCEL_PATH, sheet_name=METAS_SHEET_NAME, dtype=str)
        df_metas = df_metas.rename(columns={"Nome_Etapa": "Etapa", "Meta_Dia": "Meta"})
        df_metas = df_metas[["Etapa", "Meta"]].drop_duplicates()
        df_agrupado = df_agrupado.merge(df_metas, on="Etapa", how="left")
    except Exception as e:
        log.warning(f"Erro ao ler planilha de metas: {e}. Coluna Meta será vazia.")
        df_agrupado["Meta"] = ""

    df_agrupado["Meta_Num"] = pd.to_numeric(
        df_agrupado["Meta"].astype(str).str.replace(r"\.", "", regex=True).str.replace(",", ".", regex=False),
        errors="coerce",
    ).fillna(0.0)

    def calcular_percentual(row):
        total_analise = row["Total de Análise"]
        meta = row["Meta_Num"]
        if meta == 0:
            return 0.0
        total_val = _to_numeric_total_analise(total_analise)
        return round((total_val / meta) * 100, 2)

    df_agrupado["Percentual"] = df_agrupado.apply(calcular_percentual, axis=1)

    # Tempo Total / Total de Análise já estão em números (segundos / contagem).
    df_saida = df_agrupado[EXPECTED_COLUNAS_PRODUCAO].copy()
    df_saida["Percentual"] = df_agrupado["Percentual"]
    return df_saida


def _construir_resumo_horas_por_matricula(df: pd.DataFrame) -> pd.DataFrame:
    def formatar_hora_pct(total, pct):
        if total == 0:
            return ""
        return f"{int(total)} ({pct:.2f}%)"

    if df.empty:
        cols = ["Matrícula"] + [f"{h:02d}:00" for h in range(24)] + ["total", "Leader: UserLanID"]
        return pd.DataFrame(columns=cols)

    df_resumo = df.copy()
    df_resumo["Hora_Slot"] = df_resumo["Hora"].apply(_map_hour_slot)
    df_resumo = df_resumo[df_resumo["Hora_Slot"].notna()].copy()
    df_resumo["Hora_Slot"] = df_resumo["Hora_Slot"].astype(int)
    df_resumo["Total de Análise"] = df_resumo["Total de Análise"].apply(_to_numeric_total_analise)
    df_resumo["Percentual"] = pd.to_numeric(df_resumo["Percentual"], errors="coerce").fillna(0.0)

    leader_map = _carregar_leader_por_matricula()

    agrupado = (
        df_resumo.groupby(["Matrícula", "Hora_Slot"], sort=False, as_index=False)
        .agg({
            "Total de Análise": "sum",
            "Percentual": "sum",
        })
    )

    linhas = []
    for matricula, grupo in agrupado.groupby("Matrícula", sort=False):
        linha = {"Matrícula": matricula}
        total_geral = 0.0
        pct_geral = 0.0
        
        # Encontrar a primeira hora com atividade para este usuário
        horas_com_atividade = sorted(grupo["Hora_Slot"].unique())
        primeira_hora_ativa = horas_com_atividade[0] if horas_com_atividade else None

        for hora in range(24):
            total_hora = grupo.loc[grupo["Hora_Slot"] == hora, "Total de Análise"].sum()
            pct_hora = grupo.loc[grupo["Hora_Slot"] == hora, "Percentual"].sum()
            
            # Regra: se produção da primeira hora ativa do usuário for menor que 25, somar na próxima hora
            if hora == primeira_hora_ativa and total_hora < 25 and total_hora > 0:
                # Encontrar a próxima hora com atividade e somar
                proximas_horas = [h for h in horas_com_atividade if h > hora]
                if proximas_horas:
                    proxima_hora = proximas_horas[0]
                    total_proxima = grupo.loc[grupo["Hora_Slot"] == proxima_hora, "Total de Análise"].sum()
                    pct_proxima = grupo.loc[grupo["Hora_Slot"] == proxima_hora, "Percentual"].sum()
                    if total_proxima > 0:  # Só somar se houver produção na próxima hora
                        linha[f"{proxima_hora:02d}:00"] = formatar_hora_pct(total_hora + total_proxima, pct_hora + pct_proxima)
                        total_geral += total_hora + total_proxima
                        pct_geral += pct_hora + pct_proxima
                        # Marcar próxima hora como processada
                        grupo = grupo[grupo["Hora_Slot"] != proxima_hora]
                    else:
                        # Se não há produção na próxima hora, manter na primeira hora
                        linha[f"{hora:02d}:00"] = formatar_hora_pct(total_hora, pct_hora)
                        total_geral += total_hora
                        pct_geral += pct_hora
                else:
                    # Se não há próxima hora, manter na primeira hora
                    linha[f"{hora:02d}:00"] = formatar_hora_pct(total_hora, pct_hora)
                    total_geral += total_hora
                    pct_geral += pct_hora
            elif total_hora > 0 or hora == primeira_hora_ativa:
                # Mostrar horas com atividade ou a primeira hora mesmo sem atividade
                if f"{hora:02d}:00" not in linha:  # Não sobrescrever se já foi processada
                    linha[f"{hora:02d}:00"] = formatar_hora_pct(total_hora, pct_hora)
                    total_geral += total_hora
                    pct_geral += pct_hora

        linha["total"] = formatar_hora_pct(total_geral, pct_geral)
        linha["Leader: UserLanID"] = leader_map.get(str(matricula).strip(), "")
        linhas.append(linha)

    df_final = pd.DataFrame(linhas)
    colunas = ["Matrícula"] + [f"{h:02d}:00" for h in range(24)] + ["total", "Leader: UserLanID"]
    for col in colunas:
        if col not in df_final.columns:
            df_final[col] = ""

    return df_final[colunas]


def _esperar_arquivo_estavel(path: Path, timeout=30, intervalo=0.5):
    """
    Aguarda até o arquivo estar estável (tamanho não muda entre leituras).
    Reduz o risco de ler arquivo ainda sendo escrito.
    
    Args:
        path: Caminho do arquivo a monitorar
        timeout: Tempo máximo de espera em segundos
        intervalo: Intervalo entre verificações em segundos
    
    Returns:
        True se arquivo estabilizou, False se timeout expirou
    """
    path = Path(path)
    if not path.exists():
        log.warning(f"Arquivo não existe: {path}")
        #notify("Produção", "aviso", "⚠️ Arquivo não encontrado", f"Caminho: {path}")
        return False
    
    t_ini = time.time()
    tamanho_ant = -1
    
    while time.time() - t_ini < timeout:
        try:
            tamanho = path.stat().st_size
        except FileNotFoundError:
            time.sleep(intervalo)
            continue
        
        if tamanho > 0 and tamanho == tamanho_ant:
            log.debug(f"Arquivo estável: {path.name} ({tamanho} bytes)")
            return True
        
        tamanho_ant = tamanho
        time.sleep(intervalo)
    
    log.warning(f"Timeout aguardando estabilidade do arquivo: {path}")
    #notify("Produção", "aviso", "⚠️ Arquivo pode estar corrompido", f"Arquivo: {path.name}\nNão estabilizou em {timeout}s")
    return False



def start(settings=None):
    """
    Inicia o robô de produção (ponto de entrada padrão).
    
    Args:
        settings: Dicionário com configurações customizadas (opcional)
    """
    baixar_production_loop(settings=settings)


def stop():
    """Solicita parada do robô de forma cooperativa."""
    parar_event.set()


def baixar_production_loop(intervalo=DEFAULT_INTERVAL, tempo_max=None, settings=None):
    """
    Loop principal de extração de relatórios de produtividade.
    
    Args:
        intervalo: Intervalo legado em segundos (só usado se tempo_espera_minutos não vier em settings)
        tempo_max: Tempo máximo de execução em segundos
        settings: Dicionário com configurações de caminhos e credenciais
    """
    _reset_progress_state()
    _set_status("Producao: iniciando loop")
    _set_progress(0, "Producao: iniciando loop")

    tempo_espera_minutos = _resolver_tempo_espera_minutos(settings, intervalo)
    _set_status(f"Producao: tempo de espera entre ciclos = {tempo_espera_minutos} min")

    with MetricsContext("production") as exec_ctx:
        inicio = time.time()
        ciclo = 1
        error_handler = ErrorRecoveryHandler(max_errors=5, retry_delay=2)
        drv = None
        tz_br = _get_tz_br()
        
        try:
            while not parar_event.is_set():
                _executar_ciclo_producao(
                    ciclo,
                    drv,
                    exec_ctx,
                    error_handler,
                    tempo_max,
                    inicio,
                    settings,
                    tempo_espera_minutos,
                )
                ciclo += 1

        finally:
            try:
                if drv:
                    safe_close_driver(drv)
            except Exception as e:
                log.error(f"Erro ao fechar driver: {e}")
            _set_status("Producao: loop encerrado")


def _executar_ciclo_producao(ciclo, drv, exec_ctx, error_handler, tempo_max, inicio, settings, tempo_espera_minutos):
    """
    Executa um ciclo completo de extração de produtividade.
    
    Args:
        ciclo: Número do ciclo
        drv: Webdriver (pode ser None)
        exec_ctx: Contexto de execução
        error_handler: Gerenciador de erros
        tempo_max: Tempo máximo de execução
        inicio: Timestamp de início
        settings: Configurações customizadas
        tempo_espera_minutos: Espera entre ciclos (mínimo 5)
    """
    try:
        tz_br = _get_tz_br()
        rodar_imediatamente = bool((settings or {}).get("rodar_imediatamente", True))
        
        if ciclo == 1 and rodar_imediatamente:
            _set_status("Primeira execução imediata; próximas respeitarão o tempo de espera.")
        else:
            _sleep_tempo_espera(tempo_espera_minutos)
        
        if tempo_max and (time.time() - inicio) > tempo_max:
            _set_status("Tempo máximo atingido, encerrando")
            parar_event.set()
            return
        
        _set_status(f"Producao: iniciando ciclo {ciclo}")
        _begin_cycle()
        _executar_extracao_principal_e_ged(ciclo, drv, exec_ctx, tz_br, settings)
        error_handler.reset()

    except FalhaAmbosSistemasDownload as e:
        log.error(str(e))
        if not _exec_summary.get("notificado"):
            _registrar_erro_producao(str(e)[:120])
            _enviar_notificacao_ciclo()
        try:
            _tirar_screenshot_erro(drv, f"falha_ambos_ciclo{ciclo}", "production")
        except Exception:
            pass
        try:
            output_cfg = (os.getenv("PRODUCTION_OUTPUT_DIR") or os.getenv("ROBOT_OUTPUT_DIR") or "").strip()
            local_sharepoint_produção = Path(
                (settings or {}).get("local_sharepoint_producao") or output_cfg or DEFAULT_SHAREPOINT_PRODUCAO
            )
            _salvar_log_execucao(
                local_sharepoint_produção=local_sharepoint_produção,
                confer_ok=False,
                brflow_ok=False,
                confer_erro=e.confer_erro,
                brflow_erro=e.brflow_erro,
                data_processamento=datetime.now(_get_tz_br()),
            )
        except Exception:
            pass
        if not error_handler.handle_error(
            drv, f"Falha download em ambos os sistemas (ciclo {ciclo})", _set_status, log
        ):
            parar_event.set()

    except (TimeoutException, WebDriverException) as e:
        log.error(f"Erro Selenium no ciclo {ciclo}: {e}")
        if not _exec_summary.get("notificado"):
            _reset_exec_summary()
            _exec_summary["ciclo"] = ciclo
            _inicializar_status_producao()
            _registrar_erro_producao(f"Erro Selenium (ciclo {ciclo}): {str(e)[:100]}")
            _enviar_notificacao_ciclo()
        try:
            _tirar_screenshot_erro(drv, f"selenium_erro_ciclo{ciclo}", "production")
        except Exception:
            pass
        try:
            output_cfg = (os.getenv("PRODUCTION_OUTPUT_DIR") or os.getenv("ROBOT_OUTPUT_DIR") or "").strip()
            local_sharepoint_produção = Path(
                (settings or {}).get("local_sharepoint_producao") or output_cfg or DEFAULT_SHAREPOINT_PRODUCAO
            )
            _salvar_log_execucao(
                local_sharepoint_produção=local_sharepoint_produção,
                status_custom=f"❌ Erro Selenium (ciclo {ciclo}): {str(e)[:200]}",
                data_processamento=datetime.now(_get_tz_br()),
            )
        except Exception:
            pass
        if not error_handler.handle_error(drv, f"Erro Selenium ciclo {ciclo}", _set_status, log):
            parar_event.set()
            
    except Exception as e:
        log.error(f"Erro inesperado no ciclo {ciclo}: {e}", exc_info=True)
        if not _exec_summary.get("notificado"):
            _reset_exec_summary()
            _exec_summary["ciclo"] = ciclo
            _inicializar_status_producao()
            _registrar_erro_producao(f"Erro no ciclo {ciclo}: {str(e)[:100]}")
            _enviar_notificacao_ciclo()
        try:
            _tirar_screenshot_erro(drv, f"erro_ciclo{ciclo}", "production")
        except Exception:
            pass
        try:
            output_cfg = (os.getenv("PRODUCTION_OUTPUT_DIR") or os.getenv("ROBOT_OUTPUT_DIR") or "").strip()
            local_sharepoint_produção = Path(
                (settings or {}).get("local_sharepoint_producao") or output_cfg or DEFAULT_SHAREPOINT_PRODUCAO
            )
            _salvar_log_execucao(
                local_sharepoint_produção=local_sharepoint_produção,
                status_custom=f"❌ Erro no ciclo {ciclo}: {str(e)[:200]}",
                data_processamento=datetime.now(_get_tz_br()),
            )
        except Exception:
            pass
        if not error_handler.handle_error(drv, f"Erro geral ciclo {ciclo}", _set_status, log):
            parar_event.set()


def _executar_extracao_principal_e_ged(ciclo, drv, exec_ctx, tz_br, settings):
    """Executa o ciclo e publica uma unica conclusao depois do GED.

    A excecao da extracao principal e preservada, mas o GED continua sendo
    tentado. Notificacao e progresso final ficam centralizados aqui para nao
    anunciarem conclusao antes de o resultado do GED estar registrado.
    """
    try:
        return _executar_extracao(ciclo, drv, exec_ctx, tz_br, settings)
    finally:
        _maybe_run_ged_irregularidade_reinspecao(settings)
        if not parar_event.is_set():
            _set_progress(100, "Producao: ciclo concluido")
        _enviar_notificacao_ciclo()

def normalizar_brflow(df):
    if df.empty:
        return df

    df = df.copy()
    df.columns = df.columns.map(str).str.strip()

    colunas_desejadas = [
        "Matrícula",
        "Data de Análise",
        "Workflow",
        "Etapa",
        "Tempo Total",
        "Total de Análise",
    ]

    df = df[[c for c in colunas_desejadas if c in df.columns]].copy()

    return df

def normalizar_confer(df):
    if df.empty:
        return df

    df = df.copy()
    df.columns = df.columns.map(str).str.strip()

    df = df.rename(columns={
        "Data/Hora da Conferência": "Data de Análise",
        "Tempo de Análise": "Tempo Total",
        "Ilha": "Workflow",
        "Matrícula do Colaborador": "Matrícula"
    })

    # Normalizar matrícula do Confer para minúsculo
    if "Matrícula" in df.columns:
        df["Matrícula"] = (
            df["Matrícula"]
            .astype(str)
            .str.strip()
            .str.lower()
        )

    # Cada linha do Confer equivale a 1 análise/protocolo
    if "Total de Análise" not in df.columns:
        df["Total de Análise"] = 1

    colunas_desejadas = [
        "Matrícula",
        "Data de Análise",
        "Workflow",
        "Etapa",
        "Tempo Total",
        "Total de Análise",
    ]

    df = df[[c for c in colunas_desejadas if c in df.columns]].copy()

    return df

def _salvar_log_execucao(local_sharepoint_produção, confer_ok=None, brflow_ok=None, confer_erro=None, brflow_erro=None, data_processamento=None, status_custom=None):
    """
    Salva um arquivo JSON com resultado da execução.
    Sobrescreve o arquivo anterior a cada execução (sem histórico).
    
    Args:
        local_sharepoint_produção: Caminho base do SharePoint de Produção
        confer_ok: Boolean indicando se Confer foi bem-sucedido
        brflow_ok: Boolean indicando se BRFlow foi bem-sucedido
        confer_erro: String com mensagem de erro do Confer (se houver)
        brflow_erro: String com mensagem de erro do BRFlow (se houver)
        data_processamento: Datetime de processamento (opcional)
        status_custom: String customizada para o log (se fornecida, usa essa em vez de processar os booleans)
    """
    try:
        pasta_log = DEFAULT_SHAREPOINT_BOTS / "Log" / "Producao"
        pasta_log.mkdir(parents=True, exist_ok=True)
        
        arquivo_log = pasta_log / "execucao.json"
        
        if status_custom:
            status = status_custom
            conteudo = {
                "status": status,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            if confer_ok and brflow_ok:
                status = "✓ AMBOS OS SISTEMAS COM SUCESSO"
            elif confer_ok and not brflow_ok:
                status = "⚠ SUCESSO PARCIAL (brflow falhou)"
            elif brflow_ok and not confer_ok:
                status = "⚠ SUCESSO PARCIAL (confer falhou)"
            else:
                status = "❌ FALHA EM AMBOS OS SISTEMAS"

            conteudo = {
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "confer_ok": bool(confer_ok),
                "brflow_ok": bool(brflow_ok),
            }
            if data_processamento is not None:
                conteudo["data_processamento"] = data_processamento.isoformat()
            if confer_erro:
                conteudo["confer_erro"] = str(confer_erro)[:500]
            if brflow_erro:
                conteudo["brflow_erro"] = str(brflow_erro)[:500]

        with open(arquivo_log, 'w', encoding='utf-8') as f:
            json.dump(conteudo, f, ensure_ascii=False, indent=2)

        log.info(f"Log de execução salvo: {arquivo_log}")
        
    except Exception as e:
        log.error(f"Erro ao salvar log de execução: {e}", exc_info=True)


def _executar_extracao(ciclo, drv, exec_ctx, tz_br, settings):
    _reset_exec_summary()
    _exec_summary["ciclo"] = ciclo
    s = settings or {}
    _inicializar_status_producao(s)

    executar_confer = _production_flag(s, "executar_confer", True)
    executar_brflow = _production_flag(s, "executar_brflow", True)

    if not executar_confer and not executar_brflow:
        _set_status("Producao: Confer e BRFlow desabilitados — apenas pós-ciclo")
        _registrar_status_producao(TASK_PLANILHA, False, "sem fontes")
        _maybe_run_produtividade_case(s)
        return

    local_download = DOWNLOADS_TEMP_PRODUCTION
    local_download.mkdir(parents=True, exist_ok=True)

    # Limpa temporários ao iniciar o ciclo
    _set_status("Limpando pastas temporárias antes da extração")
    _limpar_pasta_temporaria(local_download, recriar=True)

    output_cfg = (os.getenv("PRODUCTION_OUTPUT_DIR") or os.getenv("ROBOT_OUTPUT_DIR") or "").strip()
    local_sharepoint_produção = Path(
        s.get("local_sharepoint_producao") or output_cfg or DEFAULT_SHAREPOINT_PRODUCAO
    )

    prefixo = s.get("prefixo") or "relatorio_produtividade"
    extensao = s.get("extensao") or ".csv"
    matricula = s.get("matricula") or os.getenv("NIVEL_USER")
    senha = s.get("senha") or os.getenv("NIVEL_PASS")

    if okta is None or brflow is None:
        raise RuntimeError("Seletores não configurados")

    # Salva log inicial indicando que a extração está em andamento
    try:
        _salvar_log_execucao(
            local_sharepoint_produção=local_sharepoint_produção,
            status_custom="⏳ Rodando extração... aguardando resultados dos sistemas"
        )
    except Exception as e:
        log.warning(f"Erro ao salvar log inicial: {e}")

    _set_status("Iniciando extração paralela: Confer + BRFlow")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = []
        future_por_sistema = {}

        if executar_confer:
            future_confer = executor.submit(
                _worker_confer,
                local_download,
                matricula,
                senha,
                settings,
            )
            futures.append(future_confer)
            future_por_sistema[future_confer] = "confer"
        else:
            future_confer = None

        if executar_brflow:
            future_brflow = executor.submit(
                _worker_brflow,
                local_download,
                prefixo,
                extensao,
                matricula,
                senha,
                tz_br,
                settings,
            )
            futures.append(future_brflow)
            future_por_sistema[future_brflow] = "brflow"
        else:
            future_brflow = None

        confer_result = None
        brflow_result = None
        confer_erro = None
        brflow_erro = None

        for future in as_completed(futures):
            if parar_event.is_set():
                return

            try:
                result = future.result()
                sistema = result.get("type")

                if sistema == "confer":
                    confer_result = result
                    _set_status("✓ Confer: download concluído com sucesso")

                elif sistema == "brflow":
                    brflow_result = result
                    _set_status("✓ BRFlow: download concluído com sucesso")

                else:
                    raise RuntimeError(f"Tipo de resultado desconhecido: {sistema}")

            except Exception as e:
                sistema = future_por_sistema[future]
                label = _SISTEMA_LABEL[sistema]

                if sistema == "confer":
                    confer_erro = str(e)
                    _registrar_status_producao(TASK_CONFER, False, str(e)[:40])
                else:
                    brflow_erro = str(e)
                    _registrar_status_producao(TASK_BRFLOW, False, str(e)[:40])

                log.error(f"❌ Erro ao extrair dados do {label}: {e}", exc_info=True)
                _set_status(f"⚠ {label} falhou: {str(e)[:80]}")
                _registrar_erro_producao(f"{label}: {str(e)[:100]}")
                continue

    sistemas_habilitados = sum((executar_confer, executar_brflow))
    if not confer_result and not brflow_result and sistemas_habilitados:
        if _exec_summary["task_status"].get(TASK_LOG_EVENTOS, {}).get("estado") == "pendente":
            _registrar_status_producao(TASK_LOG_EVENTOS, False, "download falhou")
        if _exec_summary["task_status"].get(TASK_MONITOR, {}).get("estado") == "pendente":
            _registrar_status_producao(TASK_MONITOR, False, "download falhou")
        if sistemas_habilitados == 1:
            unico = "Confer" if executar_confer else "BRFlow"
            erro = confer_erro if executar_confer else brflow_erro
            _registrar_erro_producao(f"Falha em {unico}: {erro or 'sem dados'}")
        else:
            _registrar_erro_producao(
                f"Falha em ambos os sistemas — Confer: {confer_erro or 'sem dados'} | "
                f"BRFlow: {brflow_erro or 'sem dados'}"
            )
        raise FalhaAmbosSistemasDownload(confer_erro=confer_erro, brflow_erro=brflow_erro)

    # Carrega dados disponíveis e descarta sistemas com arquivo vazio
    if confer_result:
        try:
            confer_csv_path = _garantir_csv_com_dados(confer_result["path"], "confer")
            df_confer, _ = _ler_csv_robusto(str(confer_csv_path))
            df_confer = normalizar_confer(df_confer)
            if df_confer.empty:
                msg = f"Relatório Confer sem dados após normalização ({confer_csv_path.name})"
                confer_erro, brflow_erro = _registrar_falha_sistema("confer", msg, confer_erro, brflow_erro)
                _registrar_status_producao(TASK_CONFER, False, "sem dados")
                _registrar_erro_producao(f"Confer: {msg}")
                confer_result = None
                df_confer = pd.DataFrame()
            else:
                confer_result = {**confer_result, "path": confer_csv_path}
                _registrar_status_producao(TASK_CONFER, True)
        except Exception as e:
            confer_erro, brflow_erro = _registrar_falha_sistema("confer", str(e), confer_erro, brflow_erro)
            _registrar_status_producao(TASK_CONFER, False, str(e)[:40])
            _registrar_erro_producao(f"Confer: {str(e)[:100]}")
            confer_result = None
            df_confer = pd.DataFrame()
    else:
        df_confer = pd.DataFrame()
        if confer_erro and TASK_CONFER in _exec_summary["task_status"]:
            if _exec_summary["task_status"][TASK_CONFER].get("estado") == "pendente":
                _registrar_status_producao(TASK_CONFER, False, str(confer_erro)[:40])

    if brflow_result:
        try:
            brflow_csv_path = _garantir_csv_com_dados(brflow_result["path"], "brflow")
            data_processamento = brflow_result["data"]
            df_brflow, _ = _ler_csv_robusto(str(brflow_csv_path))
            df_brflow = normalizar_brflow(df_brflow)
            if df_brflow.empty:
                msg = f"Relatório BRFlow sem dados após normalização ({brflow_csv_path.name})"
                confer_erro, brflow_erro = _registrar_falha_sistema("brflow", msg, confer_erro, brflow_erro)
                _registrar_status_producao(TASK_BRFLOW, False, "sem dados")
                _registrar_erro_producao(f"BRFlow: {msg}")
                brflow_result = None
                df_brflow = pd.DataFrame()
                data_processamento = datetime.now(tz_br)
            else:
                brflow_result = {**brflow_result, "path": brflow_csv_path}
                _registrar_status_producao(TASK_BRFLOW, True)
                monitor_ok = brflow_result.get("monitor_ok")
                if monitor_ok is True:
                    _registrar_status_producao(TASK_MONITOR, True)
                elif monitor_ok is False:
                    _registrar_status_producao(TASK_MONITOR, False, "download falhou")
        except Exception as e:
            confer_erro, brflow_erro = _registrar_falha_sistema("brflow", str(e), confer_erro, brflow_erro)
            _registrar_status_producao(TASK_BRFLOW, False, str(e)[:40])
            _registrar_erro_producao(f"BRFlow: {str(e)[:100]}")
            brflow_result = None
            df_brflow = pd.DataFrame()
            data_processamento = datetime.now(tz_br)
    else:
        df_brflow = pd.DataFrame()
        data_processamento = datetime.now(tz_br)
        if brflow_erro and TASK_BRFLOW in _exec_summary["task_status"]:
            if _exec_summary["task_status"][TASK_BRFLOW].get("estado") == "pendente":
                _registrar_status_producao(TASK_BRFLOW, False, str(brflow_erro)[:40])

    monitor_final = _unificar_log_eventos_com_monitor_hxh(
        confer_result,
        brflow_result,
        data_processamento,
        settings,
    )
    _publicar_monitor_base_se_necessario(brflow_result, monitor_final)

    if not confer_result and not brflow_result:
        _registrar_erro_producao(
            f"Falha em ambos os sistemas — Confer: {confer_erro or 'sem dados'} | "
            f"BRFlow: {brflow_erro or 'sem dados'}"
        )
        raise FalhaAmbosSistemasDownload(confer_erro=confer_erro, brflow_erro=brflow_erro)

    if confer_erro:
        log.warning(f"Sistema Confer falhou. Continuando com BRFlow apenas. Erro: {confer_erro}")
    if brflow_erro:
        log.warning(f"Sistema BRFlow falhou. Continuando com Confer apenas. Erro: {brflow_erro}")

    _set_status("Download concluído dos sistema(s) disponível(is)")

    # ✅ Join dos arquivos (um ou ambos podem ter dados)
    _timing_start_step("Join CSVs")

    df_combined = pd.concat([df_confer, df_brflow], ignore_index=True)

    df_combined.columns = df_combined.columns.map(str).str.strip()
    df_combined = df_combined.loc[:, ~df_combined.columns.duplicated()]

    if df_combined.empty:
        _registrar_erro_producao("União Confer/BRFlow resultou vazia")
        raise FalhaAmbosSistemasDownload(
            confer_erro=confer_erro or "Relatório Confer vazio",
            brflow_erro=brflow_erro or "Relatório BRFlow vazio",
        )

    combined_csv_path = local_download / f"{prefixo}_combined_{data_processamento.strftime('%Y%m%d')}.csv"

    df_combined.to_csv(
        str(combined_csv_path),
        index=False,
        sep=';',
        encoding='utf-8-sig'
    )

    _timing_end_step("Join CSVs")

    try:
        _processar_e_salvar_csv(
            combined_csv_path,
            local_sharepoint_produção,
            prefixo,
            data_processamento,
        )
        _registrar_status_producao(TASK_PLANILHA, True)
    except Exception as exc:
        _registrar_status_producao(TASK_PLANILHA, False, "falha ao salvar")
        _registrar_erro_producao(f"Planilha: {str(exc)[:100]}")
        raise

    # Salva log de execução com resultado dos sistemas
    _salvar_log_execucao(
        local_sharepoint_produção=local_sharepoint_produção,
        confer_ok=confer_result is not None,
        brflow_ok=brflow_result is not None,
        confer_erro=confer_erro,
        brflow_erro=brflow_erro,
        data_processamento=data_processamento
    )

    _set_status("Limpando pastas temporárias após a extração")
    _limpar_pasta_temporaria(local_download, recriar=True)
    _maybe_run_produtividade_case(s)


def _maybe_run_ged_irregularidade_reinspecao(settings: dict | None) -> None:
    """Se habilitado, extrai GED irregularidade bruto e enfileira sync para fila reinspeção."""
    if parar_event.is_set():
        return
    try:
        from app.bots.production.ged_irregularidade import executar_extracao_ged_irregularidade

        executar_extracao_ged_irregularidade(
            set_status=_set_status,
            set_progress=_set_progress,
            registrar_erro=_registrar_erro_producao,
            registrar_status=_registrar_status_producao,
            settings=settings,
        )
    except Exception as exc:
        log.exception("GED irregularidade falhou no ciclo H/H")
        _registrar_erro_producao(f"GED irregularidade: {str(exc)[:120]}")
        _registrar_status_producao(TASK_GED_IRREGULARIDADE, False, str(exc)[:30])
        _set_status(f"Producao: GED irregularidade falhou — {str(exc)[:100]}")


def _maybe_run_produtividade_case(settings: dict | None) -> None:
    """Se habilitado na config do H/H, extrai Case Manager no mesmo ciclo (DocumentDB)."""
    s = settings or {}
    if not s.get("executar_produtividade_case"):
        return
    if parar_event.is_set():
        return

    case_settings = dict(s.get("produtividade_case_settings") or {})
    if not case_settings:
        raw = (os.environ.get("PRODUTIVIDADE_CASE_SETTINGS") or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    case_settings = parsed
            except json.JSONDecodeError:
                log.warning("PRODUTIVIDADE_CASE_SETTINGS inválido; Case Manager ignorado neste ciclo")
                return
    if not case_settings.get("tarefas"):
        _set_status("Producao: Case Manager ignorado (sem tarefas na config)")
        return

    _set_status("Producao: extraindo Case Manager (DocumentDB)")
    _set_progress(92, "Producao: Case Manager")
    try:
        from app.bots.produtividade_case.bot import executar_pipeline

        executar_pipeline(case_settings)
        if not parar_event.is_set():
            _set_status("Producao: Case Manager concluído")
            _set_progress(99, "Producao: Case concluido; aguardando GED")
    except Exception as exc:
        log.exception("Case Manager falhou no ciclo H/H")
        _registrar_erro_producao(f"Case Manager: {str(exc)[:120]}")
        _set_status(f"Producao: Case Manager falhou — {str(exc)[:100]}")


def _worker_confer(local_download, matricula, senha, settings=None):
    drv = None
    status_prefix = "[CONFER] "
    settings = settings or {}

    try:
        download_dir = Path(local_download) / "confer"
        download_dir.mkdir(parents=True, exist_ok=True)

        drv = create_driver(headless=_resolve_headless(), download_dir=str(download_dir))

        _set_status(f"{status_prefix}Login Okta")
        _fazer_login(drv, matricula, senha, _get_tz_br())
        _navegar_busca_confer(drv)
        _set_status(f"{status_prefix}Baixando relatório")
        csv_path = baixar_relatorio_producao_confer(
            drv,
            settings=settings,
            download_dir=download_dir
        )
        csv_path = _garantir_csv_com_dados(csv_path, "confer")

        log_eventos_csv = None
        log_eventos_ok = None
        if settings.get("baixar_log_eventos_com_producao", True):
            try:
                _set_status(f"{status_prefix}Baixando Log Eventos do dia")
                data_ref = datetime.now(_get_tz_br()).date()
                log_eventos_csv = baixar_log_eventos_confer_dia(
                    drv,
                    download_dir=download_dir,
                    data_ref=data_ref,
                )
                log_eventos_ok = bool(log_eventos_csv and Path(log_eventos_csv).is_file())
                if log_eventos_ok:
                    _set_status(f"{status_prefix}Log Eventos baixado")
                else:
                    log_eventos_ok = False
                    log.warning("%sLog Eventos: nenhum arquivo baixado", status_prefix)
            except Exception as exc:
                log_eventos_ok = False
                log.warning("%sLog Eventos: falha no download: %s", status_prefix, exc, exc_info=True)

        return {
            "type": "confer",
            "path": csv_path,
            "log_eventos_csv": log_eventos_csv,
            "log_eventos_ok": log_eventos_ok,
        }

    finally:
        if drv:
            safe_close_driver(drv)

def _worker_brflow(local_download, prefixo, extensao, matricula, senha, tz_br, settings=None):
    drv = None
    status_prefix = "[BRFLOW] "
    settings = settings or {}

    try:
        download_dir = Path(local_download) / "brflow"
        download_dir.mkdir(parents=True, exist_ok=True)

        drv = create_driver(headless=_resolve_headless(), download_dir=str(download_dir))

        _set_status(f"{status_prefix}Login Okta")
        _fazer_login(drv, matricula, senha, tz_br)

        _set_status(f"{status_prefix}Configurando BRFlow")
        data_processamento = _navegar_e_configurar(drv, tz_br)

        _set_status(f"{status_prefix}Extraindo CSV")
        prod_monitor_path = download_dir / f"{prefixo}_monitor_atual{extensao}"
        csv_path = _extrair_csv_multiplos_dias(
            drv,
            download_dir,
            prefixo,
            extensao,
            status_prefix=status_prefix,
            quantidade_dias=_resolver_dias_download_brflow(settings),
            arquivo_dia_atual=prod_monitor_path,
        )
        if not csv_path:
            dias_download = _resolver_dias_download_brflow(settings)
            raise RuntimeError(
                f"Relatório BRFlow vazio: nenhum registro nos últimos {dias_download} dias"
            )
        csv_path = _garantir_csv_com_dados(csv_path, "brflow")

        monitor_ok = None
        monitor_path = None
        if settings.get("baixar_monitor_com_producao", True):
            try:
                from app.infrastructure.brflow_monitor import (
                    DEFAULT_PREFIXO,
                    navegar_monitor_e_baixar_csv,
                    processar_e_salvar_monitor_eventos_tratado,
                )

                _set_status(f"{status_prefix}Baixando Monitor de Eventos")
                _wait_overlay_invisible(drv, timeout=10)
                csv_monitor = navegar_monitor_e_baixar_csv(
                    drv,
                    download_dir,
                    data_ref=data_processamento.date(),
                    prefixo=DEFAULT_PREFIXO,
                    extensao=extensao,
                    set_progress=_set_progress,
                )
                if csv_monitor:
                    _set_status(f"{status_prefix}Tratando Monitor de Eventos")
                    monitor_tz = _get_tz_br()
                    monitor_snapshot_at = datetime.now(monitor_tz)
                    if monitor_snapshot_at.date() != data_processamento.date():
                        monitor_snapshot_at = datetime.combine(
                            data_processamento.date(),
                            dt_time(23, 59, 59),
                            tzinfo=monitor_tz,
                        )
                    tratado = processar_e_salvar_monitor_eventos_tratado(
                        csv_monitor,
                        prod_monitor_path if prod_monitor_path.is_file() else csv_path,
                        data_processamento.date(),
                        snapshot_at=monitor_snapshot_at,
                        publicar=False,
                    )
                    try:
                        csv_monitor.unlink(missing_ok=True)
                    except Exception:
                        pass
                    if tratado:
                        monitor_ok = True
                        monitor_path = tratado
                        _set_status(f"{status_prefix}Monitor de eventos tratado salvo")
                    else:
                        monitor_ok = False
                        log.warning("%sMonitor: tratamento não produziu arquivo", status_prefix)
                else:
                    monitor_ok = False
                    log.warning("%sMonitor: nenhum arquivo baixado", status_prefix)
            except Exception as exc:
                monitor_ok = False
                log.warning("%sMonitor: falha no download acoplado: %s", status_prefix, exc, exc_info=True)

        return {
            "type": "brflow",
            "path": csv_path,
            "data": data_processamento,
            "monitor_ok": monitor_ok,
            "monitor_path": monitor_path,
        }

    finally:
        if drv:
            safe_close_driver(drv)




def _fazer_login(drv, matricula, senha, tz_br, stop_workers_event=None):
    """Realiza autenticação no Okta com otimizações de performance."""
    stop_workers_event = stop_workers_event or threading.Event()
    
    if stop_workers_event.is_set() or parar_event.is_set():
        raise RuntimeError("Worker foi sinalizado para parar")
    
    _set_progress(20, "Autenticacao: acessando Okta")
    drv.get(okta.O_LINK)

    user, pwd = matricula, senha
    if not user or not pwd:
        creds = get_credentials()
        user = user or creds[0]
        pwd = pwd or creds[1]
    
    if not user or not pwd:
        _set_status("Credenciais não fornecidas")
        log.error("Credenciais ausentes para autenticação")
        #notify("Produção", "erro", "❌ Credenciais não fornecidas", "Variáveis NIVEL_USER e NIVEL_PASS não estáo configuradas")
        raise ValueError("Credenciais ausentes")
    
    try:
        login_okta_resiliente(drv, user, pwd, timeout=TIMEOUT_DRIVER)
        log.debug("Login Okta resiliente bem-sucedido")
    except Exception as exc:
        log.error(f"Falha no login Okta resiliente: {exc}")
        #notify("Produção", "erro", "❌ Erro na autenticação Okta", f"Falha no login Okta. Erro: {str(exc)[:80]}")
        raise
    
    _set_progress(35, "Autenticacao: Okta concluido")


def _focar_aba_brflow(drv, timeout_sec: int = 20):
    """Foca a aba do BRFlow (por URL/título) e retorna o handle selecionado."""
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
            if "brflow" in url or "brflow" in titulo:
                return handle

        time.sleep(0.3)

    if ultimo_handle:
        drv.switch_to.window(ultimo_handle)
        return ultimo_handle

    raise RuntimeError("Nenhuma aba disponível para continuar a automação do BRFlow")


def _aguardar_menu_brflow(drv, wait):
    """Aguarda menu BRFlow carregar após abrir aba."""
    wait.until(EC.presence_of_element_located((By.ID, "menu")))
    try:
        WebDriverWait(drv, 5).until(
            EC.invisibility_of_element_located((By.ID, "sistema-loading"))
        )
    except TimeoutException:
        try:
            el = drv.find_element(By.ID, "sistema-loading")
            WebDriverWait(drv, 3).until(
                lambda d: el.get_attribute("style") and "display: none" in el.get_attribute("style")
            )
        except Exception:
            pass
    _wait_overlay_invisible(drv, timeout=5)


def _clicar_menu_produtividade(drv, wait, wait_short):
    """Aguarda menu e clica em Produtividade com fallback de seletores."""
    _aguardar_menu_brflow(drv, wait)

    selectors = [
        ("B_produção (#menu li[11])", brflow.B_produção),
        ("B_produção_ascii (data-ascii)", brflow.B_produção_ascii),
    ]

    last_exc = None
    for desc, xpath in selectors:
        try:
            try:
                wait_short.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            except TimeoutException:
                log.debug(f"Timeout curto em {desc}, tentando com timeout maior")
                wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            click_element(drv, "xpath", xpath)
            log.debug(f"Menu Produtividade clicado via {desc}")
            return
        except TimeoutException as exc:
            last_exc = exc
            log.debug(f"Seletor {desc} não encontrado, tentando próximo fallback")

    url = drv.current_url or ""
    log.error(f"Falha ao clicar menu Produtividade. URL: {url}")
    try:
        take_error_screenshot(drv, "falha_menu_produtividade", "production")
    except Exception as exc:
        log.debug(f"Erro ao tirar screenshot: {exc}")
    raise TimeoutException(
        f"Menu Produtividade não encontrado após fallbacks. URL: {url}"
    ) from last_exc


def _clicar_exportar_csv(drv, wait, wait_short):
    """Clica em Exportar CSV com fallback de seletores."""
    selectors = [
        ("B_P_CSV_input (formato-csv)", "id", brflow.B_P_CSV_input),
        ("B_P_CSV (label formato-csv)", "xpath", brflow.B_P_CSV),
    ]

    last_exc = None
    for desc, by, locator in selectors:
        try:
            by_enum = By.ID if by == "id" else By.XPATH
            try:
                wait_short.until(EC.element_to_be_clickable((by_enum, locator)))
            except TimeoutException:
                log.debug(f"Timeout curto em {desc}, tentando com timeout maior")
                wait.until(EC.element_to_be_clickable((by_enum, locator)))
            click_element(drv, by, locator)
            log.debug(f"Exportar CSV clicado via {desc}")
            return
        except TimeoutException as exc:
            last_exc = exc
            log.debug(f"Seletor {desc} não encontrado, tentando próximo fallback")

    url = drv.current_url or ""
    log.error(f"Falha ao clicar Exportar CSV. URL: {url}")
    try:
        take_error_screenshot(drv, "falha_exportar_csv", "production")
    except Exception as exc:
        log.debug(f"Erro ao tirar screenshot: {exc}")
    raise TimeoutException(
        f"Exportar CSV não encontrado após fallbacks. URL: {url}"
    ) from last_exc


def _clicar_pesquisar_produtividade(drv, wait, wait_short):
    """Clica em Pesquisar na tela de produtividade com fallback de seletores."""
    selectors = [
        ("B_P_pesquisar (#layout_layout2_panel_main)", brflow.B_P_pesquisar),
        ("B_P_pesquisar_class (btn-pesquisar)", brflow.B_P_pesquisar_class),
    ]

    last_exc = None
    for desc, xpath in selectors:
        try:
            try:
                wait_short.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            except TimeoutException:
                log.debug(f"Timeout curto em {desc}, tentando com timeout maior")
                wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            click_element(drv, "xpath", xpath)
            log.debug(f"Pesquisar clicado via {desc}")
            return
        except TimeoutException as exc:
            last_exc = exc
            log.debug(f"Seletor {desc} não encontrado, tentando próximo fallback")

    url = drv.current_url or ""
    log.error(f"Falha ao clicar Pesquisar. URL: {url}")
    try:
        take_error_screenshot(drv, "falha_pesquisar_produtividade", "production")
    except Exception as exc:
        log.debug(f"Erro ao tirar screenshot: {exc}")
    raise TimeoutException(
        f"Pesquisar não encontrado após fallbacks. URL: {url}"
    ) from last_exc


def _navegar_e_configurar(drv, tz_br, stop_workers_event=None):
    """Navega para BRFlow e configura período de análise com otimizações de performance.
    
    Retorna:
        datetime: Data do dia processado (em timezone Brasil)
    """
    stop_workers_event = stop_workers_event or threading.Event()
    
    if stop_workers_event.is_set() or parar_event.is_set():
        raise RuntimeError("Worker foi sinalizado para parar")
    
    # Definir data atual em timezone Brasil
    agora = datetime.now(tz_br)
    
    wait = WebDriverWait(drv, TIMEOUT_DRIVER)
    wait_short = WebDriverWait(drv, 6)  # Ajustado de 5 para 6 segundos
    
    _set_progress(40, "BRFlow: navegando via Okta")
    try:
        wait_short.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))
    except TimeoutException:
        log.debug("Timeout curto em O_pesquisar, tentando com timeout maior")
        wait.until(EC.element_to_be_clickable((By.ID, okta.O_pesquisar)))
    send_keys_to_element(drv, "id", okta.O_pesquisar, "brflow")
    drv.find_element(By.ID, okta.O_pesquisar).send_keys(Keys.ENTER)
    
    if stop_workers_event.is_set() or parar_event.is_set():
        raise RuntimeError("Worker foi sinalizado para parar")
    
    # Aguarda abertura de nova aba com timeout reduzido
    try:
        wait_short.until(lambda d: len(d.window_handles) > 1)
    except TimeoutException:
        wait.until(lambda d: len(d.window_handles) > 1)  # Fallback com timeout maior
    
    if stop_workers_event.is_set() or parar_event.is_set():
        raise RuntimeError("Worker foi sinalizado para parar")

    brflow_handle = _focar_aba_brflow(drv)
    _fechar_abas_exceto(drv, brflow_handle)
    
    _set_progress(45, "BRFlow: acessando modulo de producao")
    _clicar_menu_produtividade(drv, wait, wait_short)
    try:
        wait_short.until(EC.element_to_be_clickable((By.XPATH, brflow.B_P_data)))
    except TimeoutException:
        log.debug("Timeout curto em B_P_data, tentando com timeout maior")
        wait.until(EC.element_to_be_clickable((By.XPATH, brflow.B_P_data)))
    
    if stop_workers_event.is_set() or parar_event.is_set():
        raise RuntimeError("Worker foi sinalizado para parar")
    
    # Não configurar período aqui - será feito individualmente para cada dia na extração
    _set_progress(55, "BRFlow: navegação concluída (período será configurado por dia)")
    
    if stop_workers_event.is_set() or parar_event.is_set():
        raise RuntimeError("Worker foi sinalizado para parar")
    
    # Configurar meta
    _configurar_meta(drv)
    _set_progress(65, "BRFlow: meta configurada")
    
    # Retornar data capturada para uso ao salvar arquivo
    return agora


def _preencher_data_javascript(drv, campo_name, valor):
    """Preenche campo de data via JavaScript."""
    campo = WebDriverWait(drv, 10).until(
        EC.visibility_of_element_located((By.NAME, campo_name))
    )
    drv.execute_script("""
        const el = arguments[0], val = arguments[1];
        el.value = val;
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        el.dispatchEvent(new Event('blur', {bubbles: true}));
    """, campo, valor)
    log.debug(f"Campo {campo_name} preenchido: {valor}")


def _configurar_meta(drv):
    """Configura meta no formulário (não aborta o ciclo se o select sumir)."""
    try:
        select_el = WebDriverWait(drv, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.form-group[data-campo='2'] select[name='indMetaConfigurada']"))
        )
    except TimeoutException:
        log.warning("Select indMetaConfigurada não encontrado — seguindo sem alterar meta")
        return
    drv.execute_script("""
        const sel = arguments[0], newVal = arguments[1];
        sel.value = newVal;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        sel.dispatchEvent(new Event('input', { bubbles: true }));
        if (window.jQuery) {
            try { jQuery(sel).val(newVal).trigger('change').trigger('select2:select'); } catch(e) {}
        }
    """, select_el, "1")
    log.debug("Meta configurada como '1'")


def _replace_file_with_retry(src: Path, dest: Path, *, attempts: int = 5, delay_s: float = 0.4) -> None:
    """Move arquivo local → destino OneDrive com retry (Errno 22 / arquivo em uso)."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_exc: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            if dest.exists():
                dest.unlink()
            os.replace(src, dest)
            return
        except OSError as exc:
            last_exc = exc
            log.warning(
                "replace %s → %s falhou (tentativa %s/%s): %s",
                src.name,
                dest,
                attempt,
                attempts,
                exc,
            )
            time.sleep(delay_s * attempt)
    if last_exc:
        raise last_exc
    raise OSError(f"Falha ao mover {src} → {dest}")


def _extrair_csv_multiplos_dias(
    drv,
    local_download,
    prefixo,
    extensao,
    status_prefix="",
    stop_workers_event=None,
    quantidade_dias=3,
    arquivo_dia_atual=None,
):
    """Extrai CSVs do BRFlow para múltiplos dias separadamente e junta os resultados."""
    tz_br = _get_tz_br()
    stop_workers_event = stop_workers_event or threading.Event()

    if stop_workers_event.is_set() or parar_event.is_set():
        return None

    quantidade_dias = max(1, min(31, int(quantidade_dias)))

    # Processa do dia mais antigo para o mais recente. Assim, o recorte de hoje
    # fica o mais proximo possivel do download do Monitor de Eventos.
    agora = datetime.now(tz_br)
    dias_para_extrair = []
    for dias_atras in reversed(range(quantidade_dias)):
        data = agora - timedelta(days=dias_atras)
        dias_para_extrair.append(data)

    _set_status(f"{status_prefix}Extraindo dados de {len(dias_para_extrair)} dias separadamente")

    # Lista para armazenar os DataFrames de cada dia
    dataframes_dias = []

    for i, data_dia in enumerate(dias_para_extrair):
        if stop_workers_event.is_set() or parar_event.is_set():
            return None

        _set_status(
            f"{status_prefix}Extraindo dia {i+1}/{quantidade_dias}: "
            f"{data_dia.strftime('%d/%m/%Y')}"
        )

        # Turno operacional: 23h do dia anterior → agora (hoje) ou 23h do dia (fechado)
        referencia_periodo = datetime.now(tz_br) if data_dia.date() == agora.date() else agora
        valorI, valorF = _periodo_analise_hxh(data_dia, referencia_periodo)

        _set_progress(0, f"BRFlow: configurando período dia {i+1}")
        _set_status(f"{status_prefix}Configurando período: {valorI} → {valorF}")

        # Preencher datas via JavaScript e aguardar aplicação
        _preencher_data_javascript(drv, "datAnaliseInicial", valorI)
        _preencher_data_javascript(drv, "datAnaliseFinal", valorF)
        time.sleep(1)

        _set_status(f"{status_prefix}Período aplicado para dia {i+1}")

        # Extrair CSV para este dia
        csv_path = _extrair_csv_dia_especifico(
            drv, local_download, prefixo, extensao,
            status_prefix, stop_workers_event
        )

        if csv_path:
            # Ler o CSV e adicionar à lista
            df_dia, _ = _ler_csv_robusto(str(csv_path))
            if not df_dia.empty:
                dataframes_dias.append(df_dia)
                if arquivo_dia_atual and data_dia.date() == agora.date():
                    arquivo_dia_atual = Path(arquivo_dia_atual)
                    df_dia.to_csv(
                        str(arquivo_dia_atual),
                        index=False,
                        sep=";",
                        encoding="utf-8-sig",
                    )
                _set_status(f"{status_prefix}Dia {i+1}: {len(df_dia)} registros extraídos")
            else:
                _set_status(f"{status_prefix}Dia {i+1}: nenhum registro encontrado")

            # Remover arquivo temporário após leitura
            try:
                csv_path.unlink(missing_ok=True)
            except Exception as e:
                log.debug(f"Erro ao remover arquivo temporário {csv_path}: {e}")
        else:
            _set_status(f"{status_prefix}Dia {i+1}: falha na extração")

    if not dataframes_dias:
        _set_status(f"{status_prefix}Nenhum dado extraído dos {quantidade_dias} dias")
        return None

    # Juntar todos os DataFrames (fronteira 23:00 pode sobrepor dias consecutivos)
    df_consolidado = pd.concat(dataframes_dias, ignore_index=True)
    antes = len(df_consolidado)
    df_consolidado = df_consolidado.drop_duplicates()
    removidos = antes - len(df_consolidado)
    total_registros = len(df_consolidado)
    if removidos:
        _set_status(
            f"{status_prefix}Dados consolidados: {total_registros} registros "
            f"({removidos} duplicata(s) na fronteira 23h removida(s))"
        )
    else:
        _set_status(
            f"{status_prefix}Dados consolidados: {total_registros} registros "
            f"de {len(dias_para_extrair)} dias"
        )

    # Salvar arquivo consolidado
    arquivo_consolidado = Path(local_download) / f"{prefixo}_consolidado{extensao}"
    df_consolidado.to_csv(str(arquivo_consolidado), index=False, sep=';', encoding='utf-8-sig')

    _set_status(f"{status_prefix}Arquivo consolidado salvo: {arquivo_consolidado.name} ({total_registros} registros)")
    return arquivo_consolidado


def _extrair_csv_dia_especifico(drv, local_download, prefixo, extensao, status_prefix="", stop_workers_event=None):
    """Extrai CSV para um dia específico (versão simplificada sem retry)."""
    stop_workers_event = stop_workers_event or threading.Event()

    if stop_workers_event.is_set() or parar_event.is_set():
        return None

    try:
        # Snapshot de arquivos antes
        before = {p.name for p in Path(local_download).iterdir()} if Path(local_download).exists() else set()

        if stop_workers_event.is_set() or parar_event.is_set():
            return None

        wait = WebDriverWait(drv, TIMEOUT_DRIVER)
        wait_short = WebDriverWait(drv, 6)

        # Disparar download
        _clicar_exportar_csv(drv, wait, wait_short)
        time.sleep(2)

        _clicar_pesquisar_produtividade(drv, wait, wait_short)

        # Aguardar download
        start_time = time.time()
        novo = cp_wait_for_new_file(before, local_download, prefixo, extensao, timeout=TIMEOUT_WAIT_FILE)

        if not novo:
            _set_status(f"{status_prefix}Arquivo não detectado com prefixo '{prefixo}', tentando fallback por tempo de modificação")
            novo = _buscar_arquivo_por_data(local_download, extensao, start_time)

        if not novo:
            return None

        novo_path = Path(str(novo))

        # Aguardar estabilidade
        if not _esperar_arquivo_estavel(novo_path, timeout=TIMEOUT_FILE_STABLE):
            log.warning(f"Arquivo {novo_path.name} pode não estar estável")

        return novo_path

    except Exception as e:
        log.error(f"Erro na extração do dia específico: {e}")
        return None


def _buscar_arquivo_por_data(local_download, extensao, start_time):
    """Retorna o arquivo mais recente cuja modificação seja posterior a start_time."""
    pasta = Path(local_download)
    if not pasta.exists():
        return None

    candidatos = [
        item
        for item in pasta.iterdir()
        if item.is_file() and item.name.lower().endswith(extensao.lower()) and item.stat().st_mtime >= start_time
    ]
    if not candidatos:
        return None
    return max(candidatos, key=lambda p: p.stat().st_mtime)


def _salvar_detalhado_producao(df: pd.DataFrame, pasta: Path, prefixo: str, data_str: str) -> Path:
    """Salva XLSX detalhado via temp local → replace (evita Errno 22 no OneDrive)."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    detalhado_xlsx = pasta / f"{prefixo}_detalhado_{data_str}.xlsx"
    tmp_dir = DOWNLOADS_TEMP_PRODUCTION / "xlsx_out"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_xlsx = tmp_dir / detalhado_xlsx.name
    if tmp_xlsx.exists():
        try:
            tmp_xlsx.unlink()
        except OSError:
            pass
    df.to_excel(str(tmp_xlsx), index=False, sheet_name="Detalhado")
    time.sleep(0.05)
    _replace_file_with_retry(tmp_xlsx, detalhado_xlsx)
    _emit_production_detalhado_saved(detalhado_xlsx, row_count=len(df))
    log.info(f"XLSX detalhado salvo: {detalhado_xlsx} ({len(df)} linhas)")
    return detalhado_xlsx


def processar_csv_brflow_para_detalhado(
    csv_path,
    output_dir,
    target_date: date | None = None,
    prefixo: str = "relatorio_produtividade",
) -> Path:
    """Lê CSV BRFlow, aplica tratamento e salva XLSX detalhado."""
    csv_path = Path(csv_path)
    output_dir = Path(output_dir)
    df, _ = _ler_csv_robusto(str(csv_path))
    if df.empty:
        raise RuntimeError(f"CSV vazio: {csv_path}")

    df = normalizar_brflow(df)
    if df.empty:
        raise RuntimeError(f"CSV sem colunas BRFlow válidas: {csv_path}")

    df = _processar_dataframe_producao(df, target_date=target_date)
    if df.empty:
        dia = target_date or datetime.now(_get_tz_br()).date()
        raise RuntimeError(f"Nenhum registro após tratamento para {csv_path} (dia {dia})")

    data_str = (target_date or datetime.now(_get_tz_br()).date()).strftime("%Y-%m-%d")
    return _salvar_detalhado_producao(df, output_dir, prefixo, data_str)


def _processar_e_salvar_csv(novo_path, local_sharepoint_produção, prefixo, data_processamento=None):
    """Processa CSV extraído e salva em SharePoint.
    
    Args:
        novo_path: Caminho do arquivo CSV extraído
        local_sharepoint_produção: Diretório destino
        prefixo: Prefixo do nome do arquivo
        data_processamento: Data do dia processado (timezone Brasil). Se None, usa data atual.
    """
    try:
        _set_progress(85, "Producao: processando CSV")
        df_novo, sep_novo = _ler_csv_robusto(str(novo_path))
        
        if df_novo.empty:
            _set_status("CSV extraído está vazio")
            log.warning("CSV extraído vazio - nenhuma linha de dados")
            raise RuntimeError("CSV combinado sem linhas de dados para processamento")
        
        # Criar diretórios se necessário
        local_sharepoint_produção.mkdir(parents=True, exist_ok=True)
        PASTA_PROD_HXH_BRUTA.mkdir(parents=True, exist_ok=True)
        
        df_novo = _processar_dataframe_producao(df_novo)

        # Arquivo com data (um por dia) - usar data do processamento, não a data atual
        if data_processamento is None:
            data_processamento = datetime.now()
        data_str = data_processamento.strftime('%Y-%m-%d')
        
        # Arquivo tbl_produtividade fica na pasta atual
        produtividade_xlsx = local_sharepoint_produção / f"{prefixo}_produtividade_{data_str}.xlsx"
        
        _set_progress(90, "Producao: salvando XLSX")
        
        try:
            df_resumo = _construir_resumo_horas_por_matricula(df_novo)
            _salvar_detalhado_producao(df_novo, PASTA_PROD_HXH_BRUTA, prefixo, data_str)
            
            # Salvar tbl_produtividade em arquivo separado com tabela nomeada
            tmp_dir = DOWNLOADS_TEMP_PRODUCTION / "xlsx_out"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_prod = tmp_dir / produtividade_xlsx.name
            if tmp_prod.exists():
                try:
                    tmp_prod.unlink()
                except OSError:
                    pass
            df_resumo.to_excel(str(tmp_prod), index=False, sheet_name="tbl_produtividade")
            time.sleep(0.05)

            if load_workbook and Table and TableStyleInfo:
                wb = None
                try:
                    wb = load_workbook(str(tmp_prod))
                    if "tbl_produtividade" in wb.sheetnames:
                        ws = wb["tbl_produtividade"]
                        ref = f"A1:{ws.cell(row=ws.max_row, column=ws.max_column).coordinate}"
                        tab = Table(displayName="tbl_produtividade", ref=ref)
                        style = TableStyleInfo(name="TableStyleMedium9", showRowStripes=True)
                        tab.tableStyleInfo = style
                        if hasattr(ws._tables, "add"):
                            ws._tables.add(tab)
                        else:
                            ws._tables.append(tab)
                        wb.save(str(tmp_prod))
                except Exception as e:
                    log.warning(f"Falha ao criar tabela Excel em tbl_produtividade: {e}")
                finally:
                    if wb:
                        wb.close()
                        time.sleep(0.05)

            _replace_file_with_retry(tmp_prod, produtividade_xlsx)

            _set_status(f"Produtividade salva: {produtividade_xlsx} ({len(df_resumo)} matrículas)")
            log.info(f"XLSX produtividade salvo: {produtividade_xlsx} ({len(df_resumo)} matrículas)")
            
        except Exception as e:
            log.warning(f"Falha ao salvar XLSX: {e}")
            try:
                _salvar_detalhado_producao(df_novo, PASTA_PROD_HXH_BRUTA, prefixo, data_str)
            except Exception as e2:
                log.error(f"Falha ao salvar XLSX detalhado: {e2}")
                raise
        finally:
            # Liberar referências aos DataFrames e forçar garbage collection
            try:
                del df_novo
                del df_resumo
            except Exception:
                pass
            gc.collect()  # Força liberação de referências de arquivo em memória
            time.sleep(0.05)  # Pequeno delay para garantir liberação completa
        
        # Remover arquivo baixado
        try:
            novo_path.unlink(missing_ok=True)
        except Exception as e:
            log.debug(f"Erro ao remover arquivo temporário: {e}")
            
    except Exception as e:
        _set_status(f"Erro ao processar CSV: {str(e)[:80]}")
        log.error(f"Erro ao processar/salvar CSV: {e}", exc_info=True)
        #notify("Produção", "erro", "❌ Falha ao salvar produtividade!", f"Erro: {str(e)[:100]}")
        _tirar_screenshot_erro(None, f"processamento_erro", "production")
        raise


def _tirar_screenshot_erro(drv, prefixo, sufixo=""):
    """Tira screenshot de erro com tratamento seguro."""
    try:
        if drv:
            take_error_screenshot(drv, prefixo=prefixo, suffix=sufixo)
            log.debug(f"Screenshot de erro tirado: {prefixo}")
    except Exception as e:
        log.debug(f"Erro ao tirar screenshot: {e}")



def _validar_arquivo_log_eventos(df, data_esperada):
    """Valida CSV do Log Eventos: exige coluna Evento (evita confundir com Relatórios)."""
    if df is None or df.empty:
        return False
    colunas_lower = {str(c).strip().lower() for c in df.columns}
    return any("evento" in nome for nome in colunas_lower)


def _publicar_monitor_base_se_necessario(brflow_result, monitor_final):
    """Publica a versao base somente quando nao houve artefato unificado final."""
    if monitor_final is not None:
        return monitor_final

    monitor_base = (brflow_result or {}).get("monitor_path")
    if not monitor_base or not Path(monitor_base).is_file():
        return None

    try:
        from app.infrastructure.brflow_monitor import publicar_monitor_eventos_tratado

        publicado = publicar_monitor_eventos_tratado(monitor_base)
        log.info("Monitor base publicado apos encerramento da etapa de unificacao: %s", monitor_base)
        return publicado
    except Exception as exc:
        log.warning("Falha ao publicar Monitor base final: %s", exc, exc_info=True)
        _registrar_status_producao(TASK_MONITOR, False, "falha ao publicar")
        return None


def _unificar_log_eventos_com_monitor_hxh(confer_result, brflow_result, data_processamento, settings=None):
    """Gera sessões do Log Eventos, une com monitor tratado e atualiza status Teams."""
    settings = settings or {}
    if not settings.get("baixar_log_eventos_com_producao", True):
        return None

    log_ok = (confer_result or {}).get("log_eventos_ok")
    log_csv = (confer_result or {}).get("log_eventos_csv")
    monitor_path = (brflow_result or {}).get("monitor_path")

    if log_ok is False:
        _registrar_status_producao(TASK_LOG_EVENTOS, False, "download falhou")
        return None

    if not log_csv or not Path(log_csv).is_file():
        if settings.get("baixar_log_eventos_com_producao", True):
            obs = "sem arquivo" if confer_result is not None else "download falhou"
            if _exec_summary["task_status"].get(TASK_LOG_EVENTOS, {}).get("estado") == "pendente":
                _registrar_status_producao(TASK_LOG_EVENTOS, False, obs)
        return None

    if not monitor_path or not Path(monitor_path).is_file():
        # Download ok; sem monitor ainda conta como sucesso parcial do log.
        _registrar_status_producao(TASK_LOG_EVENTOS, True)
        log.warning("Log Eventos baixado, mas monitor tratado ausente — unificação HxH ignorada")
        return None

    if hasattr(data_processamento, "date"):
        data_ref = data_processamento.date()
    elif isinstance(data_processamento, date):
        data_ref = data_processamento
    else:
        data_ref = datetime.now(_get_tz_br()).date()

    try:
        from app.infrastructure.brflow_monitor import unificar_monitor_hxh_com_confer_log

        _set_status("Unificando Monitor HxH com Log Eventos Confer")
        unificado = unificar_monitor_hxh_com_confer_log(
            monitor_path,
            log_csv,
            data_ref,
            csv_prod_confer=(confer_result or {}).get("path"),
        )
        if unificado:
            _registrar_status_producao(TASK_LOG_EVENTOS, True)
            _set_status(f"Monitor unificado com Log Eventos: {Path(unificado).name}")
            return unificado
        _registrar_status_producao(TASK_LOG_EVENTOS, False, "falha na união")
        return None
    except Exception as exc:
        log.warning("Falha ao unificar Log Eventos com Monitor HxH: %s", exc, exc_info=True)
        _registrar_status_producao(TASK_LOG_EVENTOS, False, "falha na união")
        _registrar_erro_producao(f"Log Eventos: {str(exc)[:100]}")
        return None


# Confer: ícone do header que retorna ao menu principal (mesmo XPath da rotina)
_CONFER_VOLTAR_MENU_XPATH = "/html/body/app-root/app-home/header/nav/div/div[2]/ul/li[3]/i"


def _voltar_menu_confer(drv) -> None:
    """Volta ao menu principal do Confer após Relatórios / busca protocolo."""
    _set_status("Confer: voltando ao menu")
    try:
        click_element(drv, "xpath", _CONFER_VOLTAR_MENU_XPATH)
        time.sleep(1)
    except Exception as exc:
        log.warning("Falha ao voltar ao menu Confer: %s", exc)
        raise


def baixar_log_eventos_confer_dia(drv, download_dir=None, data_ref=None):
    """Abre Log Eventos pelo menu Confer (após voltar do Relatórios), data do dia + Pesquisar."""
    wait = WebDriverWait(drv, TIMEOUT_DRIVER)
    wait_short = WebDriverWait(drv, 6)
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

    data_ref = data_ref or datetime.now(_get_tz_br()).date()
    data_fmt = data_ref.strftime("%d/%m/%Y")

    _set_status("Abrindo Log Eventos pelo menu Confer")
    _set_progress(85, "Confer: Log Eventos")

    try:
        _focar_aba_confer(drv)
    except Exception as exc:
        log.warning("Não foi possível focar a aba Confer antes do Log Eventos: %s", exc)

    _recuperar_menu_confer_se_tela_login(drv, contexto="antes do Log Eventos")

    try:
        wait_short.until(EC.element_to_be_clickable((By.XPATH, menu_log_eventos_xpath)))
    except TimeoutException:
        wait.until(EC.element_to_be_clickable((By.XPATH, menu_log_eventos_xpath)))
    click_element(drv, "xpath", menu_log_eventos_xpath)

    try:
        wait_short.until(EC.presence_of_element_located((By.XPATH, data_log_eventos_xpath)))
    except TimeoutException:
        wait.until(EC.presence_of_element_located((By.XPATH, data_log_eventos_xpath)))

    _click_xpath_com_fallback_js(drv, radio_todos_xpath)
    send_keys_to_element(drv, "xpath", data_log_eventos_xpath, data_fmt)
    _fechar_calendario_se_aberto(drv)

    # Usar a mesma pasta do Chrome (create_driver download_dir), não subpasta — o arquivo
    # cai em confer/, não em confer/log_eventos/.
    pasta_download = Path(download_dir)
    pasta_download.mkdir(parents=True, exist_ok=True)

    arquivo_baixado = baixar_com_retry_seguro_confer(
        drv,
        botao_pesquisar_xpath,
        pasta_download,
        data_ref,
        descricao="download do Log Eventos do dia (Confer)",
        max_tentativas=min(4, MAX_RETRY_DOWNLOAD_CONFER),
        timeout_por_tentativa=60,
        # Não limpar a pasta: o CSV do Relatórios (confer_base) precisa permanecer.
        clear_before_attempt=False,
        status_fn=_set_status,
        stop_check=parar_event.is_set,
        ler_csv_fn=_ler_csv_robusto,
        validar_por_dataframe_fn=_validar_arquivo_log_eventos,
        logger=log,
    )

    if not arquivo_baixado:
        raise RuntimeError("Falha ao baixar Log Eventos do Confer: nenhum arquivo retornado")

    novo_csv = _renomear_download_com_sufixo(arquivo_baixado, "log_eventos", logger=log)
    _set_progress(92, "Confer: Log Eventos baixado")
    return novo_csv


def baixar_relatorio_producao_confer(drv, settings=None, download_dir=None):
    settings = settings or {}
    settings["download_dir"] = str(download_dir)
    wait = WebDriverWait(drv, TIMEOUT_DRIVER)
    wait_short = WebDriverWait(drv, 6)
    menu_relatorios_xpath = "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[2]/div/div/div[5]/div/div/div/a"
    painel_gestao_xpath = "/html/body/app-root/app-home/main/app-gestao/div/div[1]"
    data_inicial_xpath = "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[1]/div[1]/div[2]/div[2]/app-datepicker/div/div/div/input"
    data_final_xpath = "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[1]/div[1]/div[2]/div[3]/app-datepicker/div/div/div/input"
    radio_todos_xpath = "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[1]/div[2]/app-radio/div/div[2]/div/div[1]/div/input"
    botao_download_xpath = "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[2]/div/button/i"
    _set_status("Baixando relatório de produção do Confer")
    _set_progress(80, "Confer: baixando relatorio")
    _recuperar_menu_confer_se_tela_login(drv, contexto="antes do relatório de produção")
    click_element(drv, "xpath", menu_relatorios_xpath)
    try:
        wait_short.until(EC.presence_of_element_located((By.XPATH, painel_gestao_xpath)))
    except TimeoutException:
        wait.until(EC.presence_of_element_located((By.XPATH, painel_gestao_xpath)))
    click_element(drv, "xpath", painel_gestao_xpath)
    time.sleep(1)
    data_referencia = datetime.now(_get_tz_br()).date()
    data_atual = data_referencia.strftime("%d/%m/%Y")
    send_keys_to_element(drv, "xpath", data_inicial_xpath, data_atual)
    send_keys_to_element(drv, "xpath", data_final_xpath, data_atual)
    _fechar_calendario_se_aberto(drv)
    _click_xpath_com_fallback_js(drv, radio_todos_xpath)
    local_download = Path(download_dir)
    local_download.mkdir(parents=True, exist_ok=True)
    arquivo_baixado = baixar_com_retry_seguro_confer(
        drv,
        botao_download_xpath,
        local_download,
        data_referencia,
        descricao="download do relatório de produção",
        max_tentativas=min(4, MAX_RETRY_DOWNLOAD_CONFER),
        timeout_por_tentativa=30,
        status_fn=_set_status,
        stop_check=parar_event.is_set,
        ler_csv_fn=_ler_csv_robusto,
        validar_por_dataframe_fn=_validar_arquivo_confer_producao,
        logger=log,
    )

    # Mesmo passo da rotina / bot_confer_monitor: voltar ao menu antes do Log Eventos
    try:
        _voltar_menu_confer(drv)
    except Exception as exc:
        log.warning("Não foi possível voltar ao menu após Relatórios Confer: %s", exc)

    if not arquivo_baixado:
        raise RuntimeError("Falha ao baixar relatório de produção do Confer: nenhum arquivo retornado")

    novo_csv = _renomear_download_com_sufixo(arquivo_baixado, "confer_base", logger=log)
    _set_progress(98, "Confer: relatorio processado")
    return novo_csv


if __name__ == "__main__":
    start()


