"""Navegação e download do Monitor de Eventos BRFlow (fluxo monitor.py)."""

from __future__ import annotations

import logging
import shutil
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

from app.config.paths import (
    PASTA_MONITOR_EVENTOS_BRUTO,
    PASTA_MONITOR_EVENTOS_TRATADO,
    PREFIXO_MONITOR_EVENTOS_BRUTO,
    PREFIXO_MONITOR_EVENTOS_TRATADO,
)
from app.config.selectors import brflow
from app.infrastructure.csv_processing import read_csv as cp_read_csv, wait_for_new_file as cp_wait_for_new_file
from app.infrastructure.selenium_helpers import _wait_overlay_invisible, wait_for_element

log = logging.getLogger("robots.brflow_monitor")

MONITOR_EVENTOS_SAVED_PREFIX = "MONITOR_EVENTOS_SAVED|"
DEFAULT_PREFIXO = "relatorio_detalhado"
DEFAULT_EXTENSAO = ".csv"
DEFAULT_TIMEOUT = 320


def _emit_monitor_eventos_saved(destino: Path) -> None:
    line = f"{MONITOR_EVENTOS_SAVED_PREFIX}{destino.resolve()}"
    try:
        print(line, flush=True)
    except OSError as exc:
        log.warning("Falha ao emitir MONITOR_EVENTOS_SAVED via stdout: %s", exc)
    try:
        from app.infrastructure.bot_sync_drop import (
            DOMAIN_MONITOR_EVENTOS,
            append_marker_to_robot_log,
            write_sync_drop,
        )

        write_sync_drop(DOMAIN_MONITOR_EVENTOS, destino)
        append_marker_to_robot_log("production", line)
    except Exception as exc:
        log.warning("Fallback sync_drop monitor falhou: %s", exc)


def publicar_monitor_eventos_tratado(destino: str | Path) -> Path:
    """Publica um artefato final do monitor para a fila de sincronizacao."""
    destino = Path(destino)
    if not destino.is_file():
        raise FileNotFoundError(f"Monitor tratado inexistente para publicacao: {destino}")
    _emit_monitor_eventos_saved(destino)
    return destino


def _noop_progress(*_args, **_kwargs) -> None:
    pass


def _format_data_br(data_ref: date) -> str:
    return data_ref.strftime("%d/%m/%Y")


def _clicar_com_retry(
    drv,
    locator: str,
    *,
    by=By.XPATH,
    tentativas: int = 3,
    pausa: float = 2,
    descricao: str = "elemento",
    wait_seconds: int = 20,
) -> None:
    ultimo_erro: Exception | None = None
    for tentativa in range(1, tentativas + 1):
        try:
            elemento = WebDriverWait(drv, wait_seconds).until(
                EC.presence_of_element_located((by, locator))
            )
            drv.execute_script(
                "arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});",
                elemento,
            )
            time.sleep(0.5)
            try:
                elemento.click()
            except Exception:
                drv.execute_script("arguments[0].click();", elemento)
            return
        except Exception as exc:
            ultimo_erro = exc
            log.warning(
                "Falha ao clicar em %s (tentativa %s/%s): %s",
                descricao,
                tentativa,
                tentativas,
                exc,
            )
            time.sleep(pausa)
    if ultimo_erro:
        raise ultimo_erro


def _preencher_campo_data(drv, css_selector: str, data_str: str, wait_seconds: int = 6) -> None:
    wait_short = WebDriverWait(drv, wait_seconds)
    data_element = wait_short.until(EC.presence_of_element_located((By.CSS_SELECTOR, css_selector)))
    try:
        data_element.click()
        data_element.send_keys(Keys.CONTROL, "a")
        data_element.send_keys(Keys.DELETE)
    except Exception:
        pass
    try:
        drv.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
            data_element,
            data_str,
        )
    except Exception:
        data_element.send_keys(data_str)


def garantir_tela_monitor(drv, wait_seconds: int = 20, set_progress: Callable | None = None) -> None:
    """Abre a tela Monitor no BRFlow (mesmo fluxo de monitor.py / rotina D-1)."""
    progress = set_progress or _noop_progress
    wait = WebDriverWait(drv, wait_seconds)

    _wait_overlay_invisible(drv, timeout=10)

    try:
        _clicar_com_retry(
            drv,
            brflow.B_menu_rotina,
            descricao="menu rotina (topo)",
            wait_seconds=wait_seconds,
        )
        time.sleep(2)
    except Exception as exc:
        log.warning("Não foi possível voltar pelo menu de rotinas: %s", exc)

    _wait_overlay_invisible(drv, timeout=5)
    _clicar_com_retry(drv, brflow.B_monitor, descricao="menu monitor", wait_seconds=wait_seconds)
    progress(50, "Monitor: tela selecionada")

    for attempt in range(3):
        try:
            wait.until(EC.element_to_be_clickable((By.XPATH, brflow.B_M_pesquisar)))
            progress(55, "Monitor: tela carregada")
            return
        except TimeoutException:
            if attempt < 2:
                log.warning("Elemento B_M_pesquisar não encontrado (tentativa %s/3)", attempt + 1)
                time.sleep(2)
                drv.refresh()
                time.sleep(2)
                _wait_overlay_invisible(drv, timeout=5)
            else:
                raise


