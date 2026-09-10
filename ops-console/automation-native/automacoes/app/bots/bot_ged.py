"""Bot GED - abre a tela de login do GED e mantém sessão até parada."""

import sys
import json
import os
import re
import shutil
import calendar
from io import StringIO
from pathlib import Path
import time
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

import logging
import requests
from urllib.parse import urlparse
from selenium.common.exceptions import StaleElementReferenceException
from app.infrastructure.selenium_helpers import By, click_element, create_driver, send_keys_to_element, wait_for_element, take_error_screenshot
from app.core.credentials import get_ged_credentials
from app.core.bot_runtime import BotRuntime
from app.core.common import safe_close_driver
from app.config import *
from app.infrastructure.file_mirror import espelhar_arquivo
from app.infrastructure.confer_helpers import fechar_calendario_se_aberto

GED_LOGIN_URL = "https://ged-web-frontend.claro.br.experian.eeco/login"
GED_PROGRESS_XPATH = "/html/body/app-root/app-home/main/app-download/div/div/dl/div[2]/progress"
GED_DOWNLOAD_LINK_XPATH = "/html/body/app-root/app-home/main/app-download/div/div/div/div/a"
GED_DOWNLOAD_LINK_XPATHS = [
    "//app-download//a[contains(@class,'mdn-Link-anchor')]",
    "//app-download//a[.//span[contains(normalize-space(.),'Baixar arquivo')]]",
    "//a[contains(@class,'mdn-Link-anchor')][.//span[contains(@class,'mdn-Link-anchor-label')]]",
    GED_DOWNLOAD_LINK_XPATH,
]
GED_PROTOCOLO_BASE_URL = "https://ged-web-frontend.claro.br.experian.eeco/protocolo"
GED_FALLBACK_HTTP_SEGUNDOS = 90
GED_LINK_ESTABILIZACAO_SEGUNDOS = 0.5
GED_DOWNLOAD_DIR = PLAN_IDF_SERASA_BOTS / "downloads_temp" / "ged"
GED_ESTADO_PATH  = PASTA_CONFIG / "ged_estado.json"
_GED_OUT_ENV = os.getenv("GED_OUTPUT_DIR") or os.getenv("ROBOT_OUTPUT_DIR")
_GED_PARQUET_ONEDRIVE_ENV = os.getenv("GED_PARQUET_ONEDRIVE_DIR")
_GED_CSV_ONEDRIVE_ENV = os.getenv("GED_CSV_ONEDRIVE_DIR")
PASTA_GED_TRATADO = Path(_GED_OUT_ENV).expanduser() if _GED_OUT_ENV else (DEFAULT_SHAREPOINT_BOTS / "ged-detalhado-tratado")
PASTA_GED_PARQUET_ONEDRIVE = (
    Path(_GED_PARQUET_ONEDRIVE_ENV).expanduser()
    if _GED_PARQUET_ONEDRIVE_ENV
    else DEFAULT_SHAREPOINT_GED_PARQUET
)
PASTA_GED_CSV_GED20 = (
    Path(_GED_CSV_ONEDRIVE_ENV).expanduser()
    if _GED_CSV_ONEDRIVE_ENV
    else DEFAULT_SHAREPOINT_GED_CSV
)
GED_COLUNAS_SAIDA = [
    "Protocolo",
    "Data do Recebimento",
    "Tipo de Serviço Primário",
    "Data da Venda",
    "Data do Batimento",
    "Data Retorno Inspeção",
    "Data Envio Inspe.",
    "Canal de Ativação",
    "Status Contrato", 
    "Aceite Digital",
]
GED_COLUNAS_ALIASES = {
    "Data Retorno Inspeção ": "Data Retorno Inspeção",
}
GED_CSV_ENCODINGS = ("latin1", "cp1252", "utf-8-sig")

log = logging.getLogger("robots.bot_ged")

_runtime = BotRuntime(mode="ged")
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
parar_event = _runtime.parar_event
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress
_reset_progress_state = _runtime.reset_progress_state
_begin_cycle = _runtime.begin_cycle
drv = None


# ---------------------------------------------------------------------------
# Agendamento / controle de janela de execução
# ---------------------------------------------------------------------------

_HORA_DIURNO_INI = 7   # 07:00 inclusive
_HORA_DIURNO_FIM = 22  # 22:00 exclusive
_INTERVALO_DIURNO_SEGUNDOS = 2 * 3600  # 2 horas
_MAX_WORKERS_SIMULTANEOS = 5


