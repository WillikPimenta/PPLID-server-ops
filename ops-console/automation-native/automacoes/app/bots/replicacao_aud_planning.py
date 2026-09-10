"""Planejamento da replicação de auditoria: amostra por hora a partir do parquet D-1."""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Hashable, List, Optional, TypeVar, Union
from zoneinfo import ZoneInfo

TChave = TypeVar("TChave", bound=Hashable)

import pandas as pd

from app.config import (
    ABA_DEFAULT_WORKFLOW_D1,
    COLUNA_CONFIG_FILA,
    COLUNA_CONFIG_NOME_REGRA_BRFLOW,
    COLUNA_CONFIG_STATUS,
    COLUNA_CONFIG_USAR_ARQUIVO_CSV,
    PASTA_REPLICACAO_AUD_CONFIG,
    REPLICACAO_AUD_MARCA_PENDENTE,
    REPLICACAO_AUD_SINCRONIZAR_WORKFLOW_D1_DEFAULT,
    COLUNA_CONFIG_AMOSTRA,
    COLUNA_CONFIG_AMOSTRA_DIARIA,
    COLUNA_CONFIG_AMOSTRA_TOTAL,
    COLUNA_CONFIG_AUTOMATICOS,
    COLUNA_CONFIG_CATEGORIA,
    COLUNA_CONFIG_CLIENTE,
    COLUNA_CONFIG_MANUAIS,
    COLUNA_CONFIG_META_CLIENTE,
    COLUNA_CONFIG_META_PRODU,
    COLUNA_CONFIG_SEGMENTO,
    COLUNA_CONFIG_TOTAL,
    COLUNA_CONFIG_WORKFLOW,
    COLUNA_CONFIG_WORKFLOW_SELENIUM,
    COLUNA_CONFIG_WORKFLOW_D1,
    COLUNA_ESCALA_AUDITORES,
    COLUNA_ESCALA_AUDITORES_CASE,
    COLUNA_ESCALA_AUDITORES_BIO,
    COLUNA_ESCALA_AUDITORES_REDOC,
    COLUNA_ESCALA_DATA,
    CONFIG_AUDITORIA_XLSX,
    CONFIG_CATEGORIA_XLSX,
    CONFIG_DEFAULT_XLSX,
    ESCALA_AUDITORES_ARQUIVO,
    ESCALA_AUDITORES_CSV,
    ESCALA_AUDITORES_D1_CSV,
    PASTA_REPLICACAO_AUD_CONFIG,
    PASTA_REPLICACAO_AUD_ESCALA,
    PASTA_REPLICACAO_AUD_ESCALA_LEGADO,
    PASTA_REPLICACAO_AUD_D1_CONFIG,
    PASTA_REPLICACAO_AUD_D1_ESCALA,
    PASTA_REPLICACAO_AUD_D1_ESCALA_LEGADO,
    PASTA_REPLICACAO_AUD_VOLUMETRIA,
    PASTA_DETALHADO_D1,
    PASTA_REPLICACAO_AUD_BASE,
    PASTA_REPLICACAO_AUD_PROTOCOLOS,
    PASTA_REPLICACAO_AUD_RELATORIOS,
    PASTA_REPLICACAO_AUD_RESUMO,
    PREFIXO_DETALHADO_FINAL,
    REPLICACAO_AUD_DASHBOARD_PREFIXO,
    REPLICACAO_AUD_DIAS_HISTORICO_DEFAULT,
    REPLICACAO_AUD_EXECUCAO_PREFIXO,
    REPLICACAO_AUD_EXCLUIR_HISTORICO_DEFAULT,
    REPLICACAO_AUD_PLANO_PREFIXO,
    REPLICACAO_AUD_RELATORIO_PREFIXO,
    REPLICACAO_AUD_RESUMO_PREFIXO,
    REPLICACAO_AUD_SEED_DEFAULT,
    REPLICACAO_AUD_SOBRESCREVER_DEFAULT,
    REPLICACAO_AUD_USAR_ESCALA_DEFAULT,
    REPLICACAO_CSV_PLACEHOLDER_LIMPEZA,
    REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
    REPLICACAO_FILA_G_AUDITORIA,
    REPLICACAO_FILA_BIO,
    REPLICACAO_FILA_REDOC,
    REPLICACAO_MODO_PROTOCOLOS,
    REPLICACAO_MODO_QTD,
    REPLICACAO_D1_SUBPASTA_BRFLOW,
    REPLICACAO_D1_SUBPASTA_CASE,
    REPLICACAO_META_CLIENTE_TOLERANCIA_SUM,
    REPLICACAO_WORKFLOW_COD_DOCUMENTOSCOPIA_31,
    REPLICACAO_WORKFLOW_COD_G_AUDITORIA,
    REPLICACAO_WORKFLOW_DESTINO_DOCUMENTOSCOPIA_31,
    REPLICACAO_WORKFLOW_DESTINO_G_AUDITORIA,
    REPLICACAO_WORKFLOW_DESTINO_BIO,
    REPLICACAO_WORKFLOW_DESTINO_REDOC,
)

from app.bots.replicacao_aud_excel_format import (
    exportar_workbook_formatado,
    montar_dataframe_dashboard_secoes,
)
from app.infrastructure.parquet_reader import ler_parquet, parquet_legivel

log = logging.getLogger("robots.replicacao_aud_planning")


def _parse_bool_setting(value: Any, default: bool = False) -> bool:
    """Converts a setting received from the UI, JSON, or environment."""
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on", "sim"}:
            return True
        if normalized in {"0", "false", "no", "off", "nao"}:
            return False
        return default
    return bool(value)


class EscalaAuditoresAusenteError(ValueError):
    """Data da replicação ausente no CSV de escala de auditores."""


COLUNA_PROTOCOLO = "Protocolo"
COLUNA_WORKFLOW_PARQUET = "Workflow"
COLUNA_DATA_ANALISE = "Data de Análise"
COLUNAS_PARQUET_OBRIGATORIAS = (COLUNA_PROTOCOLO, COLUNA_WORKFLOW_PARQUET, COLUNA_DATA_ANALISE)
_FORMATOS_DATA_ANALISE = (
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)
_PLANNING_TZ = ZoneInfo("America/Sao_Paulo")

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


@dataclass
class PlanoReplicacao:
    """Resultado do planejamento para um ou mais workflows."""

    data_referencia: datetime
    pasta_protocolos: Path
    pasta_resumo: Path
    run_id: str = ""
    pasta_execucao: Path = field(default_factory=Path)
    workflows: List[str] = field(default_factory=list)
    protocolos_por_workflow: Dict[str, List[str]] = field(default_factory=dict)
    csv_paths: Dict[str, Path] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    workflows_sem_registro: List[str] = field(default_factory=list)
    plano_detalhado_path: Optional[Path] = None
    resumo_csv_path: Optional[Path] = None
    dashboard_csv_path: Optional[Path] = None
    relatorio_excel_path: Optional[Path] = None
    estado_execucao_path: Optional[Path] = None
    parquet_referencia: str = ""
    data_referencia_d1_fmt: str = ""
    data_execucao_fmt: str = ""
    seed_amostra: int = REPLICACAO_AUD_SEED_DEFAULT
    resumo: List[dict] = field(default_factory=list)
    excluidos_historico_total: int = 0
    pool_redistribuido: int = 0
    total_workflows_config: int = 0
    auditores_ativos: int = 0
    meta_produ: float = 0.0
    capacidade_produtiva: float = 0.0
    soma_amostra_diaria: int = 0
    fator_capacidade: float = 0.0
    pasta_volumetria: str = ""
    auditores_ativos_case: int = 0
    pastas_fila: Dict[str, str] = field(default_factory=dict)
    workflows_pendentes_config: List[str] = field(default_factory=list)
    workflows_pendentes_novos: List[str] = field(default_factory=list)
    workflows_pendentes_existentes: List[str] = field(default_factory=list)
    workflows_pendentes_falha_sync: List[str] = field(default_factory=list)
    default_xlsx_path: str = ""
    workflow_brflow: Dict[str, str] = field(default_factory=dict)
    workflow_fila: Dict[str, str] = field(default_factory=dict)
    volume_redistribuicao_nao_alocado: int = 0
    selection_reason_por_protocolo: Dict[str, str] = field(default_factory=dict)
    protocolo_meta_por_chave: Dict[str, dict] = field(default_factory=dict)
    qtd_por_workflow: Dict[str, int] = field(default_factory=dict)
    workflow_regra_brflow: Dict[str, str] = field(default_factory=dict)
    workflow_modo_replicacao: Dict[str, str] = field(default_factory=dict)


def _resolver_workflow_nome_brflow(row: pd.Series) -> str:
    """Nome na listagem BRFlow (Workflow Origem); fallback para coluna Workflow."""
    wf_cfg = str(row.get(COLUNA_CONFIG_WORKFLOW, "") or "").strip()
    if COLUNA_CONFIG_WORKFLOW_SELENIUM in row.index:
        sel = str(row.get(COLUNA_CONFIG_WORKFLOW_SELENIUM, "") or "").strip()
        if sel and sel.lower() not in ("nan", "none"):
            return sel
    return wf_cfg


def workflow_nome_brflow(plano: PlanoReplicacao, workflow_config: str) -> str:
    """Nome do workflow na tela BRFlow para o item do plano (chave = coluna Workflow)."""
    return str(plano.workflow_brflow.get(workflow_config, workflow_config) or workflow_config).strip()


def _as_int(val: Any, default: int = 0) -> int:
    """Converte para int tratando None/NaN (evita 'cannot convert float NaN to integer')."""
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    try:
        num = float(val)
        if math.isnan(num) or math.isinf(num):
            return default
        return int(round(num))
    except (TypeError, ValueError):
        return default


def _preencher_coluna_workflow_selenium(df: pd.DataFrame) -> pd.DataFrame:
    if COLUNA_CONFIG_WORKFLOW_SELENIUM not in df.columns:
        df[COLUNA_CONFIG_WORKFLOW_SELENIUM] = df[COLUNA_CONFIG_WORKFLOW]
    else:
        vazio = (
            ~df[COLUNA_CONFIG_WORKFLOW_SELENIUM].astype(bool)
            | (df[COLUNA_CONFIG_WORKFLOW_SELENIUM].str.lower() == "nan")
        )
        df.loc[vazio, COLUNA_CONFIG_WORKFLOW_SELENIUM] = df.loc[vazio, COLUNA_CONFIG_WORKFLOW]
    return df


def _normalizar_workflow(nome: str) -> str:
    """Normaliza nome para comparação (acentos, maiúsculas, espaços, hífens unicode)."""
    if nome is None or (isinstance(nome, float) and math.isnan(nome)):
        return ""
    texto = unicodedata.normalize("NFKC", str(nome))
    texto = texto.replace("\u2013", "-").replace("\u2014", "-")
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"\s+", " ", texto.strip())
    return texto.casefold()


def _protocolo_chave_plano(valor: Any) -> str:
    """Chave única de protocolo no plano (paridade com normalize_protocolo do backend)."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return ""
    texto = unicodedata.normalize("NFKC", str(valor)).strip()
    if not texto:
        return ""
    if re.fullmatch(r"\d+", texto):
        return str(int(texto))
    return texto.casefold()


class _HistoricoPreparado(set):
    """Set com valores originais e chaves canônicas calculadas uma única vez."""

    def __init__(self, valores=()):
        super().__init__(valores or ())
        self.chaves = {
            chave
            for item in self
            for chave in [_protocolo_chave_plano(item)]
            if chave
        }

    def add(self, item: Any) -> None:
        super().add(item)
        chave = _protocolo_chave_plano(item)
        if chave:
            self.chaves.add(chave)

    def update(self, *others) -> None:
        for other in others:
            for item in other:
                self.add(item)

    def __or__(self, other):
        merged = _HistoricoPreparado(self)
        merged.update(other)
        return merged

    def __ior__(self, other):
        self.update(other)
        return self

    def copy(self):
        cloned = _HistoricoPreparado()
        set.update(cloned, self)
        cloned.chaves = set(self.chaves)
        return cloned


def preparar_historico_planejamento(historico: Optional[set]) -> _HistoricoPreparado:
    """Prepara histórico sem mudar os valores usados pelas comparações legadas."""
    if isinstance(historico, _HistoricoPreparado):
        return historico
    return _HistoricoPreparado(historico or ())


def _historico_chaves_plano(historico: Optional[set]) -> set[str]:
    if not historico:
        return set()
    if isinstance(historico, _HistoricoPreparado):
        return historico.chaves
    return {_protocolo_chave_plano(item) for item in historico if _protocolo_chave_plano(item)}


def _filtrar_pool_por_historico(pool: pd.DataFrame, historico: Optional[set]) -> tuple[pd.DataFrame, int]:
    """Exclui protocolos já usados (comparação normalizada)."""
    if pool.empty or not historico:
        return pool, 0
    hist_keys = _historico_chaves_plano(historico)
    if not hist_keys:
        return pool, 0
    keys = pool[COLUNA_PROTOCOLO].astype(str).map(_protocolo_chave_plano)
    antes = len(pool)
    pool = pool[~keys.isin(hist_keys)].copy()
    return pool, max(0, antes - len(pool))


def _normalizar_fila(valor: Any) -> str:
    """Normaliza valor da coluna Fila (Default.xlsx). Vazio → G auditoria."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return REPLICACAO_FILA_G_AUDITORIA
    texto = str(valor).strip()
    if not texto or texto.lower() in ("nan", "none"):
        return REPLICACAO_FILA_G_AUDITORIA
    t = texto.casefold().replace(",", ".")
    if t == REPLICACAO_FILA_DOCUMENTOSCOPIA_31:
        return REPLICACAO_FILA_DOCUMENTOSCOPIA_31
    if "documentoscopia" in t and "3.1" in t:
        return REPLICACAO_FILA_DOCUMENTOSCOPIA_31
    if t in (REPLICACAO_FILA_BIO.casefold(), "biometria"):
        return REPLICACAO_FILA_BIO
    if "biometria" in t and "auditoria" in t:
        return REPLICACAO_FILA_BIO
    if t == REPLICACAO_FILA_REDOC.casefold():
        return REPLICACAO_FILA_REDOC
    if "redoc" in t:
        return REPLICACAO_FILA_REDOC
    return REPLICACAO_FILA_G_AUDITORIA


def eh_fila_documentoscopia_31(fila: str) -> bool:
    return _normalizar_fila(fila) == REPLICACAO_FILA_DOCUMENTOSCOPIA_31


def eh_fila_bio(fila: str) -> bool:
    return _normalizar_fila(fila) == REPLICACAO_FILA_BIO


def eh_fila_redoc(fila: str) -> bool:
    return _normalizar_fila(fila) == REPLICACAO_FILA_REDOC


def eh_fila_modo_qtd(fila: str) -> bool:
    fila_norm = _normalizar_fila(fila)
    return fila_norm in (REPLICACAO_FILA_BIO, REPLICACAO_FILA_REDOC)


_FILA_DESTINO_ATIVO_KEYS = {
    REPLICACAO_FILA_G_AUDITORIA: "replicacao_destino_brflow_ativo",
    REPLICACAO_FILA_DOCUMENTOSCOPIA_31: "replicacao_destino_case31_ativo",
    REPLICACAO_FILA_BIO: "replicacao_destino_bio_ativo",
    REPLICACAO_FILA_REDOC: "replicacao_destino_redoc_ativo",
}


def _cfg_bool_destino(settings: dict, key: str, default: bool = True) -> bool:
    from app.bots.replicacao_d1.settings import parse_bool_setting

    if key in settings:
        return parse_bool_setting(settings.get(key), default)
    snap = settings.get("_execution_snapshot")
    persistent: dict = {}
    if isinstance(snap, dict):
        persistent = dict(snap.get("persistent") or {})
    elif isinstance(settings.get("persistent"), dict):
        persistent = dict(settings.get("persistent") or {})
    if key in persistent:
        return parse_bool_setting(persistent.get(key), default)
    return default


def fila_replicacao_habilitada(fila: str, settings: Optional[dict] = None) -> bool:
    """True quando a replicação está ligada para a fila/ambiente informado."""
    settings = settings or {}
    fila_norm = _normalizar_fila(fila)
    key = _FILA_DESTINO_ATIVO_KEYS.get(fila_norm)
    if not key:
        return True
    return _cfg_bool_destino(settings, key, default=True)


def _normalizar_usar_arquivo_csv(valor: Any) -> Optional[bool]:
    """Normaliza a escolha explicita; valor ausente/invalido indica dado legado."""
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(valor, str):
        texto = _normalizar_workflow(valor)
        if texto in {"1", "true", "yes", "on", "sim", "s"}:
            return True
        if texto in {"0", "false", "no", "off", "nao", "n"}:
            return False
        return None
    if isinstance(valor, bool) or valor.__class__.__name__ == "bool_":
        return bool(valor)
    if isinstance(valor, (int, float)) and valor in (0, 1):
        return bool(valor)
    return None


def resolver_modo_replicacao(
    usar_arquivo_csv: Any = None,
    fila: str = "",
) -> str:
    """Resolve o modo por workflow, com fallback por fila apenas para dados legados.

    A chamada historica ``resolver_modo_replicacao(fila)`` continua aceita durante
    a transicao. Quando a escolha explicita existe, ela sempre vence a fila.
    """
    if isinstance(usar_arquivo_csv, str) and not fila:
        escolha = _normalizar_usar_arquivo_csv(usar_arquivo_csv)
        if escolha is None:
            fila = usar_arquivo_csv
            usar_arquivo_csv = None
    escolha = _normalizar_usar_arquivo_csv(usar_arquivo_csv)
    if escolha is not None:
        return REPLICACAO_MODO_PROTOCOLOS if escolha else REPLICACAO_MODO_QTD
    return REPLICACAO_MODO_QTD if eh_fila_modo_qtd(fila) else REPLICACAO_MODO_PROTOCOLOS


def resolver_modo_replicacao_linha(row: pd.Series, fila: str = "") -> str:
    """Resolve o modo transportado pela linha de configuracao do workflow."""
    valor = None
    if COLUNA_CONFIG_USAR_ARQUIVO_CSV in row.index:
        valor = row.get(COLUNA_CONFIG_USAR_ARQUIVO_CSV)
    elif "usar_arquivo_csv" in row.index:
        valor = row.get("usar_arquivo_csv")
    return resolver_modo_replicacao(valor, fila or resolver_fila_linha(row))


def _coluna_escala_por_fila(fila: str) -> str:
    fila_norm = _normalizar_fila(fila)
    if fila_norm == REPLICACAO_FILA_DOCUMENTOSCOPIA_31:
        return COLUNA_ESCALA_AUDITORES_CASE
    if fila_norm == REPLICACAO_FILA_BIO:
        return COLUNA_ESCALA_AUDITORES_BIO
    if fila_norm == REPLICACAO_FILA_REDOC:
        return COLUNA_ESCALA_AUDITORES_REDOC
    return COLUNA_ESCALA_AUDITORES


def resolver_fila_linha(row: pd.Series) -> str:
    if COLUNA_CONFIG_FILA in row.index:
        return _normalizar_fila(row.get(COLUNA_CONFIG_FILA))
    return REPLICACAO_FILA_G_AUDITORIA


def resolver_filtros_brflow_por_fila(
    fila: str,
    settings: Optional[dict] = None,
) -> dict:
    """Cliente/workflow destino BRFlow conforme fila (G auditoria vs Documentoscopia 3.1)."""
    settings = settings or {}
    persistent = {}
    snap = settings.get("_execution_snapshot")
    if isinstance(snap, dict):
        persistent = dict(snap.get("persistent") or {})
    elif isinstance(settings.get("persistent"), dict):
        persistent = dict(settings.get("persistent") or {})

    def _cfg(key: str, default: str = "") -> str:
        val = str(settings.get(key, "") or "").strip()
        if val:
            return val
        return str(persistent.get(key, default) or "").strip()

    cliente = _cfg("replicacao_cliente_cod", "751") or "751"
    if eh_fila_documentoscopia_31(fila):
        workflow_cod = _cfg(
            "replicacao_workflow_cod_31",
            REPLICACAO_WORKFLOW_COD_DOCUMENTOSCOPIA_31,
        ) or REPLICACAO_WORKFLOW_COD_DOCUMENTOSCOPIA_31
        workflow_destino = _cfg(
            "replicacao_workflow_destino_31",
            REPLICACAO_WORKFLOW_DESTINO_DOCUMENTOSCOPIA_31,
        ) or REPLICACAO_WORKFLOW_DESTINO_DOCUMENTOSCOPIA_31
    elif eh_fila_bio(fila):
        workflow_cod = _cfg("replicacao_workflow_cod_bio", "")
        workflow_destino = _cfg(
            "replicacao_workflow_destino_bio",
            REPLICACAO_WORKFLOW_DESTINO_BIO,
        ) or REPLICACAO_WORKFLOW_DESTINO_BIO
    elif eh_fila_redoc(fila):
        workflow_cod = _cfg("replicacao_workflow_cod_redoc", "")
        workflow_destino = _cfg(
            "replicacao_workflow_destino_redoc",
            REPLICACAO_WORKFLOW_DESTINO_REDOC,
        ) or REPLICACAO_WORKFLOW_DESTINO_REDOC
    else:
        workflow_cod = _cfg(
            "replicacao_workflow_cod",
            REPLICACAO_WORKFLOW_COD_G_AUDITORIA,
        ) or REPLICACAO_WORKFLOW_COD_G_AUDITORIA
        workflow_destino = _cfg(
            "replicacao_workflow_destino",
            REPLICACAO_WORKFLOW_DESTINO_G_AUDITORIA,
        ) or REPLICACAO_WORKFLOW_DESTINO_G_AUDITORIA
    return {
        "replicacao_cliente_cod": cliente,
        "replicacao_cliente_destino": _cfg("replicacao_cliente_destino", "GAQ") or "GAQ",
        "replicacao_workflow_cod": workflow_cod,
        "replicacao_workflow_destino": workflow_destino,
    }


def agrupar_workflows_por_fila(
    workflows: List[str],
    plano: PlanoReplicacao,
    settings: Optional[dict] = None,
) -> Dict[str, List[str]]:
    """Agrupa workflows do plano por fila; G auditoria antes de 3.1."""
    grupos: Dict[str, List[str]] = {}
    for wf in workflows:
        fila = _normalizar_fila(plano.workflow_fila.get(wf, REPLICACAO_FILA_G_AUDITORIA))
        if settings is not None and not fila_replicacao_habilitada(fila, settings):
            continue
        grupos.setdefault(fila, []).append(wf)
    ordem = [
        REPLICACAO_FILA_G_AUDITORIA,
        REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
        REPLICACAO_FILA_BIO,
        REPLICACAO_FILA_REDOC,
    ]
    resultado: Dict[str, List[str]] = {}
    for fila in ordem:
        if fila in grupos:
            resultado[fila] = grupos[fila]
    for fila, wfs in grupos.items():
        if fila not in resultado:
            resultado[fila] = wfs
    return resultado


def config_tem_fila_documentoscopia_31(df_config: pd.DataFrame) -> bool:
    if COLUNA_CONFIG_FILA not in df_config.columns:
        return False
    return any(eh_fila_documentoscopia_31(v) for v in df_config[COLUNA_CONFIG_FILA].tolist())


def config_usa_escala_por_fila(df_config: pd.DataFrame) -> bool:
    """True quando alguma fila exige coluna própria na escala (3.1, Bio ou Redoc)."""
    if COLUNA_CONFIG_FILA not in df_config.columns:
        return False
    filas_com_escala_propria = {
        REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
        REPLICACAO_FILA_BIO,
        REPLICACAO_FILA_REDOC,
    }
    return any(
        _normalizar_fila(v) in filas_com_escala_propria
        for v in df_config[COLUNA_CONFIG_FILA].tolist()
    )