def preencher_data_monitor(
    drv,
    data_ref: date | None = None,
    set_progress: Callable | None = None,
) -> None:
    progress = set_progress or _noop_progress
    data_ref = data_ref or date.today()
    data_str = _format_data_br(data_ref)
    try:
        progress(56, "Monitor: preenchendo data")
        _preencher_campo_data(drv, brflow.B_M_data, data_str)
        try:
            _preencher_campo_data(drv, brflow.B_M_data_final, data_str)
        except Exception as exc:
            log.debug("Data final do monitor não preenchida: %s", exc)
        progress(57, "Monitor: data preenchida")
    except Exception as exc:
        log.warning("Erro ao preencher data do monitor: %s", exc)


def selecionar_objeto_monitor(drv, set_progress: Callable | None = None) -> None:
    progress = set_progress or _noop_progress
    try:
        progress(58, "Monitor: selecionando objeto")
        elemento_objeto = wait_for_element(drv, "css", brflow.B_M_objeto, timeout=5)
        if elemento_objeto:
            drv.execute_script("arguments[0].click();", elemento_objeto)
            progress(59, "Monitor: objeto selecionado")
    except Exception:
        log.debug("Objeto do monitor não encontrado ou não disponível")


def baixar_monitor_csv(
    drv,
    local_download: str | Path,
    *,
    prefixo: str = DEFAULT_PREFIXO,
    extensao: str = DEFAULT_EXTENSAO,
    timeout: int = DEFAULT_TIMEOUT,
    set_progress: Callable | None = None,
) -> Path | None:
    """Aciona CSV + pesquisar e aguarda arquivo (sem conversão XLSX)."""
    progress = set_progress or _noop_progress
    local_download = Path(local_download)
    local_download.mkdir(parents=True, exist_ok=True)

    progress(60, "Monitor: acionando download CSV")
    try:
        _clicar_com_retry(drv, brflow.B_M_csv, by=By.ID, descricao="formato csv")
        _clicar_com_retry(drv, brflow.B_M_pesquisar, descricao="pesquisar monitor")
    except Exception as exc:
        log.warning("Falha ao acionar download monitor: %s", exc)
        return None

    before = {p.name for p in local_download.iterdir()} if local_download.exists() else set()
    progress(65, "Monitor: aguardando arquivo")
    novo = cp_wait_for_new_file(before, str(local_download), prefixo, extensao, timeout=timeout)
    if not novo:
        progress(20, "Monitor: falha no download")
        return None
    return Path(novo)


def navegar_monitor_e_baixar_csv(
    drv,
    local_download: str | Path,
    *,
    data_ref: date | None = None,
    prefixo: str = DEFAULT_PREFIXO,
    extensao: str = DEFAULT_EXTENSAO,
    timeout: int = DEFAULT_TIMEOUT,
    set_progress: Callable | None = None,
) -> Path | None:
    """Navega ao monitor, filtra data e baixa CSV bruto."""
    progress = set_progress or _noop_progress
    garantir_tela_monitor(drv, set_progress=progress)
    preencher_data_monitor(drv, data_ref, set_progress=progress)
    selecionar_objeto_monitor(drv, set_progress=progress)
    return baixar_monitor_csv(
        drv,
        local_download,
        prefixo=prefixo,
        extensao=extensao,
        timeout=timeout,
        set_progress=progress,
    )


def _normalizar_colunas_prod_hxh(df: pd.DataFrame) -> pd.DataFrame:
    rename: dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in ("matrícula", "matricula"):
            rename[col] = "Matrícula"
        elif key in ("data de análise", "data de analise"):
            rename[col] = "Data de Análise"
        elif key == "hora":
            rename[col] = "Hora"
        elif key in ("tempo total", "soma de tempo de análise em segundos"):
            rename[col] = "Tempo Total"
    if rename:
        df = df.rename(columns=rename)
    return df


