"""Task Irregularidade GED — download e tratamento do Relatório de Irregularidades."""
from __future__ import annotations

import logging
import re
import time
from io import StringIO
from pathlib import Path

import pandas as pd

from app.config import (
    DEFAULT_DOWNLOAD,
    PASTA_GED_IRREGULARIDADE_COPIA_GERENCIAL,
    PASTA_GED_IRREGULARIDADE_TRATADO,
    PREFIXO_GED_IRREGULARIDADE_TRATADO,
)
from app.config.selectors import ged
from app.bots.ged import irregularidade_download as _ged_dl
from app.core.common import safe_close_driver
from app.infrastructure.file_mirror import espelhar_arquivo
from app.bots.ged.irregularidade_download import (
    GedIrregularidadeCallbacks,
    QuinzenaIrregularidade,
    aguardar_download_csv,
    aguardar_progresso_100,
    criar_driver_ged,
    descricao_quinzena,
    download_quinzena_irregularidade,
    eh_arquivo_irregularidade,
    fechar_guias_protocolo_ged,
    limpar_temporarios_orfaos,
    login_ged,
    obter_data_ref_irregularidade,
    quinzenas_para_atualizar,
    trocar_para_guia_protocolo,
)
from app.bots.rotina.constants import (
    CSV_ENCODINGS,
    DOWNLOADS_TEMP_GED_IRREGULARIDADE,
    IRREGULARIDADE_COLUNAS_MAP,
    IRREGULARIDADE_COLUNAS_SAIDA,
    TASK_IRREGULARIDADE,
)
from app.bots.rotina.io import _limpar_downloads_temp_inicio, _obter_data_base_execucao
from app.bots.rotina.state import (
    _registrar_erro,
    _registrar_status_tarefa,
    _set_progress,
    _set_status,
)

log = logging.getLogger("robots.bot_rotina")

_CSV_ENCODINGS = tuple(CSV_ENCODINGS)

# Re-export para testes e compatibilidade
_criar_driver_ged = criar_driver_ged
_quinzenas_para_atualizar = quinzenas_para_atualizar
_descricao_quinzena = descricao_quinzena
_obter_data_ref_irregularidade = lambda: obter_data_ref_irregularidade(
    data_base=_obter_data_base_execucao().date()
)
_trocar_para_guia_protocolo = lambda drv, timeout=30: trocar_para_guia_protocolo(
    drv, _rotina_callbacks(), timeout=timeout
)
_fechar_guias_protocolo_ged = fechar_guias_protocolo_ged
_aguardar_progresso_100 = lambda drv, timeout=3600: aguardar_progresso_100(
    drv, _rotina_callbacks(), timeout=timeout
)
_aguardar_download_csv = lambda *args, **kwargs: aguardar_download_csv(
    *args, _rotina_callbacks(), **kwargs
)
_eh_arquivo_irregularidade = eh_arquivo_irregularidade
_limpar_temporarios_orfaos = limpar_temporarios_orfaos


def _encontrar_link_download(drv, timeout=2):
    return _ged_dl._encontrar_link_download(drv, timeout=timeout)


def _voltar_para_guia_principal_ged(drv, timeout=15):
    _fechar_guias_protocolo_ged(drv)
    cb = _rotina_callbacks()
    inicio = time.time()
    while (time.time() - inicio) < timeout:
        handles = list(drv.window_handles)
        if not handles:
            raise RuntimeError("Nenhuma guia do GED disponível")

        for handle in handles:
            try:
                drv.switch_to.window(handle)
                current = (drv.current_url or "").strip()
                if current.startswith(ged.PROTOCOLO_BASE_URL):
                    continue
                log.info("Irregularidade GED: voltou para aba 1 | url=%s", current)
                cb.set_status("Irregularidade GED: voltou para aba principal")
                time.sleep(0.5)
                return
            except Exception:
                continue
        time.sleep(0.5)

    handles = list(drv.window_handles)
    if not handles:
        raise RuntimeError("Nenhuma guia do GED disponível")
    drv.switch_to.window(handles[0])
    log.info("Irregularidade GED: foco forçado na aba 1 | url=%s", drv.current_url or "")
    cb.set_status("Irregularidade GED: voltou para aba principal")
    time.sleep(0.5)


def _rotina_callbacks() -> GedIrregularidadeCallbacks:
    return GedIrregularidadeCallbacks(set_status=_set_status, set_progress=_set_progress)


def _normalizar_nome_coluna(coluna) -> str:
    texto = str(coluna or "").replace("\ufeff", "").strip().strip('"').strip("'")
    return re.sub(r"\s+", " ", texto)


def tratar_irregularidade(df: pd.DataFrame) -> pd.DataFrame:
    """Renomeia colunas e limpa prefixos CO/IC em Descrição das Irregularidades."""
    if df is None or df.empty:
        return pd.DataFrame(columns=IRREGULARIDADE_COLUNAS_SAIDA)

    tratado = df.copy()
    tratado.columns = [_normalizar_nome_coluna(c) for c in tratado.columns]

    rename = {
        origem: destino
        for origem, destino in IRREGULARIDADE_COLUNAS_MAP.items()
        if origem in tratado.columns
    }
    tratado = tratado.rename(columns=rename)

    faltantes = [c for c in IRREGULARIDADE_COLUNAS_SAIDA if c not in tratado.columns]
    if faltantes:
        raise ValueError(f"Colunas obrigatórias ausentes no relatório de irregularidades: {faltantes}")

    tratado = tratado[IRREGULARIDADE_COLUNAS_SAIDA].copy()
    col_desc = "Descrição das Irregularidades"
    if col_desc in tratado.columns:
        serie = tratado[col_desc].fillna("").astype(str)
        tratado[col_desc] = (
            serie.str.replace(r"^CO - ", "", regex=True).str.replace(r"^IC - ", "", regex=True)
        )

    return tratado.reset_index(drop=True)