def filtrar_config_por_fila(
    df_config: pd.DataFrame,
    settings: Optional[dict] = None,
) -> pd.DataFrame:
    """Restringe config a uma fila (settings.replicacao_apenas_fila)."""
    settings = settings or {}
    alvo = str(settings.get("replicacao_apenas_fila", "") or "").strip()
    if not alvo:
        return df_config
    fila_norm = _normalizar_fila(alvo)
    if COLUNA_CONFIG_FILA not in df_config.columns:
        log.warning("replicacao_apenas_fila=%s ignorado: coluna Fila ausente no config", alvo)
        return df_config
    mask = df_config[COLUNA_CONFIG_FILA].map(_normalizar_fila) == fila_norm
    out = df_config.loc[mask].copy()
    if out.empty:
        raise ValueError(
            f"Nenhum workflow na fila {fila_norm!r} após filtro replicacao_apenas_fila"
        )
    log.info(
        "Filtro por fila %s: %d workflow(s) de %d",
        fila_norm,
        len(out),
        len(df_config),
    )
    return out


def filtrar_config_destinos_desabilitados(
    df_config: pd.DataFrame,
    settings: Optional[dict] = None,
) -> pd.DataFrame:
    """Remove workflows de filas com replicação desativada na configuração."""
    settings = settings or {}
    if COLUNA_CONFIG_FILA not in df_config.columns:
        return df_config
    mask = df_config[COLUNA_CONFIG_FILA].map(lambda v: fila_replicacao_habilitada(v, settings))
    out = df_config.loc[mask].copy()
    removidos = len(df_config) - len(out)
    if removidos:
        log.info(
            "Destinos desativados: %d workflow(s) excluído(s) do planejamento",
            removidos,
        )
    if not df_config.empty and out.empty:
        filas_desativadas = sorted(
            {
                _normalizar_fila(fila)
                for fila in df_config[COLUNA_CONFIG_FILA].tolist()
                if not fila_replicacao_habilitada(fila, settings)
            }
        )
        raise ValueError(
            "Todos os workflows configurados foram removidos porque o(s) destino(s) "
            f"de replicação estão desativados: {', '.join(filas_desativadas)}. "
            "Reative o destino correspondente na configuração geral."
        )
    return out


def subpasta_protocolos_fila(fila: str) -> str:
    """Subpasta de protocolos: brflow (G auditoria) ou case (Documentoscopia 3.1)."""
    return REPLICACAO_D1_SUBPASTA_CASE if eh_fila_documentoscopia_31(fila) else REPLICACAO_D1_SUBPASTA_BRFLOW


def resolver_canal_destino(fila: str) -> str:
    """Rótulo legível para BI por fila."""
    fila_norm = _normalizar_fila(fila)
    if fila_norm == REPLICACAO_FILA_DOCUMENTOSCOPIA_31:
        return "Case Manager"
    if fila_norm == REPLICACAO_FILA_BIO:
        return "BRFlow Bio"
    if fila_norm == REPLICACAO_FILA_REDOC:
        return "BRFlow Redoc"
    return "BRFlow"


def pasta_csv_workflow(
    pasta_run: Path,
    workflow: str,
    workflow_fila: Dict[str, str],
) -> Path:
    fila = _normalizar_fila(workflow_fila.get(workflow, REPLICACAO_FILA_G_AUDITORIA))
    dest = pasta_run / subpasta_protocolos_fila(fila)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _caminho_csv_workflow(
    pasta_run: Path,
    workflow: str,
    workflow_fila: Dict[str, str],
) -> Path:
    return pasta_csv_workflow(pasta_run, workflow, workflow_fila) / f"{_sanitizar_nome_arquivo(workflow)}.csv"


def _resolver_csv_workflow_plano(
    plano: PlanoReplicacao,
    workflow: str,
    csv_informado: str = "",
) -> Path:
    if csv_informado:
        path = Path(csv_informado)
        if path.exists():
            return path
    cached = plano.csv_paths.get(workflow)
    if cached and Path(cached).exists():
        return Path(cached)
    pasta_run = plano.pasta_protocolos
    candidatos = [
        _caminho_csv_workflow(pasta_run, workflow, plano.workflow_fila),
        pasta_run / f"{_sanitizar_nome_arquivo(workflow)}.csv",
    ]
    for cand in candidatos:
        if cand.exists():
            return cand
    return candidatos[0]


def _auditores_resumo_por_fila(
    fila: str,
    *,
    auditores_g: int,
    auditores_case: int,
    auditores_bio: int,
    auditores_redoc: int,
) -> int:
    fila_norm = _normalizar_fila(fila)
    if fila_norm == REPLICACAO_FILA_DOCUMENTOSCOPIA_31:
        return auditores_case
    if fila_norm == REPLICACAO_FILA_BIO:
        return auditores_bio
    if fila_norm == REPLICACAO_FILA_REDOC:
        return auditores_redoc
    return auditores_g


def _meta_resumo_fila(
    workflow: str,
    plano: PlanoReplicacao,
    info_capacidade: Optional["InfoCapacidade"] = None,
) -> dict:
    fila = _normalizar_fila(plano.workflow_fila.get(workflow, REPLICACAO_FILA_G_AUDITORIA))
    auditores_g = int(getattr(plano, "auditores_ativos", 0) or 0)
    auditores_case = int(getattr(plano, "auditores_ativos_case", 0) or 0)
    auditores_bio = 0
    auditores_redoc = 0
    if info_capacidade is not None:
        auditores_g = int(info_capacidade.auditores_ativos or auditores_g)
        auditores_case = int(info_capacidade.auditores_ativos_case or auditores_case)
        auditores_bio = int(getattr(info_capacidade, "auditores_ativos_bio", 0) or 0)
        auditores_redoc = int(getattr(info_capacidade, "auditores_ativos_redoc", 0) or 0)
    auditores_fila = _auditores_resumo_por_fila(
        fila,
        auditores_g=auditores_g,
        auditores_case=auditores_case,
        auditores_bio=auditores_bio,
        auditores_redoc=auditores_redoc,
    )
    rel = ""
    path = plano.csv_paths.get(workflow)
    if path and plano.pasta_protocolos:
        try:
            rel = str(Path(path).relative_to(plano.pasta_protocolos)).replace("\\", "/")
        except ValueError:
            rel = Path(path).name
    modo = (getattr(plano, "workflow_modo_replicacao", None) or {}).get(workflow)
    arquivo_relativo = ""
    if modo != REPLICACAO_MODO_QTD:
        arquivo_relativo = rel or (
            f"{subpasta_protocolos_fila(fila)}/{_sanitizar_nome_arquivo(workflow)}.csv"
        )
    return {
        "Fila": fila,
        "Subpasta": subpasta_protocolos_fila(fila),
        "Canal Destino": resolver_canal_destino(fila),
        "Auditores Ativos": auditores_fila,
        "Arquivo CSV Relativo": arquivo_relativo,
    }


def exportar_protocolos_csv_por_fila(
    protocolos_por_workflow: Dict[str, List[str]],
    pasta_run: Path,
    workflow_fila: Dict[str, str],
) -> Dict[str, Path]:
    pasta_run.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for workflow, protocolos in protocolos_por_workflow.items():
        if not protocolos:
            log.info(
                "CSV omitido (sem protocolos): workflow=%s",
                workflow,
            )
            continue
        pasta_wf = pasta_csv_workflow(pasta_run, workflow, workflow_fila)
        caminho = pasta_wf / f"{_sanitizar_nome_arquivo(workflow)}.csv"
        pd.Series(protocolos, dtype="string").to_csv(
            caminho, index=False, header=False, encoding="utf-8-sig"
        )
        paths[workflow] = caminho
        sub = subpasta_protocolos_fila(workflow_fila.get(workflow, REPLICACAO_FILA_G_AUDITORIA))
        log.info(
            "CSV gerado: %s/%s | workflow=%s | protocolos=%d",
            sub,
            caminho.name,
            workflow,
            len(protocolos),
        )
    return paths


def exportar_csv_fallback_vazio_por_fila(
    workflow: str,
    pasta_run: Path,
    workflow_fila: Dict[str, str],
) -> Optional[Path]:
    """Legado: workflows sem protocolos não geram mais CSV de limpeza."""
    log.info(
        "CSV omitido (sem protocolos) | workflow=%s | fila=%s",
        workflow,
        subpasta_protocolos_fila(workflow_fila.get(workflow, REPLICACAO_FILA_G_AUDITORIA)),
    )
    return None


def normalizar_csv_escala_auditores(
    path: Path,
    *,
    copiar_case_de_brflow: bool = True,
) -> pd.DataFrame:
    """Limpa colunas extras do CSV de escala e garante auditores_ativos_case."""
    raw = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    raw.columns = [str(c).strip() for c in raw.columns]
    col_data = _find_column(raw, [COLUNA_ESCALA_DATA, "Data", "dia", "Dia"])
    col_br = _find_column(
        raw,
        [COLUNA_ESCALA_AUDITORES, "auditores", "Auditores", "qtd", "quantidade"],
    )
    col_case = _find_column(
        raw,
        [COLUNA_ESCALA_AUDITORES_CASE, "auditores case", "auditores_case"],
    )
    col_bio = _find_column(
        raw,
        [COLUNA_ESCALA_AUDITORES_BIO, "auditores bio", "auditores_bio"],
    )
    col_redoc = _find_column(
        raw,
        [COLUNA_ESCALA_AUDITORES_REDOC, "auditores redoc", "auditores_redoc"],
    )
    if not col_data or not col_br:
        raise ValueError(f"CSV de escala inválido: faltam data e auditores_ativos em {path}")

    out = pd.DataFrame()
    out[COLUNA_ESCALA_DATA] = raw[col_data].astype(str).str.strip()
    out[COLUNA_ESCALA_AUDITORES] = pd.to_numeric(raw[col_br], errors="coerce").fillna(0).astype(int)
    if col_case:
        out[COLUNA_ESCALA_AUDITORES_CASE] = (
            pd.to_numeric(raw[col_case], errors="coerce").fillna(0).astype(int)
        )
    elif copiar_case_de_brflow:
        out[COLUNA_ESCALA_AUDITORES_CASE] = out[COLUNA_ESCALA_AUDITORES]
    else:
        out[COLUNA_ESCALA_AUDITORES_CASE] = 0
    if col_bio:
        out[COLUNA_ESCALA_AUDITORES_BIO] = (
            pd.to_numeric(raw[col_bio], errors="coerce").fillna(0).astype(int)
        )
    else:
        out[COLUNA_ESCALA_AUDITORES_BIO] = 0
    if col_redoc:
        out[COLUNA_ESCALA_AUDITORES_REDOC] = (
            pd.to_numeric(raw[col_redoc], errors="coerce").fillna(0).astype(int)
        )
    else:
        out[COLUNA_ESCALA_AUDITORES_REDOC] = 0

    out = out[out[COLUNA_ESCALA_DATA].astype(bool)]
    out = out.drop_duplicates(subset=[COLUNA_ESCALA_DATA], keep="last")
    return out.sort_values(COLUNA_ESCALA_DATA)