def _tempo_total_para_timedelta(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_timedelta(series.fillna(0), unit="s")
    parsed = pd.to_timedelta(series.astype(str).str.strip(), errors="coerce")
    if parsed.notna().mean() > 0.5:
        return parsed.fillna(pd.Timedelta(seconds=0))
    numeric = pd.to_numeric(series, errors="coerce").fillna(0)
    return pd.to_timedelta(numeric, unit="s")


def adaptar_prod_hxh_para_rotina(
    csv_prod: str | Path,
    data_ref: date,
    temp_dir: str | Path | None = None,
) -> Path | None:
    """Converte CSV consolidado HxH para formato esperado por tratar_arquivo."""
    from app.bots.rotina.io import _ler_csv_tratamento, verifica_usuario

    csv_prod = Path(csv_prod)
    if not csv_prod.is_file():
        log.warning("CSV produtividade HxH inexistente: %s", csv_prod)
        return None

    df = _ler_csv_tratamento(str(csv_prod))
    if df is None or df.empty:
        log.warning("CSV produtividade HxH vazio: %s", csv_prod)
        return None

    df = _normalizar_colunas_prod_hxh(df)
    required = {"Matrícula", "Data de Análise", "Tempo Total"}
    missing = required - set(df.columns)
    if missing:
        log.warning("Adapter prod HxH: colunas ausentes %s (encontradas: %s)", missing, list(df.columns))
        return None

    dat_analise = pd.to_datetime(df["Data de Análise"], dayfirst=True, errors="coerce")
    if "Hora" in df.columns:
        horas = pd.to_numeric(df["Hora"], errors="coerce").fillna(0).astype(int)
        dat_analise = dat_analise + pd.to_timedelta(horas, unit="h")

    df = df.assign(_datAnalise=dat_analise)
    df = df[df["_datAnalise"].notna() & (df["_datAnalise"].dt.date == data_ref)].copy()
    if df.empty:
        log.warning("Adapter prod HxH: nenhuma linha para %s", data_ref)
        return None

    matricula = df["Matrícula"].astype(str).str.strip().str.lower().str[:7]
    valida = matricula.apply(verifica_usuario)
    df = df[valida].copy()
    if df.empty:
        log.warning("Adapter prod HxH: nenhuma matrícula válida para %s", data_ref)
        return None

    out = pd.DataFrame(
        {
            "desMatricula": matricula[valida].values,
            "datAnalise": df["_datAnalise"].values,
            "numTempoAnalise": _tempo_total_para_timedelta(df["Tempo Total"]).values,
        }
    )
    out = out[out["numTempoAnalise"] > pd.Timedelta(seconds=0)].copy()
    if out.empty:
        log.warning("Adapter prod HxH: sem tempos de análise positivos para %s", data_ref)
        return None

    dest_dir = Path(temp_dir or csv_prod.parent)
    dest_dir.mkdir(parents=True, exist_ok=True)
    destino = dest_dir / f"prod_hxh_rotina_{data_ref.isoformat()}.parquet"
    out.to_parquet(destino, index=False, compression="snappy")
    return destino


def processar_e_salvar_monitor_eventos_tratado(
    csv_monitor: str | Path,
    csv_prod: str | Path,
    data_ref: date | None = None,
    pasta_destino: str | Path | None = None,
    snapshot_at: datetime | None = None,
    publicar: bool = True,
) -> Path | None:
    """Aplica tratamento da rotina e salva parquet tratado para sync PostgreSQL."""
    from app.bots.rotina.tasks.monitor import (
        _pretratar_monitor_verifica_usuario,
        tratar_arquivo,
    )

    csv_monitor = Path(csv_monitor)
    csv_prod = Path(csv_prod)
    data_ref = data_ref or date.today()
    pasta_destino = Path(pasta_destino or PASTA_MONITOR_EVENTOS_TRATADO)
    pasta_destino.mkdir(parents=True, exist_ok=True)

    if not csv_monitor.is_file():
        log.warning("Arquivo monitor inexistente: %s", csv_monitor)
        return None

    _pretratar_monitor_verifica_usuario(str(csv_monitor))
    prod_temp = adaptar_prod_hxh_para_rotina(csv_prod, data_ref, temp_dir=csv_monitor.parent)
    if prod_temp is None:
        return None

    try:
        trat_out = tratar_arquivo(
            str(csv_monitor),
            str(prod_temp),
            data_ref,
            snapshot_at=snapshot_at,
        )
        if not trat_out:
            log.warning("tratar_arquivo não produziu saída para %s", data_ref)
            return None

        destino = pasta_destino / f"{PREFIXO_MONITOR_EVENTOS_TRATADO}{data_ref.isoformat()}.parquet"
        shutil.copy2(trat_out, destino)
        if publicar:
            publicar_monitor_eventos_tratado(destino)
        log.info("Monitor eventos tratado salvo: %s", destino)
        return destino
    finally:
        try:
            prod_temp.unlink(missing_ok=True)
        except Exception:
            pass


def unificar_monitor_hxh_com_confer_log(
    monitor_tratado: str | Path,
    log_eventos_csv: str | Path,
    data_ref: date,
    csv_prod_confer: str | Path | None = None,
    pasta_destino: str | Path | None = None,
) -> Path | None:
    """Une monitor HxH tratado com sessões do Log Eventos Confer e reemite o hook PG."""
    from app.bots.rotina.io import _ler_csv_tratamento, _preparar_dataframe_para_parquet
    from app.bots.rotina.tasks.confer import _gerar_csv_sessoes_por_evento
    from app.bots.rotina.tasks.monitor import _normalizar_df_monitor_sessoes

    monitor_tratado = Path(monitor_tratado)
    log_eventos_csv = Path(log_eventos_csv)
    pasta_destino = Path(pasta_destino or PASTA_MONITOR_EVENTOS_TRATADO)
    pasta_destino.mkdir(parents=True, exist_ok=True)

    if not monitor_tratado.is_file():
        log.warning("Monitor tratado inexistente para unificação HxH: %s", monitor_tratado)
        return None
    if not log_eventos_csv.is_file():
        log.warning("CSV Log Eventos inexistente para unificação HxH: %s", log_eventos_csv)
        return None

    try:
        csv_sessoes = _gerar_csv_sessoes_por_evento(
            log_eventos_csv,
            matricula=None,
            csv_prod_confer=csv_prod_confer,
        )
    except Exception as exc:
        log.warning("Falha ao gerar sessões do Log Eventos HxH: %s", exc, exc_info=True)
        return None

    try:
        df_monitor = pd.read_parquet(str(monitor_tratado))
    except Exception as exc:
        log.warning("Falha ao ler monitor tratado HxH (%s): %s", monitor_tratado, exc)
        return None

    try:
        df_confer = _ler_csv_tratamento(str(csv_sessoes))
    except Exception as exc:
        log.warning("Falha ao ler sessões Confer HxH (%s): %s", csv_sessoes, exc)
        return None

    df_monitor = _normalizar_df_monitor_sessoes(df_monitor)
    df_confer = _normalizar_df_monitor_sessoes(df_confer)
    df_unificado = pd.concat([df_monitor, df_confer], ignore_index=True, sort=False)
    if df_unificado.empty:
        log.warning("Unificação HxH monitor/log eventos resultou vazia para %s", data_ref)
        return None

    colunas_ordenacao = [c for c in ["Data", "Hora", "Usuário", "Data do Evento"] if c in df_unificado.columns]
    if colunas_ordenacao:
        df_unificado = df_unificado.sort_values(by=colunas_ordenacao, kind="stable").reset_index(drop=True)

    df_unificado = _normalizar_df_monitor_sessoes(df_unificado)
    destino = pasta_destino / f"{PREFIXO_MONITOR_EVENTOS_TRATADO}{data_ref.isoformat()}.parquet"
    df_out = _preparar_dataframe_para_parquet(df_unificado)
    df_out.to_parquet(str(destino), index=False, compression="snappy")
    publicar_monitor_eventos_tratado(destino)
    log.info(
        "Monitor HxH unificado com Log Eventos: %s (%s linhas)",
        destino,
        len(df_out),
    )
    return destino


def salvar_monitor_eventos_bruto(
    csv_path: str | Path,
    pasta_destino: str | Path | None = None,
    data_ref: date | None = None,
) -> Path | None:
    """Legado: converte CSV bruto em parquet. Preferir processar_e_salvar_monitor_eventos_tratado."""
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        log.warning("Arquivo monitor inexistente: %s", csv_path)
        return None

    pasta_destino = Path(pasta_destino or PASTA_MONITOR_EVENTOS_BRUTO)
    pasta_destino.mkdir(parents=True, exist_ok=True)
    data_ref = data_ref or date.today()

    df = cp_read_csv(str(csv_path), detect_sep=True)
    if df is None or df.empty:
        log.warning("CSV monitor vazio: %s", csv_path)
        return None

    destino = pasta_destino / f"{PREFIXO_MONITOR_EVENTOS_BRUTO}{data_ref.isoformat()}.parquet"
    df.to_parquet(destino, index=False, compression="snappy")
    _emit_monitor_eventos_saved(destino)
    log.info("Monitor eventos bruto salvo: %s", destino)
    return destino