def _detectar_separador(primeira_linha: str) -> str:
    return ";" if primeira_linha.count(";") >= primeira_linha.count(",") else ","


def _ler_csv_irregularidade(caminho: Path) -> pd.DataFrame:
    ultimo_erro: Exception | None = None
    for enc in _CSV_ENCODINGS:
        try:
            raw = caminho.read_bytes().decode(enc)
            linhas = [ln.rstrip("\r").rstrip(";") for ln in raw.splitlines() if ln.strip()]
            if not linhas:
                raise ValueError("CSV vazio")
            buffer = "\n".join(linhas)
            sep = _detectar_separador(linhas[0])
            df = pd.read_csv(StringIO(buffer), sep=sep, dtype=str, engine="python", on_bad_lines="skip")
            return tratar_irregularidade(df)
        except Exception as exc:
            ultimo_erro = exc
            continue
    raise ValueError(f"Não foi possível ler CSV de irregularidades ({caminho.name}): {ultimo_erro}")


def _montar_nome_saida_quinzena(yyyymm: str, numero: int) -> str:
    return f"{PREFIXO_GED_IRREGULARIDADE_TRATADO}{yyyymm}_{numero}.csv"


def _salvar_csv_tratado(df: pd.DataFrame, quinzena: QuinzenaIrregularidade) -> Path:
    PASTA_GED_IRREGULARIDADE_TRATADO.mkdir(parents=True, exist_ok=True)
    caminho = PASTA_GED_IRREGULARIDADE_TRATADO / _montar_nome_saida_quinzena(
        quinzena.yyyymm, quinzena.numero
    )
    df.to_csv(caminho, sep=";", index=False, encoding="cp1252")
    espelhar_arquivo(
        caminho,
        [PASTA_GED_IRREGULARIDADE_COPIA_GERENCIAL / caminho.name],
        log=log,
    )
    log.info(
        "Irregularidade GED tratada salva | quinzena=%s_%s | linhas=%s | destino=%s",
        quinzena.yyyymm,
        quinzena.numero,
        f"{len(df):,}",
        caminho,
    )
    _set_status(
        f"Irregularidade GED salva | Bots: {caminho.name} | Gerencial: {caminho.name}"
    )
    print(f"ROTINA_BRUTO_SAVED|ged_irregularidade|{caminho.resolve()}", flush=True)
    return caminho


def _processar_quinzena_irregularidade(
    ged_drv,
    pasta_temp: Path,
    quinzena: QuinzenaIrregularidade,
    indice: int,
    total: int,
) -> bool:
    cb = _rotina_callbacks()
    arquivo_baixado = download_quinzena_irregularidade(
        ged_drv, pasta_temp, quinzena, indice, total, cb
    )
    if not arquivo_baixado:
        return False

    _set_status(f"Irregularidade GED [{indice}/{total}]: tratando {arquivo_baixado.name}")
    df_tratado = _ler_csv_irregularidade(arquivo_baixado)
    if df_tratado.empty:
        log.warning("Relatório de irregularidades vazio após tratamento (%s)", descricao_quinzena(quinzena))
        return False

    _salvar_csv_tratado(df_tratado, quinzena)
    return True


def _baixar_irregularidade_ged(_drv) -> bool:
    """Baixa relatório GED em driver dedicado (certificado ignorado, como bot_ged)."""
    data_ref = _obter_data_ref_irregularidade()
    quinzenas = quinzenas_para_atualizar(data_ref)
    pasta_temp = DOWNLOADS_TEMP_GED_IRREGULARIDADE
    pasta_temp.mkdir(parents=True, exist_ok=True)

    ged_drv = None
    total = len(quinzenas)
    sucessos = 0

    try:
        _limpar_downloads_temp_inicio("Extração Irregularidade GED")
        from app.bots.ged.irregularidade_download import limpar_pasta_worker

        limpar_pasta_worker(pasta_temp)

        ged_drv = criar_driver_ged(pasta_temp)
        login_ged(ged_drv, _rotina_callbacks())

        for indice, quinzena in enumerate(quinzenas, start=1):
            try:
                if _processar_quinzena_irregularidade(ged_drv, pasta_temp, quinzena, indice, total):
                    sucessos += 1
                else:
                    _registrar_erro(
                        f"Irregularidade GED [{indice}/{total}]: dados inválidos ({descricao_quinzena(quinzena)})"
                    )
            except Exception as exc:
                log.error(
                    "Erro na quinzena %s/%s (%s): %s",
                    indice,
                    total,
                    descricao_quinzena(quinzena),
                    exc,
                    exc_info=True,
                )
                _registrar_erro(
                    f"Irregularidade GED [{indice}/{total}]: {str(exc)[:100]}"
                )

        if sucessos == total:
            _set_status(f"Irregularidade GED: {total} quinzenas salvas com sucesso")
            _registrar_status_tarefa(TASK_IRREGULARIDADE, True)
            return True

        _set_status(f"Irregularidade GED: {sucessos}/{total} quinzenas salvas")
        _registrar_status_tarefa(TASK_IRREGULARIDADE, False, f"{sucessos}/{total} quinzenas")
        return False

    except Exception as exc:
        log.error("Erro na extração de irregularidades GED: %s", exc, exc_info=True)
        _registrar_erro(f"Irregularidade GED: {str(exc)[:120]}")
        _registrar_status_tarefa(TASK_IRREGULARIDADE, False, str(exc)[:30])
        return False

    finally:
        safe_close_driver(ged_drv, logger=log)