def _eh_celula_total_volumetria(valor: Any) -> bool:
    """Linhas de subtotal/rodapé do BI com rótulo Total."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return False
    return str(valor).strip().casefold() == "total"


def _eh_workflow_volumetria_valido(nome: Any) -> bool:
    """Ignora rodapés do BI, nan, Total e linhas sem workflow."""
    if nome is None or (isinstance(nome, float) and math.isnan(nome)):
        return False
    if _eh_celula_total_volumetria(nome):
        return False
    texto = str(nome).strip()
    if not texto or texto.lower() in ("nan", "none"):
        return False
    if texto.lower().startswith("filtros aplicados"):
        return False
    return True


COLUNA_VOLUMETRIA_CLIENTE = "cliente_volumetria"


def _eh_cliente_volumetria_valido(nome: Any) -> bool:
    """Ignora rodapés do BI, Total e células vazias na coluna Cliente da volumetria."""
    if _eh_celula_total_volumetria(nome):
        return False
    return _eh_workflow_volumetria_valido(nome)


def _eh_aba_calculadora_padrao(nome_aba: str) -> bool:
    chave = _normalizar_nome_aba(nome_aba)
    return chave.startswith("calculadora") and "padra" in chave


def _sanitizar_nome_arquivo(nome: str) -> str:
    limpo = _INVALID_FILENAME_CHARS.sub("_", str(nome).strip())
    return limpo.rstrip(". ") or "workflow"


def _resolver_run_id(data_exec: datetime, settings: Optional[dict] = None) -> str:
    """Define run_id da execução (YYYYMMDD_HHMMSS ou reutilização legada)."""
    settings = settings or {}
    informado = str(settings.get("run_id", "") or "").strip()
    if informado:
        return informado

    sobrescrever = bool(settings.get("sobrescrever", REPLICACAO_AUD_SOBRESCREVER_DEFAULT))
    dia = data_exec.strftime("%Y%m%d")
    pasta_dia = PASTA_REPLICACAO_AUD_PROTOCOLOS / dia
    if sobrescrever and pasta_dia.exists() and pasta_dia.is_dir():
        return dia
    return data_exec.strftime("%Y%m%d_%H%M%S")


def _pasta_saida_protocolos(run_id: str) -> Path:
    return PASTA_REPLICACAO_AUD_PROTOCOLOS / run_id


def _pasta_relatorios_excel() -> Path:
    """Subpasta resumo/relatorios/ para arquivos replicacao_aud_relatorio_*.xlsx."""
    PASTA_REPLICACAO_AUD_RELATORIOS.mkdir(parents=True, exist_ok=True)
    return PASTA_REPLICACAO_AUD_RELATORIOS


def _caminho_relatorio_excel(run_id: str) -> Path:
    return _pasta_relatorios_excel() / f"{REPLICACAO_AUD_RELATORIO_PREFIXO}{run_id}.xlsx"


def _resolver_caminho_relatorio_excel(
    run_id: str,
    path_hint: Optional[Union[str, Path]] = None,
) -> Path:
    """Path do relatório Excel (novo: resumo/relatorios/; legado: resumo/)."""
    if path_hint:
        hint = Path(path_hint)
        if hint.exists():
            return hint
    novo = _caminho_relatorio_excel(run_id)
    if novo.exists():
        return novo
    legado = PASTA_REPLICACAO_AUD_RESUMO / f"{REPLICACAO_AUD_RELATORIO_PREFIXO}{run_id}.xlsx"
    return legado if legado.exists() else novo


def caminho_estado_execucao(run_id: str) -> Path:
    return PASTA_REPLICACAO_AUD_RESUMO / f"{REPLICACAO_AUD_EXECUCAO_PREFIXO}{run_id}.json"


def resolver_ultimo_run_id() -> Optional[str]:
    """Retorna run_id do execucao_*.json mais recente em resumo/."""
    candidatos = sorted(
        PASTA_REPLICACAO_AUD_RESUMO.glob(f"{REPLICACAO_AUD_EXECUCAO_PREFIXO}*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidatos:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            run_id = str(data.get("run_id", "") or "").strip()
            if run_id:
                return run_id
        except Exception:
            continue
        nome = path.stem.replace(REPLICACAO_AUD_EXECUCAO_PREFIXO, "", 1)
        if nome:
            return nome
    pastas = sorted(
        (p for p in PASTA_REPLICACAO_AUD_PROTOCOLOS.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if pastas:
        return pastas[0].name
    return None


def _arquivo_dentro_janela(path: Path, dias: int, referencia: datetime) -> bool:
    if dias <= 0:
        return True
    limite = referencia - timedelta(days=dias)
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return mtime >= limite
    except OSError:
        return False


def _eh_protocolo_placeholder(val: Any) -> bool:
    """True se o valor é o placeholder de limpeza BRFlow (não é protocolo real)."""
    return str(val or "").strip() == REPLICACAO_CSV_PLACEHOLDER_LIMPEZA


def csv_protocolos_e_limpeza(caminho: Path) -> bool:
    """True se o CSV não contém protocolos reais (vazio, legado 0 bytes ou só placeholder)."""
    path = Path(caminho)
    if not path.exists():
        return False
    if path.stat().st_size == 0:
        return True
    try:
        df = pd.read_csv(path, header=None, dtype="string", encoding="utf-8-sig")
        vals = [
            str(v).strip()
            for v in df.iloc[:, 0].dropna().astype(str)
            if str(v).strip()
        ]
        if not vals:
            return True
        return all(_eh_protocolo_placeholder(v) for v in vals)
    except Exception:
        texto = path.read_text(encoding="utf-8-sig").strip()
        if not texto:
            return True
        return texto == REPLICACAO_CSV_PLACEHOLDER_LIMPEZA


def workflow_elegivel_upload(protocolos: list, csv_path: Path) -> bool:
    """True se o workflow tem protocolos reais para upload no BRFlow."""
    if protocolos:
        return True
    path = Path(csv_path)
    if not path.exists() or path.is_dir():
        return False
    return not csv_protocolos_e_limpeza(path)


def _csv_relativo_plano(plano: "PlanoReplicacao", csv_path: Optional[Path]) -> str:
    """Persiste caminho do CSV relativo à pasta do run (portável entre máquinas)."""
    if not csv_path:
        return ""
    path = Path(csv_path)
    pasta = Path(plano.pasta_protocolos)
    try:
        return str(path.relative_to(pasta)).replace("\\", "/")
    except ValueError:
        return path.name


def _ler_protocolos_de_csv(path: Path) -> set:
    protocolos: set = set()
    try:
        if path.stat().st_size == 0:
            return protocolos
        df = pd.read_csv(path, header=None, dtype="string", encoding="utf-8-sig")
        for val in df.iloc[:, 0].dropna().astype(str):
            val = val.strip()
            if val and not _eh_protocolo_placeholder(val):
                protocolos.add(val)
    except Exception as exc:
        log.debug("Ignorando CSV de histórico %s: %s", path.name, exc)
    return protocolos


def carregar_protocolos_historico(
    excluir_run_id: Optional[str] = None,
    dias_historico: int = REPLICACAO_AUD_DIAS_HISTORICO_DEFAULT,
    referencia: Optional[datetime] = None,
) -> set:
    """União de protocolos em protocolos/** e replicacao_aud_plano_*.csv."""
    referencia = referencia or datetime.now()
    historico: set = set()

    if PASTA_REPLICACAO_AUD_PROTOCOLOS.exists():
        for pasta_run in PASTA_REPLICACAO_AUD_PROTOCOLOS.iterdir():
            if not pasta_run.is_dir():
                continue
            if excluir_run_id and pasta_run.name == excluir_run_id:
                continue
            for csv_path in pasta_run.glob("*.csv"):
                if _arquivo_dentro_janela(csv_path, dias_historico, referencia):
                    historico |= _ler_protocolos_de_csv(csv_path)

    if PASTA_REPLICACAO_AUD_RESUMO.exists():
        for plano_path in PASTA_REPLICACAO_AUD_RESUMO.glob(f"{REPLICACAO_AUD_PLANO_PREFIXO}*.csv"):
            if not _arquivo_dentro_janela(plano_path, dias_historico, referencia):
                continue
            try:
                df = pd.read_csv(plano_path, sep=";", encoding="utf-8-sig", dtype="string")
            except Exception as exc:
                log.debug("Ignorando plano histórico %s: %s", plano_path.name, exc)
                continue
            if COLUNA_PROTOCOLO not in df.columns:
                continue
            for val in df[COLUNA_PROTOCOLO].dropna().astype(str):
                val = val.strip()
                if val and val.upper() != "TOTAL":
                    historico.add(val)

    log.info("Histórico de protocolos carregado: %d únicos (janela %d dias)", len(historico), dias_historico)
    return historico


def _find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Resolve nome de coluna ignorando case e espaços extras."""
    index = {re.sub(r"\s+", " ", str(c).strip().casefold()): c for c in df.columns}
    for nome in candidates:
        chave = re.sub(r"\s+", " ", str(nome).strip().casefold())
        if chave in index:
            return index[chave]
    return None


def _settings_usam_escala_d1(settings: Optional[dict] = None) -> bool:
    """True quando settings apontam para a base D-1 (não a legada brflow-auditoria-replic)."""
    settings = settings or {}
    custom_base = str(settings.get("replicacao_config_base", "") or "").strip()
    if custom_base:
        norm = custom_base.replace("\\", "/").casefold()
        if "brflow-auditoria-replic-d1" in norm:
            return True
        try:
            base = Path(custom_base).resolve()
            d1_config = PASTA_REPLICACAO_AUD_D1_CONFIG.resolve()
            d1_root = d1_config.parent.resolve()
            if base in (d1_config, d1_root) or d1_root in base.parents:
                return True
        except Exception:
            pass
    custom_escala = str(settings.get("escala_auditores_csv", "") or "").strip()
    if custom_escala and "brflow-auditoria-replic-d1" in custom_escala.replace("\\", "/"):
        return True
    return False


def _resolver_csv_escala_por_pastas(
    pasta_escala: Path,
    pasta_escala_legado: Path,
    csv_padrao: Path,
) -> Path:
    """Resolve CSV dentro de uma pasta escala/ (padrão > legado > *.csv mais recente)."""
    if csv_padrao.exists():
        return csv_padrao

    legado = pasta_escala_legado / ESCALA_AUDITORES_ARQUIVO
    if legado.exists():
        log.warning(
            "CSV de escala em pasta legada %s; mova para %s",
            pasta_escala_legado,
            pasta_escala,
        )
        return legado

    if pasta_escala.exists():
        csvs = sorted(
            pasta_escala.glob("*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if csvs:
            log.info(
                "Usando CSV de escala: %s (padrão %s não encontrado)",
                csvs[0].name,
                ESCALA_AUDITORES_ARQUIVO,
            )
            return csvs[0]

    return csv_padrao


def _resolver_csv_escala_auditores(settings: Optional[dict] = None) -> Path:
    """Resolve o CSV de escala (settings > D-1 > legado brflow-auditoria-replic)."""
    settings = settings or {}
    custom = str(settings.get("escala_auditores_csv", "") or "").strip()
    if custom:
        return Path(custom)

    if _settings_usam_escala_d1(settings):
        csv_path = _resolver_csv_escala_por_pastas(
            PASTA_REPLICACAO_AUD_D1_ESCALA,
            PASTA_REPLICACAO_AUD_D1_ESCALA_LEGADO,
            ESCALA_AUDITORES_D1_CSV,
        )
        log.info("Escala D-1 resolvida: %s", csv_path)
        return csv_path

    csv_path = _resolver_csv_escala_por_pastas(
        PASTA_REPLICACAO_AUD_ESCALA,
        PASTA_REPLICACAO_AUD_ESCALA_LEGADO,
        ESCALA_AUDITORES_CSV,
    )
    log.info("Escala legada resolvida: %s", csv_path)
    return csv_path


def _normalizar_rotulo(text: str) -> str:
    """Normaliza rótulo do bloco resumo (coluna A do Excel)."""
    texto = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", texto.strip().casefold())


def _ler_resumo_config_excel(path: Path) -> Dict[str, Any]:
    """Lê parâmetros do bloco resumo (colunas A/B) em Config_auditoria.xlsx."""
    try:
        raw = pd.read_excel(path, engine="openpyxl", header=None, usecols=[0, 1])
    except Exception as exc:
        log.debug("Resumo config (A/B) não lido de %s: %s", path.name, exc)
        return {}
    out: Dict[str, Any] = {}
    for _, row in raw.iterrows():
        if len(row) < 2 or pd.isna(row.iloc[0]):
            continue
        rotulo = _normalizar_rotulo(row.iloc[0])
        if rotulo:
            out[rotulo] = row.iloc[1]
    return out


def _buscar_valor_resumo(resumo: Dict[str, Any], candidates: List[str]) -> Any:
    """Busca valor no resumo por rótulo (ex.: 'Meta Produ (diária)' na coluna A)."""
    if not resumo:
        return None
    for nome in candidates:
        chave_busca = _normalizar_rotulo(nome)
        for rotulo, valor in resumo.items():
            if rotulo == chave_busca or rotulo.startswith(chave_busca):
                return valor
    return None


_ROTULOS_META_PRODU = [
    COLUNA_CONFIG_META_PRODU,
    "Meta Produ (diaria)",
    "meta produ",
    "Meta Produ",
    "meta_produ",
]


@dataclass
class InfoCapacidade:
    """Parâmetros da fórmula Excel de capacidade produtiva."""

    auditores_ativos: int
    meta_produ: float
    capacidade_produtiva: float
    soma_amostra_diaria: int
    fator_capacidade: float
    usar_escala: bool = True
    data_escala: Optional[datetime] = None
    auditores_ativos_case: int = 0
    meta_produ_case: float = 0.0
    auditores_ativos_bio: int = 0
    auditores_ativos_redoc: int = 0


def _workflow_d1_da_linha(row: pd.Series) -> str:
    """Nome do workflow no parquet D-1 (fallback: Workflow da tela BRFlow)."""
    wf_tela = str(row[COLUNA_CONFIG_WORKFLOW]).strip()
    if COLUNA_CONFIG_WORKFLOW_D1 not in row.index:
        return wf_tela
    bruto = row[COLUNA_CONFIG_WORKFLOW_D1]
    if bruto is None or (isinstance(bruto, float) and math.isnan(bruto)):
        return wf_tela
    texto = str(bruto).strip()
    if not texto or texto.lower() == "nan":
        return wf_tela
    return texto


def _resolver_paths_config(settings: Optional[dict] = None) -> Dict[str, Path]:
    """Resolve caminhos das fontes (settings > padrão OneDrive config/)."""
    settings = settings or {}
    base_raw = str(settings.get("replicacao_config_base", "") or "").strip()
    default_raw = str(settings.get("replicacao_config_default", "") or "").strip()
    base = Path(base_raw) if base_raw else PASTA_REPLICACAO_AUD_CONFIG
    if default_raw:
        default = Path(default_raw)
        if not base_raw:
            base = default.parent
    else:
        default = base / "Default.xlsx"
    if not default.name.lower().endswith(".xlsx"):
        default = CONFIG_DEFAULT_XLSX
        base = PASTA_REPLICACAO_AUD_CONFIG
    categoria = Path(str(settings.get("replicacao_categoria_xlsx", "") or "").strip() or base / "Categoria.xlsx")
    volumetria_raiz = Path(
        str(settings.get("replicacao_volumetria_raiz", "") or "").strip() or base / "volumetria"
    )
    volumetria_pasta = str(settings.get("replicacao_volumetria_pasta", "") or "").strip()
    return {
        "base": base,
        "default": default,
        "categoria": categoria,
        "volumetria_raiz": volumetria_raiz,
        "volumetria_pasta": Path(volumetria_pasta) if volumetria_pasta else None,
    }


def _listar_arquivos_volumetria(pasta: Path) -> List[Path]:
    """Lista exports BI (.xlsx/.csv ou arquivo sem extensão, ex.: maio26)."""
    extensoes = (".xlsx", ".xls", ".xlsm", ".csv")
    arquivos: List[Path] = []
    for p in pasta.iterdir():
        if not p.is_file() or p.name.startswith("~$"):
            continue
        if p.suffix.lower() in extensoes:
            arquivos.append(p)
            continue
        if not p.suffix:
            try:
                with open(p, "rb") as fh:
                    if fh.read(2) == b"PK":
                        arquivos.append(p)
            except OSError:
                pass
    arquivos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return arquivos


def resolver_pasta_volumetria_mais_recente(
    volumetria_raiz: Optional[Path] = None,
    settings: Optional[dict] = None,
) -> Path:
    """
    Resolve a fonte de volumetria mais recente em config/volumetria/.

    Aceita:
    - Arquivos de período na raiz (ex.: maio26.xlsx)
    - Subpastas com export dentro (ex.: maio26/export.xlsx)
    """
    settings = settings or {}
    paths = _resolver_paths_config(settings)
    if paths["volumetria_pasta"]:
        alvo = paths["volumetria_pasta"]
        if not alvo.exists():
            raise FileNotFoundError(f"Volumetria informada não encontrada: {alvo}")
        if alvo.is_file():
            log.info("Volumetria | arquivo configurado: %s", alvo.name)
            return alvo
        arquivos = _listar_arquivos_volumetria(alvo)
        if not arquivos:
            raise FileNotFoundError(f"Nenhum arquivo de volumetria em {alvo}")
        log.info("Volumetria | pasta configurada: %s | arquivo: %s", alvo.name, arquivos[0].name)
        return arquivos[0]

    raiz = Path(volumetria_raiz) if volumetria_raiz else paths["volumetria_raiz"]
    if not raiz.exists():
        raise FileNotFoundError(f"Pasta de volumetria não encontrada: {raiz}")

    candidatos: List[tuple[float, str, Path]] = []
    for arq in _listar_arquivos_volumetria(raiz):
        candidatos.append((arq.stat().st_mtime, arq.name, arq))
    for sub in (p for p in raiz.iterdir() if p.is_dir()):
        arquivos = _listar_arquivos_volumetria(sub)
        if arquivos:
            candidatos.append((arquivos[0].stat().st_mtime, sub.name, arquivos[0]))

    if not candidatos:
        raise FileNotFoundError(
            f"Nenhum arquivo de volumetria em {raiz} "
            "(ex.: maio26.xlsx na pasta ou maio26/export.xlsx em subpasta)"
        )

    candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
    escolhida = candidatos[0][2]
    log.info("Volumetria | fonte mais recente: %s", escolhida)
    return escolhida


def carregar_volumetria(
    fonte_volumetria: Path,
    settings: Optional[dict] = None,
) -> pd.DataFrame:
    """Lê export BI: Workflow, Automáticos, Manuais e Cliente opcional (arquivo ou pasta)."""
    settings = settings or {}
    if fonte_volumetria.is_file():
        path = fonte_volumetria
    else:
        arquivos = _listar_arquivos_volumetria(fonte_volumetria)
        if not arquivos:
            raise FileNotFoundError(f"Nenhum arquivo de volumetria (.xlsx/.csv) em {fonte_volumetria}")
        path = arquivos[0]
        if len(arquivos) > 1:
            log.info("Volumetria | vários arquivos; usando o mais recente: %s", path.name)

    if path.suffix.lower() == ".csv":
        raw = pd.read_csv(path, encoding="utf-8-sig", sep=None, engine="python")
    else:
        raw = pd.read_excel(path, engine="openpyxl")  # .xlsx ou maio26 sem extensão

    col_wf = _find_column(raw, [COLUNA_CONFIG_WORKFLOW, "Workflow"])
    col_cli = _find_column(raw, [COLUNA_CONFIG_CLIENTE, "Cliente", "cliente"])
    col_auto = _find_column(raw, [COLUNA_CONFIG_AUTOMATICOS, "Automaticos", "Automático", "Automatico"])
    col_manual = _find_column(raw, [COLUNA_CONFIG_MANUAIS, "Manuais", "Manual"])
    if not col_wf:
        raise ValueError(f"Coluna Workflow ausente em {path.name}")
    if not col_auto or not col_manual:
        raise ValueError(f"Colunas Automáticos/Manuais ausentes em {path.name}")

    df = pd.DataFrame()
    df[COLUNA_CONFIG_WORKFLOW] = raw[col_wf].astype(str).str.strip()
    df[COLUNA_CONFIG_AUTOMATICOS] = pd.to_numeric(raw[col_auto], errors="coerce").fillna(0)
    df[COLUNA_CONFIG_MANUAIS] = pd.to_numeric(raw[col_manual], errors="coerce").fillna(0)
    df[COLUNA_CONFIG_TOTAL] = df[COLUNA_CONFIG_AUTOMATICOS] + df[COLUNA_CONFIG_MANUAIS]
    if col_cli:
        df[COLUNA_VOLUMETRIA_CLIENTE] = raw[col_cli].astype(str).str.strip()
        df.loc[~df[COLUNA_VOLUMETRIA_CLIENTE].map(_eh_cliente_volumetria_valido), COLUNA_VOLUMETRIA_CLIENTE] = ""
    df["_wf_key"] = df[COLUNA_CONFIG_WORKFLOW].map(_normalizar_workflow)
    antes = len(df)
    df = df[df[COLUNA_CONFIG_WORKFLOW].map(_eh_workflow_volumetria_valido)]
    if len(df) < antes:
        log.info("Volumetria | %d linha(s) ignorada(s) (Total / rodapé BI / workflow vazio)", antes - len(df))
    df = df[df[COLUNA_CONFIG_TOTAL] > 0]
    df = df.drop_duplicates(subset=["_wf_key"], keep="first")
    if df.empty:
        raise ValueError(f"Nenhum workflow válido em {path.name} após filtrar rodapé BI e volumes zerados")
    com_cliente = int(df[COLUNA_VOLUMETRIA_CLIENTE].astype(bool).sum()) if COLUNA_VOLUMETRIA_CLIENTE in df.columns else 0
    log.info(
        "Volumetria | %d workflow(s) em %s%s",
        len(df),
        path.name,
        f" ({com_cliente} com Cliente)" if com_cliente else "",
    )
    return df


def _normalizar_nome_aba(nome: str) -> str:
    """Compara abas ignorando espaços, hífens, acentos e maiúsculas."""
    texto = unicodedata.normalize("NFKD", str(nome))
    sem_acento = texto.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\s\-_]+", "", sem_acento.strip().casefold())


def _contem_marca_pendente(valor: Any) -> bool:
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return False
    texto = str(valor).strip().casefold()
    marca = REPLICACAO_AUD_MARCA_PENDENTE.casefold()
    return marca in texto or texto == "pendente"


def _eh_linha_pendente_workflow_d1(row: pd.Series) -> bool:
    """Linha da aba Workflow d1 marcada para revisão manual (Cliente ou Workflow d-1)."""
    for col in (COLUNA_CONFIG_CLIENTE, COLUNA_CONFIG_WORKFLOW_D1):
        if col in row.index and _contem_marca_pendente(row[col]):
            return True
    return False


def _eh_status_workflow_d1_inativo(val: Any) -> bool:
    """True quando Status indica workflow desligado (False/0/no). Vazio/NaN = ativo."""
    if val is None:
        return False
    try:
        if pd.isna(val):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(val, bool):
        return not val
    texto = str(val).strip().casefold()
    if not texto or texto in ("nan", "none"):
        return False
    return texto in ("false", "0", "no", "n")


def carregar_chaves_workflow_d1_desabilitados(
    default_path: Optional[Path] = None,
    settings: Optional[dict] = None,
) -> set:
    """Chaves normalizadas de workflows com Status=False na aba Workflow d1."""
    paths = _resolver_paths_config(settings)
    path = Path(default_path) if default_path else paths["default"]
    if not path.exists():
        return set()

    raw = _ler_excel_aba(path, ABA_DEFAULT_WORKFLOW_D1)
    col_wf = _find_column(raw, [COLUNA_CONFIG_WORKFLOW, "Workflow"])
    col_status = _find_column(raw, [COLUNA_CONFIG_STATUS, "Status"])
    if not col_wf or not col_status:
        return set()

    chaves: set = set()
    for _, row in raw.iterrows():
        wf = str(row.get(col_wf, "") or "").strip()
        if not wf or not _eh_workflow_volumetria_valido(wf):
            continue
        if _eh_status_workflow_d1_inativo(row.get(col_status)):
            chaves.add(_normalizar_workflow(wf))
    return chaves


def _resolver_pendentes_csv_path(settings: Optional[dict] = None) -> Path:
    """CSV de audit relativo a replicacao_config_base."""
    paths = _resolver_paths_config(settings)
    return paths["base"] / "pendentes_workflow_d1.csv"


def _novo_resultado_sync_pendentes() -> Dict[str, List[str]]:
    return {"novos": [], "existentes": [], "falha_sync": []}


def _resolver_nome_aba_workflow_d1_sheetnames(sheetnames: List[str]) -> str:
    for nome in sheetnames:
        if _eh_aba_workflow_d1(nome):
            return nome
    raise ValueError(f"Aba '{ABA_DEFAULT_WORKFLOW_D1}' não encontrada. Abas: {sheetnames}")


def _listar_chaves_workflow_d1_excel(path: Path) -> set:
    """Chaves normalizadas já presentes na aba Workflow d1 (inclui pendentes)."""
    from openpyxl import load_workbook

    chaves: set = set()
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = _resolver_nome_aba_workflow_d1_sheetnames(wb.sheetnames)
        ws = wb[sheet]
        header_map: Dict[str, int] = {}
        for col in range(1, (ws.max_column or 0) + 1):
            rotulo = ws.cell(1, col).value
            if rotulo is None:
                continue
            chave_col = _normalizar_rotulo(str(rotulo))
            if chave_col in ("workflow", "cliente"):
                header_map[chave_col] = col
        col_wf = header_map.get("workflow")
        if not col_wf:
            return chaves
        for row in range(2, (ws.max_row or 1) + 1):
            wf = ws.cell(row, col_wf).value
            if _eh_workflow_volumetria_valido(wf):
                chaves.add(_normalizar_workflow(wf))
    finally:
        wb.close()
    return chaves


def _registrar_pendentes_audit_csv(
    workflow: str,
    origem_volumetria: str,
    acao: str = "registrado_pendente",
    settings: Optional[dict] = None,
) -> None:
    """Append-only em {replicacao_config_base}/pendentes_workflow_d1.csv."""
    try:
        csv_path = _resolver_pendentes_csv_path(settings)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        linha = {
            "data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "workflow": workflow,
            "origem_volumetria": origem_volumetria,
            "acao": acao,
        }
        escrever_header = not csv_path.exists()
        with open(csv_path, "a", encoding="utf-8-sig", newline="") as fh:
            if escrever_header:
                fh.write("data_hora;workflow;origem_volumetria;acao\n")
            fh.write(
                f"{linha['data_hora']};{linha['workflow']};{linha['origem_volumetria']};{linha['acao']}\n"
            )
    except Exception as exc:
        log.debug("Audit pendentes_workflow_d1.csv não gravado: %s", exc)


def sincronizar_pendentes_workflow_d1(
    default_path: Path,
    workflows_novos: List[dict],
    origem_volumetria: str = "",
    settings: Optional[dict] = None,
) -> tuple[List[str], Dict[str, List[str]]]:
    """
    Registra workflows do BI ausentes na aba Workflow d1 do Default.xlsx.
    Retorna (warnings, {novos, existentes, falha_sync}).
    """
    settings = settings or {}
    warnings: List[str] = []
    resultado = _novo_resultado_sync_pendentes()
    if not workflows_novos:
        return warnings, resultado

    sincronizar = bool(
        settings.get("replicacao_sincronizar_workflow_d1", REPLICACAO_AUD_SINCRONIZAR_WORKFLOW_D1_DEFAULT)
    )
    if not sincronizar:
        for item in workflows_novos:
            wf = str(item.get("workflow", "") or "").strip()
            if not wf:
                continue
            resultado["falha_sync"].append(wf)
            warnings.append(
                f"PENDENTE CONFIG | {wf}: ausente na aba Workflow d1 (sincronização desabilitada)"
            )
        return warnings, resultado

    path = Path(default_path)
    try:
        chaves_existentes = _listar_chaves_workflow_d1_excel(path)
    except Exception as exc:
        for item in workflows_novos:
            wf = str(item.get("workflow", "") or "").strip()
            if not wf:
                continue
            resultado["falha_sync"].append(wf)
            warnings.append(
                f"PENDENTE CONFIG | {wf}: não foi possível ler Workflow d1 ({exc})"
            )
        return warnings, resultado

    pendentes_adicionar: List[dict] = []
    for item in workflows_novos:
        wf = str(item.get("workflow", "") or "").strip()
        key = str(item.get("_wf_key", "") or _normalizar_workflow(wf))
        if not wf:
            continue
        if key in chaves_existentes:
            resultado["existentes"].append(wf)
            cli_hint = str(item.get("cliente", "") or "").strip()
            if cli_hint and _eh_cliente_volumetria_valido(cli_hint):
                msg_extra = "preencha Workflow d-1 (Cliente veio da volumetria)"
            else:
                msg_extra = "preencha Cliente e Workflow d-1"
            warnings.append(
                f"PENDENTE CONFIG | {wf}: já registrado na aba Workflow d1 — {msg_extra}"
            )
            continue
        pendentes_adicionar.append(
            {
                "workflow": wf,
                "_wf_key": key,
                "cliente": str(item.get("cliente", "") or "").strip(),
            }
        )

    if not pendentes_adicionar:
        return warnings, resultado

    try:
        import shutil
        from openpyxl import load_workbook

        bak = path.with_suffix(".xlsx.bak")
        if bak.exists():
            bak = path.parent / f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx.bak"
        shutil.copy2(path, bak)
        log.info("Backup Default.xlsx: %s", bak.name)

        wb = load_workbook(path)
        sheet_name = _resolver_nome_aba_workflow_d1_sheetnames(wb.sheetnames)
        ws = wb[sheet_name]

        col_map: Dict[str, int] = {}
        for col in range(1, (ws.max_column or 0) + 2):
            rotulo = ws.cell(1, col).value
            if rotulo is None:
                continue
            chave = _normalizar_rotulo(str(rotulo))
            if chave == "cliente":
                col_map[COLUNA_CONFIG_CLIENTE] = col
            elif chave == "workflow":
                col_map[COLUNA_CONFIG_WORKFLOW] = col
            elif chave in ("workflow d-1", "workflow d1", "workflow d 1"):
                col_map[COLUNA_CONFIG_WORKFLOW_D1] = col
            elif chave == "status":
                col_map[COLUNA_CONFIG_STATUS] = col

        if COLUNA_CONFIG_WORKFLOW not in col_map:
            col_map[COLUNA_CONFIG_WORKFLOW] = 2
            ws.cell(1, 2, COLUNA_CONFIG_WORKFLOW)
        if COLUNA_CONFIG_CLIENTE not in col_map:
            col_map[COLUNA_CONFIG_CLIENTE] = 1
            ws.cell(1, 1, COLUNA_CONFIG_CLIENTE)
        if COLUNA_CONFIG_WORKFLOW_D1 not in col_map:
            col_map[COLUNA_CONFIG_WORKFLOW_D1] = 3
            ws.cell(1, 3, COLUNA_CONFIG_WORKFLOW_D1)
        if COLUNA_CONFIG_STATUS not in col_map:
            nova_col = max(col_map.values()) + 1
            col_map[COLUNA_CONFIG_STATUS] = nova_col
            ws.cell(1, nova_col, COLUNA_CONFIG_STATUS)

        proxima_linha = max(ws.max_row or 1, 1) + 1
        for item in pendentes_adicionar:
            wf = item["workflow"]
            cli_bi = str(item.get("cliente", "") or "").strip()
            if cli_bi and _eh_cliente_volumetria_valido(cli_bi):
                ws.cell(proxima_linha, col_map[COLUNA_CONFIG_CLIENTE], cli_bi)
                msg_pendente = "preencha Workflow d-1 antes da próxima execução (Cliente veio da volumetria)"
            else:
                ws.cell(proxima_linha, col_map[COLUNA_CONFIG_CLIENTE], REPLICACAO_AUD_MARCA_PENDENTE)
                msg_pendente = "preencha Cliente e Workflow d-1 antes da próxima execução"
            ws.cell(proxima_linha, col_map[COLUNA_CONFIG_WORKFLOW], wf)
            ws.cell(proxima_linha, col_map[COLUNA_CONFIG_WORKFLOW_D1], REPLICACAO_AUD_MARCA_PENDENTE)
            ws.cell(proxima_linha, col_map[COLUNA_CONFIG_STATUS], REPLICACAO_AUD_MARCA_PENDENTE)
            proxima_linha += 1
            _registrar_pendentes_audit_csv(wf, origem_volumetria, settings=settings)
            resultado["novos"].append(wf)
            warnings.append(
                f"PENDENTE CONFIG | {wf}: registrado na aba Workflow d1 — {msg_pendente}"
            )
            log.warning("Workflow d1 | pendente registrado: %s", wf)

        wb.save(path)
        wb.close()
    except PermissionError:
        for item in pendentes_adicionar:
            wf = item["workflow"]
            resultado["falha_sync"].append(wf)
            warnings.append(
                f"PENDENTE CONFIG | {wf}: Default.xlsx em uso — "
                "feche o Excel e execute novamente para registrar na aba Workflow d1"
            )
    except Exception as exc:
        for item in pendentes_adicionar:
            wf = item["workflow"]
            resultado["falha_sync"].append(wf)
            warnings.append(
                f"PENDENTE CONFIG | {wf}: falha ao gravar Default.xlsx ({exc})"
            )

    return warnings, resultado


def _eh_aba_workflow_d1(nome_aba: str) -> bool:
    chave = _normalizar_nome_aba(nome_aba)
    return chave in {
        _normalizar_nome_aba(ABA_DEFAULT_WORKFLOW_D1),
        _normalizar_nome_aba("workflow d-1"),
        _normalizar_nome_aba("Workflow d-1"),
    }


def _ler_excel_aba(path: Path, sheet_name: str) -> pd.DataFrame:
    with pd.ExcelFile(path, engine="openpyxl") as xl:
        if _eh_aba_workflow_d1(sheet_name):
            for nome in xl.sheet_names:
                if _eh_aba_workflow_d1(nome):
                    return xl.parse(nome)
            raise ValueError(
                f"Aba '{ABA_DEFAULT_WORKFLOW_D1}' não encontrada em {path.name}. Abas: {xl.sheet_names}"
            )
        alvo = sheet_name.strip().casefold()
        for nome in xl.sheet_names:
            if nome.strip().casefold() == alvo:
                return xl.parse(nome)
        raise ValueError(f"Aba '{sheet_name}' não encontrada em {path.name}. Abas: {xl.sheet_names}")


def carregar_mapa_workflow_d1(
    default_path: Optional[Path] = None,
    settings: Optional[dict] = None,
) -> pd.DataFrame:
    """Aba Workflow d1: Workflow -> Cliente, Workflow d-1."""
    paths = _resolver_paths_config(settings)
    path = Path(default_path) if default_path else paths["default"]
    if not path.exists():
        raise FileNotFoundError(f"Default.xlsx não encontrado: {path}")

    raw = _ler_excel_aba(path, ABA_DEFAULT_WORKFLOW_D1)
    col_wf = _find_column(raw, [COLUNA_CONFIG_WORKFLOW, "Workflow"])
    col_cli = _find_column(raw, [COLUNA_CONFIG_CLIENTE, "Cliente", "cliente"])
    col_d1 = _find_column(
        raw,
        [COLUNA_CONFIG_WORKFLOW_D1, "Workflow d-1", "Workflow d1", "Workflow D-1"],
    )
    col_sel = _find_column(
        raw,
        [COLUNA_CONFIG_WORKFLOW_SELENIUM, "Workflow - selenium", "Workflow selenium"],
    )
    col_status = _find_column(raw, [COLUNA_CONFIG_STATUS, "Status"])
    col_fila = _find_column(raw, [COLUNA_CONFIG_FILA, "Fila", "fila"])
    col_usar_csv = _find_column(
        raw,
        [COLUNA_CONFIG_USAR_ARQUIVO_CSV, "usar_arquivo_csv", "Arquivo CSV"],
    )
    col_regra_brflow = _find_column(
        raw,
        [COLUNA_CONFIG_NOME_REGRA_BRFLOW, "Nome regra BRFlow", "Regra BRFlow"],
    )
    if not col_wf:
        raise ValueError(f"Coluna Workflow ausente na aba '{ABA_DEFAULT_WORKFLOW_D1}' de {path.name}")
    if not col_cli:
        raise ValueError(f"Coluna Cliente ausente na aba '{ABA_DEFAULT_WORKFLOW_D1}' de {path.name}")

    df = pd.DataFrame()
    df[COLUNA_CONFIG_WORKFLOW] = raw[col_wf].astype(str).str.strip()
    df[COLUNA_CONFIG_CLIENTE] = raw[col_cli].astype(str).str.strip()
    if col_d1:
        df[COLUNA_CONFIG_WORKFLOW_D1] = raw[col_d1].astype(str).str.strip()
    else:
        df[COLUNA_CONFIG_WORKFLOW_D1] = df[COLUNA_CONFIG_WORKFLOW]
    vazio = ~df[COLUNA_CONFIG_WORKFLOW_D1].astype(bool) | (df[COLUNA_CONFIG_WORKFLOW_D1].str.lower() == "nan")
    df.loc[vazio, COLUNA_CONFIG_WORKFLOW_D1] = df.loc[vazio, COLUNA_CONFIG_WORKFLOW]
    if col_sel:
        df[COLUNA_CONFIG_WORKFLOW_SELENIUM] = raw[col_sel].astype(str).str.strip()
    else:
        df[COLUNA_CONFIG_WORKFLOW_SELENIUM] = df[COLUNA_CONFIG_WORKFLOW]
    df = _preencher_coluna_workflow_selenium(df)
    if col_fila:
        df[COLUNA_CONFIG_FILA] = raw[col_fila].map(_normalizar_fila)
    else:
        df[COLUNA_CONFIG_FILA] = REPLICACAO_FILA_G_AUDITORIA
    df[COLUNA_CONFIG_USAR_ARQUIVO_CSV] = (
        raw[col_usar_csv] if col_usar_csv else pd.Series(pd.NA, index=raw.index, dtype="object")
    )
    df[COLUNA_CONFIG_NOME_REGRA_BRFLOW] = (
        raw[col_regra_brflow].fillna("").astype(str).str.strip() if col_regra_brflow else ""
    )
    df["_wf_key"] = df[COLUNA_CONFIG_WORKFLOW].map(_normalizar_workflow)
    df = df[df[COLUNA_CONFIG_WORKFLOW].astype(bool)]
    if col_status:
        antes_status = len(df)
        inativo = raw[col_status].reindex(df.index).apply(_eh_status_workflow_d1_inativo)
        df = df[~inativo.fillna(False)]
        ignorados = antes_status - len(df)
        if ignorados:
            log.info(
                "Workflow d1 | %d workflow(s) ignorado(s) por Status=False",
                ignorados,
            )
    df = df[~df.apply(_eh_linha_pendente_workflow_d1, axis=1)]
    df = df.drop_duplicates(subset=["_wf_key"], keep="first")
    return df


def carregar_categoria_clientes(
    categoria_path: Optional[Path] = None,
    settings: Optional[dict] = None,
) -> pd.DataFrame:
    """Cliente -> Segmento, Categoria, Meta Cliente (mensal)."""
    paths = _resolver_paths_config(settings)
    path = Path(categoria_path) if categoria_path else paths["categoria"]
    if not path.exists():
        raise FileNotFoundError(f"Categoria.xlsx não encontrado: {path}")

    raw = pd.read_excel(path, engine="openpyxl")
    col_cli = _find_column(raw, [COLUNA_CONFIG_CLIENTE, "Cliente", "cliente"])
    col_seg = _find_column(raw, [COLUNA_CONFIG_SEGMENTO, "Segmento", "segmento"])
    col_cat = _find_column(raw, [COLUNA_CONFIG_CATEGORIA, "Categoria", "categoria"])
    col_meta = _find_column(
        raw,
        [
            COLUNA_CONFIG_META_CLIENTE,
            "Meta",
            "Meta Mensal",
            "Meta Cliente",
            "meta",
            "meta mensal",
        ],
    )
    if not col_cli:
        raise ValueError(f"Coluna Cliente ausente em {path.name}")

    df = pd.DataFrame()
    df[COLUNA_CONFIG_CLIENTE] = raw[col_cli].astype(str).str.strip()
    df[COLUNA_CONFIG_SEGMENTO] = (
        raw[col_seg].astype(str).str.strip() if col_seg else ""
    )
    df[COLUNA_CONFIG_CATEGORIA] = (
        raw[col_cat].astype(str).str.strip() if col_cat else ""
    )
    if col_meta:
        meta_num = pd.to_numeric(raw[col_meta], errors="coerce")
        df[COLUNA_CONFIG_META_CLIENTE] = meta_num
    else:
        df[COLUNA_CONFIG_META_CLIENTE] = pd.NA
    df["_cli_key"] = df[COLUNA_CONFIG_CLIENTE].map(_normalizar_workflow)
    df = df[df[COLUNA_CONFIG_CLIENTE].astype(bool)]
    df = df.drop_duplicates(subset=["_cli_key"], keep="first")
    return df


def validar_metas_categoria_clientes(
    categorias: pd.DataFrame,
    *,
    tolerancia_soma: int = REPLICACAO_META_CLIENTE_TOLERANCIA_SUM,
    capacidade_mensal_estimada: Optional[int] = None,
) -> List[str]:
    """Valida limites mensais de balanceamento por cliente; retorna avisos."""
    warnings: List[str] = []
    if categorias is None or categorias.empty:
        return warnings
    if COLUNA_CONFIG_META_CLIENTE not in categorias.columns:
        warnings.append("Categoria.xlsx: coluna de limite mensal de balanceamento por cliente ausente")
        return warnings

    sem_meta = categorias[
        pd.to_numeric(categorias[COLUNA_CONFIG_META_CLIENTE], errors="coerce").isna()
    ]
    for cli in sem_meta[COLUNA_CONFIG_CLIENTE].tolist():
        warnings.append(
            f"Cliente '{cli}': sem limite mensal de balanceamento em Categoria.xlsx "
            "(workflows do cliente ficam sem teto na redistribuição)"
        )

    limites = pd.to_numeric(categorias[COLUNA_CONFIG_META_CLIENTE], errors="coerce")
    negativas = categorias[limites < 0]
    for cli in negativas[COLUNA_CONFIG_CLIENTE].tolist():
        warnings.append(f"Cliente '{cli}': limite mensal de balanceamento inválido (< 0)")

    # Mantidos apenas por compatibilidade com chamadas antigas. Limites por
    # cliente/workflow não representam capacidade e nunca devem ser somados.
    _ = tolerancia_soma, capacidade_mensal_estimada

    return warnings


def validar_metas_workflows_config(
    config: pd.DataFrame,
    *,
    tolerancia_soma: int = REPLICACAO_META_CLIENTE_TOLERANCIA_SUM,
    capacidade_mensal_estimada: Optional[int] = None,
) -> List[str]:
    """Valida metas individuais usadas como limite de redistribuição.

    A meta herdada do cliente é repetida nos workflows apenas para calcular o
    headroom de cada destino. Esses valores não formam uma capacidade agregada
    e, portanto, nunca devem ser somados ou comparados com a escala mensal.
    """
    warnings: List[str] = []
    if config is None or config.empty:
        return warnings
    if COLUNA_CONFIG_META_CLIENTE not in config.columns:
        return warnings

    # Mantém os argumentos por compatibilidade com chamadas antigas, mas a
    # comparação agregada deixou de representar a regra de negócio.
    _ = tolerancia_soma, capacidade_mensal_estimada
    metas = pd.to_numeric(config[COLUNA_CONFIG_META_CLIENTE], errors="coerce")
    for index, row in config.iterrows():
        workflow = str(row.get(COLUNA_CONFIG_WORKFLOW, "") or "").strip()
        valor = metas.loc[index]
        if pd.isna(valor):
            warnings.append(
                f"Workflow '{workflow}': sem limite mensal de balanceamento (cliente sem limite em Categoria.xlsx)"
            )
        elif float(valor) < 0:
            warnings.append(f"Workflow '{workflow}': limite mensal de balanceamento inválido (< 0)")

    return warnings


def _ler_planilha_data_only(path: Path, sheet_name: Optional[str] = None) -> pd.DataFrame:
    """Lê valores calculados (requer Default salvo no Excel após recálculo)."""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        if sheet_name:
            alvo = sheet_name
            if alvo not in wb.sheetnames:
                alvo_norm = alvo.strip().casefold()
                for nome in wb.sheetnames:
                    if nome.strip().casefold() == alvo_norm:
                        alvo = nome
                        break
                else:
                    raise ValueError(f"Aba '{sheet_name}' não encontrada em {path.name}")
            ws = wb[alvo]
        else:
            ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return pd.DataFrame()
        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        dados = rows[1:]
        return pd.DataFrame(dados, columns=header)
    finally:
        wb.close()


def _resolver_aba_calculadora_padrao(path: Path) -> str:
    with pd.ExcelFile(path, engine="openpyxl") as xl:
        for nome in xl.sheet_names:
            if _eh_aba_calculadora_padrao(nome):
                return nome
    raise ValueError(f"Aba Calculadora Padrão não encontrada em {path.name}. Abas: {xl.sheet_names}")


def _ler_meta_calculadora_padrao(path: Path) -> Dict[str, float]:
    """Lê parâmetros do bloco A/B da aba Calculadora Padrão."""
    from openpyxl import load_workbook

    sheet = _resolver_aba_calculadora_padrao(path)
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[sheet]
        rotulos: Dict[str, float] = {}

        def _registrar(rotulo: Any, valor: Any) -> None:
            if rotulo is None:
                return
            chave = _normalizar_rotulo(str(rotulo))
            num = pd.to_numeric(valor, errors="coerce")
            if pd.notna(num):
                rotulos[chave] = float(num)

        for r in range(1, 20):
            _registrar(ws.cell(r, 1).value, ws.cell(r, 2).value)

        return {
            "dias_uteis": rotulos.get(_normalizar_rotulo("Dias úteis"), 24.0),
            "meta_produ": rotulos.get(_normalizar_rotulo(COLUNA_CONFIG_META_PRODU), 0.0),
            "confianca": rotulos.get(_normalizar_rotulo("Nível de confiança"), 0.99),
            "margin_high": rotulos.get(_normalizar_rotulo("Margem de erro (high)"), 0.0115),
            "margin_low": rotulos.get(_normalizar_rotulo("Margem de erro (low/mid)"), 0.0178),
        }
    finally:
        wb.close()


def _calcular_amostra_total_planilha(total_vol: float, meta: Dict[str, float], categoria: str) -> int:
    """Espelha fórmula da coluna Amostra total (Calculadora Padrão)."""
    if total_vol <= 0:
        return 0
    conf = float(meta.get("confianca", 0.99))
    p = 0.5
    z = conf**2 * p * (1 - p)
    cat = str(categoria or "").strip().lower()
    margin = float(meta.get("margin_high", 0.0115))
    if cat and "high" not in cat and cat not in ("alto", "alta"):
        margin = float(meta.get("margin_low", 0.0178))
    denom = margin**2 + (z / total_vol)
    if denom <= 0:
        return 0
    return int(round(z / denom))


def _calcular_amostra_diaria_planilha(amostra_total: int, meta: Dict[str, float]) -> int:
    """Espelha ROUNDUP(Amostra total / Dias úteis)."""
    dias = max(1, int(meta.get("dias_uteis", 24)))
    if amostra_total <= 0:
        return 0
    return int(math.ceil(amostra_total / dias))


def _ler_amostras_materializadas_calculadora(path: Path) -> pd.DataFrame:
    """Lê Workflow + amostras se a planilha tiver valores calculados (não só fórmulas)."""
    from openpyxl import load_workbook

    sheet = _resolver_aba_calculadora_padrao(path)
    wb = load_workbook(path, data_only=True, read_only=True)
    linhas: List[dict] = []
    try:
        ws = wb[sheet]
        for r in range(2, (ws.max_row or 0) + 1):
            wf = ws.cell(r, 5).value
            if not _eh_workflow_volumetria_valido(wf):
                continue
            diaria = pd.to_numeric(ws.cell(r, 12).value, errors="coerce")
            if pd.isna(diaria) or int(diaria) <= 0:
                continue
            total = pd.to_numeric(ws.cell(r, 11).value, errors="coerce")
            conf = pd.to_numeric(ws.cell(r, 13).value, errors="coerce")
            linhas.append(
                {
                    COLUNA_CONFIG_WORKFLOW: str(wf).strip(),
                    "_wf_key": _normalizar_workflow(wf),
                    "amostra_diaria": int(diaria),
                    "amostra_total": int(total) if pd.notna(total) else 0,
                    "amostra_conf_prod": int(conf) if pd.notna(conf) else int(diaria),
                }
            )
    finally:
        wb.close()
    if not linhas:
        return pd.DataFrame()
    return pd.DataFrame(linhas).drop_duplicates(subset=["_wf_key"], keep="first")


def calcular_amostras_calculadora_padrao(
    path: Path,
    df_workflows: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calcula Amostra total/diária a partir do Total da volumetria e parâmetros da Calculadora Padrão.
    Usado quando a planilha tem fórmulas mas colunas Workflow/Amostra ainda não materializadas.
    """
    meta = _ler_meta_calculadora_padrao(path)
    linhas: List[dict] = []
    for _, row in df_workflows.iterrows():
        total_vol = float(row.get(COLUNA_CONFIG_TOTAL) or 0)
        if total_vol <= 0:
            continue
        categoria = str(row.get(COLUNA_CONFIG_CATEGORIA, "") or "")
        amostra_total = _calcular_amostra_total_planilha(total_vol, meta, categoria)
        amostra_diaria = _calcular_amostra_diaria_planilha(amostra_total, meta)
        if amostra_diaria <= 0:
            continue
        linhas.append(
            {
                "_wf_key": row["_wf_key"],
                "amostra_total": amostra_total,
                "amostra_diaria": amostra_diaria,
                "amostra_conf_prod": amostra_diaria,
            }
        )
    if not linhas:
        return pd.DataFrame()
    log.info(
        "Calculadora Padrão | %d amostra(s) calculada(s) a partir da volumetria (fórmulas Excel)",
        len(linhas),
    )
    return pd.DataFrame(linhas)


def carregar_amostras_default(
    default_path: Optional[Path] = None,
    settings: Optional[dict] = None,
    df_workflows: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Amostras por workflow: valores materializados na planilha ou cálculo pelas fórmulas."""
    paths = _resolver_paths_config(settings)
    path = Path(default_path) if default_path else paths["default"]
    if not path.exists():
        raise FileNotFoundError(f"Default.xlsx não encontrado: {path}")

    cols = ["_wf_key", "amostra_diaria", "amostra_total", "amostra_conf_prod"]
    materializado = _ler_amostras_materializadas_calculadora(path)

    if df_workflows is None or df_workflows.empty:
        if not materializado.empty:
            log.info("Calculadora Padrão | %d amostra(s) lidas (valores materializados)", len(materializado))
            return materializado
        log.warning(
            "Default.xlsx: coluna Workflow vazia na Calculadora Padrão e sem volumetria para calcular. "
            "Abra e salve o arquivo no Excel para materializar fórmulas, ou confira o merge volumetria + Workflow d1."
        )
        return pd.DataFrame(columns=cols)

    keys_needed = set(df_workflows["_wf_key"].astype(str).tolist())
    if materializado.empty:
        calculado = calcular_amostras_calculadora_padrao(path, df_workflows)
        if not calculado.empty:
            return calculado
        log.warning(
            "Default.xlsx: coluna Workflow vazia na Calculadora Padrão e sem volumetria para calcular. "
            "Abra e salve o arquivo no Excel para materializar fórmulas, ou confira o merge volumetria + Workflow d1."
        )
        return pd.DataFrame(columns=cols)

    keys_cobertas = set(materializado["_wf_key"].astype(str).tolist())
    faltantes = keys_needed - keys_cobertas
    if not faltantes:
        log.info("Calculadora Padrão | %d amostra(s) lidas (valores materializados)", len(materializado))
        return materializado[materializado["_wf_key"].isin(keys_needed)].copy()

    df_faltantes = df_workflows[df_workflows["_wf_key"].isin(faltantes)]
    calculado = calcular_amostras_calculadora_padrao(path, df_faltantes)
    partes = [materializado[materializado["_wf_key"].isin(keys_needed)]]
    if not calculado.empty:
        partes.append(calculado)
    hibrido = pd.concat(partes, ignore_index=True).drop_duplicates(subset=["_wf_key"], keep="first")
    log.info(
        "Calculadora Padrão | %d materializada(s) + %d calculada(s) (híbrido)",
        len(materializado[materializado["_wf_key"].isin(keys_needed)]),
        len(calculado),
    )
    return hibrido


def montar_config_replicacao(
    settings: Optional[dict] = None,
    default_path: Optional[Union[str, Path]] = None,
) -> tuple[pd.DataFrame, List[str], str]:
    """
    Monta config a partir de volumetria + Default.xlsx + Categoria.xlsx.
    Retorna (dataframe, warnings, pasta_volumetria).
    """
    settings = settings or {}
    paths = _resolver_paths_config(settings)
    if default_path:
        paths["default"] = Path(default_path)

    warnings: List[str] = []
    fonte_vol = resolver_pasta_volumetria_mais_recente(settings=settings)
    vol = carregar_volumetria(fonte_vol, settings=settings)
    pasta_vol = str(fonte_vol)
    desabilitados = carregar_chaves_workflow_d1_desabilitados(paths["default"], settings=settings)
    if desabilitados:
        antes_vol = len(vol)
        vol = vol[~vol["_wf_key"].astype(str).isin(desabilitados)].copy()
        removidos = antes_vol - len(vol)
        if removidos:
            msg = (
                f"{removidos} workflow(s) ignorado(s): Status=False no Default.xlsx (Workflow d1)"
            )
            warnings.append(msg)
            log.info("Config | %s", msg)
    mapa_d1 = carregar_mapa_workflow_d1(paths["default"], settings=settings)
    categorias = carregar_categoria_clientes(paths["categoria"], settings=settings)

    merged = vol.merge(
        mapa_d1[
            [
                COLUNA_CONFIG_WORKFLOW,
                COLUNA_CONFIG_WORKFLOW_SELENIUM,
                COLUNA_CONFIG_CLIENTE,
                COLUNA_CONFIG_WORKFLOW_D1,
                COLUNA_CONFIG_USAR_ARQUIVO_CSV,
                "_wf_key",
            ]
        ],
        on="_wf_key",
        how="left",
        suffixes=("_vol", "_map"),
    )
    col_wf_map = f"{COLUNA_CONFIG_WORKFLOW}_map"
    col_wf_vol = f"{COLUNA_CONFIG_WORKFLOW}_vol"
    if col_wf_map in merged.columns:
        merged[COLUNA_CONFIG_WORKFLOW] = merged[col_wf_map].fillna(merged.get(col_wf_vol, ""))
        merged = merged.drop(columns=[c for c in (col_wf_vol, col_wf_map) if c in merged.columns])
    sem_mapa_mask = merged[COLUNA_CONFIG_CLIENTE].isna() | (merged[COLUNA_CONFIG_CLIENTE] == "")
    workflows_novos_bi: List[dict] = []
    for _, row in merged.loc[sem_mapa_mask].iterrows():
        wf_vol = str(row.get(col_wf_vol, "") or "").strip() if col_wf_vol in row.index else ""
        wf = wf_vol or str(row.get(COLUNA_CONFIG_WORKFLOW, "") or "").strip()
        if not wf:
            continue
        item: dict = {"workflow": wf, "_wf_key": row["_wf_key"]}
        cli_vol = str(row.get(COLUNA_VOLUMETRIA_CLIENTE, "") or "").strip()
        if cli_vol and _eh_cliente_volumetria_valido(cli_vol):
            item["cliente"] = cli_vol
        workflows_novos_bi.append(item)

    workflows_pendentes_config = [item["workflow"] for item in workflows_novos_bi]
    sync_result = _novo_resultado_sync_pendentes()
    if workflows_novos_bi:
        sync_msgs, sync_result = sincronizar_pendentes_workflow_d1(
            paths["default"],
            workflows_novos_bi,
            origem_volumetria=pasta_vol,
            settings=settings,
        )
        warnings.extend(sync_msgs)

    merged = merged[~sem_mapa_mask].copy()

    if COLUNA_VOLUMETRIA_CLIENTE in merged.columns:
        for _, row in merged.iterrows():
            cli_vol = str(row.get(COLUNA_VOLUMETRIA_CLIENTE, "") or "").strip()
            cli_map = str(row.get(COLUNA_CONFIG_CLIENTE, "") or "").strip()
            if (
                cli_vol
                and cli_map
                and _normalizar_workflow(cli_vol) != _normalizar_workflow(cli_map)
            ):
                wf_nome = str(row.get(COLUNA_CONFIG_WORKFLOW, "") or "").strip()
                warnings.append(
                    f"Cliente BI diverge do Default para {wf_nome}: '{cli_vol}' vs '{cli_map}'"
                )
        merged = merged.drop(columns=[COLUNA_VOLUMETRIA_CLIENTE])

    merged["_cli_key"] = merged[COLUNA_CONFIG_CLIENTE].map(_normalizar_workflow)
    merged = merged.merge(
        categorias[[COLUNA_CONFIG_SEGMENTO, COLUNA_CONFIG_CATEGORIA, "_cli_key"]],
        on="_cli_key",
        how="left",
    )
    sem_cat = merged[COLUNA_CONFIG_SEGMENTO].isna() | (merged[COLUNA_CONFIG_SEGMENTO] == "")
    for cli in merged.loc[sem_cat, COLUNA_CONFIG_CLIENTE].unique().tolist():
        warnings.append(f"Cliente '{cli}': sem Segmento/Categoria em Categoria.xlsx")

    # Workflows configurados no Default ausentes na volumetria do dia (CSV vazio / limpeza BRFlow)
    keys_merged = set(merged["_wf_key"].astype(str))
    extras_mapa = mapa_d1[~mapa_d1["_wf_key"].astype(str).isin(keys_merged)].copy()
    if not extras_mapa.empty:
        linhas_extra = []
        for _, mrow in extras_mapa.iterrows():
            linhas_extra.append({
                COLUNA_CONFIG_WORKFLOW: mrow[COLUNA_CONFIG_WORKFLOW],
                COLUNA_CONFIG_WORKFLOW_SELENIUM: mrow[COLUNA_CONFIG_WORKFLOW_SELENIUM],
                COLUNA_CONFIG_WORKFLOW_D1: mrow[COLUNA_CONFIG_WORKFLOW_D1],
                COLUNA_CONFIG_CLIENTE: mrow[COLUNA_CONFIG_CLIENTE],
                COLUNA_CONFIG_USAR_ARQUIVO_CSV: mrow.get(
                    COLUNA_CONFIG_USAR_ARQUIVO_CSV, pd.NA
                ),
                COLUNA_CONFIG_AUTOMATICOS: 0,
                COLUNA_CONFIG_MANUAIS: 0,
                COLUNA_CONFIG_TOTAL: 0,
                "_wf_key": mrow["_wf_key"],
            })
        df_extra = pd.DataFrame(linhas_extra)
        df_extra["_cli_key"] = df_extra[COLUNA_CONFIG_CLIENTE].map(_normalizar_workflow)
        df_extra = df_extra.merge(
            categorias[[COLUNA_CONFIG_SEGMENTO, COLUNA_CONFIG_CATEGORIA, "_cli_key"]],
            on="_cli_key",
            how="left",
        )
        merged = pd.concat([merged, df_extra], ignore_index=True)
        warnings.append(
            f"{len(df_extra)} workflow(s) do Default ausente(s) na volumetria de hoje "
            "(incluídos para CSV vazio / limpeza BRFlow)"
        )
        log.info(
            "Config | %d workflow(s) do Default fora da volumetria (CSV vazio diário)",
            len(df_extra),
        )

    amostras = carregar_amostras_default(
        paths["default"],
        settings=settings,
        df_workflows=merged,
    )

    merged = merged.merge(
        amostras[
            [
                "_wf_key",
                "amostra_diaria",
                "amostra_total",
                "amostra_conf_prod",
            ]
        ],
        on="_wf_key",
        how="left",
    )
    sem_amostra = merged["amostra_diaria"].isna()
    for wf in merged.loc[sem_amostra, COLUNA_CONFIG_WORKFLOW].tolist():
        warnings.append(
            f"{wf}: sem amostra no Default.xlsx (Amostra diária = 0; CSV vazio para limpeza BRFlow)"
        )
    merged["amostra_diaria"] = pd.to_numeric(merged["amostra_diaria"], errors="coerce").fillna(0).astype(int)
    merged["amostra_total"] = pd.to_numeric(merged["amostra_total"], errors="coerce").fillna(0).astype(int)
    merged["amostra_conf_prod"] = pd.to_numeric(merged["amostra_conf_prod"], errors="coerce").fillna(0).astype(int)

    amostra_zero = int((merged["amostra_diaria"] == 0).sum())
    if amostra_zero:
        warnings.append(
            f"{amostra_zero} workflow(s) com amostra diária = 0 "
            "(CSV vazio para limpeza BRFlow)"
        )

    if merged.empty:
        detalhe = "; ".join(warnings[:8]) if warnings else "sem detalhes adicionais"
        raise ValueError(
            "Nenhum workflow válido após merge (volumetria + Default + Categoria). "
            f"Causas prováveis: workflow ausente na aba Workflow d1, amostra diária zerada, "
            f"ou volumetria sem match. Avisos: {detalhe}"
        )

    out = merged[
        [
            COLUNA_CONFIG_WORKFLOW,
            COLUNA_CONFIG_WORKFLOW_SELENIUM,
            COLUNA_CONFIG_WORKFLOW_D1,
            COLUNA_CONFIG_CLIENTE,
            COLUNA_CONFIG_SEGMENTO,
            COLUNA_CONFIG_CATEGORIA,
            COLUNA_CONFIG_USAR_ARQUIVO_CSV,
            COLUNA_CONFIG_AUTOMATICOS,
            COLUNA_CONFIG_MANUAIS,
            COLUNA_CONFIG_TOTAL,
            "amostra_diaria",
            "amostra_total",
            "amostra_conf_prod",
        ]
    ].copy()
    out[COLUNA_CONFIG_SEGMENTO] = out[COLUNA_CONFIG_SEGMENTO].fillna("").astype(str)
    out[COLUNA_CONFIG_CATEGORIA] = out[COLUNA_CONFIG_CATEGORIA].fillna("").astype(str)

    meta_calc = _ler_meta_calculadora_padrao(paths["default"])
    if meta_calc.get("meta_produ", 0) > 0:
        out.attrs["meta_produ_resumo"] = float(meta_calc["meta_produ"])
        log.info("Default.xlsx: meta produ=%.2f (Calculadora Padrão)", float(meta_calc["meta_produ"]))
    else:
        resumo = _ler_resumo_config_excel(paths["default"])
        meta_resumo = _buscar_valor_resumo(resumo, _ROTULOS_META_PRODU)
        if meta_resumo is not None:
            num = pd.to_numeric(meta_resumo, errors="coerce")
            if pd.notna(num) and float(num) > 0:
                out.attrs["meta_produ_resumo"] = float(num)
                log.info("Default.xlsx: meta produ=%.2f (bloco resumo)", float(num))

    out.attrs["pasta_volumetria"] = str(pasta_vol)
    out.attrs["default_xlsx_path"] = str(paths["default"])
    out.attrs["config_warnings"] = warnings
    out.attrs["workflows_pendentes_config"] = workflows_pendentes_config
    out.attrs["workflows_pendentes_novos"] = list(sync_result["novos"])
    out.attrs["workflows_pendentes_existentes"] = list(sync_result["existentes"])
    out.attrs["workflows_pendentes_falha_sync"] = list(sync_result["falha_sync"])
    for msg in warnings:
        log.warning("Config | %s", msg)

    log.info(
        "Config montada | %d workflow(s) | volumetria=%s",
        len(out),
        Path(pasta_vol).name,
    )
    return out, warnings, pasta_vol


def _carregar_config_planilha_unica(path: Path) -> pd.DataFrame:
    """Compatibilidade: planilha única com Workflow, Workflow d-1 e Amostra diária."""
    if not path.exists():
        raise FileNotFoundError(f"Planilha de configuração não encontrada: {path}")

    raw = pd.read_excel(path, engine="openpyxl")
    col_workflow = _find_column(raw, [COLUNA_CONFIG_WORKFLOW, "Workflow"])
    if not col_workflow:
        raise ValueError(f"Coluna Workflow ausente em {path.name}")

    col_diaria = _find_column(
        raw,
        [COLUNA_CONFIG_AMOSTRA_DIARIA, "Amostra diaria", "Amostra Diária", COLUNA_CONFIG_AMOSTRA],
    )
    if not col_diaria:
        raise ValueError(
            f"Coluna de amostra diária ausente em {path.name} "
            f"(esperado '{COLUNA_CONFIG_AMOSTRA_DIARIA}' ou '{COLUNA_CONFIG_AMOSTRA}')"
        )

    col_wf_d1 = _find_column(
        raw,
        [COLUNA_CONFIG_WORKFLOW_D1, "Workflow d-1", "Workflow d1", "Workflow D-1"],
    )
    col_usar_csv = _find_column(
        raw,
        [COLUNA_CONFIG_USAR_ARQUIVO_CSV, "usar_arquivo_csv", "Arquivo CSV"],
    )

    df = pd.DataFrame()
    df[COLUNA_CONFIG_WORKFLOW] = raw[col_workflow].astype(str).str.strip()
    if col_wf_d1:
        df[COLUNA_CONFIG_WORKFLOW_D1] = raw[col_wf_d1].astype(str).str.strip()
        vazio = ~df[COLUNA_CONFIG_WORKFLOW_D1].astype(bool) | (
            df[COLUNA_CONFIG_WORKFLOW_D1].str.lower() == "nan"
        )
        df.loc[vazio, COLUNA_CONFIG_WORKFLOW_D1] = df.loc[vazio, COLUNA_CONFIG_WORKFLOW]
    else:
        df[COLUNA_CONFIG_WORKFLOW_D1] = df[COLUNA_CONFIG_WORKFLOW]
    df[COLUNA_CONFIG_USAR_ARQUIVO_CSV] = (
        raw[col_usar_csv] if col_usar_csv else pd.Series(pd.NA, index=raw.index, dtype="object")
    )
    df["amostra_diaria"] = pd.to_numeric(raw[col_diaria], errors="coerce").fillna(0).astype(int)
    df = df[df[COLUNA_CONFIG_WORKFLOW].astype(bool)]
    df = df[df["amostra_diaria"] > 0]
    if df.empty:
        raise ValueError(f"Nenhuma linha válida em {path.name} (Workflow + amostra diária > 0)")

    resumo = _ler_resumo_config_excel(path)
    meta_resumo = _buscar_valor_resumo(resumo, _ROTULOS_META_PRODU)
    if meta_resumo is not None:
        num = pd.to_numeric(meta_resumo, errors="coerce")
        if pd.notna(num) and float(num) > 0:
            df.attrs["meta_produ_resumo"] = float(num)

    return df


def carregar_config_auditoria(
    caminho: Optional[Union[str, Path]] = None,
    settings: Optional[dict] = None,
) -> pd.DataFrame:
    """Monta config (fontes em config/) ou planilha única legada para testes."""
    settings = dict(settings or {})
    if caminho:
        path = Path(caminho)
        if path.is_file() and not settings.get("replicacao_usar_fontes_config"):
            return _carregar_config_planilha_unica(path)
        if path.is_file():
            settings["replicacao_config_default"] = str(path)
        elif path.is_dir():
            settings["replicacao_config_base"] = str(path)

    df, warnings, pasta_vol = montar_config_replicacao(settings=settings)
    df.attrs["config_warnings"] = warnings
    df.attrs["pasta_volumetria"] = pasta_vol
    return df


def _meta_colunas_config(row: pd.Series, pasta_volumetria: str = "") -> dict:
    """Colunas extras de volumetria/cliente para resumo e plano detalhado."""
    return {
        COLUNA_CONFIG_CLIENTE: str(row.get(COLUNA_CONFIG_CLIENTE, "") or ""),
        COLUNA_CONFIG_SEGMENTO: str(row.get(COLUNA_CONFIG_SEGMENTO, "") or ""),
        COLUNA_CONFIG_CATEGORIA: str(row.get(COLUNA_CONFIG_CATEGORIA, "") or ""),
        COLUNA_CONFIG_META_CLIENTE: _as_int(row.get(COLUNA_CONFIG_META_CLIENTE, 0)),
        COLUNA_CONFIG_AUTOMATICOS: _as_int(row.get(COLUNA_CONFIG_AUTOMATICOS, 0)),
        COLUNA_CONFIG_MANUAIS: _as_int(row.get(COLUNA_CONFIG_MANUAIS, 0)),
        COLUNA_CONFIG_TOTAL: _as_int(row.get(COLUNA_CONFIG_TOTAL, 0)),
        "Amostra Total Config": _as_int(row.get("amostra_total", 0)),
        "Amostra Conf Prod Config": _as_int(row.get("amostra_conf_prod", 0)),
        "Pasta Volumetria": pasta_volumetria,
    }


def carregar_meta_produ(
    df_config: pd.DataFrame,
    settings: Optional[dict] = None,
    *,
    fila: Optional[str] = None,
) -> float:
    """Meta de produção por auditor.

    ``fila`` Documentoscopia 3.1 usa ``meta_produ_case`` (fallback: ``meta_produ``).
    Demais filas / sem fila usam ``meta_produ`` (BRFlow / G auditoria).
    """
    settings = settings or {}
    fila_norm = _normalizar_fila(fila) if fila is not None else ""

    if fila_norm == REPLICACAO_FILA_DOCUMENTOSCOPIA_31:
        override_case = settings.get("meta_produ_case", "")
        if override_case not in (None, ""):
            try:
                valor = float(override_case)
                if valor > 0:
                    return valor
            except (TypeError, ValueError):
                pass
        calc = settings.get("_calculadora_params") or {}
        if isinstance(calc, dict) and calc.get("meta_produ_case") not in (None, ""):
            try:
                valor = float(calc["meta_produ_case"])
                if valor > 0:
                    return valor
            except (TypeError, ValueError):
                pass

    if fila_norm == REPLICACAO_FILA_BIO:
        override_bio = settings.get("meta_produ_bio", "")
        if override_bio not in (None, ""):
            try:
                valor = float(override_bio)
                if valor > 0:
                    return valor
            except (TypeError, ValueError):
                pass

    if fila_norm == REPLICACAO_FILA_REDOC:
        override_redoc = settings.get("meta_produ_redoc", "")
        if override_redoc not in (None, ""):
            try:
                valor = float(override_redoc)
                if valor > 0:
                    return valor
            except (TypeError, ValueError):
                pass

    override = settings.get("meta_produ", "")
    if override not in (None, ""):
        try:
            valor = float(override)
            if valor > 0:
                return valor
        except (TypeError, ValueError):
            pass

    meta_resumo = df_config.attrs.get("meta_produ_resumo")
    if meta_resumo is not None:
        try:
            valor = float(meta_resumo)
            if valor > 0:
                return valor
        except (TypeError, ValueError):
            pass

    if "_meta_produ" in df_config.columns:
        serie = pd.to_numeric(df_config["_meta_produ"], errors="coerce").dropna()
        if not serie.empty:
            return float(serie.iloc[0])

    raise ValueError(
        "meta produ não encontrada na planilha nem em settings. "
        f"Informe '{COLUNA_CONFIG_META_PRODU}' no bloco resumo (colunas A/B) do Excel "
        "ou meta_produ / meta_produ_case na configuração."
    )


def _parse_data_escala(val: Any) -> Optional[datetime]:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    if isinstance(val, datetime):
        return val.replace(hour=0, minute=0, second=0, microsecond=0)
    texto = str(val).strip()
    if not texto:
        return None
    compacto = re.sub(r"[^0-9]", "", texto)
    if len(compacto) >= 8:
        try:
            return datetime.strptime(compacto[:8], "%Y%m%d")
        except ValueError:
            pass
    try:
        return pd.to_datetime(val, dayfirst=True).to_pydatetime().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    except Exception:
        return None


def _resolver_data_escala_replicacao(
    data_ref: datetime,
    settings: Optional[dict] = None,
) -> datetime:
    """
    Data usada no CSV de escala: dia da replicação (não o parquet D-1).

    Execução normal: amanhã em relação a hoje.
    Com replicacao_aud_data_ref: dia seguinte ao parquet informado.
    """
    settings = settings or {}
    custom = str(settings.get("replicacao_aud_data_escala", "") or "").strip()
    if custom:
        return datetime.strptime(custom, "%Y%m%d").replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    base = data_ref.replace(hour=0, minute=0, second=0, microsecond=0)
    if settings.get("replicacao_aud_data_ref"):
        return base + timedelta(days=1)

    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return hoje + timedelta(days=1)


def _auditores_de_escala_df(
    df: pd.DataFrame,
    data_escala: datetime,
    col_alvo: str,
) -> int:
    """Lê auditores ativos de DataFrame de escala (snapshot/banco)."""
    col_data = _find_column(df, [COLUNA_ESCALA_DATA, "Data", "dia", "Dia"])
    aliases_qtd = [col_alvo]
    if col_alvo == COLUNA_ESCALA_AUDITORES:
        aliases_qtd.extend(["auditores", "Auditores", "qtd", "quantidade"])
    elif col_alvo == COLUNA_ESCALA_AUDITORES_CASE:
        aliases_qtd.extend(["auditores case", "Auditores Case", "auditores_case"])
    elif col_alvo == COLUNA_ESCALA_AUDITORES_BIO:
        aliases_qtd.extend(["auditores bio", "Auditores Bio", "auditores_bio"])
    elif col_alvo == COLUNA_ESCALA_AUDITORES_REDOC:
        aliases_qtd.extend(["auditores redoc", "Auditores Redoc", "auditores_redoc"])
    col_qtd = _find_column(df, aliases_qtd)
    if not col_qtd and col_alvo == COLUNA_ESCALA_AUDITORES_CASE:
        col_qtd = _find_column(
            df,
            [COLUNA_ESCALA_AUDITORES, "auditores", "Auditores", "qtd", "quantidade"],
        )
    if not col_data or not col_qtd:
        raise ValueError(
            f"Escala inválida (banco): colunas obrigatórias '{COLUNA_ESCALA_DATA}' e '{col_alvo}'"
        )

    registros: List[tuple[datetime, int]] = []
    for _, row in df.iterrows():
        dt = _parse_data_escala(row[col_data])
        if dt is None:
            continue
        try:
            qtd = int(row[col_qtd])
        except (TypeError, ValueError):
            continue
        if qtd < 0:
            continue
        registros.append((dt.replace(hour=0, minute=0, second=0, microsecond=0), qtd))

    if not registros:
        raise ValueError("Nenhuma linha válida na escala (banco)")

    alvo = data_escala.replace(hour=0, minute=0, second=0, microsecond=0)
    por_data = {dt: qtd for dt, qtd in registros}
    if alvo in por_data:
        log.info(
            "Auditores ativos (%s) para %s: %d (escala banco)",
            col_alvo,
            alvo.strftime("%d/%m/%Y"),
            por_data[alvo],
        )
        return por_data[alvo]

    raise EscalaAuditoresAusenteError(
        f"Escala ausente para o dia da replicação {alvo.strftime('%d/%m/%Y')} ({alvo.strftime('%Y%m%d')}). "
        f"Cadastre a escala no portal (Planejamento → Automações)."
    )


def carregar_escala_auditores(
    data_escala: datetime,
    path: Optional[Union[str, Path]] = None,
    settings: Optional[dict] = None,
    coluna_auditores: Optional[str] = None,
) -> int:
    """Retorna auditores ativos para a data informada (settings > CSV / snapshot DB)."""
    settings = settings or {}
    col_alvo = coluna_auditores or COLUNA_ESCALA_AUDITORES
    usar_override = col_alvo == COLUNA_ESCALA_AUDITORES

    if usar_override:
        override = settings.get("auditores_ativos", "")
        if override not in (None, ""):
            try:
                n = int(override)
                if n >= 0:
                    log.info("Auditores ativos (override settings): %d", n)
                    return n
            except (TypeError, ValueError):
                raise ValueError(f"auditores_ativos inválido em settings: {override!r}")

    escala_df = settings.get("_escala_df")
    if escala_df is None:
        try:
            from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa

            if is_fonte_banco_ativa(settings):
                from app.bots.replicacao_d1_db_bridge import load_planning_dataframes

                load_planning_dataframes(settings)
                escala_df = settings.get("_escala_df")
        except Exception:
            escala_df = None

    if escala_df is not None and not getattr(escala_df, "empty", True):
        return _auditores_de_escala_df(escala_df, data_escala, col_alvo)

    try:
        from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa

        if is_fonte_banco_ativa(settings):
            raise EscalaAuditoresAusenteError(
                f"Escala ausente no banco para {data_escala.strftime('%d/%m/%Y')}. "
                "Cadastre em Planejamento → Automações (fonte_banco_ativa=True; sem fallback CSV)."
            )
    except EscalaAuditoresAusenteError:
        raise
    except Exception:
        pass

    csv_path = path or _resolver_csv_escala_auditores(settings)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Arquivo de escala de auditores não encontrado: {csv_path}. "
            f"Crie {ESCALA_AUDITORES_ARQUIVO} em {PASTA_REPLICACAO_AUD_ESCALA} "
            f"(colunas {COLUNA_ESCALA_DATA} e {col_alvo}) "
            "ou informe auditores_ativos na configuração."
        )

    df = pd.read_csv(csv_path, sep=None, engine="python", encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]
    col_data = _find_column(df, [COLUNA_ESCALA_DATA, "Data", "dia", "Dia"])
    aliases_qtd = [col_alvo]
    if col_alvo == COLUNA_ESCALA_AUDITORES:
        aliases_qtd.extend(["auditores", "Auditores", "qtd", "quantidade"])
    elif col_alvo == COLUNA_ESCALA_AUDITORES_CASE:
        aliases_qtd.extend(["auditores case", "Auditores Case", "auditores_case"])
    elif col_alvo == COLUNA_ESCALA_AUDITORES_BIO:
        aliases_qtd.extend(["auditores bio", "Auditores Bio", "auditores_bio"])
    elif col_alvo == COLUNA_ESCALA_AUDITORES_REDOC:
        aliases_qtd.extend(["auditores redoc", "Auditores Redoc", "auditores_redoc"])
    col_qtd = _find_column(df, aliases_qtd)
    if not col_qtd and col_alvo == COLUNA_ESCALA_AUDITORES_CASE:
        log.warning(
            "Coluna %s ausente em %s; usando %s como fallback",
            col_alvo,
            csv_path.name,
            COLUNA_ESCALA_AUDITORES,
        )
        col_qtd = _find_column(
            df,
            [COLUNA_ESCALA_AUDITORES, "auditores", "Auditores", "qtd", "quantidade"],
        )
    if not col_data or not col_qtd:
        raise ValueError(
            f"CSV de escala inválido ({csv_path.name}): "
            f"colunas obrigatórias '{COLUNA_ESCALA_DATA}' e '{col_alvo}'"
        )

    registros: List[tuple[datetime, int]] = []
    for _, row in df.iterrows():
        dt = _parse_data_escala(row[col_data])
        if dt is None:
            continue
        try:
            qtd = int(row[col_qtd])
        except (TypeError, ValueError):
            continue
        if qtd < 0:
            continue
        registros.append((dt.replace(hour=0, minute=0, second=0, microsecond=0), qtd))

    if not registros:
        raise ValueError(f"Nenhuma linha válida em {csv_path.name}")

    alvo = data_escala.replace(hour=0, minute=0, second=0, microsecond=0)
    por_data = {dt: qtd for dt, qtd in registros}

    if alvo in por_data:
        log.info(
            "Auditores ativos (%s) para %s: %d (CSV %s)",
            col_alvo,
            alvo.strftime("%d/%m/%Y"),
            por_data[alvo],
            csv_path.name,
        )
        return por_data[alvo]

    raise EscalaAuditoresAusenteError(
        f"Escala ausente para o dia da replicação {alvo.strftime('%d/%m/%Y')} ({alvo.strftime('%Y%m%d')}). "
        f"Adicione a linha em {csv_path} "
        f"(colunas {COLUNA_ESCALA_DATA}, {col_alvo})."
    )


def validar_escala_replicacao_pre_exec(
    settings: Optional[dict] = None,
    df_config: Optional[pd.DataFrame] = None,
) -> None:
    """Valida escala para o dia da replicação antes de iniciar o robô."""
    settings = settings or {}
    if not bool(settings.get("usar_escala_auditores", REPLICACAO_AUD_USAR_ESCALA_DEFAULT)):
        return

    override = settings.get("auditores_ativos", "")
    if override not in (None, ""):
        return

    data_ref = datetime.now() - timedelta(days=1)
    raw_ref = str(settings.get("replicacao_aud_data_ref", "") or "").strip()
    if raw_ref:
        data_ref = datetime.strptime(raw_ref, "%Y%m%d")

    data_escala = _resolver_data_escala_replicacao(data_ref, settings=settings)
    carregar_escala_auditores(data_escala, settings=settings)
    if df_config is None:
        return
    filas_presentes = {
        _normalizar_fila(v)
        for v in df_config.get(COLUNA_CONFIG_FILA, pd.Series(dtype=object)).tolist()
    }
    if REPLICACAO_FILA_DOCUMENTOSCOPIA_31 in filas_presentes:
        carregar_escala_auditores(
            data_escala,
            settings=settings,
            coluna_auditores=COLUNA_ESCALA_AUDITORES_CASE,
        )
    if REPLICACAO_FILA_BIO in filas_presentes:
        carregar_escala_auditores(
            data_escala,
            settings=settings,
            coluna_auditores=COLUNA_ESCALA_AUDITORES_BIO,
        )
    if REPLICACAO_FILA_REDOC in filas_presentes:
        carregar_escala_auditores(
            data_escala,
            settings=settings,
            coluna_auditores=COLUNA_ESCALA_AUDITORES_REDOC,
        )


def calcular_amostras_por_capacidade(
    df_config: pd.DataFrame,
    auditores_ativos: int,
    meta_produ: float,
) -> tuple[pd.DataFrame, InfoCapacidade]:
    """
    Aplica fórmula Excel:
    capacidade = auditores * meta_produ
    amostra_ajustada = ARRED(amostra_diaria * capacidade / soma_amostra_diaria)
    """
    if auditores_ativos < 0:
        raise ValueError("auditores_ativos deve ser >= 0")
    if meta_produ <= 0:
        raise ValueError("meta_produ deve ser > 0")

    out = df_config.copy()
    soma = int(out["amostra_diaria"].sum())
    if auditores_ativos == 0:
        out["amostra_ajustada"] = 0
        info = InfoCapacidade(
            auditores_ativos=0,
            meta_produ=meta_produ,
            capacidade_produtiva=0.0,
            soma_amostra_diaria=soma,
            fator_capacidade=0.0,
        )
        log.info(
            "Capacidade produtiva | auditores=0 | amostras zeradas para %d workflow(s)",
            len(out),
        )
        return out, info

    capacidade = float(auditores_ativos * meta_produ)
    fator = capacidade / soma if soma > 0 else 0.0
    out["amostra_ajustada"] = (
        (out["amostra_diaria"] * fator).round(0).fillna(0).astype(int)
    )

    info = InfoCapacidade(
        auditores_ativos=auditores_ativos,
        meta_produ=meta_produ,
        capacidade_produtiva=capacidade,
        soma_amostra_diaria=soma,
        fator_capacidade=fator,
    )
    log.info(
        "Capacidade produtiva | auditores=%d | meta=%.2f | capacidade=%.0f | soma_diaria=%d | fator=%.4f",
        auditores_ativos,
        meta_produ,
        capacidade,
        soma,
        fator,
    )
    return out, info


def aplicar_escala_auditores_config(
    df_config: pd.DataFrame,
    data_ref: datetime,
    settings: Optional[dict] = None,
) -> tuple[pd.DataFrame, InfoCapacidade]:
    """Aplica ou ignora escala de auditores conforme settings."""
    settings = settings or {}
    usar_escala = bool(settings.get("usar_escala_auditores", REPLICACAO_AUD_USAR_ESCALA_DEFAULT))

    if not usar_escala:
        out = df_config.copy()
        out["amostra_ajustada"] = (
            pd.to_numeric(out["amostra_diaria"], errors="coerce").fillna(0).astype(int)
        )
        soma = int(out["amostra_diaria"].sum())
        info = InfoCapacidade(
            auditores_ativos=0,
            meta_produ=0.0,
            capacidade_produtiva=float(soma),
            soma_amostra_diaria=soma,
            fator_capacidade=1.0,
            usar_escala=False,
            data_escala=None,
        )
        log.info("Escala de auditores desabilitada; usando amostra diária sem ajuste de capacidade")
        return out, info

    data_escala = _resolver_data_escala_replicacao(data_ref, settings=settings)
    log.info(
        "Escala de auditores | parquet D-1=%s | dia da replicação (escala)=%s",
        data_ref.strftime("%d/%m/%Y"),
        data_escala.strftime("%d/%m/%Y"),
    )
    auditores = carregar_escala_auditores(data_escala, settings=settings)
    meta = carregar_meta_produ(df_config, settings=settings)
    out, info = calcular_amostras_por_capacidade(df_config, auditores, meta)
    info.data_escala = data_escala
    return out, info


def aplicar_escala_auditores_config_por_fila(
    df_config: pd.DataFrame,
    data_ref: datetime,
    settings: Optional[dict] = None,
) -> tuple[pd.DataFrame, InfoCapacidade]:
    """Aplica escala de auditores por fila (G auditoria vs Documentoscopia 3.1 / Bio / Redoc)."""
    if COLUNA_CONFIG_FILA not in df_config.columns or not config_usa_escala_por_fila(df_config):
        return aplicar_escala_auditores_config(df_config, data_ref, settings=settings)

    settings = settings or {}
    usar_escala = bool(settings.get("usar_escala_auditores", REPLICACAO_AUD_USAR_ESCALA_DEFAULT))

    if not usar_escala:
        return aplicar_escala_auditores_config(df_config, data_ref, settings=settings)

    data_escala = _resolver_data_escala_replicacao(data_ref, settings=settings)
    log.info(
        "Escala de auditores por fila | parquet D-1=%s | dia da replicação (escala)=%s",
        data_ref.strftime("%d/%m/%Y"),
        data_escala.strftime("%d/%m/%Y"),
    )

    meta = carregar_meta_produ(df_config, settings=settings)
    meta_case = carregar_meta_produ(df_config, settings=settings, fila=REPLICACAO_FILA_DOCUMENTOSCOPIA_31)
    meta_bio = carregar_meta_produ(df_config, settings=settings, fila=REPLICACAO_FILA_BIO)
    meta_redoc = carregar_meta_produ(df_config, settings=settings, fila=REPLICACAO_FILA_REDOC)
    partes: List[pd.DataFrame] = []
    auditores_g = 0
    auditores_case = 0
    auditores_bio = 0
    auditores_redoc = 0
    soma_total = 0
    capacidade_total = 0.0

    filas_escala = (
        REPLICACAO_FILA_G_AUDITORIA,
        REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
        REPLICACAO_FILA_BIO,
        REPLICACAO_FILA_REDOC,
    )
    meta_por_fila = {
        REPLICACAO_FILA_G_AUDITORIA: meta,
        REPLICACAO_FILA_DOCUMENTOSCOPIA_31: meta_case,
        REPLICACAO_FILA_BIO: meta_bio,
        REPLICACAO_FILA_REDOC: meta_redoc,
    }

    for fila in filas_escala:
        mask = df_config[COLUNA_CONFIG_FILA].map(_normalizar_fila) == fila
        sub = df_config.loc[mask].copy()
        if sub.empty:
            continue
        col_escala = _coluna_escala_por_fila(fila)
        auditores = carregar_escala_auditores(
            data_escala,
            settings=settings,
            coluna_auditores=col_escala,
        )
        meta_fila = meta_por_fila[fila]
        sub_out, info_sub = calcular_amostras_por_capacidade(sub, auditores, meta_fila)
        partes.append(sub_out)
        soma_total += info_sub.soma_amostra_diaria
        capacidade_total += info_sub.capacidade_produtiva
        if fila == REPLICACAO_FILA_DOCUMENTOSCOPIA_31:
            auditores_case = auditores
        elif fila == REPLICACAO_FILA_BIO:
            auditores_bio = auditores
        elif fila == REPLICACAO_FILA_REDOC:
            auditores_redoc = auditores
        else:
            auditores_g = auditores
        log.info(
            "Escala fila %s | auditores=%d | meta_produ=%.2f | workflows=%d | soma_diaria=%d",
            fila,
            auditores,
            meta_fila,
            len(sub),
            info_sub.soma_amostra_diaria,
        )

    if not partes:
        return aplicar_escala_auditores_config(df_config, data_ref, settings=settings)

    out = df_config.copy()
    out["amostra_ajustada"] = 0
    for parte in partes:
        out.loc[parte.index, "amostra_ajustada"] = parte["amostra_ajustada"]
    fator = capacidade_total / soma_total if soma_total > 0 else 1.0
    info = InfoCapacidade(
        auditores_ativos=auditores_g,
        meta_produ=meta,
        capacidade_produtiva=capacidade_total,
        soma_amostra_diaria=soma_total,
        fator_capacidade=fator,
        data_escala=data_escala,
        auditores_ativos_case=auditores_case,
        meta_produ_case=meta_case,
        auditores_ativos_bio=auditores_bio,
        auditores_ativos_redoc=auditores_redoc,
    )
    return out, info


def _iterar_candidatos_parquet(pasta: Path) -> List[Path]:
    return sorted(pasta.glob(f"{PREFIXO_DETALHADO_FINAL}*.parquet"), reverse=True)


def _resolver_parquet_legivel(
    candidatos: List[Path],
    *,
    alvo: Path,
    motivo: str,
) -> Optional[Path]:
    for candidato in candidatos:
        if parquet_legivel(candidato):
            if candidato != alvo:
                log.warning("%s; usando: %s", motivo, candidato.name)
            return candidato
        log.debug("Ignorando parquet ilegível: %s", candidato.name)
    return None


def resolver_parquet_d1(
    data_ref: Optional[datetime] = None,
    pasta: Optional[Path] = None,
    fallback_ultimo: bool = False,
) -> Path:
    """Resolve o parquet do dia anterior (D-1)."""
    pasta = pasta or PASTA_DETALHADO_D1
    data_ref = data_ref or (datetime.now() - timedelta(days=1))
    nome = f"{PREFIXO_DETALHADO_FINAL}{data_ref.strftime('%Y%m%d')}.parquet"
    caminho = pasta / nome

    if caminho.exists() and parquet_legivel(caminho):
        return caminho

    if caminho.exists() and not parquet_legivel(caminho):
        log.warning(
            "Parquet D-1 existe mas não é legível (%s); possível placeholder OneDrive",
            caminho.name,
        )

    if fallback_ultimo:
        candidatos = _iterar_candidatos_parquet(pasta)
        if caminho.exists() and caminho not in candidatos:
            candidatos.insert(0, caminho)
        motivo = (
            f"Parquet D-1 ilegível ({caminho.name})"
            if caminho.exists()
            else f"Parquet D-1 não encontrado ({caminho.name})"
        )
        escolhido = _resolver_parquet_legivel(candidatos, alvo=caminho, motivo=motivo)
        if escolhido is not None:
            return escolhido

    if caminho.exists():
        raise FileNotFoundError(
            f"Parquet D-1 inacessível no OneDrive: {caminho}. "
            "Use 'Manter sempre neste dispositivo' no Explorer, aguarde a sincronização "
            "ou habilite fallback_ultimo_parquet em settings."
        )

    raise FileNotFoundError(
        f"Parquet D-1 não encontrado: {caminho}. "
        "Execute a rotina de detalhado ou habilite fallback_ultimo_parquet em settings."
    )


def _validar_parquet(df: pd.DataFrame) -> None:
    faltantes = [c for c in COLUNAS_PARQUET_OBRIGATORIAS if c not in df.columns]
    if faltantes:
        raise ValueError(f"Parquet sem colunas obrigatórias: {faltantes}")


def _parse_data_analise(serie: pd.Series) -> pd.Series:
    """Converte Data de Análise para datetime sem inferência ambígua (evita UserWarning)."""
    if pd.api.types.is_datetime64_any_dtype(serie):
        parsed = pd.to_datetime(serie, errors="coerce")
        if getattr(parsed.dt, "tz", None) is not None:
            parsed = parsed.dt.tz_convert(_PLANNING_TZ).dt.tz_localize(None)
        return parsed

    resultado = pd.Series(pd.NaT, index=serie.index, dtype="datetime64[ns]")
    texto = serie.astype("string").str.strip()
    texto = texto.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})

    for fmt in _FORMATOS_DATA_ANALISE:
        pendentes = resultado.isna()
        if not pendentes.any():
            break
        parseado = pd.to_datetime(texto[pendentes], format=fmt, errors="coerce")
        resultado.loc[pendentes] = parseado

    pendentes = resultado.isna()
    if pendentes.any():
        # ISO 8601 / timestamps com timezone vindos do PostgreSQL (retroativo híbrido).
        parseado = pd.to_datetime(texto[pendentes], errors="coerce", utc=True)
        if getattr(parseado.dt, "tz", None) is not None:
            parseado = parseado.dt.tz_convert(_PLANNING_TZ).dt.tz_localize(None)
        resultado.loc[pendentes] = parseado

    return resultado


def _data_analise_sem_hora_util(parsed: pd.Series) -> pd.Series:
    """Detecta timestamps só com data (meia-noite local ou artefato UTC 03:00)."""
    if parsed.empty:
        return parsed.astype(bool)
    return parsed.isna() | (
        (parsed.dt.hour == 0) & (parsed.dt.minute == 0) & (parsed.dt.second == 0)
    ) | (
        (parsed.dt.hour == 3) & (parsed.dt.minute == 0) & (parsed.dt.second == 0)
    )


_PLANNING_WF_KEY = "__planning_wf_key"
_PLANNING_PROTO_TEXT = "__planning_proto_text"
_PLANNING_DATA = "__planning_data_analise"
_PLANNING_HORA = "__planning_hora"
_PLANNING_INDEX_ATTR = "_replicacao_planning_index_v1"


def preparar_pool_planejamento(df: pd.DataFrame) -> pd.DataFrame:
    """Materializa chaves e índices reutilizados por todas as seleções do run.

    A preparação é idempotente e conserva a ordem original, que faz parte do
    contrato de determinismo da amostragem com ``random_state``.
    """
    if df is None:
        return pd.DataFrame()
    meta = df.attrs.get(_PLANNING_INDEX_ATTR)
    required = {_PLANNING_WF_KEY, _PLANNING_PROTO_TEXT, _PLANNING_DATA, _PLANNING_HORA}
    if (
        isinstance(meta, dict)
        and int(meta.get("rows", -1)) == len(df)
        and required.issubset(df.columns)
    ):
        return df

    out = df.copy()
    out[_PLANNING_WF_KEY] = out[COLUNA_WORKFLOW_PARQUET].astype(str).map(_normalizar_workflow)
    out[_PLANNING_PROTO_TEXT] = out[COLUNA_PROTOCOLO].astype(str)
    out[_PLANNING_DATA] = _parse_data_analise(out[COLUNA_DATA_ANALISE])
    out[_PLANNING_HORA] = out[_PLANNING_DATA].dt.hour

    wf_positions: Dict[str, List[int]] = {}
    proto_positions: Dict[str, List[int]] = {}
    for pos, (wf_key, proto_text) in enumerate(
        zip(out[_PLANNING_WF_KEY].tolist(), out[_PLANNING_PROTO_TEXT].tolist())
    ):
        wf_positions.setdefault(str(wf_key), []).append(pos)
        proto_positions.setdefault(str(proto_text), []).append(pos)
    out.attrs[_PLANNING_INDEX_ATTR] = {
        "rows": len(out),
        "workflow_positions": wf_positions,
        "protocol_positions": proto_positions,
    }
    return out


def _data_planejamento(sub: pd.DataFrame) -> pd.Series:
    if _PLANNING_DATA in sub.columns:
        return sub[_PLANNING_DATA]
    return _parse_data_analise(sub[COLUNA_DATA_ANALISE])


def _filtrar_por_workflow(df: pd.DataFrame, workflow: str) -> pd.DataFrame:
    alvo = _normalizar_workflow(workflow)
    meta = df.attrs.get(_PLANNING_INDEX_ATTR)
    if (
        isinstance(meta, dict)
        and int(meta.get("rows", -1)) == len(df)
        and _PLANNING_WF_KEY in df.columns
    ):
        positions = (meta.get("workflow_positions") or {}).get(alvo, [])
        return df.iloc[positions].copy()
    if _PLANNING_WF_KEY in df.columns:
        return df[df[_PLANNING_WF_KEY] == alvo].copy()
    mask = df[COLUNA_WORKFLOW_PARQUET].astype(str).map(_normalizar_workflow) == alvo
    return df[mask].copy()


def _filtrar_por_protocolos_exatos(df: pd.DataFrame, protocolos: List[str]) -> pd.DataFrame:
    """Equivale ao ``astype(str).isin`` legado usando o índice preparado."""
    if not protocolos:
        return df.iloc[0:0].copy()
    meta = df.attrs.get(_PLANNING_INDEX_ATTR)
    if (
        isinstance(meta, dict)
        and int(meta.get("rows", -1)) == len(df)
        and _PLANNING_PROTO_TEXT in df.columns
    ):
        lookup = meta.get("protocol_positions") or {}
        wanted = {str(item) for item in protocolos}
        positions = sorted(
            pos
            for value in wanted
            for pos in lookup.get(value, [])
        )
        return df.iloc[positions].copy()
    return df[df[COLUNA_PROTOCOLO].astype(str).isin(protocolos)].copy()


def contar_por_hora(df: pd.DataFrame, workflow: str) -> Dict[int, int]:
    """Conta registros do workflow por hora (Data de Análise)."""
    sub = _filtrar_por_workflow(df, workflow)
    if sub.empty:
        return {}

    sub[COLUNA_DATA_ANALISE] = _data_planejamento(sub)
    sub = sub[sub[COLUNA_DATA_ANALISE].notna()]
    if sub.empty:
        return {}

    horas = sub[COLUNA_DATA_ANALISE].dt.hour
    return {int(h): int(c) for h, c in horas.value_counts().sort_index().items()}


def distribuir_proporcional(contagens: Dict[TChave, int], amostra_total: int) -> Dict[TChave, int]:
    """Distribui amostra_total proporcionalmente às contagens (largest remainder)."""
    if amostra_total <= 0 or not contagens:
        return {k: 0 for k in contagens}
    total = sum(contagens.values())
    if total == 0:
        return {k: 0 for k in contagens}

    raw = {k: amostra_total * c / total for k, c in contagens.items()}
    base = {k: math.floor(v) for k, v in raw.items()}
    restante = amostra_total - sum(base.values())
    if restante > 0:
        ordenadas = sorted(contagens.keys(), key=lambda k: raw[k] - base[k], reverse=True)
        for i in range(restante):
            base[ordenadas[i % len(ordenadas)]] += 1
    return base


def alocar_amostra_por_hora(contagens: Dict[int, int], amostra_total: int) -> Dict[int, int]:
    """Distribui amostra por hora; retorna apenas horas com quota > 0."""
    alocado = distribuir_proporcional(contagens, amostra_total)
    return {h: q for h, q in alocado.items() if q > 0}


def redistribuir_amostra_proporcional(
    amostras_destino: Dict[str, int],
    total_redistribuir: int,
) -> Dict[str, int]:
    """Retorna bônus por workflow (cota redistribuída de workflows sem D-1)."""
    if total_redistribuir <= 0 or not amostras_destino:
        return {wf: 0 for wf in amostras_destino}
    return distribuir_proporcional(amostras_destino, total_redistribuir)


def carregar_workflows_amostra_100(settings: Optional[dict] = None) -> set[str]:
    """Workflows configurados para modo 100% (chaves normalizadas via _normalizar_workflow)."""
    settings = settings or {}
    raw = settings.get("replicacao_workflows_amostra_100")
    nomes: List[str] = []
    if isinstance(raw, list):
        nomes = [str(item).strip() for item in raw if str(item).strip()]
    elif isinstance(raw, str):
        for parte in re.split(r"[,;\n\r]+", raw):
            nome = parte.strip()
            if nome:
                nomes.append(nome)
    return {_normalizar_workflow(nome) for nome in nomes if _normalizar_workflow(nome)}


def _clamp_amostra_pct(pct: int) -> int:
    return max(1, min(100, int(pct)))


def carregar_workflows_amostra_override(settings: Optional[dict] = None) -> Dict[str, int]:
    """Mapa workflow normalizado -> % do D-1 disponível (1-100)."""
    settings = settings or {}
    overrides: Dict[str, int] = {}

    for key in carregar_workflows_amostra_100(settings):
        overrides[key] = 100

    raw = settings.get("replicacao_workflows_amostra_pct")
    if isinstance(raw, dict):
        for wf, pct in raw.items():
            nome = str(wf).strip()
            key = _normalizar_workflow(nome)
            if not key:
                continue
            try:
                overrides[key] = _clamp_amostra_pct(int(pct))
            except (TypeError, ValueError):
                continue

    return overrides


def formatar_amostra_override(pct: int) -> str:
    return f"{_clamp_amostra_pct(pct)}%"


def contar_disponivel_por_hora(
    df: pd.DataFrame,
    workflow: str,
    historico: Optional[set] = None,
) -> Dict[int, int]:
    """Conta protocolos únicos por hora após filtro de histórico."""
    sub = _filtrar_por_workflow(df, workflow)
    sub[COLUNA_DATA_ANALISE] = _data_planejamento(sub)
    sub = sub[sub[COLUNA_DATA_ANALISE].notna()].copy()
    sub["_hora"] = sub[COLUNA_DATA_ANALISE].dt.hour
    if historico:
        sub = sub[~sub[COLUNA_PROTOCOLO].astype(str).isin(historico)]
    sub = sub.drop_duplicates(subset=[COLUNA_PROTOCOLO])
    if sub.empty:
        return {}
    return {int(h): int(c) for h, c in sub.groupby("_hora").size().astype(int).items()}


def selecionar_protocolos_por_porcentagem(
    df: pd.DataFrame,
    workflow: str,
    pct: int,
    seed: int = REPLICACAO_AUD_SEED_DEFAULT,
    historico: Optional[set] = None,
) -> tuple[pd.DataFrame, int]:
    """Seleciona pct% dos protocolos disponíveis no D-1 (distribuídos por hora)."""
    pct = _clamp_amostra_pct(pct)
    if pct >= 100:
        return selecionar_todos_protocolos(df, workflow, historico=historico)

    contagens = contar_disponivel_por_hora(df, workflow, historico=historico)
    total = sum(contagens.values())
    if total <= 0:
        return pd.DataFrame(columns=list(df.columns) + ["_hora"]), 0

    target = max(1, round(total * pct / 100))
    alocacao = alocar_amostra_por_hora(contagens, target)
    return selecionar_protocolos(
        df,
        workflow,
        alocacao,
        seed=seed,
        historico=historico,
    )


def selecionar_todos_protocolos(
    df: pd.DataFrame,
    workflow: str,
    historico: Optional[set] = None,
) -> tuple[pd.DataFrame, int]:
    """Seleciona todos os protocolos únicos do workflow (sem amostragem)."""
    sub = _filtrar_por_workflow(df, workflow)
    sub[COLUNA_DATA_ANALISE] = _data_planejamento(sub)
    sub = sub[sub[COLUNA_DATA_ANALISE].notna()].copy()
    sub["_hora"] = sub[COLUNA_DATA_ANALISE].dt.hour

    historico = preparar_historico_planejamento(historico)
    pool = sub.drop_duplicates(subset=[COLUNA_PROTOCOLO])
    excluidos_total = 0
    if historico:
        antes = len(pool)
        pool = pool[~pool[COLUNA_PROTOCOLO].astype(str).isin(historico)]
        excluidos_total = max(0, antes - len(pool))

    if pool.empty:
        return pd.DataFrame(columns=list(df.columns) + ["_hora"]), excluidos_total
    return pool.reset_index(drop=True), excluidos_total


def _extrair_dia_retroativo(row: Any) -> str:
    """Parseia YYYY-MM-DD de _selection_reason (retroativo:YYYY-MM-DD[:parquet])."""
    reason = str(getattr(row, "get", lambda k, d="": d)("_selection_reason", "") or "").strip()
    if not reason.startswith("retroativo:"):
        return ""
    parts = reason.split(":")
    if len(parts) >= 2:
        return parts[1][:10]
    return ""


def _is_retroativo_row(row: Any) -> bool:
    reason = str(getattr(row, "get", lambda k, d="": d)("_selection_reason", "") or "").strip()
    return reason.startswith("retroativo:")


def _particionar_pool_retroativo(
    sub_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    """Separa pool de referência D-1 e retroativo; retorna dias retroativos ordenados."""
    if sub_df.empty or "_selection_reason" not in sub_df.columns:
        return sub_df.copy(), pd.DataFrame(columns=sub_df.columns), []
    mask_retro = sub_df["_selection_reason"].astype(str).str.startswith("retroativo:")
    retro_df = sub_df[mask_retro].copy()
    ref_df = sub_df[~mask_retro].copy()
    dias = sorted(
        {
            dia
            for _, row in retro_df.iterrows()
            for dia in [_extrair_dia_retroativo(row)]
            if dia
        }
    )
    return ref_df, retro_df, dias


def _alocar_cota_retroativo_por_dia(
    retro_df: pd.DataFrame,
    dias: List[str],
    cota_total: int,
    workflow: str,
    *,
    historico: Optional[set] = None,
) -> Dict[str, int]:
    """Distribui cota retroativa entre dias; mínimo 1/dia quando cota >= dias com dados."""
    if cota_total <= 0 or not dias or retro_df.empty:
        return {}
    day_counts: Dict[str, int] = {}
    for dia in dias:
        prefix = f"retroativo:{dia}"
        day_df = retro_df[retro_df["_selection_reason"].astype(str).str.startswith(prefix)]
        contagens = contar_disponivel_por_hora(day_df, workflow, historico=historico)
        day_counts[dia] = sum(contagens.values())
    dias_com_dados = [d for d in dias if day_counts.get(d, 0) > 0]
    if not dias_com_dados:
        return {}
    if cota_total >= len(dias_com_dados):
        result = {d: 1 for d in dias_com_dados}
        restante = cota_total - len(dias_com_dados)
        if restante > 0:
            extra = distribuir_proporcional(
                {d: day_counts[d] for d in dias_com_dados},
                restante,
            )
            for dia, qtd in extra.items():
                result[dia] = result.get(dia, 0) + qtd
        return result
    return distribuir_proporcional(
        {d: day_counts[d] for d in dias_com_dados},
        cota_total,
    )


def selecionar_protocolos_retroativo_split(
    df: pd.DataFrame,
    workflow: str,
    amostra_total: int,
    *,
    pct_referencia: int = 50,
    seed: int = REPLICACAO_AUD_SEED_DEFAULT,
    historico: Optional[set] = None,
) -> tuple[pd.DataFrame, int, List[str]]:
    """
    Split fixo referência/retroativo com estratificação por dia retroativo.

    Metade (pct_referencia) vem do pool D-1 de referência; o restante é distribuído
    entre os dias retroativos (mínimo 1 por dia com dados quando a cota permitir).
    """
    amostra_total = max(0, int(amostra_total))
    avisos: List[str] = []
    cols = list(df.columns) + ["_hora"]
    if amostra_total <= 0:
        return pd.DataFrame(columns=cols), 0, avisos

    sub = _filtrar_por_workflow(df, workflow)
    ref_df, retro_df, dias = _particionar_pool_retroativo(sub)

    if retro_df.empty:
        contagens = contar_disponivel_por_hora(df, workflow, historico=historico)
        aloc = alocar_amostra_por_hora(contagens, amostra_total)
        sel, excl = selecionar_protocolos(
            df, workflow, aloc, seed=seed, historico=historico
        )
        return sel, excl, avisos

    pct_ref = max(0, min(100, int(pct_referencia)))
    cota_ref = round(amostra_total * pct_ref / 100)
    cota_retro = amostra_total - cota_ref

    if ref_df.empty and cota_ref > 0:
        avisos.append(
            f"Retroativo split ({workflow}): referência D-1 vazia; "
            f"cota retroativa absorve {cota_ref} protocolo(s)."
        )
        cota_retro = amostra_total
        cota_ref = 0

    historico = historico or set()
    excl_total = 0
    partes: List[pd.DataFrame] = []
    usados: set[str] = set()

    def _hist_efetivo() -> set:
        return historico | usados

    def _registrar_usados(frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        usados.update(
            _protocolo_chave_plano(p)
            for p in frame[COLUNA_PROTOCOLO].astype(str)
            if _protocolo_chave_plano(p)
        )

    if cota_ref > 0 and not ref_df.empty:
        contagens_ref = contar_disponivel_por_hora(
            ref_df, workflow, historico=_hist_efetivo()
        )
        aloc_ref = alocar_amostra_por_hora(contagens_ref, cota_ref)
        sel_ref, excl_ref = selecionar_protocolos(
            ref_df, workflow, aloc_ref, seed=seed, historico=_hist_efetivo()
        )
        excl_total += excl_ref
        if not sel_ref.empty:
            partes.append(sel_ref)
            _registrar_usados(sel_ref)
        deficit_ref = cota_ref - len(sel_ref)
        if deficit_ref > 0:
            cota_retro += deficit_ref
            avisos.append(
                f"Retroativo split ({workflow}): déficit referência ({deficit_ref}) "
                "redistribuído para dias retroativos."
            )

    if cota_retro > 0 and not retro_df.empty and dias:
        day_quota = _alocar_cota_retroativo_por_dia(
            retro_df,
            dias,
            cota_retro,
            workflow,
            historico=_hist_efetivo(),
        )
        seed_day = seed + 1000
        for dia in sorted(day_quota.keys()):
            quota = int(day_quota[dia])
            if quota <= 0:
                continue
            prefix = f"retroativo:{dia}"
            day_df = retro_df[
                retro_df["_selection_reason"].astype(str).str.startswith(prefix)
            ]
            if day_df.empty:
                continue
            contagens_day = contar_disponivel_por_hora(
                day_df, workflow, historico=_hist_efetivo()
            )
            aloc_day = alocar_amostra_por_hora(contagens_day, quota)
            sel_day, excl_day = selecionar_protocolos(
                day_df, workflow, aloc_day, seed=seed_day, historico=_hist_efetivo()
            )
            seed_day += 1
            excl_total += excl_day
            if not sel_day.empty:
                partes.append(sel_day)
                _registrar_usados(sel_day)

    if not partes:
        return pd.DataFrame(columns=cols), excl_total, avisos
    merged = pd.concat(partes, ignore_index=True)
    merged["_proto_key"] = merged[COLUNA_PROTOCOLO].astype(str).map(_protocolo_chave_plano)
    merged = merged.drop_duplicates(subset=["_proto_key"], keep="first").drop(columns=["_proto_key"])
    return merged, excl_total, avisos


def selecionar_protocolos(
    df: pd.DataFrame,
    workflow: str,
    alocacao_hora: Dict[int, int],
    seed: int = REPLICACAO_AUD_SEED_DEFAULT,
    historico: Optional[set] = None,
) -> tuple[pd.DataFrame, int]:
    """Amostra protocolos por hora conforme cotas. Retorna (df, qtd_excluidos_historico)."""
    sub = _filtrar_por_workflow(df, workflow)
    sub[COLUNA_DATA_ANALISE] = _data_planejamento(sub)
    sub = sub[sub[COLUNA_DATA_ANALISE].notna()].copy()
    sub["_hora"] = sub[COLUNA_DATA_ANALISE].dt.hour

    historico = historico or set()
    excluidos_total = 0
    partes: List[pd.DataFrame] = []

    for hora in sorted(alocacao_hora.keys()):
        quota = alocacao_hora[hora]
        if quota <= 0:
            continue
        pool = sub[sub["_hora"] == hora].drop_duplicates(subset=[COLUNA_PROTOCOLO])
        pool, excl_hist = _filtrar_pool_por_historico(pool, historico)
        excluidos_total += excl_hist
        n = min(quota, len(pool))
        if n <= 0:
            continue
        partes.append(pool.sample(n=n, random_state=seed + int(hora)))

    if not partes:
        return pd.DataFrame(columns=list(df.columns) + ["_hora"]), excluidos_total
    return pd.concat(partes, ignore_index=True), excluidos_total


def exportar_protocolos_csv(
    protocolos_por_workflow: Dict[str, List[str]],
    pasta_saida: Path,
) -> Dict[str, Path]:
    """Exporta um CSV por workflow (só protocolos, sem cabeçalho)."""
    pasta_saida.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for workflow, protocolos in protocolos_por_workflow.items():
        if not protocolos:
            log.info("CSV omitido (sem protocolos): workflow=%s", workflow)
            continue
        nome_arquivo = f"{_sanitizar_nome_arquivo(workflow)}.csv"
        caminho = pasta_saida / nome_arquivo
        pd.Series(protocolos, dtype="string").to_csv(
            caminho, index=False, header=False, encoding="utf-8-sig"
        )
        paths[workflow] = caminho
        log.info(
            "CSV gerado: %s | workflow=%s | protocolos=%d",
            caminho.name,
            workflow,
            len(protocolos),
        )
    return paths


def exportar_csv_fallback_vazio(workflow: str, pasta_saida: Path) -> Optional[Path]:
    """Legado: workflows sem protocolos não geram mais CSV de limpeza."""
    log.info("CSV omitido (sem protocolos): workflow=%s", workflow)
    return None


def _classificar_status(amostra_efetiva: int, salvo: int, sem_registro: bool) -> str:
    if sem_registro:
        return "SEM_REGISTRO_D1"
    if salvo <= 0:
        return "VAZIO"
    if salvo < amostra_efetiva:
        return "PARCIAL"
    return "OK"


def _formatar_datetime_brflow(iso_str: str) -> str:
    """Converte atualizado_em ISO para exibição dd/mm/yyyy HH:MM:SS."""
    raw = str(iso_str or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return raw


def _parse_data_referencia_d1(valor: str, fallback: Optional[datetime] = None) -> datetime:
    texto = str(valor or "").strip()
    if texto:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(texto, fmt)
            except ValueError:
                continue
    return fallback or datetime.now()


def _montar_dataframe_resumo(
    linhas: List[dict],
    parquet_path: Path,
    data_ref: datetime,
    run_id: str,
    data_exec: Optional[datetime] = None,
) -> pd.DataFrame:
    """DataFrame de métricas por workflow + linha TOTAL (sem colunas BRFlow)."""
    data_exec = data_exec or datetime.now()
    if not linhas:
        return pd.DataFrame()

    df = pd.DataFrame(linhas)
    if "Excluidos Historico" not in df.columns:
        df["Excluidos Historico"] = 0
    if "Amostra Diaria" not in df.columns:
        df["Amostra Diaria"] = 0

    def _somar_coluna(col: str, fallback_col: Optional[str] = None) -> int:
        vals = pd.to_numeric(df[col], errors="coerce")
        if fallback_col and fallback_col in df.columns:
            faltando = vals.isna()
            vals.loc[faltando] = pd.to_numeric(df.loc[faltando, fallback_col], errors="coerce")
        return int(vals.fillna(0).sum())

    total_diaria = _somar_coluna("Amostra Diaria")
    total_solicitada = _somar_coluna("Amostra Solicitada", "Protocolos Salvos")
    total_redist = _somar_coluna("Amostra Redistribuida")
    total_efetiva = _somar_coluna("Amostra Efetiva")
    total_salvos = _somar_coluna("Protocolos Salvos")
    total_excl_hist = _somar_coluna("Excluidos Historico")
    total = {
        "Workflow": "TOTAL",
        "Amostra Diaria": total_diaria,
        "Amostra Solicitada": total_solicitada,
        "Amostra Redistribuida": total_redist,
        "Amostra Efetiva": total_efetiva,
        "Protocolos Salvos": total_salvos,
        "Excluidos Historico": total_excl_hist,
        "Disponivel D1": int(df["Disponivel D1"].sum()),
        "Horas Utilizadas": "",
        "Faixa Horaria": "",
        "Status": (
            f"OK={int((df['Status'] == 'OK').sum())} | "
            f"PARCIAL={int((df['Status'] == 'PARCIAL').sum())} | "
            f"SEM_D1={int((df['Status'] == 'SEM_REGISTRO_D1').sum())}"
        ),
        "Pct Atingido": round(100 * total_salvos / max(1, total_efetiva), 1),
        "Arquivo CSV": "",
        "Observacao": "",
        "RunId": run_id,
        "Parquet Referencia": parquet_path.name,
        "Data Referencia D1": data_ref.strftime("%d/%m/%Y"),
        "Data Execucao": data_exec.strftime("%d/%m/%Y %H:%M"),
        "Seed Amostra": "",
    }
    return pd.concat([df, pd.DataFrame([total])], ignore_index=True)


def _enriquecer_resumo_com_estado_brflow(
    df: pd.DataFrame,
    plano: "PlanoReplicacao",
    estado: Optional[dict],
) -> pd.DataFrame:
    """Adiciona colunas de upload/status BRFlow ao resumo."""
    if df.empty:
        return df
    workflows_estado = (estado or {}).get("workflows", {})
    brflow_cols: List[str] = []
    status_cols: List[str] = []
    upload_cols: List[str] = []
    ultima_cols: List[str] = []

    for _, row in df.iterrows():
        wf = str(row.get("Workflow", "") or "")
        if wf == "TOTAL":
            brflow_cols.append("")
            status_cols.append("")
            upload_cols.append("")
            ultima_cols.append("")
            continue
        info = workflows_estado.get(wf, {})
        br = str(
            info.get("workflow_brflow")
            or plano.workflow_brflow.get(wf, "")
            or wf
        ).strip()
        status = str(info.get("status", "") or "PENDENTE").strip() or "PENDENTE"
        atualizado = str(info.get("atualizado_em", "") or "")
        fmt = _formatar_datetime_brflow(atualizado)
        brflow_cols.append(br)
        status_cols.append(status)
        upload_cols.append(fmt if status == "SALVO_OK" else "")
        ultima_cols.append(fmt)

    out = df.copy()
    out["Workflow BRFlow"] = brflow_cols
    out["Status BRFlow"] = status_cols
    out["Data Hora Upload BRFlow"] = upload_cols
    out["Data Hora Ultima Atualizacao BRFlow"] = ultima_cols
    return out


def _metricas_dashboard_brflow(estado: Optional[dict]) -> dict:
    workflows = (estado or {}).get("workflows", {})
    contagem = {
        "SALVO_OK": 0,
        "ERRO": 0,
        "PULADO": 0,
        "INATIVO": 0,
        "PENDENTE": 0,
        "UPLOAD_OK": 0,
    }
    for info in workflows.values():
        st = str(info.get("status", "PENDENTE") or "PENDENTE")
        if st not in contagem:
            contagem[st] = contagem.get(st, 0) + 1
        else:
            contagem[st] += 1
    return {
        "WorkflowsSalvosBRFlow": contagem.get("SALVO_OK", 0) + contagem.get("UPLOAD_OK", 0),
        "WorkflowsErroBRFlow": contagem.get("ERRO", 0),
        "WorkflowsPuladosBRFlow": contagem.get("PULADO", 0),
        "WorkflowsInativosBRFlow": contagem.get("INATIVO", 0),
        "WorkflowsPendentesBRFlow": contagem.get("PENDENTE", 0),
    }


def _montar_dataframe_dashboard(
    plano: "PlanoReplicacao",
    parquet_path: Path,
    data_ref: datetime,
    estado: Optional[dict] = None,
    data_fim_execucao: Optional[datetime] = None,
) -> pd.DataFrame:
    """KPIs da execução (formato Métrica | Valor)."""
    linhas_wf = [r for r in plano.resumo if r.get("Workflow") != "TOTAL"]
    total_solicitada = sum(_as_int(r.get("Amostra Solicitada", 0)) for r in linhas_wf)
    total_efetiva = sum(_as_int(r.get("Amostra Efetiva", 0)) for r in linhas_wf)
    total_salvos = sum(_as_int(r.get("Protocolos Salvos", 0)) for r in linhas_wf)
    com_d1 = len([r for r in linhas_wf if r.get("Status") != "SEM_REGISTRO_D1"])
    sem_d1 = len([r for r in linhas_wf if r.get("Status") == "SEM_REGISTRO_D1"])
    parciais = len([r for r in linhas_wf if r.get("Status") == "PARCIAL"])
    workflows_upload = len(plano.workflows)
    data_fim = data_fim_execucao or datetime.now()

    metricas = {
        "RunId": plano.run_id,
        "PastaVolumetria": plano.pasta_volumetria,
        "TotalWorkflowsConfig": plano.total_workflows_config,
        "WorkflowsComD1": com_d1,
        "WorkflowsSemD1": sem_d1,
        "AmostraSolicitadaTotal": total_solicitada,
        "AmostraEfetivaTotal": total_efetiva,
        "ProtocolosSalvosTotal": total_salvos,
        "PctAtingidoGlobal": round(100 * total_salvos / max(1, total_efetiva), 1),
        "PoolRedistribuido": plano.pool_redistribuido,
        "ExcluidosHistoricoTotal": plano.excluidos_historico_total,
        "WorkflowsParciais": parciais,
        "WorkflowsComUpload": workflows_upload,
        "TempoEstimadoMin": workflows_upload * 2,
        "PastaProtocolos": str(plano.pasta_protocolos),
        "PastaResumo": str(plano.pasta_resumo),
        "RelatorioExcel": str(plano.relatorio_excel_path or ""),
        "ParquetReferencia": parquet_path.name,
        "DataReferenciaD1": data_ref.strftime("%d/%m/%Y"),
        "AuditoresAtivos": plano.auditores_ativos,
        "MetaProdu": plano.meta_produ,
        "CapacidadeProdutiva": plano.capacidade_produtiva,
        "SomaAmostraDiaria": plano.soma_amostra_diaria,
        "FatorCapacidade": round(plano.fator_capacidade, 4),
        "SomaAmostraAjustada": plano.soma_amostra_diaria,
        "AderenciaCapacidadePct": (
            round(100 * plano.soma_amostra_diaria / max(1.0, plano.capacidade_produtiva), 1)
            if plano.capacidade_produtiva > 0
            else ""
        ),
        "DataInicioExecucao": (estado or {}).get("iniciado_em", ""),
        "DataFimExecucao": data_fim.strftime("%d/%m/%Y %H:%M:%S"),
        **_metricas_dashboard_brflow(estado),
    }
    return montar_dataframe_dashboard_secoes(metricas)


def exportar_relatorio_excel(
    plano: "PlanoReplicacao",
    parquet_path: Path,
    data_ref: datetime,
    estado: Optional[dict] = None,
    df_plano: Optional[pd.DataFrame] = None,
    data_exec: Optional[datetime] = None,
    pasta_saida: Optional[Path] = None,
) -> Path:
    """Exporta Plano, Resumo e Dashboard em um único .xlsx."""
    data_exec = data_exec or datetime.now()
    pasta_saida = pasta_saida or _pasta_relatorios_excel()
    pasta_saida.mkdir(parents=True, exist_ok=True)
    caminho = pasta_saida / f"{REPLICACAO_AUD_RELATORIO_PREFIXO}{plano.run_id}.xlsx"

    linhas = [r for r in plano.resumo if r.get("Workflow") != "TOTAL"]
    df_resumo = _montar_dataframe_resumo(
        linhas,
        parquet_path=parquet_path,
        data_ref=data_ref,
        run_id=plano.run_id,
        data_exec=data_exec,
    )
    df_resumo = _enriquecer_resumo_com_estado_brflow(df_resumo, plano, estado)
    df_dashboard = _montar_dataframe_dashboard(
        plano,
        parquet_path,
        data_ref,
        estado=estado,
        data_fim_execucao=datetime.now(),
    )
    if df_plano is None:
        df_plano = pd.DataFrame()

    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        exportar_workbook_formatado(
            writer,
            plano,
            df_plano,
            df_resumo,
            df_dashboard,
            estado,
        )

    plano.relatorio_excel_path = caminho
    plano.parquet_referencia = parquet_path.name
    plano.data_referencia_d1_fmt = data_ref.strftime("%d/%m/%Y")
    plano.data_execucao_fmt = data_exec.strftime("%d/%m/%Y %H:%M")
    log.info(
        "Relatório Excel salvo: %s | plano=%d linhas | resumo=%d linhas",
        caminho,
        len(df_plano),
        len(df_resumo),
    )
    return caminho


def atualizar_relatorio_excel(plano: "PlanoReplicacao", estado: dict) -> Optional[Path]:
    """Regenera o .xlsx após execução Selenium (status/data upload BRFlow)."""
    if not plano.run_id:
        return None
    if not plano.resumo and plano.relatorio_excel_path and Path(plano.relatorio_excel_path).exists():
        try:
            df_lido = pd.read_excel(plano.relatorio_excel_path, sheet_name="Resumo", engine="openpyxl")
            plano.resumo = df_lido[df_lido["Workflow"].astype(str) != "TOTAL"].to_dict("records")
        except Exception as exc:
            log.warning("Não foi possível reler aba Resumo do Excel: %s", exc)
    if not plano.resumo:
        legado = PASTA_REPLICACAO_AUD_RESUMO / f"{REPLICACAO_AUD_RESUMO_PREFIXO}{plano.run_id}.csv"
        if legado.exists():
            df_leg = pd.read_csv(legado, sep=";", encoding="utf-8-sig")
            plano.resumo = df_leg[df_leg["Workflow"].astype(str) != "TOTAL"].to_dict("records")

    parquet_name = str(estado.get("parquet_referencia") or plano.parquet_referencia or "")
    if not parquet_name and plano.resumo:
        parquet_name = str(plano.resumo[0].get("Parquet Referencia", "") or "parquet.parquet")
    data_ref_txt = str(
        estado.get("data_referencia_d1")
        or plano.data_referencia_d1_fmt
        or (plano.resumo[0].get("Data Referencia D1", "") if plano.resumo else "")
    )
    data_ref = _parse_data_referencia_d1(data_ref_txt, plano.data_referencia)

    df_plano: Optional[pd.DataFrame] = None
    rel_path = _resolver_caminho_relatorio_excel(
        plano.run_id,
        path_hint=plano.relatorio_excel_path,
    )
    if Path(rel_path).exists():
        try:
            df_plano = pd.read_excel(rel_path, sheet_name="Plano", engine="openpyxl")
        except Exception:
            df_plano = pd.DataFrame()

    data_exec_txt = str(estado.get("data_execucao") or plano.data_execucao_fmt or "")
    try:
        data_exec = datetime.strptime(data_exec_txt, "%d/%m/%Y %H:%M")
    except ValueError:
        try:
            data_exec = datetime.strptime(data_exec_txt.split()[0], "%d/%m/%Y")
        except ValueError:
            data_exec = datetime.now()

    return exportar_relatorio_excel(
        plano,
        Path(parquet_name),
        data_ref,
        estado=estado,
        df_plano=df_plano,
        data_exec=data_exec,
        pasta_saida=rel_path.parent,
    )


def exportar_resumo_csv(
    linhas: List[dict],
    parquet_path: Path,
    data_ref: datetime,
    run_id: str,
    pasta_saida: Optional[Path] = None,
    data_exec: Optional[datetime] = None,
) -> Path:
    """Compatibilidade: exporta apenas aba Resumo em CSV."""
    data_exec = data_exec or datetime.now()
    pasta_saida = pasta_saida or PASTA_REPLICACAO_AUD_RESUMO
    pasta_saida.mkdir(parents=True, exist_ok=True)
    caminho = pasta_saida / f"{REPLICACAO_AUD_RESUMO_PREFIXO}{run_id}.csv"
    df = _montar_dataframe_resumo(linhas, parquet_path, data_ref, run_id, data_exec)
    df.to_csv(caminho, index=False, encoding="utf-8-sig", sep=";")
    log.info("Resumo CSV (legado) salvo: %s", caminho)
    return caminho


def exportar_dashboard_csv(
    plano: "PlanoReplicacao",
    parquet_path: Path,
    data_ref: datetime,
    estado: Optional[dict] = None,
) -> Path:
    """Compatibilidade: exporta apenas Dashboard em CSV."""
    pasta = plano.pasta_resumo
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{REPLICACAO_AUD_DASHBOARD_PREFIXO}{plano.run_id}.csv"
    df = _montar_dataframe_dashboard(plano, parquet_path, data_ref, estado=estado)
    df.to_csv(caminho, index=False, encoding="utf-8-sig", sep=";")
    log.info("Dashboard CSV (legado) salvo: %s", caminho)
    return caminho


def inicializar_estado_execucao(
    plano: "PlanoReplicacao",
    *,
    parquet_referencia: str = "",
    data_referencia_d1: str = "",
    data_execucao: str = "",
    seed_amostra: Optional[int] = None,
) -> dict:
    """Cria estrutura JSON inicial de execução Selenium."""
    workflows_estado = {}
    sem_registro = set(plano.workflows_sem_registro)
    for workflow in plano.workflows:
        csv_path = plano.csv_paths.get(workflow)
        protocolos = plano.protocolos_por_workflow.get(workflow) or []
        fila = plano.workflow_fila.get(workflow, REPLICACAO_FILA_G_AUDITORIA)
        modo = plano.workflow_modo_replicacao.get(workflow) or resolver_modo_replicacao(None, fila)
        wf_br = str(plano.workflow_brflow.get(workflow, workflow) or workflow).strip()
        path = Path(csv_path) if csv_path else Path()
        qtd_calculada = int(plano.qtd_por_workflow.get(workflow, 0) or 0)
        sem_upload = (
            qtd_calculada <= 0
            if modo == REPLICACAO_MODO_QTD
            else not workflow_elegivel_upload(protocolos, path)
        )
        entry = {
            "status": "PULADO" if sem_upload else "PENDENTE",
            "csv": _csv_relativo_plano(plano, Path(csv_path)) if csv_path else "",
            "workflow_brflow": wf_br,
            "modo": modo,
            "atualizado_em": "",
        }
        if modo == REPLICACAO_MODO_QTD:
            entry["qtd_calculada"] = qtd_calculada
        if workflow in sem_registro:
            entry["motivo"] = "sem D-1"
        elif sem_upload:
            entry["motivo"] = "qtd zero" if modo == REPLICACAO_MODO_QTD else "sem protocolos"
        workflows_estado[workflow] = entry
    estado = {
        "run_id": plano.run_id,
        "iniciado_em": datetime.now().isoformat(timespec="seconds"),
        "pasta_protocolos": str(plano.pasta_protocolos),
        "default_xlsx_path": str(plano.default_xlsx_path or ""),
        "parquet_referencia": parquet_referencia,
        "data_referencia_d1": data_referencia_d1,
        "data_execucao": data_execucao,
        "seed_amostra": seed_amostra if seed_amostra is not None else plano.seed_amostra,
        "workflow_modo_replicacao": dict(plano.workflow_modo_replicacao),
        "workflows": workflows_estado,
    }
    path = plano.estado_execucao_path or caminho_estado_execucao(plano.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(estado, fh, ensure_ascii=False, indent=2)
    return estado


def salvar_estado_execucao(estado: dict, path: Optional[Path] = None) -> None:
    path = path or caminho_estado_execucao(str(estado.get("run_id", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(estado, fh, ensure_ascii=False, indent=2)


def carregar_estado_execucao(run_id: str) -> Optional[dict]:
    path = caminho_estado_execucao(run_id)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def gerar_plano_replicacao(
    config_path: Optional[Union[str, Path]] = None,
    data_ref: Optional[datetime] = None,
    seed: Optional[int] = None,
    fallback_ultimo_parquet: bool = False,
    salvar_plano_detalhado: bool = True,
    settings: Optional[dict] = None,
) -> PlanoReplicacao:
    """
    Gera plano de replicação: lê config, parquet D-1, amostra por hora e exporta CSVs.
    """
    settings = settings or {}
    if seed is None:
        seed = int(settings.get("replicacao_aud_seed", REPLICACAO_AUD_SEED_DEFAULT))
    if "fallback_ultimo_parquet" in settings:
        fallback_ultimo_parquet = _parse_bool_setting(
            settings["fallback_ultimo_parquet"], fallback_ultimo_parquet
        )
    if data_ref is None and settings.get("replicacao_aud_data_ref"):
        raw_data = str(settings["replicacao_aud_data_ref"]).strip()
        data_ref = datetime.strptime(raw_data, "%Y%m%d")

    config_raw = carregar_config_auditoria(config_path, settings=settings)
    pasta_volumetria = str(config_raw.attrs.get("pasta_volumetria", "") or "")
    config_warnings = list(config_raw.attrs.get("config_warnings", []) or [])
    workflows_pendentes_config = list(config_raw.attrs.get("workflows_pendentes_config", []) or [])
    workflows_pendentes_novos = list(config_raw.attrs.get("workflows_pendentes_novos", []) or [])
    workflows_pendentes_existentes = list(config_raw.attrs.get("workflows_pendentes_existentes", []) or [])
    workflows_pendentes_falha_sync = list(config_raw.attrs.get("workflows_pendentes_falha_sync", []) or [])
    default_xlsx_path = str(config_raw.attrs.get("default_xlsx_path", "") or "")
    data_ref = data_ref or (datetime.now() - timedelta(days=1))

    try:
        config, info_capacidade = aplicar_escala_auditores_config(
            config_raw, data_ref=data_ref, settings=settings
        )
    except (FileNotFoundError, ValueError) as exc:
        if bool(settings.get("usar_escala_auditores", REPLICACAO_AUD_USAR_ESCALA_DEFAULT)):
            raise
        log.warning("Escala de auditores não aplicada: %s", exc)
        config = config_raw.copy()
        config["amostra_ajustada"] = (
            pd.to_numeric(config["amostra_diaria"], errors="coerce").fillna(0).astype(int)
        )
        soma_esc = _as_int(config["amostra_diaria"].sum())
        info_capacidade = InfoCapacidade(0, 0.0, float(soma_esc), soma_esc, 1.0, False)

    parquet_path = resolver_parquet_d1(
        data_ref=data_ref,
        fallback_ultimo=fallback_ultimo_parquet,
    )
    log.info("Carregando parquet: %s", parquet_path)
    df = ler_parquet(parquet_path, log=log)
    _validar_parquet(df)

    data_exec = datetime.now()
    run_id = _resolver_run_id(data_exec, settings)
    pasta_protocolos = _pasta_saida_protocolos(run_id)
    pasta_resumo = PASTA_REPLICACAO_AUD_RESUMO
    pasta_protocolos.mkdir(parents=True, exist_ok=True)

    excluir_historico = bool(
        settings.get("excluir_historico", REPLICACAO_AUD_EXCLUIR_HISTORICO_DEFAULT)
    )
    dias_historico = int(settings.get("dias_historico", REPLICACAO_AUD_DIAS_HISTORICO_DEFAULT))
    historico: set = set()
    if excluir_historico:
        historico = carregar_protocolos_historico(
            excluir_run_id=run_id,
            dias_historico=dias_historico,
            referencia=data_exec,
        )

    plano = PlanoReplicacao(
        data_referencia=data_ref,
        pasta_protocolos=pasta_protocolos,
        pasta_resumo=pasta_resumo,
        run_id=run_id,
        pasta_execucao=pasta_protocolos,
        total_workflows_config=len(config),
        pool_redistribuido=0,
        estado_execucao_path=caminho_estado_execucao(run_id),
        auditores_ativos=info_capacidade.auditores_ativos,
        meta_produ=info_capacidade.meta_produ,
        capacidade_produtiva=info_capacidade.capacidade_produtiva,
        soma_amostra_diaria=info_capacidade.soma_amostra_diaria,
        fator_capacidade=info_capacidade.fator_capacidade,
        pasta_volumetria=pasta_volumetria,
    )
    plano.warnings.extend(config_warnings)
    plano.workflows_pendentes_config = workflows_pendentes_config
    plano.workflows_pendentes_novos = workflows_pendentes_novos
    plano.workflows_pendentes_existentes = workflows_pendentes_existentes
    plano.workflows_pendentes_falha_sync = workflows_pendentes_falha_sync
    plano.default_xlsx_path = default_xlsx_path
    for _, row in config.iterrows():
        wf = str(row[COLUNA_CONFIG_WORKFLOW]).strip()
        if wf:
            plano.workflow_brflow[wf] = _resolver_workflow_nome_brflow(row)
    detalhes: List[pd.DataFrame] = []
    meta_base = {
        "RunId": run_id,
        "Parquet Referencia": parquet_path.name,
        "Data Referencia D1": data_ref.strftime("%d/%m/%Y"),
        "Data Execucao": data_exec.strftime("%d/%m/%Y %H:%M"),
        "Seed Amostra": seed,
        "Data Escala Auditores": (
            info_capacidade.data_escala.strftime("%d/%m/%Y")
            if info_capacidade.usar_escala and info_capacidade.data_escala
            else ""
        ),
        "Auditores Ativos": info_capacidade.auditores_ativos if info_capacidade.usar_escala else "",
        "Meta Produ": round(info_capacidade.meta_produ, 2) if info_capacidade.usar_escala else "",
        "Capacidade Produtiva": round(info_capacidade.capacidade_produtiva, 1),
        "Soma Amostra Diaria": info_capacidade.soma_amostra_diaria,
        "Fator Capacidade": round(info_capacidade.fator_capacidade, 4),
        "Pasta Volumetria": pasta_volumetria,
    }

    # Passagem 1: classificar workflows e calcular pool de redistribuição
    itens_config: List[dict] = []
    for _, row in config.iterrows():
        workflow = row[COLUNA_CONFIG_WORKFLOW]
        workflow_d1 = _workflow_d1_da_linha(row)
        amostra_diaria = _as_int(row["amostra_diaria"])
        amostra = _as_int(row["amostra_ajustada"])
        contagens = contar_por_hora(df, workflow_d1)
        itens_config.append({
            "workflow": workflow,
            "workflow_d1": workflow_d1,
            "amostra": amostra,
            "amostra_diaria": amostra_diaria,
            "contagens": contagens,
            "tem_d1": bool(contagens),
            "row": row,
        })

    overrides_amostra = carregar_workflows_amostra_override(settings)
    for item in itens_config:
        key = _normalizar_workflow(str(item["workflow"]))
        item["amostra_override_pct"] = overrides_amostra.get(key)

    if overrides_amostra:
        config_keys = {_normalizar_workflow(str(item["workflow"])) for item in itens_config}
        desconhecidos = set(overrides_amostra.keys()) - config_keys
        if desconhecidos:
            plano.warnings.append(
                "Amostra por %: workflow(s) configurado(s) não encontrado(s) no plano: "
                + ", ".join(sorted(desconhecidos))
            )

    sem_d1 = [item for item in itens_config if not item["tem_d1"]]
    com_d1 = [item for item in itens_config if item["tem_d1"]]
    sem_d1_redist = [item for item in sem_d1 if item.get("amostra_override_pct") is None]
    com_d1_redist = [item for item in com_d1 if item.get("amostra_override_pct") is None]
    pool_redistribuir = sum(item["amostra"] for item in sem_d1_redist)
    plano.pool_redistribuido = pool_redistribuir

    if pool_redistribuir > 0 and not com_d1:
        plano.warnings.append(
            f"{pool_redistribuir} protocolos sem destino: nenhum workflow com registro no D-1"
        )
        log.warning("Pool de redistribuição sem destino (%d protocolos)", pool_redistribuir)
    elif pool_redistribuir > 0:
        log.info(
            "Redistribuindo %d protocolos de %d workflow(s) sem D-1 para %d workflow(s)",
            pool_redistribuir,
            len(sem_d1_redist),
            len(com_d1_redist),
        )

    bonus_por_workflow = redistribuir_amostra_proporcional(
        {item["workflow"]: item["amostra"] for item in com_d1_redist},
        pool_redistribuir,
    )

    # Passagem 2: workflows sem D-1 (CSV vazio)
    for item in sem_d1:
        workflow = item["workflow"]
        workflow_d1 = item["workflow_d1"]
        amostra = item["amostra"]
        nome_csv = f"{_sanitizar_nome_arquivo(workflow)}.csv"
        if workflow_d1 != workflow:
            msg = (
                f"{workflow}: Workflow d-1 '{workflow_d1}' sem registros no parquet "
                f"({parquet_path.name}); CSV não gerado"
            )
        else:
            msg = (
                f"{workflow}: sem registros no parquet D-1 ({parquet_path.name}); "
                "CSV não gerado"
            )
        plano.warnings.append(msg)
        if item.get("amostra_override_pct") is not None:
            plano.warnings.append(
                f"{workflow}: amostra {formatar_amostra_override(item['amostra_override_pct'])} "
                "inaplicável (sem registros no parquet D-1)"
            )
        plano.workflows_sem_registro.append(workflow)
        plano.protocolos_por_workflow[workflow] = []
        obs_redist = (
            f"Cota de {amostra} redistribuída aos demais workflows"
            if pool_redistribuir > 0 and com_d1_redist
            else "Sem registros no parquet D-1"
        )
        obs_parts_sem = [obs_redist]
        if item.get("amostra_override_pct") is not None:
            obs_parts_sem.append(
                f"Amostra {formatar_amostra_override(item['amostra_override_pct'])} inaplicável"
            )
        plano.resumo.append({
            "Workflow": workflow,
            "Workflow D1": workflow_d1,
            "Amostra Diaria": item.get("amostra_diaria", amostra),
            "Amostra Solicitada": (
                formatar_amostra_override(item["amostra_override_pct"])
                if item.get("amostra_override_pct") is not None
                else amostra
            ),
            "Amostra Redistribuida": 0,
            "Amostra Efetiva": 0,
            "Protocolos Salvos": 0,
            "Excluidos Historico": 0,
            "Disponivel D1": 0,
            "Horas Utilizadas": 0,
            "Faixa Horaria": "",
            "Status": _classificar_status(0, 0, sem_registro=True),
            "Pct Atingido": 0.0,
            "Arquivo CSV": "",
            "Observacao": "; ".join(obs_parts_sem),
            **meta_base,
            **_meta_colunas_config(item["row"], pasta_volumetria),
        })

    # Passagem 3: workflows com D-1 (amostra efetiva = solicitada + bônus)
    for item in com_d1:
        workflow = item["workflow"]
        workflow_d1 = item["workflow_d1"]
        amostra = item["amostra"]
        contagens = item["contagens"]
        nome_csv = f"{_sanitizar_nome_arquivo(workflow)}.csv"
        total_disponivel = sum(contagens.values())

        if item.get("amostra_override_pct") is not None:
            override_pct = int(item["amostra_override_pct"])
            selecionados, excl_hist = selecionar_protocolos_por_porcentagem(
                df,
                workflow_d1,
                override_pct,
                seed=seed,
                historico=historico if excluir_historico else None,
            )
            plano.excluidos_historico_total += excl_hist
            qtd_salva = len(selecionados)
            if excl_hist > 0:
                plano.warnings.append(
                    f"{workflow}: {excl_hist} protocolo(s) excluído(s) por histórico de replicação"
                )
            protocolos = (
                selecionados[COLUNA_PROTOCOLO].astype(str).tolist() if not selecionados.empty else []
            )
            plano.protocolos_por_workflow[workflow] = protocolos
            horas_usadas = sorted(contagens.keys())
            faixa = f"{horas_usadas[0]:02d}h-{horas_usadas[-1]:02d}h" if horas_usadas else ""
            pct_atingido = round(100 * qtd_salva / total_disponivel, 1) if total_disponivel else 0.0
            obs_parts = [f"Amostra {formatar_amostra_override(override_pct)} do D-1"]
            plano.resumo.append({
                "Workflow": workflow,
                "Workflow D1": workflow_d1,
                "Amostra Diaria": item.get("amostra_diaria", amostra),
                "Amostra Solicitada": formatar_amostra_override(override_pct),
                "Amostra Redistribuida": 0,
                "Amostra Efetiva": qtd_salva,
                "Protocolos Salvos": qtd_salva,
                "Excluidos Historico": excl_hist,
                "Disponivel D1": total_disponivel,
                "Horas Utilizadas": len(horas_usadas),
                "Faixa Horaria": faixa,
                "Status": _classificar_status(qtd_salva, qtd_salva, sem_registro=False),
                "Pct Atingido": pct_atingido,
                "Arquivo CSV": nome_csv,
                "Observacao": "; ".join(obs_parts),
                **meta_base,
                **_meta_colunas_config(item["row"], pasta_volumetria),
            })
            if protocolos:
                det = selecionados[[COLUNA_PROTOCOLO, COLUNA_WORKFLOW_PARQUET, COLUNA_DATA_ANALISE, "_hora"]].copy()
                det = det.rename(columns={"_hora": "Hora"})
                det["WorkflowConfig"] = workflow
                detalhes.append(det)
            continue

        bonus = int(bonus_por_workflow.get(workflow, 0))
        amostra_efetiva = amostra + bonus

        alocacao = alocar_amostra_por_hora(contagens, amostra_efetiva)
        selecionados, excl_hist = selecionar_protocolos(
            df,
            workflow_d1,
            alocacao,
            seed=seed,
            historico=historico if excluir_historico else None,
        )
        plano.excluidos_historico_total += excl_hist
        qtd_salva = len(selecionados)

        if excl_hist > 0:
            plano.warnings.append(
                f"{workflow}: {excl_hist} protocolo(s) excluído(s) por histórico de replicação"
            )
        if qtd_salva < amostra_efetiva:
            plano.warnings.append(
                f"{workflow}: efetivo {amostra_efetiva} (solicitado {amostra}"
                f"{f' +{bonus} redist' if bonus else ''}), "
                f"disponíveis {total_disponivel}, selecionados {qtd_salva}"
            )

        protocolos = selecionados[COLUNA_PROTOCOLO].astype(str).tolist()
        plano.protocolos_por_workflow[workflow] = protocolos

        horas_usadas = sorted(alocacao.keys())
        faixa = f"{horas_usadas[0]:02d}h-{horas_usadas[-1]:02d}h" if horas_usadas else ""
        pct = round(100 * qtd_salva / amostra_efetiva, 1) if amostra_efetiva else 0.0
        obs_parts = []
        if bonus > 0:
            obs_parts.append(f"+{bonus} redistribuídos de workflows sem D-1")
        if qtd_salva < amostra_efetiva:
            obs_parts.append(f"Amostra parcial ({qtd_salva}/{amostra_efetiva})")
        plano.resumo.append({
            "Workflow": workflow,
            "Workflow D1": workflow_d1,
            "Amostra Diaria": item["amostra_diaria"],
            "Amostra Solicitada": amostra,
            "Amostra Redistribuida": bonus,
            "Amostra Efetiva": amostra_efetiva,
            "Protocolos Salvos": qtd_salva,
            "Excluidos Historico": excl_hist,
            "Disponivel D1": total_disponivel,
            "Horas Utilizadas": len(horas_usadas),
            "Faixa Horaria": faixa,
            "Status": _classificar_status(amostra_efetiva, qtd_salva, sem_registro=False),
            "Pct Atingido": pct,
            "Arquivo CSV": nome_csv,
            "Observacao": "; ".join(obs_parts),
            **meta_base,
            **_meta_colunas_config(item["row"], pasta_volumetria),
        })

        if not selecionados.empty:
            det = selecionados[[COLUNA_PROTOCOLO, COLUNA_WORKFLOW_PARQUET, COLUNA_DATA_ANALISE]].copy()
            det["Hora"] = selecionados["_hora"]
            det["WorkflowConfig"] = workflow
            det["AmostraEfetiva"] = amostra_efetiva
            for h, q in alocacao.items():
                det.loc[det["Hora"] == h, "QuotaHora"] = q
            detalhes.append(det)

        log.info(
            "Workflow %s | solicitada=%d | redist=+%d | efetiva=%d | selecionados=%d | horas=%s",
            workflow,
            amostra,
            bonus,
            amostra_efetiva,
            len(protocolos),
            horas_usadas,
        )

    plano.workflows = [item["workflow"] for item in itens_config]
    todos_csv = {
        wf: plano.protocolos_por_workflow.get(wf, [])
        for wf in plano.workflows
    }
    paths_ok = exportar_protocolos_csv(todos_csv, pasta_protocolos)
    plano.csv_paths.update(paths_ok)

    plano.seed_amostra = int(seed)
    df_det = pd.concat(detalhes, ignore_index=True) if detalhes else pd.DataFrame()
    estado = inicializar_estado_execucao(
        plano,
        parquet_referencia=parquet_path.name,
        data_referencia_d1=data_ref.strftime("%d/%m/%Y"),
        data_execucao=data_exec.strftime("%d/%m/%Y %H:%M"),
        seed_amostra=int(seed),
    )
    plano.estado_execucao_path = caminho_estado_execucao(run_id)
    exportar_relatorio_excel(
        plano,
        parquet_path=parquet_path,
        data_ref=data_ref,
        estado=estado,
        df_plano=df_det if salvar_plano_detalhado else pd.DataFrame(),
        data_exec=data_exec,
    )
    if salvar_plano_detalhado and not df_det.empty:
        plano.plano_detalhado_path = plano.relatorio_excel_path

    log.info(
        "Saídas da replicação | run_id=%s | base=%s | protocolos=%s | relatorio=%s | estado=%s",
        run_id,
        PASTA_REPLICACAO_AUD_BASE,
        pasta_protocolos,
        pasta_resumo,
        plano.relatorio_excel_path,
        plano.estado_execucao_path,
    )

    return plano


def carregar_plano_por_run_id(run_id: str, settings: Optional[dict] = None) -> PlanoReplicacao:
    """Reconstrói PlanoReplicacao a partir de execucao_{run_id}.json e CSVs existentes."""
    settings = settings or {}
    estado = carregar_estado_execucao(run_id)
    if not estado:
        raise FileNotFoundError(f"Estado de execução não encontrado para run_id={run_id}")

    pasta_protocolos = Path(estado.get("pasta_protocolos", "")) or _pasta_saida_protocolos(run_id)
    if not pasta_protocolos.exists():
        pasta_protocolos = _pasta_saida_protocolos(run_id)

    apenas_pendentes = bool(settings.get("apenas_pendentes", True))
    forcar = bool(settings.get("forcar_reexecucao", False))

    plano = PlanoReplicacao(
        data_referencia=datetime.now(),
        pasta_protocolos=pasta_protocolos,
        pasta_resumo=PASTA_REPLICACAO_AUD_RESUMO,
        run_id=run_id,
        pasta_execucao=pasta_protocolos,
        estado_execucao_path=caminho_estado_execucao(run_id),
    )

    rel_path = _resolver_caminho_relatorio_excel(run_id)
    if rel_path.exists():
        plano.relatorio_excel_path = rel_path
        plano.plano_detalhado_path = rel_path
        try:
            df_res = pd.read_excel(rel_path, sheet_name="Resumo", engine="openpyxl")
            plano.resumo = (
                df_res[df_res["Workflow"].astype(str) != "TOTAL"].to_dict("records")
            )
            if not df_res.empty:
                primeira = df_res.iloc[0]
                plano.parquet_referencia = str(
                    estado.get("parquet_referencia")
                    or primeira.get("Parquet Referencia", "")
                    or ""
                )
                plano.data_referencia_d1_fmt = str(
                    estado.get("data_referencia_d1")
                    or primeira.get("Data Referencia D1", "")
                    or ""
                )
        except Exception as exc:
            log.debug("Resumo não lido do Excel na retomada: %s", exc)
    else:
        resumo_path = PASTA_REPLICACAO_AUD_RESUMO / f"{REPLICACAO_AUD_RESUMO_PREFIXO}{run_id}.csv"
        if resumo_path.exists():
            plano.resumo_csv_path = resumo_path
            df_leg = pd.read_csv(resumo_path, sep=";", encoding="utf-8-sig")
            plano.resumo = df_leg[df_leg["Workflow"].astype(str) != "TOTAL"].to_dict("records")
    plano.default_xlsx_path = str(estado.get("default_xlsx_path", "") or "")
    plano.parquet_referencia = str(
        estado.get("parquet_referencia", "") or plano.parquet_referencia or ""
    )
    plano.data_referencia_d1_fmt = str(
        estado.get("data_referencia_d1", "") or plano.data_referencia_d1_fmt or ""
    )
    plano.data_execucao_fmt = str(estado.get("data_execucao", "") or "")

    workflows_estado = estado.get("workflows", {})
    mapa_brflow: Dict[str, str] = {}
    paths_cfg = _resolver_paths_config(settings)
    default_path = Path(
        str(estado.get("default_xlsx_path", "") or plano.default_xlsx_path or paths_cfg["default"])
    )
    try:
        if default_path.exists():
            mapa = carregar_mapa_workflow_d1(default_path, settings=settings)
            for _, row in mapa.iterrows():
                wf = str(row[COLUNA_CONFIG_WORKFLOW]).strip()
                if wf:
                    mapa_brflow[wf] = str(row[COLUNA_CONFIG_WORKFLOW_SELENIUM]).strip()
    except Exception as exc:
        log.debug("Mapa Workflow - selenium não recarregado na retomada: %s", exc)

    desabilitados: set = set()
    try:
        if default_path.exists():
            desabilitados = carregar_chaves_workflow_d1_desabilitados(
                default_path, settings=settings
            )
    except Exception as exc:
        log.debug("Chaves Status=False não recarregadas na retomada: %s", exc)

    for workflow, info in workflows_estado.items():
        if _normalizar_workflow(workflow) in desabilitados:
            log.info(
                "Retomada | workflow %s ignorado (Status=False no Default.xlsx)",
                workflow,
            )
            continue

        status = str(info.get("status", "PENDENTE"))
        csv_str = str(info.get("csv", "") or "")
        csv_path = _resolver_csv_workflow_plano(plano, workflow, csv_str)
        protocolos = list(_ler_protocolos_de_csv(csv_path)) if csv_path.exists() else []
        if csv_path.exists():
            plano.csv_paths[workflow] = csv_path
        plano.protocolos_por_workflow[workflow] = protocolos

        if str(info.get("motivo", "") or "").strip() == "sem D-1":
            if workflow not in plano.workflows_sem_registro:
                plano.workflows_sem_registro.append(workflow)

        if status in ("UPLOAD_OK", "SALVO_OK", "INATIVO", "PULADO") and apenas_pendentes and not forcar:
            continue
        if not workflow_elegivel_upload(protocolos, csv_path):
            log.info(
                "Retomada | workflow %s sem protocolos reais; pulando upload",
                workflow,
            )
            continue
        brflow = str(info.get("workflow_brflow", "") or mapa_brflow.get(workflow, "") or workflow).strip()
        plano.workflow_brflow[workflow] = brflow
        plano.workflows.append(workflow)

    log.info(
        "Plano carregado run_id=%s | workflows para Selenium=%d | pasta=%s",
        run_id,
        len(plano.workflows),
        pasta_protocolos,
    )
    return plano


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    plano = gerar_plano_replicacao()
    print(f"RunId: {plano.run_id}")
    print(f"Workflows: {len(plano.workflows)}")
    for wf, path in plano.csv_paths.items():
        print(f"  {wf} -> {path} ({len(plano.protocolos_por_workflow.get(wf, []))} protocolos)")
    print(f"Base: {PASTA_REPLICACAO_AUD_BASE}")
    print(f"Protocolos: {plano.pasta_protocolos}")
    print(f"Resumo: {plano.pasta_resumo}")
    if plano.relatorio_excel_path:
        print(f"  Relatório Excel: {plano.relatorio_excel_path}")
    if plano.workflows_sem_registro:
        print(f"Sem registro no D-1 (CSV vazio): {plano.workflows_sem_registro}")
    if plano.warnings:
        print("Warnings:")
        for w in plano.warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