def _cfg_headless() -> bool:
    raw = (os.getenv("GED_HEADLESS") or os.getenv("ROBOT_HEADLESS") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _cfg_max_workers(total_intervalos: int) -> int:
    raw = (os.getenv("GED_MAX_WORKERS") or os.getenv("ROBOT_MAX_WORKERS") or "").strip()
    limite = max(1, min(total_intervalos, _MAX_WORKERS_SIMULTANEOS))
    if not raw:
        return limite
    try:
        value = int(raw)
    except ValueError:
        return limite
    return max(1, min(value, limite))


def _cfg_execucao_imediata() -> str:
    """Retorna período forçado de execução imediata: 'diurno', 'noturno' ou ''."""
    raw = (os.getenv("GED_EXECUCAO_IMEDIATA") or "").strip().lower()
    return raw if raw in ("diurno", "noturno") else ""


def _is_periodo_diurno(agora: datetime | None = None) -> bool:
    """Retorna True se o horário estiver entre 06:00 e 20:00 (período diurno)."""
    hora = (agora or datetime.now()).hour
    return _HORA_DIURNO_INI <= hora < _HORA_DIURNO_FIM


def _data_noturna_atual() -> date:
    """Converte a hora atual para uma 'data de noite' unificada.

    Horas 00–05 são tratadas como extensão da noite anterior,
    garantindo que 22:00 dia 13 e 02:00 dia 14 sejam a mesma noite.
    """
    agora = datetime.now()
    if agora.hour < _HORA_DIURNO_INI:
        return (agora - timedelta(days=1)).date()
    return agora.date()


def _formatar_intervalo_mes(mes_inicio: date, mes_fim: date) -> tuple[str, str]:
    return (
        mes_inicio.strftime("%d/%m/%Y"),
        mes_fim.strftime("%d/%m/%Y"),
    )


def _ultimo_dia_mes(ano: int, mes: int) -> int:
    return calendar.monthrange(ano, mes)[1]


def _quinzena_limites(ano: int, mes: int, numero: int) -> tuple[date, date]:
    if numero == 1:
        return date(ano, mes, 1), date(ano, mes, 15)
    ultimo = _ultimo_dia_mes(ano, mes)
    return date(ano, mes, 16), date(ano, mes, ultimo)


def _dividir_intervalo_em_quinzenas(inicio: date, fim: date) -> list[tuple[date, date, int]]:
    """Divide um intervalo em quinzenas calendário (Q1: 1–15, Q2: 16–fim)."""
    if inicio > fim:
        return []

    resultados: list[tuple[date, date, int]] = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        for numero in (1, 2):
            q_inicio, q_fim = _quinzena_limites(ano, mes, numero)
            ef_inicio = max(q_inicio, inicio)
            ef_fim = min(q_fim, fim)
            if ef_inicio <= ef_fim:
                resultados.append((ef_inicio, ef_fim, numero))
        if mes == 12:
            ano += 1
            mes = 1
        else:
            mes += 1
    return resultados


def _intervalos_quinzena_formatados(
    inicio: date, fim: date,
) -> list[tuple[str, str, str, int]]:
    """Retorna quinzenas formatadas: (data_inicio, data_fim, yyyymm, numero)."""
    return [
        (
            q_inicio.strftime("%d/%m/%Y"),
            q_fim.strftime("%d/%m/%Y"),
            q_inicio.replace(day=1).strftime("%Y%m"),
            numero,
        )
        for q_inicio, q_fim, numero in _dividir_intervalo_em_quinzenas(inicio, fim)
    ]


def _calcular_periodo_diurno() -> list[tuple[str, str, str, int]]:
    """Retorna quinzenas do mês atual até hoje (limite de 15 dias por download)."""
    hoje = date.today()
    return _intervalos_quinzena_formatados(hoje.replace(day=1), hoje)


def _calcular_periodos_noturnos() -> list[tuple[str, str, str, int]]:
    """Retorna quinzenas dos 2 meses anteriores completos (mês mais recente primeiro)."""
    hoje = date.today()
    ultimo_mes_ant = hoje.replace(day=1) - timedelta(days=1)
    primeiro_mes_ant = ultimo_mes_ant.replace(day=1)
    ultimo_2meses_ant = primeiro_mes_ant - timedelta(days=1)
    primeiro_2meses_ant = ultimo_2meses_ant.replace(day=1)

    intervalos = _intervalos_quinzena_formatados(primeiro_mes_ant, ultimo_mes_ant)
    intervalos.extend(_intervalos_quinzena_formatados(primeiro_2meses_ant, ultimo_2meses_ant))
    return intervalos


def _ler_estado() -> dict:
    """Lê o arquivo de estado JSON; retorna {} em caso de ausência ou erro."""
    if GED_ESTADO_PATH.exists():
        try:
            return json.loads(GED_ESTADO_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _salvar_estado(estado: dict) -> None:
    GED_ESTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    GED_ESTADO_PATH.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _deve_executar_diurno(estado: dict) -> bool:
    """True se nunca executou ou se já passaram ≥2 horas da última execução diurna."""
    ultima_str = estado.get("ultima_execucao_diurna")
    if not ultima_str:
        return True
    try:
        dt_ultima = datetime.fromisoformat(ultima_str)
        return (datetime.now() - dt_ultima).total_seconds() >= _INTERVALO_DIURNO_SEGUNDOS
    except Exception:
        return True


def _segundos_ate_proxima_execucao_diurna(estado: dict) -> int:
    """Retorna quantos segundos faltam para completar o intervalo diurno de 2h."""
    ultima_str = estado.get("ultima_execucao_diurna")
    if not ultima_str:
        return 0
    try:
        dt_ultima = datetime.fromisoformat(ultima_str)
        restante = _INTERVALO_DIURNO_SEGUNDOS - (datetime.now() - dt_ultima).total_seconds()
        return max(0, int(restante))
    except Exception:
        return 0


def _deve_executar_noturno(estado: dict) -> bool:
    """True se o download noturno ainda não foi realizado nesta noite."""
    return estado.get("download_noturno_data") != _data_noturna_atual().isoformat()


def _segundos_ate_inicio_diurno() -> int:
    """Retorna segundos até a próxima janela diurna (06:00 local)."""
    agora = datetime.now()
    proximo = agora.replace(hour=_HORA_DIURNO_INI, minute=0, second=0, microsecond=0)
    if agora >= proximo:
        proximo = proximo + timedelta(days=1)
    return max(1, int((proximo - agora).total_seconds()))


def _executar_intervalos_paralelo(
    intervalos_execucao: list[tuple[str, str, str, int]],
    progresso_base: int = 10,
    progresso_fim: int = 95,
) -> None:
    fila_intervalos = intervalos_execucao.copy()
    total_intervalos = len(fila_intervalos)
    max_w = _cfg_max_workers(total_intervalos)
    _set_status(
        f"GED: iniciando {total_intervalos} download(s) com até {max_w} worker(s) em paralelo"
    )
    progresso_base = max(0, min(100, int(progresso_base)))
    progresso_fim = max(progresso_base, min(100, int(progresso_fim)))
    faixa = progresso_fim - progresso_base
    _set_progress(
        progresso_base,
        f"GED: downloads finalizados 0/{total_intervalos} | aguardando intervalo",
    )

    erros: list[str] = []
    concluidos = 0
    sucessos = 0
    with ThreadPoolExecutor(max_workers=max_w) as executor:
        futures = {
            executor.submit(
                _executar_intervalo,
                inicio, fim, yyyymm, numero, idx, total_intervalos,
            ): (inicio, fim, yyyymm, numero)
            for idx, (inicio, fim, yyyymm, numero) in enumerate(fila_intervalos, start=1)
        }
        for future in as_completed(futures):
            if parar_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                return
            inicio, fim, yyyymm, numero = futures[future]
            try:
                msg = future.result()
                sucessos += 1
                _set_status(msg)
            except Exception as exc:
                erros.append(f"{inicio}→{fim} (Q{numero}/{yyyymm}): {exc}")
                log.error(
                    f"GED worker falhou {inicio}→{fim} Q{numero}/{yyyymm}: {exc}",
                    exc_info=True,
                )
            finally:
                concluidos += 1
                progresso = progresso_base + int((concluidos / total_intervalos) * faixa)
                _set_progress(
                    progresso,
                    f"GED: downloads finalizados {sucessos}/{total_intervalos} | "
                    f"Q{numero}/{yyyymm} {inicio}→{fim}",
                )

    if erros:
        raise RuntimeError(
            f"GED: {len(erros)} worker(s) falharam:\n" + "\n".join(erros)
        )


def _montar_nome_saida_ged_mensal(data_referencia: date) -> str:
    """Monta o nome do parquet consolidado mensal do GED."""
    return f"{PREFIXO_GED_TRATADO}{data_referencia.strftime('%Y%m')}.parquet"


def _montar_nome_saida_ged_quinzena(yyyymm: str, numero: int) -> str:
    """Monta o nome do parquet parcial de quinzena: ged-detalhado-tratado_YYYYMM_N.parquet."""
    return f"{PREFIXO_GED_TRATADO}{yyyymm}_{numero}.parquet"


def _consolidar_quinzenas_mes(yyyymm: str) -> Path | None:
    """Consolida parquets quinzenais _1/_2 em um parquet mensal."""
    caminhos_quinzena: list[Path] = []
    for numero in (1, 2):
        caminho = PASTA_GED_TRATADO / _montar_nome_saida_ged_quinzena(yyyymm, numero)
        if caminho.exists():
            caminhos_quinzena.append(caminho)

    if not caminhos_quinzena:
        return None

    partes = [pd.read_parquet(c) for c in caminhos_quinzena]
    df_mes = pd.concat(partes, ignore_index=True)
    if not df_mes.empty:
        df_mes = df_mes.drop_duplicates().reset_index(drop=True)

    data_ref = datetime.strptime(yyyymm, "%Y%m").date()
    caminho_mensal = PASTA_GED_TRATADO / _montar_nome_saida_ged_mensal(data_ref)
    _salvar_saida_parquet(df_mes, caminho_mensal)

    for caminho in caminhos_quinzena:
        try:
            caminho.unlink(missing_ok=True)
            caminho.with_suffix(".csv").unlink(missing_ok=True)
        except Exception as exc:
            log.warning("GED: falha ao remover quinzena '%s': %s", caminho.name, exc)

    _set_status(f"GED: quinzenas consolidadas em {caminho_mensal.name}")
    return caminho_mensal


def _espelhar_saida_onedrive(caminho_parquet: Path) -> None:
    """Copia o parquet para a pasta GED parquet no OneDrive."""
    espelhar_arquivo(
        caminho_parquet,
        [PASTA_GED_PARQUET_ONEDRIVE / caminho_parquet.name],
        log=log,
    )


def _exportar_csv_ged20(df: pd.DataFrame, caminho_parquet: Path) -> Path | None:
    """Escreve CSV gerencial em GED 2.0 (mesmo stem do parquet). Bots permanece parquet-only."""
    destino = PASTA_GED_CSV_GED20 / caminho_parquet.with_suffix(".csv").name
    try:
        PASTA_GED_CSV_GED20.mkdir(parents=True, exist_ok=True)
        df.to_csv(destino, sep=";", index=False, encoding="cp1252")
        log.info("GED: CSV gerencial salvo | destino=%s", destino)
        _set_status(f"GED: CSV gerencial salvo em {destino.name}")
        return destino
    except Exception as exc:
        log.warning("GED: falha ao exportar CSV para GED 2.0 (%s): %s", destino, exc)
        return None


_REGEX_GED_MENSAL = re.compile(
    rf"^{re.escape(PREFIXO_GED_TRATADO)}(\d{{6}})\.parquet$",
    re.IGNORECASE,
)
_REGEX_GED_QUINZENA = re.compile(
    rf"^{re.escape(PREFIXO_GED_TRATADO)}(\d{{6}})_([12])\.parquet$",
    re.IGNORECASE,
)
_REGEX_GED_SEMANAL_DIURNO = re.compile(
    rf"^{re.escape(PREFIXO_GED_TRATADO)}(\d+)sem\.parquet$",
    re.IGNORECASE,
)
_REGEX_GED_PERIODO = re.compile(
    rf"^{re.escape(PREFIXO_GED_TRATADO)}(\d{{2}})-(\d{{2}})(\d{{2}})(\d{{4}})\.parquet$",
    re.IGNORECASE,
)


def _salvar_saida_parquet(df: pd.DataFrame, caminho_parquet: Path) -> Path:
    """Salva parquet na pasta principal, espelha no OneDrive e exporta CSV em GED 2.0."""
    caminho_parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(caminho_parquet, index=False)
    # Bots e pasta GED parquet permanecem sem CSV residual.
    caminho_parquet.with_suffix(".csv").unlink(missing_ok=True)
    _espelhar_saida_onedrive(caminho_parquet)
    (PASTA_GED_PARQUET_ONEDRIVE / caminho_parquet.with_suffix(".csv").name).unlink(missing_ok=True)
    _exportar_csv_ged20(df, caminho_parquet)
    # Só o mensal consolidado entra no sync bot→DB — quinzenas _1/_2 são intermediárias.
    if _REGEX_GED_MENSAL.match(caminho_parquet.name):
        print(f"ROTINA_BRUTO_SAVED|ged_detalhado|{caminho_parquet.resolve()}", flush=True)
    return caminho_parquet


def _inferir_chave_mes_parquet_ged(caminho: Path) -> str | None:
    try:
        df = pd.read_parquet(caminho, columns=["Data do Batimento"])
    except Exception:
        return None
    if df.empty or "Data do Batimento" not in df.columns:
        return None
    serie = pd.to_datetime(df["Data do Batimento"], dayfirst=True, errors="coerce").dropna()
    if serie.empty:
        return None
    return serie.min().strftime("%Y%m")


def _limpar_saidas_legadas_ged() -> None:
    """Consolida arquivos semanais/período legados em parquets mensais YYYYMM."""
    if not PASTA_GED_TRATADO.exists():
        return

    por_mes: dict[str, list[Path]] = {}
    for caminho in PASTA_GED_TRATADO.glob(f"{PREFIXO_GED_TRATADO}*.parquet"):
        if _REGEX_GED_MENSAL.match(caminho.name):
            continue

        m_quinzena = _REGEX_GED_QUINZENA.match(caminho.name)
        if m_quinzena:
            chave = m_quinzena.group(1)
            por_mes.setdefault(chave, []).append(caminho)
            continue

        m_periodo = _REGEX_GED_PERIODO.match(caminho.name)
        if m_periodo:
            chave = f"{m_periodo.group(4)}{m_periodo.group(3)}"
            por_mes.setdefault(chave, []).append(caminho)
            continue

        if _REGEX_GED_SEMANAL_DIURNO.match(caminho.name):
            chave = _inferir_chave_mes_parquet_ged(caminho) or date.today().strftime("%Y%m")
            por_mes.setdefault(chave, []).append(caminho)

    if not por_mes:
        return

    PASTA_GED_TRATADO.mkdir(parents=True, exist_ok=True)
    for chave_mes, arquivos_legados in por_mes.items():
        data_ref = datetime.strptime(chave_mes, "%Y%m").date()
        caminho_mensal = PASTA_GED_TRATADO / _montar_nome_saida_ged_mensal(data_ref)
        partes: list[pd.DataFrame] = []
        if caminho_mensal.exists():
            partes.append(pd.read_parquet(caminho_mensal))
        for arquivo in sorted(arquivos_legados):
            partes.append(pd.read_parquet(arquivo))

        if not partes:
            continue

        df_mes = pd.concat(partes, ignore_index=True)
        if not df_mes.empty:
            df_mes = df_mes.drop_duplicates().reset_index(drop=True)

        _salvar_saida_parquet(df_mes, caminho_mensal)
        removidos = 0
        for arquivo in arquivos_legados:
            try:
                arquivo.unlink(missing_ok=True)
                arquivo.with_suffix(".csv").unlink(missing_ok=True)
                removidos += 1
            except Exception as exc:
                log.warning("GED: falha ao remover legado '%s': %s", arquivo.name, exc)

        caminho_mensal.with_suffix(".csv").unlink(missing_ok=True)
        _set_status(
            f"GED: legados consolidados em {caminho_mensal.name} | removidos {removidos}/{len(arquivos_legados)}"
        )


def _validar_credenciais_ged() -> tuple[str, str]:
    """Garante GED_USER/GED_PASS antes de abrir o Chrome."""
    ged_user, ged_pass = get_ged_credentials()
    if not ged_user or not ged_pass:
        raise RuntimeError(
            "Credenciais GED ausentes (GED_USER / GED_PASS). "
            "Configure em backend/.env ou automacoes/.env e reinicie o robô."
        )
    return ged_user, ged_pass


def _limpar_residuos_pasta(caminho: Path, remover_diretorio: bool = False) -> None:
    """Remove resíduos de execução (arquivos e subpastas) de forma segura."""
    if not caminho.exists():
        return

    try:
        for item in caminho.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink(missing_ok=True)
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
            except Exception as exc:
                log.warning(f"Falha ao limpar resíduo '{item}': {exc}")

        if remover_diretorio:
            try:
                caminho.rmdir()
            except Exception:
                pass
    except Exception as exc:
        log.warning(f"Falha ao varrer pasta de resíduos '{caminho}': {exc}")


# ---------------------------------------------------------------------------


def start(settings=None):
    global drv
    parar_event.clear()
    drv = None
    _reset_progress_state()

    try:
        _set_status("GED: iniciando execucao")
        _validar_credenciais_ged()
        _set_progress(10, "GED: preparando workers")
        periodo_forcado = _cfg_execucao_imediata()
        if periodo_forcado:
            _set_status(
                f"GED: execução imediata configurada para período {periodo_forcado}; primeiro ciclo será executado sem aguardar janela"
            )
        GED_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        PASTA_GED_TRATADO.mkdir(parents=True, exist_ok=True)
        # Limpeza inicial de resíduos de execuções anteriores.
        _limpar_residuos_pasta(GED_DOWNLOAD_DIR, remover_diretorio=False)
        _limpar_saidas_legadas_ged()

        while not parar_event.is_set():
            estado = _ler_estado()
            execucao_imediata_ativa = periodo_forcado in ("diurno", "noturno")
            diurno = (periodo_forcado == "diurno") if execucao_imediata_ativa else _is_periodo_diurno()

            if diurno:
                if not execucao_imediata_ativa and not _deve_executar_diurno(estado):
                    restante = _segundos_ate_proxima_execucao_diurna(estado)
                    mins = restante // 60
                    segs = restante % 60
                    _set_status(
                        f"GED: aguardando próxima execução diurna em {mins:02d}:{segs:02d}"
                    )
                    if parar_event.wait(min(60, max(1, restante))):
                        return
                    continue

                intervalos_execucao = _calcular_periodo_diurno()
                _set_status(
                    f"GED: período diurno — {len(intervalos_execucao)} download(s) quinzenal(is) "
                    f"({intervalos_execucao[0][0]} → {intervalos_execucao[-1][1]})"
                )
                _begin_cycle()
                _executar_intervalos_paralelo(intervalos_execucao, progresso_base=10, progresso_fim=90)
                for yyyymm in sorted({item[2] for item in intervalos_execucao}):
                    _consolidar_quinzenas_mes(yyyymm)

                estado["ultima_execucao_diurna"] = datetime.now().isoformat()
                _salvar_estado(estado)
                if execucao_imediata_ativa:
                    periodo_forcado = ""
                _set_progress(100, "GED: ciclo diurno concluido")
                _set_status("GED: ciclo diurno finalizado; aguardando próxima execução")
                continue

            # Fluxo noturno: executa uma vez por noite e depois aguarda até o diurno
            if not execucao_imediata_ativa and not _deve_executar_noturno(estado):
                restante = _segundos_ate_inicio_diurno()
                hrs = restante // 3600
                mins = (restante % 3600) // 60
                _set_status(
                    f"GED: download noturno já realizado; aguardando início do diurno em {hrs:02d}:{mins:02d}"
                )
                if parar_event.wait(min(60, restante)):
                    return
                continue

            intervalos_execucao = _calcular_periodos_noturnos()
            _set_status(
                f"GED: período noturno — {len(intervalos_execucao)} download(s) quinzenal(is)"
            )
            _begin_cycle()
            _executar_intervalos_paralelo(intervalos_execucao, progresso_base=10, progresso_fim=94)
            for yyyymm in sorted({item[2] for item in intervalos_execucao}):
                _consolidar_quinzenas_mes(yyyymm)

            estado["download_noturno_data"] = _data_noturna_atual().isoformat()
            _salvar_estado(estado)
            if execucao_imediata_ativa:
                periodo_forcado = ""
            _set_progress(100, "GED: ciclo noturno concluido")
            restante = _segundos_ate_inicio_diurno()
            hrs = restante // 3600
            mins = (restante % 3600) // 60
            _set_status(
                f"GED: ciclo noturno concluído; aguardando início do diurno em {hrs:02d}:{mins:02d}"
            )
            if parar_event.wait(min(60, restante)):
                return
            continue

    except Exception as exc:
        log.error(f"Erro no bot GED: {exc}", exc_info=True)
        _set_status(f"Erro no bot GED: {exc}")
        raise
    finally:
        drv = None
        _set_status("GED: execucao finalizada")


def _executar_intervalo(
    data_inicio: str,
    data_fim: str,
    yyyymm: str,
    numero_quinzena: int,
    worker_id: int,
    total: int,
) -> str:
    """Cria driver dedicado, executa download+tratamento de um intervalo e descarta o driver."""
    prefixo = f"worker {worker_id}/{total} [Q{numero_quinzena}/{yyyymm} {data_inicio} → {data_fim}]"
    worker_download_dir = GED_DOWNLOAD_DIR / f"worker_{worker_id}"
    max_tentativas = 2
    ultimo_erro: Exception | None = None

    for tentativa in range(1, max_tentativas + 1):
        worker_download_dir.mkdir(parents=True, exist_ok=True)
        _limpar_residuos_pasta(worker_download_dir, remover_diretorio=False)
        _validar_credenciais_ged()
        worker_drv = None
        try:
            if tentativa > 1:
                _set_status(f"GED {prefixo}: retry {tentativa}/{max_tentativas}")
            else:
                _set_status(f"GED {prefixo}: iniciando")
            worker_drv = create_driver(
                headless=_cfg_headless(),
                download_dir=str(worker_download_dir),
                ignore_certificate_errors=True,
            )
            _navegar_para_GED(
                data_inicio, data_fim,
                yyyymm=yyyymm,
                numero_quinzena=numero_quinzena,
                worker_drv=worker_drv,
                worker_download_dir=worker_download_dir,
                worker_id=worker_id,
            )
            return f"GED {prefixo}: concluído com sucesso"
        except (TimeoutError, RuntimeError, FileNotFoundError, ValueError) as exc:
            ultimo_erro = exc
            log.warning(
                "GED %s: falha na tentativa %d/%d: %s",
                prefixo, tentativa, max_tentativas, exc,
            )
            if tentativa < max_tentativas:
                _set_status(f"GED {prefixo}: falha de download, tentando novamente")
                continue
            raise
        finally:
            safe_close_driver(worker_drv, logger=log)
            _limpar_residuos_pasta(worker_download_dir, remover_diretorio=True)

    if ultimo_erro:
        raise ultimo_erro
    raise RuntimeError(f"GED {prefixo}: falha sem exceção registrada")


def _navegar_para_GED(
    data_inicio: str,
    data_fim: str,
    yyyymm: str = "",
    numero_quinzena: int = 1,
    worker_drv=None,
    worker_download_dir: Path | None = None,
    worker_id: int | None = None,
):
    if worker_drv is not None:
        ged_user, ged_pass = _validar_credenciais_ged()
        _set_status(f"Navegando para GED [{data_inicio} → {data_fim}]")
        worker_drv.get(GED_LOGIN_URL)

        send_keys_to_element(worker_drv, By.XPATH, "/html/body/app-root/app-login/main/div/div/div/form/div[1]/div/input", ged_user)
        send_keys_to_element(worker_drv, By.XPATH, "/html/body/app-root/app-login/main/div/div/div/form/div[2]/div/input", ged_pass)

        click_element(worker_drv, By.XPATH, "/html/body/app-root/app-login/main/div/div/div/form/button")
        try:
            wait_for_element(worker_drv, By.XPATH, "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[1]/div/div[4]/div[1]/a", timeout=30)
            _set_status("Login no GED realizado com sucesso")
        except Exception:
            wait_for_element(worker_drv, By.XPATH, "/html/body/app-root/app-message/div/div/div", timeout=30)
            worker_drv.get("https://ged-web-api.claro.br.experian.eeco/api/v1/autenticacao/autenticar")
            worker_drv.get(GED_LOGIN_URL)
            send_keys_to_element(worker_drv, By.XPATH, "/html/body/app-root/app-login/main/div/div/div/form/div[1]/div/input", ged_user)
            send_keys_to_element(worker_drv, By.XPATH, "/html/body/app-root/app-login/main/div/div/div/form/div[2]/div/input", ged_pass)
            click_element(worker_drv, By.XPATH, "/html/body/app-root/app-login/main/div/div/div/form/button")

        click_element(worker_drv, By.XPATH, "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[1]/div/div[4]/div[1]/a")
        click_element(worker_drv, By.XPATH, "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[1]/div/div[4]/div[2]/div/div/div/a")
        send_keys_to_element(worker_drv, By.XPATH, "/html/body/app-root/app-home/main/app-digitalizacao/div/div[2]/div/app-posvenda/div[1]/div[1]/div/app-datepicker[1]/div/div/div/div[1]/div/input", data_inicio)
        send_keys_to_element(worker_drv, By.XPATH, "/html/body/app-root/app-home/main/app-digitalizacao/div/div[2]/div/app-posvenda/div[1]/div[1]/div/app-datepicker[1]/div/div/div/div[2]/div/input", data_fim)
        fechar_calendario_se_aberto(worker_drv)
        click_element(worker_drv, By.XPATH, "/html/body/app-root/app-home/main/app-digitalizacao/div/div[2]/div/app-posvenda/div[1]/div[1]/div/app-radio/form/div[2]/div/div[2]/div/div")
        click_element(worker_drv, By.XPATH, "/html/body/app-root/app-home/main/app-digitalizacao/div/div[2]/div/app-posvenda/div[1]/div[2]/div/button[2]")
        dl_dir = worker_download_dir or GED_DOWNLOAD_DIR
        dl_dir.mkdir(parents=True, exist_ok=True)
        _trocar_para_guia_protocolo(worker_drv)
        _aguardar_progresso_100(worker_drv)
        _trocar_para_guia_protocolo(worker_drv)
        link_el, href_download, xpath_usado = _aguardar_link_download(worker_drv, timeout=120)
        pastas_busca = _pastas_busca_download(dl_dir)
        arquivos_antes = _snapshot_arquivos_pastas(pastas_busca)
        url_protocolo = ""
        try:
            url_protocolo = (worker_drv.current_url or "").strip()
        except Exception:
            url_protocolo = ""
        token_esperado = _extrair_token_ged(url_protocolo) or _extrair_token_ged(href_download)
        _set_status(f"GED: clicando download (pasta esperada: {dl_dir})")
        texto_link = (link_el.text or "").strip()
        href_http = href_download if _href_util_para_http(href_download) else None
        log.info(
            "GED: preparando clique no download | xpath=%s | texto=%s | href=%s | "
            "href_http=%s | token=%s | url=%s",
            xpath_usado, texto_link, href_download, href_http, token_esperado or "-",
            url_protocolo or worker_drv.current_url,
        )
        inicio_clique = time.time()
        if not _clicar_elemento_link(worker_drv, link_el):
            raise RuntimeError(
                f"Falha ao clicar no link de download do GED "
                f"(xpath={xpath_usado}, url={worker_drv.current_url})"
            )
        arquivo_baixado = _aguardar_download_finalizado(
            dl_dir,
            arquivos_antes,
            timeout=1800,
            inicio_clique=inicio_clique,
            worker_drv=worker_drv,
            href_fallback=href_http,
            data_inicio=data_inicio,
            data_fim=data_fim,
            token_esperado=token_esperado,
        )
        _set_status(f"Download GED finalizado: {arquivo_baixado.name}")
        arquivo_claim = _claim_arquivo_download(
            arquivo_baixado,
            dl_dir,
            worker_id=worker_id or 0,
            data_inicio=data_inicio,
            data_fim=data_fim,
        )
        _validar_arquivo_antes_tratamento(arquivo_claim)
        arquivo_tratado = _tratar_arquivo_ged(
            arquivo_claim,
            data_inicio=data_inicio,
            data_fim=data_fim,
            yyyymm=yyyymm,
            numero_quinzena=numero_quinzena,
        )
        _set_status(f"Arquivo GED tratado com sucesso: {arquivo_tratado.name}")
    else:
        log.warning("Driver do bot GED não está inicializado.")


def _href_util_para_http(href: str) -> bool:
    if not href or href.lower().startswith("javascript"):
        return False
    if href in ("#", ""):
        return False
    return href.lower().startswith(("http://", "https://", "/"))


def _encontrar_link_download(worker_drv, timeout: float = 2, ensure_visible: bool = False):
    """Localiza o link 'Baixar arquivo'. Retorna (elemento, href, xpath) ou (None, '', '')."""
    for xpath in GED_DOWNLOAD_LINK_XPATHS:
        link = wait_for_element(
            worker_drv, By.XPATH, xpath, timeout=timeout, ensure_visible=ensure_visible,
        )
        if link:
            href = (link.get_attribute("href") or "").strip()
            return link, href, xpath
    return None, "", ""


def _clicar_elemento_link(worker_drv, link_el) -> bool:
    try:
        link_el.click()
        return True
    except Exception:
        try:
            worker_drv.execute_script("arguments[0].click();", link_el)
            return True
        except Exception as exc:
            log.error("GED: falha ao clicar no link de download: %s", exc)
            return False


def _finalizar_progresso_por_link(
    worker_drv,
    maior_percentual: int,
    barra_ja_apareceu: bool,
    emitir_progresso: bool,
    durante_aguardo: bool = False,
) -> bool:
    link, href, xpath = _encontrar_link_download(worker_drv, timeout=1, ensure_visible=True)
    if not link:
        return False

    texto = (link.text or "").strip()
    contexto = "durante aguardo" if durante_aguardo else "após barra sumir"
    log.info(
        "GED: progresso concluído (link Baixar arquivo detectado %s) | "
        "maior_percentual=%d | barra_vista=%s | xpath=%s | href=%s | texto=%s | url=%s",
        contexto, maior_percentual, barra_ja_apareceu, xpath, href, texto,
        worker_drv.current_url,
    )
    if emitir_progresso:
        _set_progress(100, "GED: download 100%")
    _set_status(
        "Barra de progresso GED finalizada (link Baixar arquivo detectado "
        f"{contexto}, max={maior_percentual}%)"
    )
    return True


def _aguardar_progresso_100(worker_drv, timeout: int = 4 * 3600, intervalo: float = 0.3, emitir_progresso: bool = False):
    """Aguarda a barra de progresso do GED atingir 100% ou o link de download ficar disponível."""
    if worker_drv is None:
        return

    _set_status("Aguardando barra de progresso do GED chegar em 100%")
    inicio = time.time()
    ultimo_percentual = -1
    maior_percentual = -1
    barra_ja_apareceu = False
    barra_sumiu_em: float | None = None

    def _to_float(raw: str):
        txt = (raw or "").strip().replace("%", "").replace(",", ".")
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return None

    def _ler_atributo_com_retry(elemento, nome: str, tentativas: int = 3):
        for _ in range(tentativas):
            try:
                return (elemento.get_attribute(nome) or "").strip()
            except StaleElementReferenceException:
                time.sleep(0.1)
        return ""

    while (time.time() - inicio) < timeout:
        if parar_event.is_set():
            _set_status("Aguardo da barra interrompido")
            return

        if _finalizar_progresso_por_link(
            worker_drv, maior_percentual, barra_ja_apareceu, emitir_progresso, durante_aguardo=True,
        ):
            return

        barra = wait_for_element(worker_drv, By.XPATH, GED_PROGRESS_XPATH, timeout=1)
        if not barra:
            if barra_ja_apareceu:
                if barra_sumiu_em is None:
                    barra_sumiu_em = time.time()
                elif (time.time() - barra_sumiu_em) >= 2:
                    if _finalizar_progresso_por_link(
                        worker_drv, maior_percentual, barra_ja_apareceu, emitir_progresso,
                    ):
                        return
                    if (time.time() - barra_sumiu_em) >= 120:
                        log.error(
                            "GED: barra sumiu sem link de download | maior_percentual=%d | "
                            "barra_vista=%s | url=%s",
                            maior_percentual, barra_ja_apareceu, worker_drv.current_url,
                        )
                        raise TimeoutError(
                            "Relatório processado, mas link de download não apareceu"
                        )
            time.sleep(intervalo)
            continue

        barra_sumiu_em = None
        barra_ja_apareceu = True

        percentual = None
        try:
            valor_attr = _ler_atributo_com_retry(barra, "value")
            max_attr = _ler_atributo_com_retry(barra, "max")
            aria_attr = _ler_atributo_com_retry(barra, "aria-valuenow")
        except StaleElementReferenceException:
            time.sleep(intervalo)
            continue

        if not valor_attr and not aria_attr:
            time.sleep(intervalo)
            continue

        if valor_attr:
            valor_num = _to_float(valor_attr)
            max_num = _to_float(max_attr) if max_attr else 100.0
            if valor_num is not None and max_num and max_num > 0:
                percentual = int((valor_num / max_num) * 100)
            elif valor_num is not None and "%" in valor_attr:
                percentual = int(valor_num)

        if percentual is None and aria_attr:
            aria_num = _to_float(aria_attr)
            if aria_num is not None:
                percentual = int(aria_num)

        if percentual is not None:
            percentual = max(0, min(100, percentual))
            maior_percentual = max(maior_percentual, percentual)
            if percentual != ultimo_percentual:
                ultimo_percentual = percentual
                if emitir_progresso:
                    _set_progress(percentual, f"GED: download {percentual}%")

            if percentual >= 100:
                log.info(
                    "GED: barra atingiu 100%% | maior_percentual=%d | barra_vista=%s",
                    maior_percentual, barra_ja_apareceu,
                )
                _set_status("Barra de progresso GED concluída em 100%")
                return

        time.sleep(intervalo)

    raise TimeoutError("Tempo limite excedido aguardando a barra de progresso do GED atingir 100%")


def _aguardar_link_download(worker_drv, timeout: int = 120):
    """Espera o link de download do GED na guia de protocolo. Retorna (elemento, href, xpath)."""
    if worker_drv is None:
        raise RuntimeError("Driver do GED não está inicializado")

    _trocar_para_guia_protocolo(worker_drv)
    _set_status("Aguardando link de download do GED")
    inicio = time.time()

    while (time.time() - inicio) < timeout:
        if parar_event.is_set():
            raise TimeoutError("Aguardo do link de download interrompido")

        link, href, xpath = _encontrar_link_download(
            worker_drv, timeout=2, ensure_visible=True,
        )
        if link:
            texto = (link.text or "").strip()
            log.info(
                "GED: link de download disponível | xpath=%s | href=%s | texto=%s | url=%s",
                xpath, href, texto, worker_drv.current_url,
            )
            _set_status(f"GED: link de download disponível ({texto or 'Baixar arquivo'})")
            time.sleep(GED_LINK_ESTABILIZACAO_SEGUNDOS)
            return link, href, xpath

        time.sleep(0.3)

    raise TimeoutError("Relatório processado, mas link de download não apareceu")


def _trocar_para_guia_protocolo(worker_drv, timeout: int = 30):
    """Troca para a guia do GED /protocolo onde a barra de progresso é exibida."""
    if worker_drv is None:
        return

    _set_status("Aguardando abertura da guia de protocolo do GED")
    inicio = time.time()
    while (time.time() - inicio) < timeout:
        if parar_event.is_set():
            return

        for handle in worker_drv.window_handles:
            try:
                worker_drv.switch_to.window(handle)
                current = (worker_drv.current_url or "").strip()
                if current.startswith(GED_PROTOCOLO_BASE_URL):
                    _set_status(f"Guia de protocolo detectada: {current}")
                    return
            except Exception:
                continue

        time.sleep(0.5)

    raise TimeoutError("Não foi possível localizar a guia de protocolo do GED")


_GED_SUFFIXES_TMP = (".crdownload", ".tmp", ".part", ".download", ".unconfirmed")
_GED_SUFFIXES_OK = (".csv", ".xlsx", ".xls")
_GED_PREFIXOS_ARQUIVO = ("pos_venda", "ged-detalhado")
_REGEX_TOKEN_GED = re.compile(r"(exp_[a-f0-9]+)", re.I)


def _eh_download_temporario(nome: str) -> bool:
    lower = nome.lower()
    return lower.endswith(_GED_SUFFIXES_TMP) or lower.endswith(".tmp")


def _eh_arquivo_download_ged(caminho: Path) -> bool:
    if not caminho.is_file() or _eh_download_temporario(caminho.name):
        return False
    lower = caminho.name.lower()
    if not lower.endswith(_GED_SUFFIXES_OK):
        return False
    return any(lower.startswith(pref) for pref in _GED_PREFIXOS_ARQUIVO)


def _extrair_token_ged(texto: str) -> str:
    """Extrai token do protocolo GED (ex.: exp_6a57d49cf35c4) de URL ou nome de arquivo."""
    match = _REGEX_TOKEN_GED.search(texto or "")
    return match.group(1).lower() if match else ""


def _chave_arquivo_download(caminho: Path) -> str:
    try:
        return str(caminho.resolve())
    except OSError:
        return str(caminho)


def _assinatura_arquivo_download(caminho: Path) -> tuple[int, int, int] | None:
    """Identifica uma versão do arquivo sem depender do relógio do processo."""
    try:
        stat = caminho.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)


def _snapshot_arquivos_pastas(
    pastas: list[Path],
) -> dict[str, tuple[int, int, int]]:
    """Snapshot estável do path e da versão dos arquivos já presentes."""
    arquivos: dict[str, tuple[int, int, int]] = {}
    for pasta in pastas:
        for arq in _iter_arquivos_pasta(pasta):
            assinatura = _assinatura_arquivo_download(arq)
            if assinatura is not None:
                arquivos[_chave_arquivo_download(arq)] = assinatura
    return arquivos


def _pastas_busca_download(download_dir: Path) -> list[Path]:
    """Pastas onde o Chrome pode gravar o CSV (somente worker atual e Downloads do usuário)."""
    vistos: set[str] = set()
    pastas: list[Path] = []

    def _add(p: Path) -> None:
        try:
            chave = str(p.resolve())
        except OSError:
            return
        if chave in vistos:
            return
        vistos.add(chave)
        pastas.append(p)

    _add(download_dir)
    _add(DEFAULT_DOWNLOAD)
    return pastas


def _pastas_temp_download(download_dir: Path) -> list[Path]:
    """Pastas onde verificamos .crdownload (worker + Downloads — Chrome às vezes ignora a pasta)."""
    return _pastas_busca_download(download_dir)


def _eh_arquivo_exportavel_ged(caminho: Path) -> bool:
    if not caminho.is_file() or _eh_download_temporario(caminho.name):
        return False
    return caminho.name.lower().endswith(_GED_SUFFIXES_OK)


def _arquivo_novo_apos_clique(
    caminho: Path,
    before: set[str] | dict[str, tuple[int, int, int]],
    inicio_clique: float,
) -> bool:
    """True se o arquivo não estava no snapshot ou foi regravado após o clique."""
    chave = _chave_arquivo_download(caminho)
    assinatura_atual = _assinatura_arquivo_download(caminho)
    if assinatura_atual is None:
        return False
    if chave not in before:
        return True
    if isinstance(before, dict):
        return before[chave] != assinatura_atual
    # Snapshot legado guarda apenas paths; sem assinatura não há como provar
    # deterministicamente que um path existente foi regravado.
    return False


def _log_arquivos_prefixo_rejeitado(
    pasta: Path,
    before: set[str] | dict[str, tuple[int, int, int]],
    inicio_clique: float,
    ja_logados: set[str],
) -> None:
    """Loga apenas arquivos novos após o clique (evita spam de tudo que já estava em Downloads)."""
    for arq in _iter_arquivos_pasta(pasta):
        if _eh_arquivo_download_ged(arq) or not _eh_arquivo_exportavel_ged(arq):
            continue
        if not _arquivo_novo_apos_clique(arq, before, inicio_clique):
            continue
        if arq.name not in ja_logados:
            ja_logados.add(arq.name)
            log.warning("GED: arquivo ignorado (prefixo não reconhecido): %s", arq.name)


def _resumir_arquivos_pasta(pastas: list[Path]) -> list[str]:
    resumo: list[str] = []
    for pasta in pastas:
        for arq in _iter_arquivos_pasta(pasta):
            lower = arq.name.lower()
            if lower.endswith(_GED_SUFFIXES_OK) or _eh_download_temporario(arq.name):
                resumo.append(f"{pasta.name}/{arq.name}")
    return resumo


def _baixar_ged_via_http(
    worker_drv,
    href: str,
    download_dir: Path,
    data_inicio: str,
    data_fim: str,
) -> Path:
    """Baixa o export do GED via HTTP usando cookies da sessão Selenium."""
    if not href or href.lower().startswith("javascript"):
        raise ValueError(f"href inválido para download HTTP: {href!r}")

    session = requests.Session()
    for cookie in worker_drv.get_cookies():
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )

    resp = session.get(href, timeout=120, verify=False, allow_redirects=True)
    resp.raise_for_status()

    nome: str | None = None
    cd = resp.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        match = re.search(r'filename[*]?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd, re.I)
        if match:
            nome = match.group(1).strip()

    if not nome:
        path_part = urlparse(href).path
        nome_candidato = Path(path_part).name if path_part else ""
        if nome_candidato.lower().endswith(_GED_SUFFIXES_OK):
            nome = nome_candidato

    if not nome:
        dt_inicio = datetime.strptime(str(data_inicio).strip(), "%d/%m/%Y")
        dt_fim = datetime.strptime(str(data_fim).strip(), "%d/%m/%Y")
        nome = f"pos_venda_{dt_inicio.strftime('%d%m%Y')}_{dt_fim.strftime('%d%m%Y')}.csv"

    download_dir.mkdir(parents=True, exist_ok=True)
    destino = download_dir / nome
    destino.write_bytes(resp.content)
    log.info(
        "GED: arquivo baixado via HTTP fallback: %s (%d bytes)",
        destino, len(resp.content),
    )
    _set_status(f"GED: download via HTTP fallback ({destino.name})")
    return destino


def _iter_arquivos_pasta(pasta: Path, apenas_diretos: bool = True) -> list[Path]:
    if not pasta.exists():
        return []
    encontrados: list[Path] = []
    try:
        for item in pasta.iterdir():
            if item.is_file():
                encontrados.append(item)
            elif not apenas_diretos and item.is_dir():
                for sub in item.iterdir():
                    if sub.is_file():
                        encontrados.append(sub)
    except OSError as exc:
        log.warning("GED: falha ao listar '%s': %s", pasta, exc)
    return encontrados


def _candidatos_download(
    pasta: Path,
    before: set[str] | dict[str, tuple[int, int, int]],
    inicio_clique: float,
    token_esperado: str = "",
) -> list[Path]:
    """Lista CSVs GED novos após o clique; se houver token do protocolo, exige match no nome."""
    token = (token_esperado or "").lower().strip()
    candidatos: list[Path] = []
    for arq in _iter_arquivos_pasta(pasta):
        if not _eh_arquivo_download_ged(arq):
            continue
        try:
            if arq.stat().st_size <= 0:
                continue
            if not _arquivo_novo_apos_clique(arq, before, inicio_clique):
                continue
            if token and token not in arq.name.lower():
                continue
            candidatos.append(arq)
        except OSError:
            continue
    return candidatos


def _montar_nome_claim_download(
    origem: Path,
    worker_id: int,
    data_inicio: str,
    data_fim: str,
) -> str:
    dt_inicio = datetime.strptime(str(data_inicio).strip(), "%d/%m/%Y")
    dt_fim = datetime.strptime(str(data_fim).strip(), "%d/%m/%Y")
    sufixo = f"w{worker_id}_{dt_inicio.strftime('%d%m%Y')}_{dt_fim.strftime('%d%m%Y')}"
    return f"{origem.stem}_{sufixo}{origem.suffix.lower()}"


def _claim_arquivo_download(
    origem: Path,
    download_dir: Path,
    worker_id: int,
    data_inicio: str,
    data_fim: str,
) -> Path:
    """Reserva o download com nome exclusivo do worker (copy em Downloads, rename no worker)."""
    download_dir.mkdir(parents=True, exist_ok=True)
    destino = download_dir / _montar_nome_claim_download(origem, worker_id, data_inicio, data_fim)

    try:
        origem_res = origem.resolve()
        worker_res = download_dir.resolve()
        downloads_res = DEFAULT_DOWNLOAD.resolve()
    except OSError:
        origem_res = origem
        worker_res = download_dir
        downloads_res = DEFAULT_DOWNLOAD

    if destino.exists():
        destino.unlink(missing_ok=True)

    if origem_res == destino.resolve():
        return destino

    if origem_res.parent == worker_res:
        origem.rename(destino)
        log.info("GED: download renomeado para %s", destino.name)
        _set_status(f"GED: download reservado como {destino.name}")
        return destino

    if origem_res.parent == downloads_res:
        shutil.copy2(str(origem), str(destino))
        origem.unlink(missing_ok=True)
        log.info("GED: download copiado de Downloads para %s", destino.name)
        _set_status(f"GED: download copiado de Downloads para {destino.name}")
        return destino

    shutil.copy2(str(origem), str(destino))
    log.info("GED: download copiado de %s para %s", origem.parent.name, destino.name)
    _set_status(f"GED: download copiado para {destino.name}")
    return destino


def _detectar_seps_csv_ged(primeira_linha: str) -> list[str]:
    if primeira_linha.count(";") >= primeira_linha.count(","):
        return [";", ","]
    return [",", ";"]


def _conteudo_parece_html(conteudo: str) -> bool:
    inicio = (conteudo or "").lstrip()[:500].lower()
    return inicio.startswith("<!doctype") or inicio.startswith("<html") or "<body" in inicio[:1000].lower()


def _ler_csv_ged_conteudo(buffer: str) -> pd.DataFrame:
    linhas = [ln for ln in buffer.splitlines() if ln.strip()]
    if not linhas:
        raise ValueError("CSV GED vazio após normalização")

    if _conteudo_parece_html(buffer):
        raise ValueError("Conteúdo HTML recebido em vez de CSV GED")

    ultimo_erro: Exception | None = None
    for sep in _detectar_seps_csv_ged(linhas[0]):
        try:
            df = pd.read_csv(
                StringIO(buffer),
                sep=sep,
                dtype=str,
                engine="python",
                on_bad_lines="skip",
            )
            df.columns = [_normalizar_nome_coluna_ged(c) for c in df.columns]
            df = _aplicar_aliases_colunas_ged(df)
            if "Protocolo" not in df.columns:
                raise ValueError(
                    f"Coluna Protocolo ausente com sep={sep!r}; colunas={list(df.columns)[:8]}"
                )
            _validar_alinhamento_protocolo(df)
            return df
        except Exception as exc:
            ultimo_erro = exc
            continue

    raise ValueError(f"Não foi possível parsear CSV GED com coluna Protocolo ({ultimo_erro})")


def _validar_arquivo_antes_tratamento(caminho: Path) -> None:
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo não encontrado para tratamento: {caminho}")

    try:
        tamanho = caminho.stat().st_size
    except OSError as exc:
        raise RuntimeError(f"Não foi possível ler arquivo GED: {caminho}") from exc

    if tamanho <= 0:
        raise RuntimeError(f"Arquivo GED vazio: {caminho.name}")

    if caminho.suffix.lower() == ".csv":
        ultimo_erro: Exception | None = None
        for enc in GED_CSV_ENCODINGS:
            try:
                raw = caminho.read_bytes().decode(enc)
                buffer = _normalizar_linhas_csv_ged(raw)
                _ler_csv_ged_conteudo(buffer)
                return
            except Exception as exc:
                ultimo_erro = exc
                continue
        raise RuntimeError(
            f"Arquivo GED inválido antes do tratamento ({caminho.name}): {ultimo_erro}"
        )


def _mover_para_pasta_worker(origem: Path, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    destino = download_dir / origem.name
    if destino.resolve() == origem.resolve():
        return origem
    if destino.exists():
        destino.unlink(missing_ok=True)

    try:
        from_downloads = origem.resolve().parent == DEFAULT_DOWNLOAD.resolve()
    except OSError:
        from_downloads = False

    if from_downloads:
        shutil.copy2(str(origem), str(destino))
        origem.unlink(missing_ok=True)
        log.info("GED: arquivo copiado de Downloads para %s", destino)
        _set_status("GED: download copiado de Downloads para pasta do worker")
    else:
        shutil.move(str(origem), str(destino))
        log.info("GED: arquivo movido de %s para %s", origem.parent, destino)
        _set_status(f"GED: download movido de {origem.parent.name} para pasta do worker")
    return destino


def _aguardar_download_finalizado(
    download_dir: Path,
    before: set[str] | dict[str, tuple[int, int, int]],
    timeout: int = 1800,
    inicio_clique: float | None = None,
    worker_drv=None,
    href_fallback: str | None = None,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    token_esperado: str = "",
) -> Path:
    """Aguarda CSV/XLS do GED; busca também em Downloads se o Chrome ignorar a pasta do worker."""
    download_dir = download_dir.resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    inicio_clique = inicio_clique or time.time()
    pastas = _pastas_busca_download(download_dir)
    pastas_temp = _pastas_temp_download(download_dir)
    nomes_pastas = ", ".join(p.name for p in pastas)
    token = (token_esperado or "").strip()
    status_token = f" | token={token}" if token else ""
    _set_status(f"Aguardando download GED em: {nomes_pastas}{status_token}")

    inicio = time.time()
    ultimo_nome: str | None = None
    tamanho_anterior = -1
    estavel_por = 0
    ultimo_log_vazio = 0.0
    fallback_tentado = False
    prefixos_rejeitados_logados: set[str] = set()

    while (time.time() - inicio) < timeout:
        if parar_event.is_set():
            raise TimeoutError("Aguardo de download interrompido")

        download_em_andamento = False
        candidatos_globais: list[Path] = []

        for pasta in pastas_temp:
            for arq in _iter_arquivos_pasta(pasta):
                if not _eh_download_temporario(arq.name):
                    continue
                nome_l = arq.name.lower()
                token_no_temp = _extrair_token_ged(nome_l)
                if token:
                    if token_no_temp and token_no_temp != token:
                        continue
                    try:
                        na_pasta_worker = pasta.resolve() == download_dir.resolve()
                    except OSError:
                        na_pasta_worker = False
                    # Temp genérico em Downloads pode ser de outro Chrome/worker.
                    if not token_no_temp and not na_pasta_worker:
                        continue
                download_em_andamento = True

        for pasta in pastas:
            _log_arquivos_prefixo_rejeitado(
                pasta, before, inicio_clique, prefixos_rejeitados_logados,
            )
            candidatos_globais.extend(
                _candidatos_download(pasta, before, inicio_clique, token_esperado=token),
            )

        if download_em_andamento and not candidatos_globais:
            time.sleep(0.7)
            continue

        if not candidatos_globais:
            if (
                not fallback_tentado
                and href_fallback
                and worker_drv is not None
                and data_inicio
                and data_fim
                and (time.time() - inicio) >= GED_FALLBACK_HTTP_SEGUNDOS
            ):
                fallback_tentado = True
                try:
                    return _baixar_ged_via_http(
                        worker_drv, href_fallback, download_dir, data_inicio, data_fim,
                    )
                except Exception as exc:
                    log.warning("GED: fallback HTTP falhou: %s", exc)

            if (time.time() - ultimo_log_vazio) >= 30:
                ultimo_log_vazio = time.time()
                resumo = []
                for pasta in pastas:
                    nomes = [
                        a.name for a in _iter_arquivos_pasta(pasta)
                        if _eh_arquivo_download_ged(a)
                        and (not token or token in a.name.lower())
                    ]
                    if nomes:
                        resumo.append(f"{pasta.name}: {', '.join(nomes[:5])}")
                if resumo:
                    log.info(
                        "GED: aguardando novo arquivo%s; vistos: %s",
                        status_token, " | ".join(resumo),
                    )
                else:
                    log.info("GED: aguardando novo arquivo em %s%s", nomes_pastas, status_token)
            time.sleep(0.7)
            continue

        candidato = max(candidatos_globais, key=lambda p: p.stat().st_mtime)
        try:
            tamanho_atual = candidato.stat().st_size
        except OSError:
            time.sleep(0.7)
            continue

        if candidato.name == ultimo_nome and tamanho_atual == tamanho_anterior:
            estavel_por += 1
        else:
            ultimo_nome = candidato.name
            tamanho_anterior = tamanho_atual
            estavel_por = 0

        if estavel_por >= 2:
            try:
                if candidato.resolve().parent != download_dir.resolve():
                    candidato = _mover_para_pasta_worker(candidato, download_dir)
            except OSError as exc:
                log.warning("GED: falha ao mover download para pasta do worker: %s", exc)
            return candidato

        time.sleep(0.7)

    arquivos_vistos = _resumir_arquivos_pasta(pastas)
    url_atual = ""
    if worker_drv is not None:
        try:
            url_atual = (worker_drv.current_url or "").strip()
        except Exception:
            pass
        take_error_screenshot(worker_drv, "download_timeout", "bot_ged")

    pastas_txt = "\n  ".join(str(p) for p in pastas)
    arquivos_txt = ", ".join(arquivos_vistos[:20]) if arquivos_vistos else "(nenhum)"
    raise TimeoutError(
        f"Timeout aguardando download GED (pastas verificadas:\n  {pastas_txt}"
        f"{f' | url={url_atual}' if url_atual else ''}"
        f" | arquivos: {arquivos_txt})"
    )


def _normalizar_nome_coluna_ged(coluna) -> str:
    texto = str(coluna or "").replace("\ufeff", "").strip().strip('"').strip("'")
    return re.sub(r"\s+", " ", texto)


def _aplicar_aliases_colunas_ged(df: pd.DataFrame) -> pd.DataFrame:
    rename = {col: GED_COLUNAS_ALIASES[col] for col in df.columns if col in GED_COLUNAS_ALIASES}
    if rename:
        df = df.rename(columns=rename)
    return df


def _validar_alinhamento_protocolo(df: pd.DataFrame, minimo_validos: float = 0.8) -> None:
    if "Protocolo" not in df.columns:
        raise ValueError(
            f"Coluna Protocolo ausente após parse GED; colunas={list(df.columns)[:12]}"
        )
    if df.empty:
        return

    amostra = df["Protocolo"].astype(str).str.strip().head(100)
    amostra = amostra[amostra != ""]
    if amostra.empty:
        return

    pct = amostra.str.fullmatch(r"\d+").mean()
    if pct < minimo_validos:
        raise ValueError(
            "Colunas desalinhadas no CSV GED "
            f"(Protocolo inválido na amostra: {amostra.iloc[0]!r}; "
            f"{pct:.0%} numéricos). Verifique trailing ';' ou encoding."
        )


def _normalizar_linhas_csv_ged(raw: str) -> str:
    linhas = [ln.rstrip("\r").rstrip(";") for ln in raw.splitlines() if ln.strip()]
    return "\n".join(linhas)


def _carregar_dataframe_ged(caminho: Path) -> pd.DataFrame:
    """Carrega export GED com encoding, delimitador e normalização de linhas robustos."""
    ext = caminho.suffix.lower()
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(caminho, dtype=str)
        df.columns = [_normalizar_nome_coluna_ged(c) for c in df.columns]
        return _aplicar_aliases_colunas_ged(df)

    if ext != ".csv":
        raise ValueError(f"Formato de arquivo não suportado para tratamento GED: {ext}")

    ultimo_erro: Exception | None = None
    for enc in GED_CSV_ENCODINGS:
        try:
            raw = caminho.read_bytes().decode(enc)
            buffer = _normalizar_linhas_csv_ged(raw)
            df = _ler_csv_ged_conteudo(buffer)
            log.info(
                "GED: CSV carregado | arquivo=%s | encoding=%s | colunas=%d | protocolo_sample=%s",
                caminho.name,
                enc,
                len(df.columns),
                df["Protocolo"].iloc[0] if not df.empty else "n/a",
            )
            return df
        except Exception as exc:
            ultimo_erro = exc
            continue

    raise ValueError(f"Não foi possível ler CSV GED: {caminho} ({ultimo_erro})")


def _tratar_arquivo_ged(
    caminho_arquivo: Path,
    data_inicio: str,
    data_fim: str,
    yyyymm: str,
    numero_quinzena: int,
) -> Path:
    """Mantém somente colunas de interesse e remove linhas inválidas em Data do Batimento."""
    if not caminho_arquivo.exists():
        raise FileNotFoundError(f"Arquivo não encontrado para tratamento: {caminho_arquivo}")

    df = _carregar_dataframe_ged(caminho_arquivo)
    faltantes = [col for col in GED_COLUNAS_SAIDA if col not in df.columns]
    if faltantes:
        raise ValueError(
            f"Colunas obrigatórias ausentes no arquivo GED: {faltantes} | "
            f"colunas_encontradas={list(df.columns)[:15]}"
        )

    df = df[GED_COLUNAS_SAIDA].copy()
    _validar_alinhamento_protocolo(df)

    serie = df["Data do Batimento"].astype(str).str.strip()
    invalidos = {"", "-", "null", "none", "nan"}
    mascara_valida = ~serie.str.lower().isin(invalidos)
    df = df.loc[mascara_valida].copy()

    PASTA_GED_TRATADO.mkdir(parents=True, exist_ok=True)
    if not yyyymm:
        dt_inicio = datetime.strptime(str(data_inicio).strip(), "%d/%m/%Y").date()
        yyyymm = dt_inicio.replace(day=1).strftime("%Y%m")
    nome_saida = _montar_nome_saida_ged_quinzena(yyyymm, numero_quinzena)
    caminho_saida = PASTA_GED_TRATADO / nome_saida

    _salvar_saida_parquet(df, caminho_saida)

    return caminho_saida


def _montar_nome_saida_ged_tratado(data_inicio: str, data_fim: str) -> str:
    """Monta nome mensal do arquivo de saída: ged-detalhado-tratado_YYYYMM.parquet."""
    dt_inicio = datetime.strptime(str(data_inicio).strip(), "%d/%m/%Y").date()
    return _montar_nome_saida_ged_mensal(dt_inicio.replace(day=1))


def stop():
    parar_event.set()
