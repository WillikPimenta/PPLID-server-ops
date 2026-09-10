"""Tarefas auditoria replicados D1 e etapas."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.config import *
from app.bots.rotina.constants import (
    COLUNAS_AUDITORIA_REPLICADOS_D1,
    DOWNLOADS_TEMP_ROTINA,
    TASK_AUDITORIA_ETAPAS,
    TASK_AUDITORIA_REPLICADOS_D1,
)
from app.bots.rotina.io import (
    _ler_arquivo_csv_robusto,
    _limpar_downloads_temp_inicio,
    _listar_arquivos_baixados_validos,
    _normalizar_nome_coluna,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
    _salvar_arquivo_consolidado,
)
from app.bots.rotina.selenium_brflow import _baixar_arquivos_rotina
from app.bots.rotina.state import (
    _registrar_erro,
    _registrar_status_tarefa,
    _set_progress,
    _set_status,
)

log = logging.getLogger("robots.bot_rotina")

def _resolver_relatorio_replicacao(data_replicados: str) -> Path | None:
    """Localiza replicacao_aud_d1_relatorio para D-1 da data do CSV replicados."""
    try:
        data_relatorio = (
            datetime.strptime(data_replicados, "%Y%m%d") - timedelta(days=1)
        ).strftime("%Y%m%d")
    except ValueError:
        log.warning("Data replicados D1 inválida para conferência: %s", data_replicados)
        return None

    candidatos: list[Path] = []
    padroes = (
        f"{REPLICACAO_AUD_D1_RELATORIO_PREFIXO}{data_relatorio}_*.xlsx",
        f"{REPLICACAO_AUD_D1_RELATORIO_PREFIXO}{data_relatorio}.xlsx",
    )
    for pasta in (PASTA_REPLICACAO_AUD_D1_RELATORIOS, PASTA_REPLICACAO_AUD_D1_RESUMO):
        for padrao in padroes:
            candidatos.extend(pasta.glob(padrao))

    if not candidatos:
        log.info(
            "Conferência replicados D1: relatório não encontrado para %s em relatorios/ ou resumo/",
            data_relatorio,
        )
        return None

    return max(set(candidatos), key=lambda p: p.stat().st_mtime)


def _caminho_conferencia_replicados(data_ref: str) -> Path:
    return PASTA_REPLICACAO_AUD_D1_CONFIRMED / f"{REPLICACAO_AUD_D1_CONFERENCIA_PREFIXO}{data_ref}.csv"


def _normalizar_protocolo_conferencia(valor) -> str:
    texto = str(valor or "").strip()
    if not texto or texto == REPLICACAO_CSV_PLACEHOLDER_LIMPEZA:
        return ""
    return texto


def _gerar_conferencia_replicados_d1(
    df_replicados: pd.DataFrame,
    data_ref: str,
    arquivo_replicados: str,
) -> Path | None:
    """Compara protocolos do relatório de replicação com Protocolo Origem do CSV."""
    relatorio_path = _resolver_relatorio_replicacao(data_ref)
    if relatorio_path is None:
        return None

    try:
        df_plano = pd.read_excel(relatorio_path, sheet_name="Plano", engine="openpyxl")
    except Exception as exc:
        log.warning(
            "Conferência replicados D1: falha ao ler aba Plano de %s: %s",
            relatorio_path.name,
            exc,
        )
        return None

    if df_plano is None or df_plano.empty:
        log.info(
            "Conferência replicados D1: aba Plano vazia em %s — nada a conferir",
            relatorio_path.name,
        )
        return None

    df_plano.columns = [_normalizar_nome_coluna(col) for col in df_plano.columns]
    if "Protocolo" not in df_plano.columns:
        log.warning(
            "Conferência replicados D1: coluna Protocolo ausente em %s",
            relatorio_path.name,
        )
        return None

    col_workflow = next(
        (c for c in ("WorkflowConfig", "Workflow", "Workflow BRFlow") if c in df_plano.columns),
        None,
    )

    protocolos_relatorio = []
    vistos = set()
    for _, row in df_plano.iterrows():
        protocolo = _normalizar_protocolo_conferencia(row.get("Protocolo"))
        if not protocolo or protocolo in vistos:
            continue
        vistos.add(protocolo)
        item = {"Protocolo": protocolo}
        if col_workflow:
            item["Workflow"] = str(row.get(col_workflow) or "").strip()
        protocolos_relatorio.append(item)

    if not protocolos_relatorio:
        log.info(
            "Conferência replicados D1: nenhum protocolo válido em %s",
            relatorio_path.name,
        )
        return None

    if "Protocolo Origem" not in df_replicados.columns:
        log.warning("Conferência replicados D1: coluna Protocolo Origem ausente no CSV")
        return None

    protocolos_replicados = {
        p
        for p in (
            _normalizar_protocolo_conferencia(v)
            for v in df_replicados["Protocolo Origem"].dropna()
        )
        if p
    }

    linhas = []
    faltantes = 0
    for item in protocolos_relatorio:
        protocolo = item["Protocolo"]
        replicado = protocolo in protocolos_replicados
        if not replicado:
            faltantes += 1
        linha = {
            "Protocolo": protocolo,
            "Status": "REPLICADO" if replicado else "FALTANTE",
            "RelatorioReferencia": relatorio_path.name,
            "ReplicadosReferencia": arquivo_replicados,
        }
        if col_workflow:
            linha["Workflow"] = item.get("Workflow", "")
        linhas.append(linha)

    df_conferencia = pd.DataFrame(linhas)
    if col_workflow:
        df_conferencia = df_conferencia[
            ["Protocolo", "Workflow", "Status", "RelatorioReferencia", "ReplicadosReferencia"]
        ]

    PASTA_REPLICACAO_AUD_D1_CONFIRMED.mkdir(parents=True, exist_ok=True)
    caminho_saida = _caminho_conferencia_replicados(data_ref)
    df_conferencia.to_csv(
        str(caminho_saida),
        index=False,
        encoding="utf-8-sig",
        sep=";",
        quotechar='"',
        quoting=1,
    )

    total = len(protocolos_relatorio)
    confirmados = total - faltantes
    log.info(
        "Conferência replicados D1: %d/%d protocolos confirmados, %d faltantes | %s",
        confirmados,
        total,
        faltantes,
        caminho_saida.name,
    )
    if faltantes > 0:
        _registrar_erro(f"⚠️ Replicados D1: {faltantes} protocolo(s) não encontrado(s)")
    return caminho_saida


def _ler_replicados_d1_csv(caminho: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(caminho, sep=";", encoding="utf-8-sig", dtype=str)
    except Exception:
        try:
            df = pd.read_csv(caminho, sep=";", encoding="cp1252", dtype=str)
        except Exception as exc:
            log.warning("Falha ao ler replicados D1 %s: %s", caminho.name, exc)
            return None
    if df is None or df.empty:
        return None
    df.columns = [_normalizar_nome_coluna(col) for col in df.columns]
    return df


def _reprocessar_conferencia_replicados_pendentes(
    forcar: bool = False,
    data_ref: str | None = None,
) -> list[dict]:
    """Gera replicacao_aud_d1_conferencia para replicados D-1 pendentes (ou forçado)."""
    arquivos = sorted(
        PASTA_AUDITORIA_REPLICADOS_D1.glob(f"{PREFIXO_AUDITORIA_REPLICADOS_D1}*.csv"),
        key=lambda p: p.name,
    )
    if data_ref:
        arquivos = [
            p for p in arquivos
            if p.stem == f"{PREFIXO_AUDITORIA_REPLICADOS_D1}{data_ref}"
        ]

    resultados: list[dict] = []
    for caminho in arquivos:
        ref = caminho.stem.replace(PREFIXO_AUDITORIA_REPLICADOS_D1, "", 1)
        saida = _caminho_conferencia_replicados(ref)
        item = {"data_ref": ref, "replicados": caminho.name, "status": "", "saida": None}

        if saida.exists() and not forcar:
            item["status"] = "ja_existe"
            item["saida"] = str(saida)
            resultados.append(item)
            continue

        if _resolver_relatorio_replicacao(ref) is None:
            item["status"] = "sem_relatorio"
            resultados.append(item)
            continue

        df = _ler_replicados_d1_csv(caminho)
        if df is None:
            item["status"] = "erro"
            item["detalhe"] = "csv_invalido"
            resultados.append(item)
            continue

        try:
            gerado = _gerar_conferencia_replicados_d1(df, ref, caminho.name)
        except Exception as exc:
            log.error("Conferência replicados D1 [%s]: %s", ref, exc, exc_info=True)
            item["status"] = "erro"
            item["detalhe"] = str(exc)[:120]
            resultados.append(item)
            continue

        if gerado is None:
            item["status"] = "sem_relatorio"
        else:
            item["status"] = "ok"
            item["saida"] = str(gerado)
        resultados.append(item)

    return resultados


def _baixar_auditoria_replicados_d1(drv, descricao):
    """
    Baixa 'BRBR6121 G AUDITORIA Replicados D1 - PARTE 1', filtra 10 colunas
    e salva CSV em PASTA_AUDITORIA_REPLICADOS_D1 com data d-1
    (brflow-replicadosd1-tratado_{YYYYMMDD}.csv).
    """
    try:
        pasta_temp = DOWNLOADS_TEMP_ROTINA
        _set_status(f"Iniciando download da auditoria replicados D1: {descricao}")
        _set_progress(62, "Auditoria replicados D1: iniciando download")

        data_d1 = _obter_data_base_execucao() - timedelta(days=1)
        data_ref = data_d1.strftime("%Y%m%d")

        pasta_destino = PASTA_AUDITORIA_REPLICADOS_D1
        try:
            pasta_destino.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.error(f"Não foi possível criar pasta de destino da auditoria replicados D1: {exc}")
            _registrar_erro(f"❌ Pasta de destino inacessível (auditoria replicados D1): {str(exc)[:120]}")
            _registrar_status_tarefa(TASK_AUDITORIA_REPLICADOS_D1, False, "pasta inacessível")
            return

        _limpar_downloads_temp_inicio("Extração BRBR6121 Replicados D1")
        _baixar_arquivos_rotina(drv, descricao, str(pasta_temp), correspondencia_exata=True)
        time.sleep(2)

        arquivos_na_pasta = _listar_arquivos_baixados_validos(pasta_temp)
        if not arquivos_na_pasta:
            log.warning(f"Nenhum arquivo baixado para auditoria replicados D1: {descricao}")
            _set_status("Auditoria replicados D1: nenhum arquivo encontrado")
            _registrar_erro(f"⚠️ Auditoria replicados D1 sem arquivo: {descricao}")
            _registrar_status_tarefa(TASK_AUDITORIA_REPLICADOS_D1, False, "sem arquivo")
            return

        arquivo_origem = sorted(arquivos_na_pasta, key=os.path.getctime)[0]
        df = _ler_arquivo_csv_robusto(arquivo_origem)
        if df is None or df.empty:
            log.warning(f"Auditoria replicados D1 inválida: {arquivo_origem}")
            _set_status("Auditoria replicados D1: arquivo inválido")
            _registrar_erro(f"⚠️ Auditoria replicados D1 inválida: {Path(arquivo_origem).name}")
            _registrar_status_tarefa(TASK_AUDITORIA_REPLICADOS_D1, False, "arquivo inválido")
            return

        df.columns = [_normalizar_nome_coluna(col) for col in df.columns]
        colunas_faltantes = [c for c in COLUNAS_AUDITORIA_REPLICADOS_D1 if c not in df.columns]
        if colunas_faltantes:
            msg = f"Colunas ausentes: {', '.join(colunas_faltantes[:5])}"
            log.warning(f"Auditoria replicados D1 — {msg}")
            _registrar_erro(f"⚠️ Auditoria replicados D1: {msg}")
            _registrar_status_tarefa(TASK_AUDITORIA_REPLICADOS_D1, False, "colunas ausentes")
            return

        df = df[COLUNAS_AUDITORIA_REPLICADOS_D1].copy()
        nome_arquivo_final = f"{PREFIXO_AUDITORIA_REPLICADOS_D1}{data_ref}"
        arquivo_final = pasta_destino / f"{nome_arquivo_final}.csv"
        df.to_csv(
            str(arquivo_final),
            index=False,
            encoding="utf-8-sig",
            sep=";",
            quotechar='"',
            quoting=1,
        )

        try:
            os.remove(arquivo_origem)
        except Exception as exc:
            log.warning(f"Erro ao remover arquivo temporário da auditoria replicados D1 {arquivo_origem}: {exc}")

        try:
            _gerar_conferencia_replicados_d1(df, data_ref, arquivo_final.name)
        except Exception as exc:
            log.error(
                "Conferência replicados D1 falhou após salvar CSV (não invalida download): %s",
                exc,
                exc_info=True,
            )
            _registrar_erro(f"⚠️ Conferência replicados D1: {str(exc)[:120]}")

        log.info(f"✅ Auditoria replicados D1 salva: {arquivo_final}")
        _set_status("✅ BRBR6121 Replicados D1 salvo")
        _set_progress(63, "Auditoria replicados D1: concluida")
        _registrar_status_tarefa(TASK_AUDITORIA_REPLICADOS_D1, True)
        print(f"REPLICACAO_D1_REPLICADOS_SAVED|{Path(arquivo_final).resolve()}", flush=True)

    except Exception as e:
        log.error(f"Erro ao baixar auditoria replicados D1: {e}", exc_info=True)
        _set_status(f"Erro na auditoria replicados D1: {e}")
        _registrar_erro(f"❌ Erro BRBR6121 Replicados D1: {str(e)[:120]}")
        _registrar_status_tarefa(TASK_AUDITORIA_REPLICADOS_D1, False, str(e)[:30])


def _emitir_sync_g_auditoria(arquivo_final: Path) -> bool:
    """Emite o contrato bot→banco somente para Parquet finalizado e não vazio."""
    if not arquivo_final.is_file() or arquivo_final.stat().st_size <= 0:
        return False
    print(
        f"ROTINA_BRUTO_SAVED|g_auditoria|{arquivo_final.resolve()}",
        flush=True,
    )
    return True


def _baixar_auditoria_etapas(drv, descricao):
    """
    Baixa o relatório 'G Auditoria com etapas' sem nenhum processamento/merge.
    O arquivo é convertido para parquet e salvo com data d-1
    (relativa à data de execução) em DEFAULT_SHAREPOINT_BOTS.
    Também grava CSV em PASTA_GAUDITORIA_ROTINAS como YYYYMMDD.csv.
    """
    try:
        pasta_temp = DOWNLOADS_TEMP_ROTINA
        _set_status(f"Iniciando download da auditoria: {descricao}")
        _set_progress(63, "Auditoria com etapas: iniciando download")

        # Calcular data d-1 a partir da data de execução atual
        data_d1 = _obter_data_base_execucao() - timedelta(days=1)
        nome_arquivo_final = f"{PREFIXO_AUDITORIA}{data_d1.strftime('%Y%m%d')}"

        # Garantir que a pasta destino existe
        pasta_destino = PASTA_AUDITORIA_TRATADO
        try:
            pasta_destino.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.error(f"Não foi possível criar pasta de destino da auditoria: {exc}")
            _registrar_erro(f"❌ Pasta de destino inacessível (auditoria): {str(exc)[:120]}")
            _registrar_status_tarefa(TASK_AUDITORIA_ETAPAS, False, "pasta inacessível")
            return

        # Limpar pasta temporária e baixar
        _limpar_downloads_temp_inicio("Extração G Auditoria com etapas")
        _baixar_arquivos_rotina(drv, descricao, str(pasta_temp), correspondencia_exata=True)
        time.sleep(2)

        # Coletar arquivos baixados
        arquivos_na_pasta = _listar_arquivos_baixados_validos(pasta_temp)

        if not arquivos_na_pasta:
            log.warning(f"Nenhum arquivo baixado para auditoria: {descricao}")
            _set_status("Auditoria: nenhum arquivo encontrado")
            _registrar_erro(f"⚠️ Auditoria sem arquivo: {descricao}")
            _registrar_status_tarefa(TASK_AUDITORIA_ETAPAS, False, "sem arquivo")
            return

        # Usar apenas o primeiro arquivo (normalmente único)
        arquivo_origem = sorted(arquivos_na_pasta, key=os.path.getctime)[0]
        arquivo_final = pasta_destino / f"{nome_arquivo_final}.parquet"

        df_auditoria = _ler_arquivo_csv_robusto(arquivo_origem)
        if df_auditoria is None or df_auditoria.empty:
            log.warning(f"Auditoria inválida para conversão em parquet: {arquivo_origem}")
            _set_status("Auditoria: arquivo inválido para conversão parquet")
            _registrar_erro(f"⚠️ Auditoria inválida: falha ao converter {Path(arquivo_origem).name}")
            _registrar_status_tarefa(TASK_AUDITORIA_ETAPAS, False, "arquivo inválido")
            return

        data_ref = data_d1.strftime("%Y%m%d")
        try:
            PASTA_GAUDITORIA_ROTINAS.mkdir(parents=True, exist_ok=True)
            _salvar_arquivo_consolidado(df_auditoria, data_ref, PASTA_GAUDITORIA_ROTINAS)
        except Exception as exc:
            log.warning(f"Falha ao salvar CSV da auditoria em PASTA_GAUDITORIA_ROTINAS: {exc}")

        df_auditoria = _preparar_dataframe_para_parquet(df_auditoria)
        df_auditoria.to_parquet(str(arquivo_final), index=False, compression='snappy')
        _emitir_sync_g_auditoria(arquivo_final)

        try:
            os.remove(arquivo_origem)
        except Exception as exc:
            log.warning(f"Erro ao remover arquivo temporário da auditoria {arquivo_origem}: {exc}")

        log.info(f"✅ Auditoria salva: {arquivo_final} | Destino: {os.path.abspath(arquivo_final)}")
        _set_status(f"✅ G Auditoria com etapas salva")
        _set_progress(64, "Auditoria com etapas: concluida")
        _registrar_status_tarefa(TASK_AUDITORIA_ETAPAS, True)

    except Exception as e:
        log.error(f"Erro ao baixar auditoria com etapas: {e}", exc_info=True)
        _set_status(f"Erro na auditoria: {e}")
        _registrar_erro(f"❌ Erro G Auditoria com etapas: {str(e)[:120]}")
        _registrar_status_tarefa(TASK_AUDITORIA_ETAPAS, False, str(e)[:30])
