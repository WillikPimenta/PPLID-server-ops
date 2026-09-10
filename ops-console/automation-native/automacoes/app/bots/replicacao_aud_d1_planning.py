"""Planejamento da replicação D-1: volumetria do parquet diário + redistribuição priorizada."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from app.bots import replicacao_aud_planning as rap
from app.bots.meta_cliente_mensal import (
    carregar_headroom_meta_mensal,
    carregar_metas_por_workflow,
    recalcular_snapshot_mensal,
    registrar_consumo_meta_plano_concluido,
    remover_consumo_meta_run,
    resolver_ano_mes,
    run_id_registrado_no_ledger,
)
from app.bots.replicacao_d1.planning_phases import (
    PLANNING_ALLOCATE,
    PLANNING_CONFIG,
    PLANNING_DONE,
    PLANNING_RETRO,
    PLANNING_SOURCE,
    PLANNING_START,
    plan_log,
    truncate_hash,
)
from app.bots.replicacao_aud_excel_format import (
    exportar_workbook_formatado,
    montar_dataframe_dashboard_secoes_d1,
)
from app.config import (
    COLUNA_CONFIG_AUTOMATICOS,
    COLUNA_CONFIG_CATEGORIA,
    COLUNA_CONFIG_CLIENTE,
    COLUNA_CONFIG_FILA,
    COLUNA_CONFIG_MANUAIS,
    COLUNA_CONFIG_META_CLIENTE,
    COLUNA_CONFIG_NOME_REGRA_BRFLOW,
    COLUNA_CONFIG_SEGMENTO,
    COLUNA_CONFIG_TOTAL,
    COLUNA_CONFIG_USAR_ARQUIVO_CSV,
    COLUNA_CONFIG_WORKFLOW,
    COLUNA_CONFIG_WORKFLOW_D1,
    COLUNA_CONFIG_WORKFLOW_SELENIUM,
    CONFIG_DEFAULT_D1_XLSX,
    ESCALA_AUDITORES_D1_CSV,
    PASTA_DETALHADO_D1,
    PASTA_REPLICACAO_AUD_D1_PROTOCOLOS,
    PASTA_REPLICACAO_AUD_D1_RELATORIOS,
    PASTA_REPLICACAO_AUD_D1_RESUMO,
    REPLICACAO_AUD_D1_DASHBOARD_PREFIXO,
    REPLICACAO_AUD_D1_DIAS_HISTORICO_DEFAULT,
    REPLICACAO_AUD_D1_DIAS_RETENCAO_PLANOS_DEFAULT,
    REPLICACAO_AUD_D1_EXECUCAO_PREFIXO,
    REPLICACAO_AUD_D1_EXCLUIR_HISTORICO_DEFAULT,
    REPLICACAO_AUD_D1_FALLBACK_PARQUET_DEFAULT,
    REPLICACAO_AUD_D1_LIMPAR_PLANOS_AO_GERAR_DEFAULT,
    REPLICACAO_AUD_D1_LIMPAR_PLANOS_APOS_CONCLUSAO_DEFAULT,
    REPLICACAO_AUD_D1_LIMPAR_PLANOS_AUTOMATICO_DEFAULT,
    REPLICACAO_AUD_D1_MANTER_PLANOS_ULTIMOS_N_DEFAULT,
    REPLICACAO_AUD_D1_PLANO_PREFIXO,
    REPLICACAO_AUD_D1_RELATORIO_PREFIXO,
    REPLICACAO_AUD_D1_RESUMO_PREFIXO,
    REPLICACAO_AUD_D1_SEED_DEFAULT,
    REPLICACAO_AUD_D1_SOBRESCREVER_DEFAULT,
    REPLICACAO_AUD_D1_USAR_ESCALA_DEFAULT,
    REPLICACAO_FILA_G_AUDITORIA,
    REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
    REPLICACAO_D1_SUBPASTA_BRFLOW,
    REPLICACAO_D1_SUBPASTA_CASE,
    REPLICACAO_AUD_SINCRONIZAR_WORKFLOW_D1_DEFAULT,
    META_CLIENTE_FALLBACK_SEM_CAP_DEFAULT,
    PASTA_REPLICACAO_AUD_D1_CONFIG,
    PASTA_REPLICACAO_AUD_D1_BASE,
    PASTA_REPLICACAO_AUD_D1_BI,
    ARQUIVO_PARQUET_CONSOLIDADO_D1,
)
from app.infrastructure.parquet_reader import ler_parquet

log = logging.getLogger("robots.replicacao_aud_d1_planning")
_DATABASE_ONLY_RUN_IDS: set[str] = set()
RETROATIVO_SPLIT_REFERENCIA_PCT = 50


# Re-exportações usadas pelo bot Selenium
PlanoReplicacao = rap.PlanoReplicacao
InfoCapacidade = rap.InfoCapacidade
EscalaAuditoresAusenteError = rap.EscalaAuditoresAusenteError
COLUNA_PROTOCOLO = rap.COLUNA_PROTOCOLO
COLUNA_WORKFLOW_PARQUET = rap.COLUNA_WORKFLOW_PARQUET
COLUNA_DATA_ANALISE = rap.COLUNA_DATA_ANALISE
COLUNA_VOLUMETRIA_CLIENTE = rap.COLUNA_VOLUMETRIA_CLIENTE
_normalizar_workflow = rap._normalizar_workflow
_workflow_d1_da_linha = rap._workflow_d1_da_linha
_resolver_workflow_nome_brflow = rap._resolver_workflow_nome_brflow
workflow_nome_brflow = rap.workflow_nome_brflow
_as_int = rap._as_int
_sanitizar_nome_arquivo = rap._sanitizar_nome_arquivo
_eh_cliente_volumetria_valido = rap._eh_cliente_volumetria_valido
_eh_workflow_volumetria_valido = rap._eh_workflow_volumetria_valido
_meta_colunas_config = rap._meta_colunas_config
_classificar_status = rap._classificar_status
_parse_bool_setting = rap._parse_bool_setting
validar_escala_replicacao_pre_exec = rap.validar_escala_replicacao_pre_exec
_resolver_data_escala_replicacao = rap._resolver_data_escala_replicacao
resolver_parquet_d1 = rap.resolver_parquet_d1
_validar_parquet = rap._validar_parquet
contar_por_hora = rap.contar_por_hora
alocar_amostra_por_hora = rap.alocar_amostra_por_hora
selecionar_protocolos = rap.selecionar_protocolos
selecionar_protocolos_retroativo_split = rap.selecionar_protocolos_retroativo_split
selecionar_todos_protocolos = rap.selecionar_todos_protocolos
selecionar_protocolos_por_porcentagem = rap.selecionar_protocolos_por_porcentagem
contar_disponivel_por_hora = rap.contar_disponivel_por_hora
carregar_workflows_amostra_100 = rap.carregar_workflows_amostra_100
carregar_workflows_amostra_override = rap.carregar_workflows_amostra_override
formatar_amostra_override = rap.formatar_amostra_override
exportar_protocolos_csv = rap.exportar_protocolos_csv
exportar_protocolos_csv_por_fila = rap.exportar_protocolos_csv_por_fila
exportar_csv_fallback_vazio_por_fila = rap.exportar_csv_fallback_vazio_por_fila
_meta_resumo_fila = rap._meta_resumo_fila
_resolver_csv_workflow_plano = rap._resolver_csv_workflow_plano
normalizar_csv_escala_auditores = rap.normalizar_csv_escala_auditores
subpasta_protocolos_fila = rap.subpasta_protocolos_fila
exportar_csv_fallback_vazio = rap.exportar_csv_fallback_vazio
_csv_relativo_plano = rap._csv_relativo_plano
distribuir_proporcional = rap.distribuir_proporcional
aplicar_escala_auditores_config = rap.aplicar_escala_auditores_config
filtrar_config_por_fila = rap.filtrar_config_por_fila
filtrar_config_destinos_desabilitados = rap.filtrar_config_destinos_desabilitados
aplicar_escala_auditores_config_por_fila = rap.aplicar_escala_auditores_config_por_fila
resolver_fila_linha = rap.resolver_fila_linha
carregar_mapa_workflow_d1 = rap.carregar_mapa_workflow_d1
carregar_categoria_clientes = rap.carregar_categoria_clientes
carregar_chaves_workflow_d1_desabilitados = rap.carregar_chaves_workflow_d1_desabilitados
sincronizar_pendentes_workflow_d1 = rap.sincronizar_pendentes_workflow_d1
_ler_meta_calculadora_padrao = rap._ler_meta_calculadora_padrao
_calcular_amostra_total_planilha = rap._calcular_amostra_total_planilha
_ler_amostras_materializadas_calculadora = rap._ler_amostras_materializadas_calculadora
_montar_dataframe_resumo = rap._montar_dataframe_resumo
_montar_dataframe_dashboard = rap._montar_dataframe_dashboard
_enriquecer_resumo_com_estado_brflow = rap._enriquecer_resumo_com_estado_brflow
_parse_data_referencia_d1 = rap._parse_data_referencia_d1
_parse_data_analise = rap._parse_data_analise
_arquivo_dentro_janela = rap._arquivo_dentro_janela
_formatar_datetime_brflow = rap._formatar_datetime_brflow
resolver_canal_destino = rap.resolver_canal_destino
eh_fila_modo_qtd = rap.eh_fila_modo_qtd
resolver_modo_replicacao = rap.resolver_modo_replicacao
resolver_modo_replicacao_linha = rap.resolver_modo_replicacao_linha
REPLICACAO_MODO_PROTOCOLOS = rap.REPLICACAO_MODO_PROTOCOLOS
REPLICACAO_MODO_QTD = rap.REPLICACAO_MODO_QTD
_ler_protocolos_de_csv = rap._ler_protocolos_de_csv
csv_protocolos_e_limpeza = rap.csv_protocolos_e_limpeza
validar_metas_categoria_clientes = rap.validar_metas_categoria_clientes

_HEADROOM_ILIMITADO = 10**9


def _calcular_amostra_base_qtd(
    *,
    amostra: int,
    amostra_override_pct: int | None,
    total_disponivel: int,
) -> int:
    """Quantidade alvo do workflow a partir da calculadora, sem selecionar protocolos."""
    base = max(0, _as_int(amostra))
    if amostra_override_pct is None:
        return base
    pct = int(amostra_override_pct)
    if pct >= 100:
        target = total_disponivel if total_disponivel > 0 else base
    elif base > 0:
        target = max(1, round(base * pct / 100))
    else:
        target = 0
    if total_disponivel > 0:
        target = min(target, total_disponivel)
    return max(0, target)


def _protocolos_exportaveis_por_modo(plano: PlanoReplicacao) -> Dict[str, List[str]]:
    """Retorna apenas selecoes concretas de workflows congelados em modo CSV."""
    return {
        workflow: plano.protocolos_por_workflow.get(workflow, [])
        for workflow in plano.workflows
        if plano.workflow_modo_replicacao.get(workflow) == REPLICACAO_MODO_PROTOCOLOS
    }


def _append_resumo_modo_qtd(
    plano: PlanoReplicacao,
    *,
    workflow: str,
    workflow_d1: str,
    item: dict,
    amostra_efetiva_final: int,
    amostra_solicitada,
    amostra_redistribuida: int,
    total_disponivel: int,
    meta_base: dict,
    info_capacidade,
    parquet_ref: str,
    observacao_extra: str = "",
) -> None:
    obs_parts = ["Modo quantidade"]
    if observacao_extra:
        obs_parts.append(observacao_extra)
    if amostra_redistribuida > 0:
        obs_parts.append(
            f"+{amostra_redistribuida} redistribuídos (prioridade cliente/categoria/saldo de balanceamento)"
        )
    plano.resumo.append(
        {
            "Workflow": workflow,
            "Workflow D1": workflow_d1,
            "Amostra Diaria": item["amostra_diaria"],
            "Amostra Solicitada": amostra_solicitada,
            "Amostra Redistribuida": amostra_redistribuida,
            "Amostra Efetiva": amostra_efetiva_final,
            "Protocolos Salvos": 0,
            "Excluidos Historico": 0,
            "Disponivel D1": total_disponivel,
            "Horas Utilizadas": 0,
            "Faixa Horaria": "",
            "Status": _classificar_status(
                amostra_efetiva_final, amostra_efetiva_final, sem_registro=total_disponivel <= 0
            ),
            "Pct Atingido": (
                round(100 * amostra_efetiva_final / item["amostra"], 1)
                if _as_int(item.get("amostra", 0)) > 0
                else 0.0
            ),
            "Arquivo CSV": "— (modo qtd)",
            "Modo Replicacao": REPLICACAO_MODO_QTD,
            "Observacao": "; ".join(obs_parts),
            "RedistribuicaoTier": "",
            **meta_base,
            **_meta_resumo_fila(workflow, plano, info_capacidade),
            **_meta_colunas_config_d1(item["row"], parquet_ref),
        }
    )


def _reduzir_headroom_base_run(
    headroom: Dict[str, int],
    itens_com_d1: List[dict],
) -> Dict[str, int]:
    """Simula consumo da amostra base do run antes da redistribuição (regra B), por workflow."""
    out = dict(headroom)
    for item in itens_com_d1:
        wf = _wf_key_item(item)
        if out.get(wf, 0) >= _HEADROOM_ILIMITADO:
            continue
        out[wf] = max(0, out.get(wf, 0) - _as_int(item.get("amostra", 0)))
    return out


def _contagens_restantes_por_hora(
    df: pd.DataFrame,
    workflow_d1: str,
    *,
    excluir_protocolos: Optional[set] = None,
    historico: Optional[set] = None,
) -> Dict[int, int]:
    """Conta protocolos disponíveis por hora após exclusões."""
    if rap._PLANNING_WF_KEY in df.columns:
        sub = rap._filtrar_por_workflow(df, workflow_d1)
        sub[COLUNA_DATA_ANALISE] = rap._data_planejamento(sub)
    else:
        # Compatibilidade estrita: este helper historicamente usa igualdade
        # textual, ao contrário das demais seleções normalizadas.
        sub = df[df[COLUNA_WORKFLOW_PARQUET].astype(str) == str(workflow_d1)].copy()
        sub[COLUNA_DATA_ANALISE] = _parse_data_analise(sub[COLUNA_DATA_ANALISE])
    sub = sub[sub[COLUNA_DATA_ANALISE].notna()].copy()
    sub["_hora"] = sub[COLUNA_DATA_ANALISE].dt.hour
    excluir = set(excluir_protocolos or set())
    if historico:
        excluir |= set(historico)
    if excluir:
        sub = sub[~sub[COLUNA_PROTOCOLO].astype(str).isin(excluir)]
    sub = sub.drop_duplicates(subset=[COLUNA_PROTOCOLO])
    if sub.empty:
        return {}
    return sub.groupby("_hora").size().astype(int).to_dict()


def _ensure_d1_settings(settings: Optional[dict] = None) -> dict:
    settings = dict(settings or {})
    if not str(settings.get("replicacao_config_base", "") or "").strip():
        settings["replicacao_config_base"] = str(PASTA_REPLICACAO_AUD_D1_CONFIG)
    if not str(settings.get("escala_auditores_csv", "") or "").strip():
        settings["escala_auditores_csv"] = str(
            rap._resolver_csv_escala_auditores(settings)
        )
    settings["fallback_ultimo_parquet"] = _parse_bool_setting(
        settings.get("fallback_ultimo_parquet"),
        REPLICACAO_AUD_D1_FALLBACK_PARQUET_DEFAULT,
    )
    raw_fb = settings.get("fallback_parquet_dias_ausentes")
    if raw_fb is None:
        persistent = _persistent_dict_from_settings(settings)
        if isinstance(persistent, dict):
            raw_fb = persistent.get("fallback_parquet_dias_ausentes")
    settings["fallback_parquet_dias_ausentes"] = _parse_bool_setting(raw_fb, False)
    return settings


def planejamento_otimizado_ativo(settings: Optional[dict] = None) -> bool:
    """Ponto de rollout local; a origem da flag é injetada pelo chamador."""
    settings = settings or {}
    raw = settings.get(
        "optimized_planning",
        settings.get("replicacao_d1_planejamento_otimizado", False),
    )
    return _parse_bool_setting(raw, False)


def assinatura_logica_plano(plano: PlanoReplicacao) -> str:
    """Digest estável para shadow/paridade, sem caminhos ou timestamps de artefato."""
    resumo = []
    ignorar = {"Data Execucao", "Arquivo CSV", "Parquet Referencia", "Pasta Volumetria"}
    for row in plano.resumo or []:
        resumo.append({k: v for k, v in row.items() if k not in ignorar})
    payload = {
        "workflows": list(plano.workflows or []),
        "protocolos_por_workflow": [
            [workflow, list((plano.protocolos_por_workflow or {}).get(workflow, []))]
            for workflow in plano.workflows or []
        ],
        "qtd_por_workflow": [
            [workflow, int(qtd)]
            for workflow, qtd in (plano.qtd_por_workflow or {}).items()
        ],
        "resumo": resumo,
        "warnings": list(plano.warnings or []),
        "selection_reason": sorted(
            (str(k), str(v))
            for k, v in (plano.selection_reason_por_protocolo or {}).items()
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolver_run_id_d1(data_exec: datetime, settings: Optional[dict] = None) -> str:
    settings = settings or {}
    informado = str(settings.get("run_id", "") or "").strip()
    if informado:
        return informado
    sobrescrever = bool(settings.get("sobrescrever", REPLICACAO_AUD_D1_SOBRESCREVER_DEFAULT))
    dia = data_exec.strftime("%Y%m%d")
    pasta_dia = PASTA_REPLICACAO_AUD_D1_PROTOCOLOS / dia
    if sobrescrever and pasta_dia.exists() and pasta_dia.is_dir():
        return dia
    return data_exec.strftime("%Y%m%d_%H%M%S")


def _run_id_resolution_reason(data_exec: datetime, settings: dict, run_id: str) -> str:
    if str(settings.get("run_id", "") or "").strip():
        return "informado"
    dia = data_exec.strftime("%Y%m%d")
    if run_id == dia:
        return "sobrescrever_dia"
    return "timestamp"


def _pasta_saida_protocolos_d1(run_id: str) -> Path:
    return PASTA_REPLICACAO_AUD_D1_PROTOCOLOS / run_id


def _pasta_relatorios_excel_d1() -> Path:
    PASTA_REPLICACAO_AUD_D1_RELATORIOS.mkdir(parents=True, exist_ok=True)
    return PASTA_REPLICACAO_AUD_D1_RELATORIOS


def _caminho_relatorio_excel_d1(run_id: str) -> Path:
    return _pasta_relatorios_excel_d1() / f"{REPLICACAO_AUD_D1_RELATORIO_PREFIXO}{run_id}.xlsx"


def _resolver_caminho_relatorio_excel_d1(
    run_id: str,
    path_hint: Optional[Union[str, Path]] = None,
) -> Path:
    if path_hint:
        hint = Path(path_hint)
        if hint.exists():
            return hint
    novo = _caminho_relatorio_excel_d1(run_id)
    if novo.exists():
        return novo
    legado = PASTA_REPLICACAO_AUD_D1_RESUMO / f"{REPLICACAO_AUD_D1_RELATORIO_PREFIXO}{run_id}.xlsx"
    return legado if legado.exists() else novo


def caminho_estado_execucao(run_id: str) -> Path:
    return PASTA_REPLICACAO_AUD_D1_RESUMO / f"{REPLICACAO_AUD_D1_EXECUCAO_PREFIXO}{run_id}.json"


def resolver_ultimo_run_id() -> Optional[str]:
    from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa, latest_plan_run_id_db

    if is_fonte_banco_ativa():
        return latest_plan_run_id_db()
    candidatos = sorted(
        PASTA_REPLICACAO_AUD_D1_RESUMO.glob(f"{REPLICACAO_AUD_D1_EXECUCAO_PREFIXO}*.json"),
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
        nome = path.stem.replace(REPLICACAO_AUD_D1_EXECUCAO_PREFIXO, "", 1)
        if nome:
            return nome
    pastas = sorted(
        (p for p in PASTA_REPLICACAO_AUD_D1_PROTOCOLOS.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if pastas:
        return pastas[0].name
    return None


def carregar_protocolos_historico(
    excluir_run_id: Optional[str] = None,
    dias_historico: int = REPLICACAO_AUD_D1_DIAS_HISTORICO_DEFAULT,
    referencia: Optional[datetime] = None,
) -> set:
    referencia = referencia or datetime.now()
    from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa, load_historical_protocols_db

    if is_fonte_banco_ativa():
        historico_db = load_historical_protocols_db(
            excluir_run_id=str(excluir_run_id or ""),
            dias_historico=dias_historico,
            referencia=referencia,
        )
        log.info("Histórico D-1 carregado do banco: %d únicos", len(historico_db))
        return historico_db
    historico: set = set()

    if PASTA_REPLICACAO_AUD_D1_PROTOCOLOS.exists():
        for pasta_run in PASTA_REPLICACAO_AUD_D1_PROTOCOLOS.iterdir():
            if not pasta_run.is_dir():
                continue
            if excluir_run_id and pasta_run.name == excluir_run_id:
                continue
            for csv_path in pasta_run.rglob("*.csv"):
                if _arquivo_dentro_janela(csv_path, dias_historico, referencia):
                    historico |= _ler_protocolos_de_csv(csv_path)

    if PASTA_REPLICACAO_AUD_D1_RESUMO.exists():
        for plano_path in PASTA_REPLICACAO_AUD_D1_RESUMO.glob(f"{REPLICACAO_AUD_D1_PLANO_PREFIXO}*.csv"):
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

    log.info("Histórico D-1 carregado: %d únicos (janela %d dias)", len(historico), dias_historico)
    return historico


COLUNA_WORKFLOW_PARQUET_NOME = "_nome_workflow_parquet"
COLUNA_PARQUET_MATRICULA = "matrícula"


def _serie_flag_matricula_parquet(df: pd.DataFrame) -> Optional[pd.Series]:
    """Normaliza matrícula do parquet: 0=manual, 1=automático."""
    from app.bots.rotina.io import _classificar_matricula_flag, _coluna_matricula_series

    col = _coluna_matricula_series(df)
    if col is None:
        col_nome = rap._find_column(df, [COLUNA_PARQUET_MATRICULA, "matricula", "Matrícula"])
        if not col_nome:
            return None
        col = df[col_nome]
        if isinstance(col, pd.DataFrame):
            col = col.iloc[:, 0]
    return col.map(_classificar_matricula_flag).astype(int)


def _resolver_mix_manual_automatico(settings: Optional[dict] = None) -> tuple[bool, int, int]:
    """Retorna (usar_mix, pct_manual, pct_automatico)."""
    settings = settings or {}
    usar = bool(settings.get("usar_amostra_mix_manual_automatico", False))
    try:
        pct_m = max(0, min(100, int(settings.get("amostra_pct_manual", 70))))
    except (TypeError, ValueError):
        pct_m = 70
    try:
        pct_a = max(0, min(100, int(settings.get("amostra_pct_automatico", 30))))
    except (TypeError, ValueError):
        pct_a = 30
    if pct_m + pct_a <= 0:
        return False, pct_m, pct_a
    return usar, pct_m, pct_a


def _alocar_cotas_mix(total: int, pct_manual: int, pct_automatico: int) -> tuple[int, int]:
    """Divide a meta total em cotas Manual / Automático (pesos normalizados)."""
    total = max(0, int(total))
    if total <= 0:
        return 0, 0
    peso_m = max(0, int(pct_manual))
    peso_a = max(0, int(pct_automatico))
    soma = peso_m + peso_a
    if soma <= 0:
        return total, 0
    n_manual = int(round(total * peso_m / soma))
    n_manual = max(0, min(total, n_manual))
    n_auto = total - n_manual
    return n_manual, n_auto


def _df_com_flag_matricula(df: pd.DataFrame) -> pd.DataFrame:
    """Garante coluna `_flag_mat` (0=manual, 1=automático) no DataFrame."""
    out = df
    if "_flag_mat" in out.columns:
        return out
    flags = _serie_flag_matricula_parquet(out)
    out = out.copy()
    if flags is None:
        out["_flag_mat"] = 0
    else:
        out["_flag_mat"] = flags.values
    return out


def selecionar_protocolos_com_mix_matricula(
    df: pd.DataFrame,
    workflow: str,
    amostra_total: int,
    *,
    pct_manual: int,
    pct_automatico: int,
    seed: int = REPLICACAO_AUD_D1_SEED_DEFAULT,
    historico: Optional[set] = None,
) -> tuple[pd.DataFrame, int, dict]:
    """
    Seleciona protocolos com preferência Manual (0) × Automático (1) por matrícula.

    Os percentuais são preferência, não cota rígida: se faltar Manual no workflow,
    completa com Automático e vice-versa, até a amostra solicitada.
    """
    amostra_total = max(0, int(amostra_total))
    detalhe = {
        "pct_manual": int(pct_manual),
        "pct_automatico": int(pct_automatico),
        "cota_manual": 0,
        "cota_automatico": 0,
        "salvos_manual": 0,
        "salvos_automatico": 0,
        "fallback_manual": 0,
        "fallback_automatico": 0,
    }
    if amostra_total <= 0:
        return pd.DataFrame(), 0, detalhe

    base = _df_com_flag_matricula(df)
    # Reduz o pool uma vez antes de separar Manual/Automático. No caminho
    # preparado isto é um lookup posicional, em vez de duas varreduras globais.
    base = rap._filtrar_por_workflow(base, workflow)
    n_manual, n_auto = _alocar_cotas_mix(amostra_total, pct_manual, pct_automatico)
    detalhe["cota_manual"] = n_manual
    detalhe["cota_automatico"] = n_auto

    df_manual = base[base["_flag_mat"].astype(int) == 0]
    df_auto = base[base["_flag_mat"].astype(int) == 1]
    excluidos = 0

    def _selecionar_pool(
        pool_df: pd.DataFrame,
        qtd: int,
        seed_offset: int,
        *,
        hist: Optional[set] = None,
    ) -> tuple[pd.DataFrame, int]:
        if qtd <= 0 or pool_df is None or pool_df.empty:
            return pd.DataFrame(), 0
        hist_eff = hist if hist is not None else historico
        contagens = contar_disponivel_por_hora(pool_df, workflow, historico=hist_eff)
        if not contagens:
            return pd.DataFrame(), 0
        aloc = alocar_amostra_por_hora(contagens, qtd)
        return selecionar_protocolos(
            pool_df,
            workflow,
            aloc,
            seed=seed + seed_offset,
            historico=hist_eff,
        )

    def _ja_usados(*frames: pd.DataFrame) -> set[str]:
        base_historico = rap.preparar_historico_planejamento(historico)
        usados = base_historico.copy()
        for frame in frames:
            if frame is not None and not frame.empty and COLUNA_PROTOCOLO in frame.columns:
                usados.update(frame[COLUNA_PROTOCOLO].astype(str))
        return usados

    def _montar() -> pd.DataFrame:
        partes_local: List[pd.DataFrame] = []
        if not sel_m.empty:
            partes_local.append(sel_m)
        if not sel_a.empty:
            partes_local.append(sel_a)
        if not partes_local:
            return pd.DataFrame()
        return pd.concat(partes_local, ignore_index=True).drop_duplicates(
            subset=[COLUNA_PROTOCOLO], keep="first"
        )

    sel_m, excl_m = _selecionar_pool(df_manual, n_manual, 17)
    sel_a, excl_a = _selecionar_pool(df_auto, n_auto, 31)
    excluidos += excl_m + excl_a

    # Preferência insuficiente → completa com o outro tipo.
    falta_m = max(0, n_manual - len(sel_m))
    if falta_m > 0:
        extra, excl_x = _selecionar_pool(df_auto, falta_m, 47, hist=_ja_usados(sel_m, sel_a))
        if not extra.empty:
            detalhe["fallback_automatico"] += len(extra)
            sel_a = pd.concat([sel_a, extra], ignore_index=True) if not sel_a.empty else extra
            excluidos += excl_x

    salvos_auto_pref = max(0, len(sel_a) - int(detalhe["fallback_automatico"]))
    falta_a = max(0, n_auto - salvos_auto_pref)
    if falta_a > 0:
        extra, excl_x = _selecionar_pool(df_manual, falta_a, 59, hist=_ja_usados(sel_m, sel_a))
        if not extra.empty:
            detalhe["fallback_manual"] += len(extra)
            sel_m = pd.concat([sel_m, extra], ignore_index=True) if not sel_m.empty else extra
            excluidos += excl_x

    # Ainda faltando para a amostra total? Qualquer pool disponível.
    out = _montar()
    restante = max(0, amostra_total - len(out))
    if restante > 0:
        hist_rest = _ja_usados(out)
        for seed_off, pool_df, chave_fb in (
            (71, df_manual, "fallback_manual"),
            (83, df_auto, "fallback_automatico"),
        ):
            if restante <= 0 or pool_df.empty:
                continue
            extra, excl_x = _selecionar_pool(pool_df, restante, seed_off, hist=hist_rest)
            if extra.empty:
                continue
            detalhe[chave_fb] += len(extra)
            if chave_fb == "fallback_manual":
                sel_m = pd.concat([sel_m, extra], ignore_index=True) if not sel_m.empty else extra
            else:
                sel_a = pd.concat([sel_a, extra], ignore_index=True) if not sel_a.empty else extra
            excluidos += excl_x
            hist_rest |= set(extra[COLUNA_PROTOCOLO].astype(str))
            out = _montar()
            restante = max(0, amostra_total - len(out))

    if out.empty:
        return pd.DataFrame(), excluidos, detalhe
    if len(out) > amostra_total:
        out = out.head(amostra_total)
    if "_flag_mat" in out.columns:
        detalhe["salvos_manual"] = int((out["_flag_mat"].astype(int) == 0).sum())
        detalhe["salvos_automatico"] = int((out["_flag_mat"].astype(int) == 1).sum())
    else:
        detalhe["salvos_manual"] = len(sel_m)
        detalhe["salvos_automatico"] = len(sel_a)
    return out.reset_index(drop=True), excluidos, detalhe


def _merge_mix_detalhe(dest: dict, src: dict) -> None:
    for key in ("cota_manual", "cota_automatico", "salvos_manual", "salvos_automatico", "fallback_manual", "fallback_automatico"):
        dest[key] = int(dest.get(key, 0) or 0) + int(src.get(key, 0) or 0)


def selecionar_protocolos_com_mix_matricula_retroativo_split(
    df: pd.DataFrame,
    workflow: str,
    amostra_total: int,
    *,
    pct_referencia: int = RETROATIVO_SPLIT_REFERENCIA_PCT,
    pct_manual: int,
    pct_automatico: int,
    seed: int = REPLICACAO_AUD_D1_SEED_DEFAULT,
    historico: Optional[set] = None,
) -> tuple[pd.DataFrame, int, dict, List[str]]:
    """Split 50/50 referência/retroativo com mix manual×automático em cada partição."""
    amostra_total = max(0, int(amostra_total))
    avisos: List[str] = []
    detalhe = {
        "pct_manual": int(pct_manual),
        "pct_automatico": int(pct_automatico),
        "cota_manual": 0,
        "cota_automatico": 0,
        "salvos_manual": 0,
        "salvos_automatico": 0,
        "fallback_manual": 0,
        "fallback_automatico": 0,
    }
    if amostra_total <= 0:
        return pd.DataFrame(), 0, detalhe, avisos

    sub = rap._filtrar_por_workflow(df, workflow)
    ref_df, retro_df, dias = rap._particionar_pool_retroativo(sub)

    if retro_df.empty:
        sel, excl, det = selecionar_protocolos_com_mix_matricula(
            df,
            workflow,
            amostra_total,
            pct_manual=pct_manual,
            pct_automatico=pct_automatico,
            seed=seed,
            historico=historico,
        )
        return sel, excl, det, avisos

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

    historico = rap.preparar_historico_planejamento(historico)
    excl_total = 0
    partes: List[pd.DataFrame] = []
    usados: set[str] = set()

    def _hist_efetivo() -> set:
        return historico | usados

    def _registrar_usados(frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        usados.update(
            rap._protocolo_chave_plano(p)
            for p in frame[COLUNA_PROTOCOLO].astype(str)
            if rap._protocolo_chave_plano(p)
        )

    if cota_ref > 0 and not ref_df.empty:
        sel_ref, excl_ref, det_ref = selecionar_protocolos_com_mix_matricula(
            ref_df,
            workflow,
            cota_ref,
            pct_manual=pct_manual,
            pct_automatico=pct_automatico,
            seed=seed,
            historico=_hist_efetivo(),
        )
        excl_total += excl_ref
        _merge_mix_detalhe(detalhe, det_ref)
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
        day_quota = rap._alocar_cota_retroativo_por_dia(
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
            sel_day, excl_day, det_day = selecionar_protocolos_com_mix_matricula(
                day_df,
                workflow,
                quota,
                pct_manual=pct_manual,
                pct_automatico=pct_automatico,
                seed=seed_day,
                historico=_hist_efetivo(),
            )
            seed_day += 1
            excl_total += excl_day
            _merge_mix_detalhe(detalhe, det_day)
            if not sel_day.empty:
                partes.append(sel_day)
                _registrar_usados(sel_day)

    if not partes:
        return pd.DataFrame(), excl_total, detalhe, avisos
    merged = pd.concat(partes, ignore_index=True)
    merged["_proto_key"] = merged[COLUNA_PROTOCOLO].astype(str).map(rap._protocolo_chave_plano)
    merged = merged.drop_duplicates(subset=["_proto_key"], keep="first").drop(columns=["_proto_key"])
    if len(merged) > amostra_total:
        merged = merged.head(amostra_total)
    return merged.reset_index(drop=True), excl_total, detalhe, avisos


def _obs_mix_matricula(mix: Optional[dict]) -> str:
    if not mix:
        return ""
    fb_m = int(mix.get("fallback_manual", 0) or 0)
    fb_a = int(mix.get("fallback_automatico", 0) or 0)
    texto = (
        f"Pref. Manual×Auto {mix.get('pct_manual', 0)}%/{mix.get('pct_automatico', 0)}% "
        f"(salvos {mix.get('salvos_manual', 0)}/{mix.get('salvos_automatico', 0)})"
    )
    if fb_m or fb_a:
        texto += f"; completou c/ Manual+{fb_m} Auto+{fb_a}"
    return texto


def carregar_volumetria_d1_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega contagem do parquet D-1 por Workflow (nome D-1)."""
    _validar_parquet(df)
    col_wf = COLUNA_WORKFLOW_PARQUET
    col_cli = rap._find_column(df, [COLUNA_CONFIG_CLIENTE, "Cliente", "cliente"])

    flags = _serie_flag_matricula_parquet(df)
    work = df[[col_wf]].copy()
    if flags is not None:
        work["_flag_mat"] = flags.values
    else:
        log.warning(
            "Coluna 'matrícula' ausente no parquet; volumetria Manual = total, Automáticos = 0"
        )
        work["_flag_mat"] = 0

    counts = (
        work.groupby([col_wf, "_flag_mat"], dropna=False)
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    for flag in (0, 1):
        if flag not in counts.columns:
            counts[flag] = 0
    counts[COLUNA_CONFIG_MANUAIS] = counts[0].astype(int)
    counts[COLUNA_CONFIG_AUTOMATICOS] = counts[1].astype(int)
    counts[COLUNA_CONFIG_TOTAL] = counts[COLUNA_CONFIG_MANUAIS] + counts[COLUNA_CONFIG_AUTOMATICOS]
    agrupado = counts.drop(columns=[0, 1], errors="ignore")
    agrupado = agrupado[agrupado[col_wf].map(_eh_workflow_volumetria_valido)].copy()
    agrupado = agrupado.rename(columns={col_wf: COLUNA_WORKFLOW_PARQUET_NOME})
    agrupado["_wf_d1_key"] = agrupado[COLUNA_WORKFLOW_PARQUET_NOME].map(_normalizar_workflow)

    if col_cli:
        clientes = (
            df.groupby(col_wf)[col_cli]
            .agg(lambda s: next((str(v).strip() for v in s if _eh_cliente_volumetria_valido(v)), ""))
            .reset_index()
        )
        clientes = clientes.rename(columns={col_wf: COLUNA_WORKFLOW_PARQUET_NOME})
        agrupado = agrupado.merge(clientes, on=COLUNA_WORKFLOW_PARQUET_NOME, how="left")
        agrupado[COLUNA_VOLUMETRIA_CLIENTE] = agrupado[col_cli].fillna("").astype(str)
        agrupado = agrupado.drop(columns=[col_cli])
    else:
        agrupado[COLUNA_VOLUMETRIA_CLIENTE] = ""

    log.info(
        "Volumetria D-1 | %d workflow(s) | total=%d | automáticos=%d | manuais=%d",
        len(agrupado),
        int(agrupado[COLUNA_CONFIG_TOTAL].sum()),
        int(agrupado[COLUNA_CONFIG_AUTOMATICOS].sum()),
        int(agrupado[COLUNA_CONFIG_MANUAIS].sum()),
    )
    return agrupado


def calcular_amostras_from_meta(df_workflows: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """Calculadora D-1 a partir de parâmetros (snapshot/banco), sem Excel."""
    linhas: List[dict] = []
    for _, row in df_workflows.iterrows():
        total_vol = float(row.get(COLUNA_CONFIG_TOTAL) or 0)
        if total_vol <= 0:
            continue
        categoria = str(row.get(COLUNA_CONFIG_CATEGORIA, "") or "")
        amostra_total = _calcular_amostra_total_planilha(total_vol, meta, categoria)
        amostra_diaria = amostra_total
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
    log.info("Calculadora D-1 (banco) | %d amostra(s) calculada(s)", len(linhas))
    return pd.DataFrame(linhas)


def calcular_amostras_calculadora_d1(
    path: Path,
    df_workflows: pd.DataFrame,
) -> pd.DataFrame:
    """Calculadora Padrão com count D-1: amostra_diaria = amostra_total (sem /24)."""
    meta = _ler_meta_calculadora_padrao(path)
    return calcular_amostras_from_meta(df_workflows, meta)


def carregar_amostras_d1(
    default_path: Optional[Path] = None,
    settings: Optional[dict] = None,
    df_workflows: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    settings = _ensure_d1_settings(settings)
    paths = rap._resolver_paths_config(settings)
    path = Path(default_path) if default_path else paths["default"]
    if not path.exists():
        raise FileNotFoundError(f"Default.xlsx não encontrado: {path}")

    cols = ["_wf_key", "amostra_diaria", "amostra_total", "amostra_conf_prod"]
    materializado = _ler_amostras_materializadas_calculadora(path)

    if df_workflows is None or df_workflows.empty:
        return materializado if not materializado.empty else pd.DataFrame(columns=cols)

    keys_needed = set(df_workflows["_wf_key"].astype(str).tolist())
    if materializado.empty:
        return calcular_amostras_calculadora_d1(path, df_workflows)

    keys_cobertas = set(materializado["_wf_key"].astype(str).tolist())
    faltantes = keys_needed - keys_cobertas
    if not faltantes:
        return materializado[materializado["_wf_key"].isin(keys_needed)].copy()

    df_faltantes = df_workflows[df_workflows["_wf_key"].isin(faltantes)]
    calculado = calcular_amostras_calculadora_d1(path, df_faltantes)
    partes = [materializado[materializado["_wf_key"].isin(keys_needed)]]
    if not calculado.empty:
        partes.append(calculado)
    return pd.concat(partes, ignore_index=True).drop_duplicates(subset=["_wf_key"], keep="first")


def _montar_config_replicacao_d1_from_banco(
    df_parquet: pd.DataFrame,
    settings: dict,
) -> tuple[pd.DataFrame, List[str], str]:
    """Monta config a partir do parquet D-1 + snapshot PostgreSQL (sem Excel)."""
    from app.bots.replicacao_d1_db_bridge import load_planning_dataframes, register_pending_workflow

    warnings: List[str] = []
    vol = carregar_volumetria_d1_parquet(df_parquet)
    parquet_ref = str(settings.get("_parquet_ref", "") or "parquet D-1")

    run_id = str(settings.get("run_id", "") or "").strip() or None
    db_data = load_planning_dataframes(settings, run_id=run_id)
    mapa_d1 = db_data["mapa_workflow_d1"].copy()
    categorias = db_data["categoria_clientes"].copy()
    calc_params = dict(db_data.get("calculadora_params") or {})

    if mapa_d1.empty:
        raise ValueError("Nenhum workflow ativo cadastrado no banco para planejamento D-1.")
    if COLUNA_CONFIG_USAR_ARQUIVO_CSV not in mapa_d1.columns:
        mapa_d1[COLUNA_CONFIG_USAR_ARQUIVO_CSV] = pd.NA
    if COLUNA_CONFIG_NOME_REGRA_BRFLOW not in mapa_d1.columns:
        mapa_d1[COLUNA_CONFIG_NOME_REGRA_BRFLOW] = ""

    mapa_d1["_wf_d1_key"] = mapa_d1[COLUNA_CONFIG_WORKFLOW_D1].map(_normalizar_workflow)
    merged = vol.merge(
        mapa_d1[
            [
                COLUNA_CONFIG_WORKFLOW,
                COLUNA_CONFIG_WORKFLOW_SELENIUM,
                COLUNA_CONFIG_CLIENTE,
                COLUNA_CONFIG_WORKFLOW_D1,
                COLUNA_CONFIG_FILA,
                COLUNA_CONFIG_USAR_ARQUIVO_CSV,
                COLUNA_CONFIG_NOME_REGRA_BRFLOW,
                "_wf_key",
                "_wf_d1_key",
            ]
        ],
        on="_wf_d1_key",
        how="left",
        suffixes=("_vol", "_map"),
    )
    col_wf_map = f"{COLUNA_CONFIG_WORKFLOW}_map"
    col_wf_vol = f"{COLUNA_CONFIG_WORKFLOW}_vol"
    if col_wf_map in merged.columns:
        merged[COLUNA_CONFIG_WORKFLOW] = merged[col_wf_map].fillna(merged.get(col_wf_vol, ""))
        merged = merged.drop(columns=[c for c in (col_wf_vol, col_wf_map) if c in merged.columns])

    sem_mapa_mask = merged[COLUNA_CONFIG_CLIENTE].isna() | (merged[COLUNA_CONFIG_CLIENTE] == "")
    workflows_pendentes_config: List[str] = []
    workflows_pendentes_novos: List[str] = []
    workflows_pendentes_existentes: List[str] = []
    workflows_pendentes_falha_sync: List[str] = []
    for _, row in merged.loc[sem_mapa_mask].iterrows():
        wf_d1 = str(
            row.get(COLUNA_WORKFLOW_PARQUET_NOME, "")
            or row.get(COLUNA_CONFIG_WORKFLOW_D1, "")
            or ""
        ).strip()
        if not wf_d1:
            continue
        cli_vol = str(row.get(COLUNA_VOLUMETRIA_CLIENTE, "") or "").strip()
        cliente = cli_vol if cli_vol and _eh_cliente_volumetria_valido(cli_vol) else None
        try:
            _chave, created = register_pending_workflow(wf_d1, cliente=cliente)
            workflows_pendentes_config.append(wf_d1)
            if created:
                workflows_pendentes_novos.append(wf_d1)
            else:
                workflows_pendentes_existentes.append(wf_d1)
        except Exception as exc:
            workflows_pendentes_falha_sync.append(f"{wf_d1}: {exc}")

    if workflows_pendentes_config:
        warnings.append(
            f"{len(workflows_pendentes_config)} workflow(s) com registros D-1 ainda sem cadastro "
            "foram excluídos do plano e registrados como pendentes."
        )
    if workflows_pendentes_falha_sync:
        warnings.append(
            f"Falha ao registrar {len(workflows_pendentes_falha_sync)} workflow(s) pendente(s) no banco."
        )

    merged = merged[~sem_mapa_mask].copy()

    if COLUNA_VOLUMETRIA_CLIENTE in merged.columns:
        merged = merged.drop(columns=[COLUNA_VOLUMETRIA_CLIENTE], errors="ignore")
    if COLUNA_WORKFLOW_PARQUET_NOME in merged.columns:
        merged = merged.drop(columns=[COLUNA_WORKFLOW_PARQUET_NOME], errors="ignore")

    merged["_cli_key"] = merged[COLUNA_CONFIG_CLIENTE].map(_normalizar_workflow)
    _cols_cat = [COLUNA_CONFIG_SEGMENTO, COLUNA_CONFIG_CATEGORIA, "_cli_key"]
    if COLUNA_CONFIG_META_CLIENTE in categorias.columns:
        _cols_cat.insert(2, COLUNA_CONFIG_META_CLIENTE)
    merged = merged.merge(categorias[_cols_cat], on="_cli_key", how="left")
    sem_cat = merged[COLUNA_CONFIG_SEGMENTO].isna() | (merged[COLUNA_CONFIG_SEGMENTO] == "")
    for cli in merged.loc[sem_cat, COLUNA_CONFIG_CLIENTE].unique().tolist():
        warnings.append(f"Cliente '{cli}': sem Segmento/Categoria no cadastro (banco)")

    keys_merged = set(merged["_wf_d1_key"].astype(str))
    extras_mapa = mapa_d1[~mapa_d1["_wf_d1_key"].astype(str).isin(keys_merged)].copy()
    if not extras_mapa.empty:
        log.info(
            "Ignorando %d workflow(s) cadastrados sem registros na fonte D-1",
            len(extras_mapa),
        )

    amostras = calcular_amostras_from_meta(merged, calc_params)
    merged = merged.merge(
        amostras[["_wf_key", "amostra_diaria", "amostra_total", "amostra_conf_prod"]],
        on="_wf_key",
        how="left",
    )
    sem_amostra = merged["amostra_diaria"].isna()
    workflows_sem_amostra = merged.loc[sem_amostra, COLUNA_CONFIG_WORKFLOW].tolist()
    if workflows_sem_amostra:
        warnings.append(
            f"{len(workflows_sem_amostra)} workflow(s) com registros D-1 estão sem amostra calculada."
        )
    merged["amostra_diaria"] = pd.to_numeric(merged["amostra_diaria"], errors="coerce").fillna(0).astype(int)
    merged["amostra_total"] = pd.to_numeric(merged["amostra_total"], errors="coerce").fillna(0).astype(int)
    merged["amostra_conf_prod"] = pd.to_numeric(merged["amostra_conf_prod"], errors="coerce").fillna(0).astype(int)

    if COLUNA_CONFIG_FILA not in merged.columns:
        merged[COLUNA_CONFIG_FILA] = REPLICACAO_FILA_G_AUDITORIA
    else:
        merged[COLUNA_CONFIG_FILA] = merged[COLUNA_CONFIG_FILA].map(rap._normalizar_fila)

    if merged.empty:
        detalhe = "; ".join(warnings[:8]) if warnings else "sem detalhes adicionais"
        raise ValueError(
            "Nenhum workflow válido após merge (parquet D-1 + banco). "
            f"Avisos: {detalhe}"
        )

    out_cols = [
        COLUNA_CONFIG_WORKFLOW,
        COLUNA_CONFIG_WORKFLOW_SELENIUM,
        COLUNA_CONFIG_WORKFLOW_D1,
        COLUNA_CONFIG_CLIENTE,
        COLUNA_CONFIG_FILA,
        COLUNA_CONFIG_USAR_ARQUIVO_CSV,
        COLUNA_CONFIG_NOME_REGRA_BRFLOW,
        COLUNA_CONFIG_SEGMENTO,
        COLUNA_CONFIG_CATEGORIA,
        COLUNA_CONFIG_AUTOMATICOS,
        COLUNA_CONFIG_MANUAIS,
        COLUNA_CONFIG_TOTAL,
        "amostra_diaria",
        "amostra_total",
        "amostra_conf_prod",
    ]
    if COLUNA_CONFIG_META_CLIENTE in merged.columns:
        out_cols.insert(7, COLUNA_CONFIG_META_CLIENTE)
    out = merged[out_cols].copy()
    out[COLUNA_CONFIG_SEGMENTO] = out[COLUNA_CONFIG_SEGMENTO].fillna("").astype(str)
    out[COLUNA_CONFIG_CATEGORIA] = out[COLUNA_CONFIG_CATEGORIA].fillna("").astype(str)
    if COLUNA_CONFIG_META_CLIENTE in out.columns:
        out[COLUNA_CONFIG_META_CLIENTE] = (
            pd.to_numeric(out[COLUNA_CONFIG_META_CLIENTE], errors="coerce").fillna(0).astype(int)
        )

    if float(calc_params.get("meta_produ", 0)) > 0:
        out.attrs["meta_produ_resumo"] = float(calc_params["meta_produ"])

    out.attrs["config_warnings"] = warnings
    out.attrs["workflows_pendentes_config"] = workflows_pendentes_config
    out.attrs["workflows_pendentes_novos"] = workflows_pendentes_novos
    out.attrs["workflows_pendentes_existentes"] = workflows_pendentes_existentes
    out.attrs["workflows_pendentes_falha_sync"] = workflows_pendentes_falha_sync
    out.attrs["default_xlsx_path"] = ""
    out.attrs["fonte_banco"] = True
    return out, warnings, parquet_ref


def montar_config_replicacao_d1(
    df_parquet: pd.DataFrame,
    settings: Optional[dict] = None,
    default_path: Optional[Union[str, Path]] = None,
) -> tuple[pd.DataFrame, List[str], str]:
    """Monta config a partir do parquet D-1 + Default.xlsx + Categoria.xlsx."""
    settings = _ensure_d1_settings(settings)
    from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa

    if is_fonte_banco_ativa(settings):
        return _montar_config_replicacao_d1_from_banco(df_parquet, settings)

    paths = rap._resolver_paths_config(settings)
    if default_path:
        paths["default"] = Path(default_path)

    warnings: List[str] = []
    vol = carregar_volumetria_d1_parquet(df_parquet)
    parquet_ref = str(settings.get("_parquet_ref", "") or "parquet D-1")

    desabilitados = carregar_chaves_workflow_d1_desabilitados(paths["default"], settings=settings)
    if desabilitados:
        mapa_tmp = carregar_mapa_workflow_d1(paths["default"], settings=settings)
        mapa_tmp["_wf_d1_key"] = mapa_tmp[COLUNA_CONFIG_WORKFLOW_D1].map(_normalizar_workflow)
        keys_des = set(mapa_tmp.loc[mapa_tmp["_wf_key"].astype(str).isin(desabilitados), "_wf_d1_key"].astype(str))
        antes = len(vol)
        vol = vol[~vol["_wf_d1_key"].astype(str).isin(keys_des)].copy()
        removidos = antes - len(vol)
        if removidos:
            msg = f"{removidos} workflow(s) ignorado(s): Status=False no Default.xlsx (Workflow d1)"
            warnings.append(msg)
            log.info("Config D-1 | %s", msg)

    mapa_d1 = carregar_mapa_workflow_d1(paths["default"], settings=settings)
    mapa_d1["_wf_d1_key"] = mapa_d1[COLUNA_CONFIG_WORKFLOW_D1].map(_normalizar_workflow)
    categorias = carregar_categoria_clientes(paths["categoria"], settings=settings)
    warnings.extend(validar_metas_categoria_clientes(categorias))

    merged = vol.merge(
        mapa_d1[
            [
                COLUNA_CONFIG_WORKFLOW,
                COLUNA_CONFIG_WORKFLOW_SELENIUM,
                COLUNA_CONFIG_CLIENTE,
                COLUNA_CONFIG_WORKFLOW_D1,
                COLUNA_CONFIG_FILA,
                COLUNA_CONFIG_USAR_ARQUIVO_CSV,
                COLUNA_CONFIG_NOME_REGRA_BRFLOW,
                "_wf_key",
                "_wf_d1_key",
            ]
        ],
        on="_wf_d1_key",
        how="left",
        suffixes=("_vol", "_map"),
    )
    col_wf_map = f"{COLUNA_CONFIG_WORKFLOW}_map"
    col_wf_vol = f"{COLUNA_CONFIG_WORKFLOW}_vol"
    if col_wf_map in merged.columns:
        merged[COLUNA_CONFIG_WORKFLOW] = merged[col_wf_map].fillna(merged.get(col_wf_vol, ""))
        merged = merged.drop(columns=[c for c in (col_wf_vol, col_wf_map) if c in merged.columns])

    sem_mapa_mask = merged[COLUNA_CONFIG_CLIENTE].isna() | (merged[COLUNA_CONFIG_CLIENTE] == "")
    workflows_novos: List[dict] = []
    for _, row in merged.loc[sem_mapa_mask].iterrows():
        wf_d1 = str(
            row.get(COLUNA_WORKFLOW_PARQUET_NOME, "")
            or row.get(COLUNA_CONFIG_WORKFLOW_D1, "")
            or ""
        ).strip()
        if not wf_d1:
            continue
        item: dict = {"workflow": wf_d1, "_wf_key": _normalizar_workflow(wf_d1)}
        cli_vol = str(row.get(COLUNA_VOLUMETRIA_CLIENTE, "") or "").strip()
        if cli_vol and _eh_cliente_volumetria_valido(cli_vol):
            item["cliente"] = cli_vol
        workflows_novos.append(item)

    workflows_pendentes_config = [item["workflow"] for item in workflows_novos]
    sync_result = rap._novo_resultado_sync_pendentes()
    if workflows_novos:
        sync_msgs, sync_result = sincronizar_pendentes_workflow_d1(
            paths["default"],
            workflows_novos,
            origem_volumetria=parquet_ref,
            settings=settings,
        )
        warnings.extend(sync_msgs)

    merged = merged[~sem_mapa_mask].copy()

    if COLUNA_VOLUMETRIA_CLIENTE in merged.columns:
        merged = merged.drop(columns=[COLUNA_VOLUMETRIA_CLIENTE], errors="ignore")
    if COLUNA_WORKFLOW_PARQUET_NOME in merged.columns:
        merged = merged.drop(columns=[COLUNA_WORKFLOW_PARQUET_NOME], errors="ignore")

    merged["_cli_key"] = merged[COLUNA_CONFIG_CLIENTE].map(_normalizar_workflow)
    _cols_cat = [COLUNA_CONFIG_SEGMENTO, COLUNA_CONFIG_CATEGORIA, "_cli_key"]
    if COLUNA_CONFIG_META_CLIENTE in categorias.columns:
        _cols_cat.insert(2, COLUNA_CONFIG_META_CLIENTE)
    merged = merged.merge(
        categorias[_cols_cat],
        on="_cli_key",
        how="left",
    )
    sem_cat = merged[COLUNA_CONFIG_SEGMENTO].isna() | (merged[COLUNA_CONFIG_SEGMENTO] == "")
    for cli in merged.loc[sem_cat, COLUNA_CONFIG_CLIENTE].unique().tolist():
        warnings.append(f"Cliente '{cli}': sem Segmento/Categoria em Categoria.xlsx")

    keys_merged = set(merged["_wf_d1_key"].astype(str))
    extras_mapa = mapa_d1[~mapa_d1["_wf_d1_key"].astype(str).isin(keys_merged)].copy()
    if not extras_mapa.empty:
        linhas_extra = []
        for _, mrow in extras_mapa.iterrows():
            linhas_extra.append(
                {
                    COLUNA_CONFIG_WORKFLOW: mrow[COLUNA_CONFIG_WORKFLOW],
                    COLUNA_CONFIG_WORKFLOW_SELENIUM: mrow[COLUNA_CONFIG_WORKFLOW_SELENIUM],
                    COLUNA_CONFIG_WORKFLOW_D1: mrow[COLUNA_CONFIG_WORKFLOW_D1],
                    COLUNA_CONFIG_CLIENTE: mrow[COLUNA_CONFIG_CLIENTE],
                    COLUNA_CONFIG_FILA: mrow.get(COLUNA_CONFIG_FILA, REPLICACAO_FILA_G_AUDITORIA),
                    COLUNA_CONFIG_USAR_ARQUIVO_CSV: mrow.get(
                        COLUNA_CONFIG_USAR_ARQUIVO_CSV, pd.NA
                    ),
                    COLUNA_CONFIG_NOME_REGRA_BRFLOW: mrow.get(
                        COLUNA_CONFIG_NOME_REGRA_BRFLOW, ""
                    ),
                    COLUNA_CONFIG_AUTOMATICOS: 0,
                    COLUNA_CONFIG_MANUAIS: 0,
                    COLUNA_CONFIG_TOTAL: 0,
                    "_wf_key": mrow["_wf_key"],
                    "_wf_d1_key": mrow["_wf_d1_key"],
                }
            )
        df_extra = pd.DataFrame(linhas_extra)
        df_extra["_cli_key"] = df_extra[COLUNA_CONFIG_CLIENTE].map(_normalizar_workflow)
        df_extra = df_extra.merge(
            categorias[_cols_cat],
            on="_cli_key",
            how="left",
        )
        merged = pd.concat([merged, df_extra], ignore_index=True)
        warnings.append(
            f"{len(df_extra)} workflow(s) do Default ausente(s) no parquet D-1 "
            "(incluídos para CSV vazio / limpeza BRFlow)"
        )

    amostras = carregar_amostras_d1(paths["default"], settings=settings, df_workflows=merged)
    merged = merged.merge(
        amostras[["_wf_key", "amostra_diaria", "amostra_total", "amostra_conf_prod"]],
        on="_wf_key",
        how="left",
    )
    sem_amostra = merged["amostra_diaria"].isna()
    for wf in merged.loc[sem_amostra, COLUNA_CONFIG_WORKFLOW].tolist():
        warnings.append(
            f"{wf}: sem amostra calculada (Amostra diária = 0; CSV vazio para limpeza BRFlow)"
        )
    merged["amostra_diaria"] = pd.to_numeric(merged["amostra_diaria"], errors="coerce").fillna(0).astype(int)
    merged["amostra_total"] = pd.to_numeric(merged["amostra_total"], errors="coerce").fillna(0).astype(int)
    merged["amostra_conf_prod"] = pd.to_numeric(merged["amostra_conf_prod"], errors="coerce").fillna(0).astype(int)

    if COLUNA_CONFIG_FILA not in merged.columns:
        merged[COLUNA_CONFIG_FILA] = REPLICACAO_FILA_G_AUDITORIA
    else:
        merged[COLUNA_CONFIG_FILA] = merged[COLUNA_CONFIG_FILA].map(rap._normalizar_fila)

    if merged.empty:
        detalhe = "; ".join(warnings[:8]) if warnings else "sem detalhes adicionais"
        raise ValueError(
            "Nenhum workflow válido após merge (parquet D-1 + Default + Categoria). "
            f"Avisos: {detalhe}"
        )

    out_cols = [
            COLUNA_CONFIG_WORKFLOW,
            COLUNA_CONFIG_WORKFLOW_SELENIUM,
            COLUNA_CONFIG_WORKFLOW_D1,
            COLUNA_CONFIG_CLIENTE,
            COLUNA_CONFIG_FILA,
            COLUNA_CONFIG_USAR_ARQUIVO_CSV,
            COLUNA_CONFIG_NOME_REGRA_BRFLOW,
            COLUNA_CONFIG_SEGMENTO,
            COLUNA_CONFIG_CATEGORIA,
            COLUNA_CONFIG_AUTOMATICOS,
            COLUNA_CONFIG_MANUAIS,
            COLUNA_CONFIG_TOTAL,
            "amostra_diaria",
            "amostra_total",
            "amostra_conf_prod",
        ]
    if COLUNA_CONFIG_META_CLIENTE in merged.columns:
        out_cols.insert(7, COLUNA_CONFIG_META_CLIENTE)
    out = merged[out_cols].copy()
    out[COLUNA_CONFIG_SEGMENTO] = out[COLUNA_CONFIG_SEGMENTO].fillna("").astype(str)
    out[COLUNA_CONFIG_CATEGORIA] = out[COLUNA_CONFIG_CATEGORIA].fillna("").astype(str)
    if COLUNA_CONFIG_META_CLIENTE in out.columns:
        out[COLUNA_CONFIG_META_CLIENTE] = (
            pd.to_numeric(out[COLUNA_CONFIG_META_CLIENTE], errors="coerce").fillna(0).astype(int)
        )

    meta_calc = _ler_meta_calculadora_padrao(paths["default"])
    if meta_calc.get("meta_produ", 0) > 0:
        out.attrs["meta_produ_resumo"] = float(meta_calc["meta_produ"])

    out.attrs["config_warnings"] = warnings
    out.attrs["workflows_pendentes_config"] = workflows_pendentes_config
    out.attrs["workflows_pendentes_novos"] = sync_result.get("novos", [])
    out.attrs["workflows_pendentes_existentes"] = sync_result.get("existentes", [])
    out.attrs["workflows_pendentes_falha_sync"] = sync_result.get("falha_sync", [])
    out.attrs["default_xlsx_path"] = str(paths["default"])
    return out, warnings, parquet_ref


def carregar_config_auditoria_d1(
    df_parquet: pd.DataFrame,
    settings: Optional[dict] = None,
) -> pd.DataFrame:
    df, warnings, parquet_ref = montar_config_replicacao_d1(df_parquet, settings=settings)
    df.attrs["config_warnings"] = warnings
    df.attrs["parquet_referencia"] = parquet_ref
    return df


def _meta_colunas_config_d1(row: pd.Series, parquet_ref: str = "") -> dict:
    meta = _meta_colunas_config(row, pasta_volumetria="")
    # O limite mensal é uma regra interna de roteamento e não deve aparecer
    # como coluna analítica no plano ou no resumo exportado.
    meta.pop(COLUNA_CONFIG_META_CLIENTE, None)
    fonte_banco = str(parquet_ref).startswith("postgresql:")
    meta["Parquet Referencia"] = "" if fonte_banco else parquet_ref
    meta["Fonte Volumetria"] = "PostgreSQL" if fonte_banco else "parquet D-1"
    return meta


def _cli_key_item(item: dict) -> str:
    return _normalizar_workflow(str(item.get("cliente", "") or ""))


def _wf_key_item(item: dict) -> str:
    return _normalizar_workflow(str(item.get("workflow", "") or ""))


def _montar_tiers_redistribuicao(
    fonte: dict,
    destinos: List[dict],
) -> tuple[List[dict], List[dict], List[dict], str]:
    cli_fonte = _normalizar_workflow(str(fonte.get("cliente", "") or ""))
    cat_fonte = _normalizar_workflow(str(fonte.get("categoria", "") or ""))

    tier1 = [
        d for d in destinos
        if cli_fonte and _cli_key_item(d) == cli_fonte
    ]
    wf_t1 = {str(d["workflow"]) for d in tier1}
    tier2 = [
        d for d in destinos
        if cat_fonte
        and _normalizar_workflow(str(d.get("categoria", "") or "")) == cat_fonte
        and str(d["workflow"]) not in wf_t1
    ]
    wf_t12 = wf_t1 | {str(d["workflow"]) for d in tier2}
    tier3 = [d for d in destinos if str(d["workflow"]) not in wf_t12]

    if tier1:
        tier_nome = "CLIENTE"
    elif tier2:
        tier_nome = "CATEGORIA"
    else:
        tier_nome = "GERAL"
    return tier1, tier2, tier3, tier_nome


def _alocar_cota_com_meta(
    destinos: List[dict],
    cota: int,
    headroom: Dict[str, int],
    *,
    ignorar_meta: bool = False,
) -> tuple[Dict[str, int], int]:
    if cota <= 0 or not destinos:
        return {}, cota

    usar_cap = False
    elegiveis = destinos
    if not ignorar_meta:
        tem_meta = any(
            headroom.get(_wf_key_item(d), _HEADROOM_ILIMITADO) < _HEADROOM_ILIMITADO
            for d in destinos
        )
        if tem_meta:
            com_headroom = [d for d in destinos if headroom.get(_wf_key_item(d), 0) > 0]
            if not com_headroom:
                return {}, cota
            elegiveis = com_headroom
            usar_cap = True

    pesos = {str(d["workflow"]): _as_int(d.get("amostra", 0)) for d in elegiveis}
    if usar_cap:
        max_aloc = min(
            cota,
            sum(headroom.get(_wf_key_item(d), 0) for d in elegiveis),
        )
    else:
        max_aloc = cota

    if max_aloc <= 0:
        return {}, cota

    bruto = distribuir_proporcional(pesos, max_aloc)
    bonus: Dict[str, int] = {}
    for wf, qtd in bruto.items():
        dest = next(d for d in elegiveis if str(d["workflow"]) == wf)
        wf_key = _wf_key_item(dest)
        alocado = int(qtd)
        if usar_cap:
            alocado = min(alocado, headroom.get(wf_key, 0))
            headroom[wf_key] = max(0, headroom.get(wf_key, 0) - alocado)
        if alocado > 0:
            bonus[wf] = bonus.get(wf, 0) + alocado

    sobra = cota - sum(bonus.values())
    return bonus, sobra


def _redistribuir_fonte_com_meta(
    fonte: dict,
    destinos: List[dict],
    headroom: Dict[str, int],
    *,
    excluir_workflows: Optional[set] = None,
    fallback_sem_cap: bool = True,
) -> tuple[Dict[str, int], int, str]:
    excluir_workflows = excluir_workflows or set()
    wf_fonte = str(fonte.get("workflow", "") or "")
    if wf_fonte:
        excluir_workflows = set(excluir_workflows) | {wf_fonte}
    dest_filtrados = [d for d in destinos if str(d["workflow"]) not in excluir_workflows]
    cota = _as_int(fonte.get("amostra", 0))
    if cota <= 0 or not dest_filtrados:
        return {}, cota, ""

    tier1, tier2, tier3, tier_nome = _montar_tiers_redistribuicao(fonte, dest_filtrados)
    tiers: List[tuple[str, List[dict]]] = []
    if tier1:
        tiers.append(("CLIENTE", tier1))
    if tier2:
        tiers.append(("CATEGORIA", tier2))
    if tier3:
        tiers.append(("GERAL", tier3))
    if not tiers:
        tiers.append(("GERAL", dest_filtrados))

    bonus_total: Dict[str, int] = defaultdict(int)
    remaining = cota
    tier_final = tier_nome
    for nome, tier_dest in tiers:
        if remaining <= 0:
            break
        parcial, remaining = _alocar_cota_com_meta(tier_dest, remaining, headroom)
        if parcial:
            tier_final = nome
            for wf, q in parcial.items():
                bonus_total[wf] += q

    if remaining > 0 and fallback_sem_cap:
        parcial, remaining = _alocar_cota_com_meta(
            dest_filtrados, remaining, headroom, ignorar_meta=True
        )
        for wf, q in parcial.items():
            bonus_total[wf] += q
        if parcial and not tier_final:
            tier_final = "GERAL"
    elif remaining > 0:
        log.warning(
            "Redistribuição D-1: %d protocolo(s) sem destino (fallback sem cap desativado) | fonte=%s",
            remaining,
            wf_fonte,
        )

    fonte["redistribuicao_tier"] = tier_final
    return dict(bonus_total), remaining, tier_final


def redistribuir_amostra_priorizada_com_meta(
    fontes: List[dict],
    destinos_com_d1: List[dict],
    headroom_por_workflow: Optional[Dict[str, int]] = None,
    *,
    fallback_sem_cap: bool = True,
) -> tuple[Dict[str, int], int]:
    """
    Redistribui cota respeitando headroom mensal por workflow (soft cap).
    Cascata: Cliente → Categoria → Geral; fallback sem meta se todos bateram.
    Retorna (bonus_por_workflow, volume_nao_alocado_total).
    """
    bonus: Dict[str, int] = defaultdict(int)
    sobra_total = 0
    if not fontes or not destinos_com_d1:
        return dict(bonus), sobra_total

    headroom = dict(headroom_por_workflow or {})
    for fonte in fontes:
        parcial, sobra, _ = _redistribuir_fonte_com_meta(
            fonte, destinos_com_d1, headroom, fallback_sem_cap=fallback_sem_cap
        )
        for wf, qtd in parcial.items():
            bonus[wf] += int(qtd)
        sobra_total += int(sobra)
        if sobra > 0 and fallback_sem_cap:
            log.warning(
                "Redistribuição D-1: %d protocolo(s) sem destino após meta/tiers (fonte=%s)",
                sobra,
                fonte.get("workflow", ""),
            )
    return dict(bonus), sobra_total


def redistribuir_amostra_priorizada(
    fontes_sem_d1: List[dict],
    destinos_com_d1: List[dict],
) -> Dict[str, int]:
    """
    Redistribui cota de workflows sem D-1 em cascata:
    1) mesmo Cliente, 2) mesma Categoria, 3) todos com D-1.
    """
    headroom = {_wf_key_item(d): _HEADROOM_ILIMITADO for d in destinos_com_d1}
    bonus, _ = redistribuir_amostra_priorizada_com_meta(
        fontes_sem_d1, destinos_com_d1, headroom
    )
    return bonus


def _filtrar_linhas_resumo_fila(plano: PlanoReplicacao, fila: str) -> List[dict]:
    alvo = rap._normalizar_fila(fila)
    return [
        r
        for r in plano.resumo
        if r.get("Workflow") != "TOTAL"
        and rap._normalizar_fila(r.get("Fila", plano.workflow_fila.get(str(r.get("Workflow", "")), "")))
        == alvo
    ]


def _caminho_parquet_consolidado_d1(settings: Optional[dict] = None) -> Path:
    settings = settings or {}
    base = str(settings.get("replicacao_config_base", "") or "").strip()
    if base:
        pasta = Path(base).parent / "bi"
    else:
        pasta = PASTA_REPLICACAO_AUD_D1_BI
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta / ARQUIVO_PARQUET_CONSOLIDADO_D1


def _enriquecer_df_plano_canal_destino(
    df_plano: pd.DataFrame,
    plano: PlanoReplicacao,
) -> pd.DataFrame:
    if df_plano.empty:
        return df_plano
    out = df_plano.copy()
    if "Canal Destino" in out.columns and out["Canal Destino"].astype(str).str.strip().ne("").any():
        return out
    resumo_por_wf = {
        str(r.get("Workflow", "")): r
        for r in plano.resumo
        if r.get("Workflow") != "TOTAL"
    }
    canais: List[str] = []
    for _, row in out.iterrows():
        wf = str(row.get("WorkflowConfig", "") or "")
        res = resumo_por_wf.get(wf, {})
        canal = str(res.get("Canal Destino", "") or "").strip()
        if not canal:
            fila = plano.workflow_fila.get(wf, REPLICACAO_FILA_G_AUDITORIA)
            canal = resolver_canal_destino(fila)
        canais.append(canal)
    out["Canal Destino"] = canais
    return out


def _montar_dataframe_dashboard_d1(
    plano: PlanoReplicacao,
    parquet_path: Path,
    data_ref: datetime,
    estado: Optional[dict] = None,
    data_fim_execucao: Optional[datetime] = None,
) -> pd.DataFrame:
    """Dashboard D-1 enxuto (capacidade e redistribuição)."""
    metricas = {
        "AuditoresAtivos": plano.auditores_ativos,
        "AuditoresAtivosCase": plano.auditores_ativos_case,
        "MetaProdu": plano.meta_produ,
        "CapacidadeProdutiva": plano.capacidade_produtiva,
        "SomaAmostraDiaria": plano.soma_amostra_diaria,
        "FatorCapacidade": round(plano.fator_capacidade, 4),
        "AderenciaCapacidadePct": (
            round(100 * plano.soma_amostra_diaria / max(1.0, plano.capacidade_produtiva), 1)
            if plano.capacidade_produtiva > 0
            else ""
        ),
        "PoolRedistribuido": plano.pool_redistribuido,
        "ExcluidosHistoricoTotal": plano.excluidos_historico_total,
        "VolumeRedistribuicaoNaoAlocado": plano.volume_redistribuicao_nao_alocado,
    }
    return montar_dataframe_dashboard_secoes_d1(metricas)


def _montar_dataframe_parquet_bi(
    plano: PlanoReplicacao,
    estado: Optional[dict],
    df_plano: pd.DataFrame,
) -> pd.DataFrame:
    """Uma linha por protocolo com colunas curadas para Power BI."""
    if df_plano.empty:
        return pd.DataFrame()

    df_plano = _enriquecer_df_plano_canal_destino(df_plano, plano)
    estado = estado or {}
    resumo_por_wf = {
        str(r.get("Workflow", "")): r
        for r in plano.resumo
        if r.get("Workflow") != "TOTAL"
    }
    workflows_estado = estado.get("workflows", {})

    data_ref = str(
        estado.get("data_referencia_d1")
        or plano.data_referencia_d1_fmt
        or ""
    )
    data_exec = str(estado.get("data_execucao") or plano.data_execucao_fmt or "")
    parquet_ref = str(estado.get("parquet_referencia") or plano.parquet_referencia or "")

    linhas: List[dict] = []
    for _, row in df_plano.iterrows():
        wf = str(row.get("WorkflowConfig", "") or "")
        res = resumo_por_wf.get(wf, {})
        info = workflows_estado.get(wf, {})
        fila = plano.workflow_fila.get(wf, REPLICACAO_FILA_G_AUDITORIA)
        canal = str(res.get("Canal Destino", "") or row.get("Canal Destino", "") or resolver_canal_destino(fila))
        status_br = str(info.get("status", "") or "PENDENTE").strip() or "PENDENTE"
        atualizado = str(info.get("atualizado_em", "") or "")
        upload_fmt = _formatar_datetime_brflow(atualizado) if status_br == "SALVO_OK" else ""

        protocolo = str(row.get(COLUNA_PROTOCOLO, "") or "")
        data_analise = row.get(COLUNA_DATA_ANALISE, "")
        if hasattr(data_analise, "strftime"):
            data_analise_txt = data_analise.strftime("%Y-%m-%d %H:%M:%S")
        else:
            data_analise_txt = str(data_analise or "")

        hora_val = row.get("Hora", "")
        try:
            hora_int = int(hora_val) if hora_val != "" and hora_val is not None else None
        except (TypeError, ValueError):
            hora_int = None

        quota = row.get("QuotaHora", row.get("AmostraEfetiva", ""))
        try:
            quota_val = int(quota) if quota != "" and quota is not None else None
        except (TypeError, ValueError):
            quota_val = None

        linhas.append(
            {
                "run_id": plano.run_id,
                "data_referencia_d1": data_ref,
                "data_execucao": data_exec,
                "parquet_referencia": parquet_ref,
                "protocolo": protocolo,
                "data_analise": data_analise_txt,
                "hora": hora_int,
                "workflow_config": wf,
                "workflow_d1": str(res.get("Workflow D1", "") or row.get(COLUNA_WORKFLOW_PARQUET, "") or ""),
                "workflow_brflow": str(
                    info.get("workflow_brflow")
                    or plano.workflow_brflow.get(wf, "")
                    or wf
                ),
                "canal_destino": canal,
                "cliente": str(res.get(COLUNA_CONFIG_CLIENTE, "") or ""),
                "segmento": str(res.get(COLUNA_CONFIG_SEGMENTO, "") or ""),
                "categoria": str(res.get(COLUNA_CONFIG_CATEGORIA, "") or ""),
                "status_brflow": status_br,
                "data_hora_upload_brflow": upload_fmt,
                "status_amostra": str(res.get("Status", "") or ""),
                "pct_atingido_workflow": res.get("Pct Atingido", ""),
                "amostra_efetiva_workflow": res.get("Amostra Efetiva", row.get("AmostraEfetiva", "")),
                "quota_hora": quota_val,
            }
        )

    return pd.DataFrame(linhas)


def exportar_parquet_consolidado_d1(
    plano: PlanoReplicacao,
    estado: Optional[dict],
    df_plano: Optional[pd.DataFrame] = None,
    settings: Optional[dict] = None,
) -> Optional[Path]:
    """Append idempotente no parquet mestre de protocolos para BI."""
    if not plano.run_id:
        return None

    df_plano = df_plano if df_plano is not None else pd.DataFrame()
    df_novo = _montar_dataframe_parquet_bi(plano, estado, df_plano)
    if df_novo.empty:
        log.warning("Parquet BI D-1: plano vazio para run_id=%s", plano.run_id)
        return None

    from app.bots.rotina.io import _preparar_dataframe_para_parquet

    destino = _caminho_parquet_consolidado_d1(settings)
    run_id = str(plano.run_id)

    if destino.exists():
        try:
            df_existente = pd.read_parquet(destino)
            if "run_id" in df_existente.columns:
                df_existente = df_existente[df_existente["run_id"].astype(str) != run_id]
            df_final = pd.concat([df_existente, df_novo], ignore_index=True)
        except Exception as exc:
            log.warning("Parquet BI D-1: falha ao ler existente (%s); sobrescrevendo run", exc)
            df_final = df_novo
    else:
        df_final = df_novo

    df_final = _preparar_dataframe_para_parquet(df_final)
    destino.parent.mkdir(parents=True, exist_ok=True)
    df_final.to_parquet(str(destino), index=False, compression="snappy")
    log.info("Parquet BI D-1 consolidado | run_id=%s | +%d linhas | %s", run_id, len(df_novo), destino)
    return destino


def _carregar_df_plano_run_d1(
    run_id: str,
    plano: PlanoReplicacao,
    estado: Optional[dict],
) -> pd.DataFrame:
    """Carrega aba Plano do Excel; se ausente, monta a partir dos CSVs de protocolos."""
    estado = estado or {}
    rel_path = _resolver_caminho_relatorio_excel_d1(
        run_id,
        path_hint=plano.relatorio_excel_path,
    )
    if rel_path.exists():
        try:
            df = pd.read_excel(rel_path, sheet_name="Plano", engine="openpyxl")
            if not df.empty and COLUNA_PROTOCOLO in df.columns:
                return _enriquecer_df_plano_canal_destino(df, plano)
        except Exception as exc:
            log.warning("Aba Plano não lida para run_id=%s: %s", run_id, exc)

    linhas: List[dict] = []
    for workflow, info in estado.get("workflows", {}).items():
        csv_raw = str(info.get("csv", "") or "").strip()
        csv_path = Path(csv_raw) if csv_raw else Path()
        if not csv_path.exists():
            cached = plano.csv_paths.get(workflow) if isinstance(plano.csv_paths, dict) else None
            if cached:
                csv_path = Path(cached)
        if not csv_path.exists():
            nome = f"{_sanitizar_nome_arquivo(workflow)}.csv"
            sub = str(info.get("subpasta", "") or "").strip()
            base = plano.pasta_protocolos
            candidatos = [base / sub / nome, base / nome] if sub else [base / nome]
            for cand in candidatos:
                if cand.exists():
                    csv_path = cand
                    break
        if not csv_path.exists():
            continue

        fila = _resolver_fila_workflow_retomada(workflow, info, plano.workflow_fila)
        canal = resolver_canal_destino(fila)
        resumo_wf = next(
            (r for r in plano.resumo if str(r.get("Workflow", "")) == workflow),
            {},
        )
        wf_d1 = str(resumo_wf.get("Workflow D1", "") or "")
        for protocolo in sorted(_ler_protocolos_de_csv(csv_path)):
            linhas.append(
                {
                    COLUNA_PROTOCOLO: protocolo,
                    COLUNA_WORKFLOW_PARQUET: wf_d1,
                    "WorkflowConfig": workflow,
                    "Canal Destino": canal,
                }
            )

    return pd.DataFrame(linhas)


def listar_run_ids_com_dados_d1(settings: Optional[dict] = None) -> List[str]:
    """Run IDs com estado JSON e/ou relatório Excel D-1."""
    settings = _ensure_d1_settings(settings or {})
    base_resumo = PASTA_REPLICACAO_AUD_D1_RESUMO
    if str(settings.get("replicacao_config_base", "") or "").strip():
        base_resumo = Path(settings["replicacao_config_base"]).parent / "resumo"

    run_ids: dict[str, float] = {}
    prefix_exec = REPLICACAO_AUD_D1_EXECUCAO_PREFIXO
    prefix_rel = REPLICACAO_AUD_D1_RELATORIO_PREFIXO

    for path in base_resumo.glob(f"{prefix_exec}*.json"):
        run_id = path.stem.replace(prefix_exec, "", 1)
        if run_id:
            run_ids[run_id] = max(run_ids.get(run_id, 0), path.stat().st_mtime)

    rel_dir = _pasta_relatorios_excel_d1()
    for path in rel_dir.glob(f"{prefix_rel}*.xlsx"):
        run_id = path.stem.replace(prefix_rel, "", 1)
        if run_id:
            run_ids[run_id] = max(run_ids.get(run_id, 0), path.stat().st_mtime)

    legado_dir = base_resumo
    for path in legado_dir.glob(f"{prefix_rel}*.xlsx"):
        run_id = path.stem.replace(prefix_rel, "", 1)
        if run_id:
            run_ids[run_id] = max(run_ids.get(run_id, 0), path.stat().st_mtime)

    ordenados = sorted(run_ids.items(), key=lambda item: item[1], reverse=True)
    return [run_id for run_id, _ in ordenados]


def reconstruir_parquet_consolidado_d1(
    settings: Optional[dict] = None,
    run_ids: Optional[List[str]] = None,
    *,
    substituir: bool = True,
) -> dict:
    """Gera o parquet mestre BI a partir de runs/planos já existentes."""
    settings = _ensure_d1_settings(settings or {})
    candidatos = run_ids if run_ids is not None else listar_run_ids_com_dados_d1(settings)

    partes: List[pd.DataFrame] = []
    resultado: dict = {
        "path": None,
        "linhas": 0,
        "runs_ok": [],
        "runs_vazio": [],
        "runs_erro": [],
    }

    for run_id in candidatos:
        run_id = str(run_id or "").strip()
        if not run_id:
            continue
        try:
            estado = carregar_estado_execucao(run_id)
            if estado:
                cfg = {**settings, "apenas_pendentes": False, "forcar_reexecucao": True}
                plano = carregar_plano_por_run_id(run_id, settings=cfg)
            else:
                rel = _resolver_caminho_relatorio_excel_d1(run_id)
                if not rel.exists():
                    resultado["runs_vazio"].append(run_id)
                    continue
                plano = PlanoReplicacao(
                    data_referencia=datetime.now(),
                    pasta_protocolos=_pasta_saida_protocolos_d1(run_id),
                    pasta_resumo=PASTA_REPLICACAO_AUD_D1_RESUMO,
                    run_id=run_id,
                    relatorio_excel_path=rel,
                )
                df_res = pd.read_excel(rel, sheet_name="Resumo", engine="openpyxl")
                plano.resumo = df_res[df_res["Workflow"].astype(str) != "TOTAL"].to_dict("records")
                estado = {}

            df_plano = _carregar_df_plano_run_d1(run_id, plano, estado)
            df_run = _montar_dataframe_parquet_bi(plano, estado, df_plano)
            if df_run.empty:
                resultado["runs_vazio"].append(run_id)
                continue
            partes.append(df_run)
            resultado["runs_ok"].append(run_id)
        except Exception as exc:
            log.exception("Falha ao reconstruir parquet para run_id=%s", run_id)
            resultado["runs_erro"].append({"run_id": run_id, "erro": str(exc)})

    if not partes:
        log.warning("Parquet BI D-1: nenhum dado para consolidar")
        return resultado

    from app.bots.rotina.io import _preparar_dataframe_para_parquet

    df_final = pd.concat(partes, ignore_index=True)
    destino = _caminho_parquet_consolidado_d1(settings)

    if not substituir and destino.exists():
        try:
            df_existente = pd.read_parquet(destino)
            runs_ok = set(resultado["runs_ok"])
            df_existente = df_existente[~df_existente["run_id"].astype(str).isin(runs_ok)]
            df_final = pd.concat([df_existente, df_final], ignore_index=True)
        except Exception as exc:
            log.warning("Parquet BI D-1: falha ao mesclar existente: %s", exc)

    df_final = _preparar_dataframe_para_parquet(df_final)
    destino.parent.mkdir(parents=True, exist_ok=True)
    df_final.to_parquet(str(destino), index=False, compression="snappy")

    resultado["path"] = str(destino)
    resultado["linhas"] = len(df_final)
    log.info(
        "Parquet BI D-1 reconstruído | runs=%d | linhas=%d | %s",
        len(resultado["runs_ok"]),
        len(df_final),
        destino,
    )
    return resultado


def _exportar_relatorio_fila(
    plano: PlanoReplicacao,
    parquet_path: Path,
    data_ref: datetime,
    estado: Optional[dict],
    df_plano: pd.DataFrame,
    data_exec: datetime,
    subpasta: str,
    fila: str,
) -> Optional[Path]:
    linhas = _filtrar_linhas_resumo_fila(plano, fila)
    if not linhas:
        return None
    pasta_saida = PASTA_REPLICACAO_AUD_D1_RELATORIOS / subpasta
    pasta_saida.mkdir(parents=True, exist_ok=True)
    caminho = pasta_saida / f"{REPLICACAO_AUD_D1_RELATORIO_PREFIXO}{plano.run_id}.xlsx"

    df_resumo = _montar_dataframe_resumo(
        linhas,
        parquet_path=parquet_path,
        data_ref=data_ref,
        run_id=plano.run_id,
        data_exec=data_exec,
    )
    df_resumo = _enriquecer_resumo_com_estado_brflow(df_resumo, plano, estado)
    wf_set = {str(r.get("Workflow", "")) for r in linhas}
    if not df_plano.empty and "WorkflowConfig" in df_plano.columns:
        df_plano_f = df_plano[df_plano["WorkflowConfig"].astype(str).isin(wf_set)].copy()
        df_plano_f = _enriquecer_df_plano_canal_destino(df_plano_f, plano)
    else:
        df_plano_f = pd.DataFrame()

    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        exportar_workbook_formatado(
            writer,
            plano,
            df_plano_f,
            df_resumo,
            pd.DataFrame(),
            estado,
            modo_d1=True,
            somente_plano_resumo=True,
        )

    log.info("Relatório Excel D-1 (%s / %s): %s", subpasta, fila, caminho)
    return caminho


def exportar_relatorio_excel(
    plano: PlanoReplicacao,
    parquet_path: Path,
    data_ref: datetime,
    estado: Optional[dict] = None,
    df_plano: Optional[pd.DataFrame] = None,
    data_exec: Optional[datetime] = None,
    pasta_saida: Optional[Path] = None,
) -> Path:
    data_exec = data_exec or datetime.now()
    pasta_saida = pasta_saida or _pasta_relatorios_excel_d1()
    pasta_saida.mkdir(parents=True, exist_ok=True)
    caminho = pasta_saida / f"{REPLICACAO_AUD_D1_RELATORIO_PREFIXO}{plano.run_id}.xlsx"

    linhas = [r for r in plano.resumo if r.get("Workflow") != "TOTAL"]
    df_resumo = _montar_dataframe_resumo(
        linhas,
        parquet_path=parquet_path,
        data_ref=data_ref,
        run_id=plano.run_id,
        data_exec=data_exec,
    )
    df_resumo = _enriquecer_resumo_com_estado_brflow(df_resumo, plano, estado)
    df_dashboard = _montar_dataframe_dashboard_d1(
        plano, parquet_path, data_ref, estado=estado, data_fim_execucao=datetime.now()
    )
    if df_plano is None:
        df_plano = pd.DataFrame()
    else:
        df_plano = _enriquecer_df_plano_canal_destino(df_plano, plano)

    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        exportar_workbook_formatado(
            writer,
            plano,
            df_plano,
            df_resumo,
            df_dashboard,
            estado,
            modo_d1=True,
        )

    _exportar_relatorio_fila(
        plano,
        parquet_path,
        data_ref,
        estado,
        df_plano if df_plano is not None else pd.DataFrame(),
        data_exec,
        REPLICACAO_D1_SUBPASTA_BRFLOW,
        REPLICACAO_FILA_G_AUDITORIA,
    )
    _exportar_relatorio_fila(
        plano,
        parquet_path,
        data_ref,
        estado,
        df_plano if df_plano is not None else pd.DataFrame(),
        data_exec,
        REPLICACAO_D1_SUBPASTA_CASE,
        REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
    )

    plano.relatorio_excel_path = caminho
    plano.parquet_referencia = parquet_path.name
    plano.data_referencia_d1_fmt = data_ref.strftime("%d/%m/%Y")
    plano.data_execucao_fmt = data_exec.strftime("%d/%m/%Y %H:%M")
    log.info("Relatório Excel D-1 salvo: %s", caminho)
    notificar_replicacao_d1_db_sync(plano.run_id, caminho)
    return caminho


def atualizar_relatorio_excel(plano: PlanoReplicacao, estado: dict) -> Optional[Path]:
    if not plano.run_id:
        return None
    if not plano.resumo and plano.relatorio_excel_path and Path(plano.relatorio_excel_path).exists():
        try:
            df_lido = pd.read_excel(plano.relatorio_excel_path, sheet_name="Resumo", engine="openpyxl")
            plano.resumo = df_lido[df_lido["Workflow"].astype(str) != "TOTAL"].to_dict("records")
        except Exception as exc:
            log.warning("Não foi possível reler aba Resumo do Excel D-1: %s", exc)

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
    rel_path = _resolver_caminho_relatorio_excel_d1(plano.run_id, path_hint=plano.relatorio_excel_path)
    if Path(rel_path).exists():
        try:
            df_plano = pd.read_excel(rel_path, sheet_name="Plano", engine="openpyxl")
        except Exception:
            df_plano = pd.DataFrame()

    data_exec_txt = str(estado.get("data_execucao") or plano.data_execucao_fmt or "")
    try:
        data_exec = datetime.strptime(data_exec_txt, "%d/%m/%Y %H:%M")
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


def atualizar_relatorio_e_parquet_d1(
    plano: PlanoReplicacao,
    estado: dict,
    settings: Optional[dict] = None,
) -> Optional[Path]:
    """Atualiza Excel e parquet consolidado BI após Selenium."""
    path = atualizar_relatorio_excel(plano, estado)
    if path:
        try:
            rel_path = _resolver_caminho_relatorio_excel_d1(plano.run_id, path_hint=plano.relatorio_excel_path)
            df_plano = pd.DataFrame()
            if Path(rel_path).exists():
                try:
                    df_plano = pd.read_excel(rel_path, sheet_name="Plano", engine="openpyxl")
                except Exception:
                    df_plano = pd.DataFrame()
            exportar_parquet_consolidado_d1(plano, estado, df_plano=df_plano, settings=settings)
        except Exception:
            log.exception("Falha ao exportar parquet consolidado D-1")
        notificar_replicacao_d1_db_sync(plano.run_id, path)
    return path


def notificar_replicacao_d1_db_sync(run_id: str, excel_path: Optional[Path]) -> None:
    """Emite prefixo stdout para o portal enfileirar sync bot→DB."""
    if not run_id or not excel_path:
        return
    path = Path(excel_path)
    if not path.is_file():
        return
    print(f"REPLICACAO_D1_SAVED|{run_id}|{path.resolve()}", flush=True)


def inicializar_estado_execucao(
    plano: PlanoReplicacao,
    *,
    parquet_referencia: str = "",
    data_referencia_d1: str = "",
    data_execucao: str = "",
    seed_amostra: Optional[int] = None,
) -> dict:
    workflows_estado = {}
    sem_registro = set(plano.workflows_sem_registro)
    for workflow in plano.workflows:
        csv_path = plano.csv_paths.get(workflow)
        protocolos = plano.protocolos_por_workflow.get(workflow) or []
        wf_br = str(plano.workflow_brflow.get(workflow, workflow) or workflow).strip()
        fila = plano.workflow_fila.get(workflow, REPLICACAO_FILA_G_AUDITORIA)
        modo = plano.workflow_modo_replicacao.get(workflow) or resolver_modo_replicacao(None, fila)
        modo_qtd = modo == REPLICACAO_MODO_QTD
        qtd_calculada = int(plano.qtd_por_workflow.get(workflow, 0) or 0) if modo_qtd else 0
        if modo_qtd:
            sem_upload = qtd_calculada <= 0
        elif protocolos:
            sem_upload = False
        elif csv_path:
            sem_upload = not rap.workflow_elegivel_upload(protocolos, Path(csv_path))
        else:
            sem_upload = True
        entry = {
            "status": "PULADO" if sem_upload else "PENDENTE",
            "resultado": "pulado" if sem_upload else "pendente",
            "csv": _csv_relativo_plano(plano, Path(csv_path)) if csv_path else "",
            "workflow_brflow": wf_br,
            "fila": fila,
            "modo": modo,
            "subpasta": subpasta_protocolos_fila(fila),
            "atualizado_em": "",
            "started_at": "",
            "finished_at": "",
            "attempt_number": 0,
            "fase_execucao": "planejamento",
            "motivo_codigo": "",
            "motivo_resumo": "",
        }
        if modo_qtd:
            entry["qtd_calculada"] = qtd_calculada
            entry["quantidade_alvo"] = qtd_calculada
            entry["quantidade_encontrada"] = 0
        if plano.workflow_regra_brflow.get(workflow):
            entry["nome_regra_brflow"] = plano.workflow_regra_brflow[workflow]
        if workflow in sem_registro:
            entry["motivo"] = "sem D-1"
            entry["motivo_codigo"] = "SEM_D1"
        elif sem_upload:
            entry["motivo"] = "qtd zero" if modo_qtd else "sem protocolos"
            entry["motivo_codigo"] = "QTD_ZERO" if modo_qtd else "SEM_PROTOCOLOS"
        entry["motivo_resumo"] = entry.get("motivo", "")
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
        "workflows": workflows_estado,
        "workflow_modo_replicacao": dict(plano.workflow_modo_replicacao),
        "pastas_fila": dict(plano.pastas_fila),
        "auditores_ativos_brflow": plano.auditores_ativos,
        "auditores_ativos_case": plano.auditores_ativos_case,
        "modo": "replicacao_auditoria_d1",
    }
    salvar_estado_execucao(estado, plano.estado_execucao_path)
    return estado


def salvar_estado_execucao(estado: dict, path: Optional[Path] = None) -> None:
    from app.bots.replicacao_d1_db_bridge import save_execution_state_db

    run_id = str(estado.get("run_id", "") or "").strip()
    if run_id in _DATABASE_ONLY_RUN_IDS:
        if not save_execution_state_db(estado):
            raise RuntimeError("Não foi possível persistir o estado D-1 no PostgreSQL.")
        return
    from app.bots.replicacao_d1.atomic import write_json_atomic

    path = path or caminho_estado_execucao(str(estado.get("run_id", "")))
    write_json_atomic(path, estado)


def carregar_estado_execucao(run_id: str) -> Optional[dict]:
    from app.bots.replicacao_d1_db_bridge import load_execution_state_db

    if run_id in _DATABASE_ONLY_RUN_IDS:
        return load_execution_state_db(run_id)
    path = caminho_estado_execucao(run_id)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _resolver_fila_workflow_retomada(
    workflow: str,
    info: dict,
    mapa_fila: Dict[str, str],
) -> str:
    """Resolve fila na retomada: estado JSON > Default.xlsx > subpasta > G auditoria."""
    fila_estado = str(info.get("fila", "") or "").strip()
    if fila_estado:
        return rap._normalizar_fila(fila_estado)
    if workflow in mapa_fila:
        return mapa_fila[workflow]
    sub = str(info.get("subpasta", "") or "").strip().casefold()
    if sub == REPLICACAO_D1_SUBPASTA_CASE:
        return REPLICACAO_FILA_DOCUMENTOSCOPIA_31
    return REPLICACAO_FILA_G_AUDITORIA


def _resolver_modo_workflow_retomada(
    workflow: str,
    info: dict,
    *,
    fila: str,
    mapa_estado: Dict[str, str],
    mapa_config: Dict[str, str],
) -> str:
    """Estado congelado vence config; fila e usada somente no legado sem modo."""
    modo_info = str(info.get("modo", "") or "").strip().casefold()
    if modo_info in (REPLICACAO_MODO_PROTOCOLOS, REPLICACAO_MODO_QTD):
        return modo_info
    modo_estado = str(mapa_estado.get(workflow, "") or "").strip().casefold()
    if modo_estado in (REPLICACAO_MODO_PROTOCOLOS, REPLICACAO_MODO_QTD):
        return modo_estado
    modo_config = str(mapa_config.get(workflow, "") or "").strip().casefold()
    if modo_config in (REPLICACAO_MODO_PROTOCOLOS, REPLICACAO_MODO_QTD):
        return modo_config
    return resolver_modo_replicacao(None, fila)


def carregar_plano_por_run_id(run_id: str, settings: Optional[dict] = None) -> PlanoReplicacao:
    settings = _ensure_d1_settings(settings)
    from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa, load_plan_for_execution_db

    if is_fonte_banco_ativa(settings):
        _DATABASE_ONLY_RUN_IDS.add(run_id)
        plano, _estado = load_plan_for_execution_db(run_id, settings)
        return plano
    estado = carregar_estado_execucao(run_id)
    if not estado:
        raise FileNotFoundError(f"Estado de execução D-1 não encontrado para run_id={run_id}")

    pasta_protocolos = Path(estado.get("pasta_protocolos", "")) or _pasta_saida_protocolos_d1(run_id)
    if not pasta_protocolos.exists():
        pasta_protocolos = _pasta_saida_protocolos_d1(run_id)

    apenas_pendentes = bool(settings.get("apenas_pendentes", True))
    forcar = bool(settings.get("forcar_reexecucao", False))

    plano = PlanoReplicacao(
        data_referencia=datetime.now(),
        pasta_protocolos=pasta_protocolos,
        pasta_resumo=PASTA_REPLICACAO_AUD_D1_RESUMO,
        run_id=run_id,
        pasta_execucao=pasta_protocolos,
        estado_execucao_path=caminho_estado_execucao(run_id),
    )

    rel_path = _resolver_caminho_relatorio_excel_d1(run_id)
    if rel_path.exists():
        plano.relatorio_excel_path = rel_path
        plano.plano_detalhado_path = rel_path
        try:
            df_res = pd.read_excel(rel_path, sheet_name="Resumo", engine="openpyxl")
            plano.resumo = df_res[df_res["Workflow"].astype(str) != "TOTAL"].to_dict("records")
        except Exception as exc:
            log.debug("Resumo não lido do Excel D-1 na retomada: %s", exc)
    else:
        resumo_path = PASTA_REPLICACAO_AUD_D1_RESUMO / f"{REPLICACAO_AUD_D1_RESUMO_PREFIXO}{run_id}.csv"
        if resumo_path.exists():
            plano.resumo_csv_path = resumo_path
            df_leg = pd.read_csv(resumo_path, sep=";", encoding="utf-8-sig")
            plano.resumo = df_leg[df_leg["Workflow"].astype(str) != "TOTAL"].to_dict("records")

    plano.default_xlsx_path = str(estado.get("default_xlsx_path", "") or "")
    plano.parquet_referencia = str(estado.get("parquet_referencia", "") or "")
    plano.pastas_fila = dict(estado.get("pastas_fila", {}) or {})
    plano.auditores_ativos = int(estado.get("auditores_ativos_brflow", 0) or 0)
    plano.auditores_ativos_case = int(estado.get("auditores_ativos_case", 0) or 0)
    plano.data_referencia_d1_fmt = str(estado.get("data_referencia_d1", "") or "")
    plano.data_execucao_fmt = str(estado.get("data_execucao", "") or "")

    paths_cfg = rap._resolver_paths_config(settings)
    default_path = Path(
        str(estado.get("default_xlsx_path", "") or plano.default_xlsx_path or paths_cfg["default"])
    )
    mapa_brflow: Dict[str, str] = {}
    mapa_fila: Dict[str, str] = {}
    mapa_modo: Dict[str, str] = {}
    desabilitados: set = set()
    try:
        if default_path.exists():
            mapa = carregar_mapa_workflow_d1(default_path, settings=settings)
            for _, row in mapa.iterrows():
                wf = str(row[COLUNA_CONFIG_WORKFLOW]).strip()
                if wf:
                    mapa_brflow[wf] = str(row[COLUNA_CONFIG_WORKFLOW_SELENIUM]).strip()
                    fila = resolver_fila_linha(row)
                    mapa_fila[wf] = fila
                    mapa_modo[wf] = resolver_modo_replicacao_linha(row, fila)
            desabilitados = carregar_chaves_workflow_d1_desabilitados(default_path, settings=settings)
    except Exception as exc:
        log.debug("Mapa Workflow d1 não recarregado na retomada D-1: %s", exc)

    mapa_modo_estado = dict(estado.get("workflow_modo_replicacao", {}) or {})
    for workflow, info in estado.get("workflows", {}).items():
        if _normalizar_workflow(workflow) in desabilitados:
            continue
        status = str(info.get("status", "PENDENTE"))
        fila = _resolver_fila_workflow_retomada(workflow, info, mapa_fila)
        plano.workflow_fila[workflow] = fila
        modo = _resolver_modo_workflow_retomada(
            workflow,
            info,
            fila=fila,
            mapa_estado=mapa_modo_estado,
            mapa_config=mapa_modo,
        )
        plano.workflow_modo_replicacao[workflow] = modo
        info["modo"] = modo
        csv_path = Path()
        protocolos: list[str] = []
        if modo == REPLICACAO_MODO_QTD:
            plano.qtd_por_workflow[workflow] = int(info.get("qtd_calculada", 0) or 0)
        else:
            csv_str = str(info.get("csv", "") or "")
            csv_path = _resolver_csv_workflow_plano(plano, workflow, csv_str)
            protocolos = list(_ler_protocolos_de_csv(csv_path)) if csv_path.exists() else []
            if csv_path.exists():
                plano.csv_paths[workflow] = csv_path
        plano.protocolos_por_workflow[workflow] = protocolos
        regra = str(info.get("nome_regra_brflow", "") or "").strip()
        if regra:
            plano.workflow_regra_brflow[workflow] = regra
        if str(info.get("motivo", "") or "").strip() == "sem D-1":
            if workflow not in plano.workflows_sem_registro:
                plano.workflows_sem_registro.append(workflow)
        if status in ("UPLOAD_OK", "SALVO_OK", "INATIVO", "PULADO") and apenas_pendentes and not forcar:
            continue
        elegivel = (
            plano.qtd_por_workflow.get(workflow, 0) > 0
            if modo == REPLICACAO_MODO_QTD
            else rap.workflow_elegivel_upload(protocolos, csv_path)
        )
        if not elegivel:
            log.info(
                "Retomada D-1 | workflow %s sem protocolos reais; marcando PULADO",
                workflow,
            )
            info["status"] = "PULADO"
            info["motivo"] = info.get("motivo") or (
                "qtd zero" if modo == REPLICACAO_MODO_QTD else "sem protocolos"
            )
            info["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
            continue
        brflow = str(info.get("workflow_brflow", "") or mapa_brflow.get(workflow, "") or workflow).strip()
        plano.workflow_brflow[workflow] = brflow
        plano.workflows.append(workflow)

    estado_path = caminho_estado_execucao(run_id)
    if estado_path.exists():
        salvar_estado_execucao(estado, estado_path)

    return plano


def listar_run_ids_d1() -> List[str]:
    """Lista run_ids com estado de execução D-1, do mais recente ao mais antigo."""
    if not PASTA_REPLICACAO_AUD_D1_RESUMO.exists():
        return []
    paths = sorted(
        PASTA_REPLICACAO_AUD_D1_RESUMO.glob(f"{REPLICACAO_AUD_D1_EXECUCAO_PREFIXO}*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    run_ids: List[str] = []
    for path in paths:
        run_id = path.stem.replace(REPLICACAO_AUD_D1_EXECUCAO_PREFIXO, "", 1)
        if run_id:
            run_ids.append(run_id)
    return run_ids


def _run_tem_pendencias(run_id: str) -> bool:
    estado = carregar_estado_execucao(run_id) or {}
    for info in estado.get("workflows", {}).values():
        st = str(info.get("status", "PENDENTE") or "PENDENTE")
        if st in ("PENDENTE", "UPLOAD_OK"):
            return True
    return False


def _artefatos_plano_run_d1(run_id: str) -> List[Path]:
    rel = REPLICACAO_AUD_D1_RELATORIO_PREFIXO
    artefatos = [
        caminho_estado_execucao(run_id),
        _pasta_saida_protocolos_d1(run_id),
        _caminho_relatorio_excel_d1(run_id),
        PASTA_REPLICACAO_AUD_D1_RESUMO / f"{REPLICACAO_AUD_D1_RESUMO_PREFIXO}{run_id}.csv",
        PASTA_REPLICACAO_AUD_D1_RESUMO / f"{REPLICACAO_AUD_D1_PLANO_PREFIXO}{run_id}.csv",
        PASTA_REPLICACAO_AUD_D1_RESUMO / f"{REPLICACAO_AUD_D1_DASHBOARD_PREFIXO}{run_id}.csv",
        PASTA_REPLICACAO_AUD_D1_RESUMO / f"{rel}{run_id}.xlsx",
        PASTA_REPLICACAO_AUD_D1_RELATORIOS / REPLICACAO_D1_SUBPASTA_BRFLOW / f"{rel}{run_id}.xlsx",
        PASTA_REPLICACAO_AUD_D1_RELATORIOS / REPLICACAO_D1_SUBPASTA_CASE / f"{rel}{run_id}.xlsx",
    ]
    return artefatos


def apagar_plano_run_d1(
    run_id: str,
    *,
    forcar: bool = False,
    remover_ledger: bool = False,
    settings: Optional[dict] = None,
) -> dict:
    """Remove artefatos de um run D-1; opcionalmente remove consumo do ledger mensal."""
    resultado = {
        "run_id": run_id,
        "removidos": [],
        "ignorado": False,
        "motivo": "",
        "ledger_removido": False,
        "ledger_meses": [],
    }
    if not run_id:
        resultado["motivo"] = "run_id vazio"
        return resultado
    if not forcar and _run_tem_pendencias(run_id):
        resultado["ignorado"] = True
        resultado["motivo"] = "run com workflows pendentes"
        return resultado

    if not forcar:
        try:
            from app.bots.replicacao_d1_db_bridge import avaliar_retencao_artifact_run_db

            exigir_gate = bool(settings and settings.get("fonte_banco_ativa"))
            gate = avaliar_retencao_artifact_run_db(
                run_id,
                require_ingestion=exigir_gate,
            )
            if gate is not None and not gate.get("allowed"):
                resultado["ignorado"] = True
                motivos = gate.get("reasons") or ["gate de retenção"]
                resultado["motivo"] = "; ".join(str(m) for m in motivos)
                return resultado
        except Exception:
            log.debug("Gate retenção DB indisponível | run=%s", run_id, exc_info=True)

    for path in _artefatos_plano_run_d1(run_id):
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            resultado["removidos"].append(str(path))
        except OSError as exc:
            resultado["motivo"] = str(exc)

    if remover_ledger and resultado["removidos"]:
        ledger_res = remover_consumo_meta_run(run_id, settings=settings)
        resultado["ledger_removido"] = bool(ledger_res.get("removido"))
        resultado["ledger_meses"] = list(ledger_res.get("meses") or [])

    log.info(
        "Plano D-1 removido | run=%s | arquivos=%d | forcar=%s | ledger=%s",
        run_id,
        len(resultado["removidos"]),
        forcar,
        resultado["ledger_removido"],
    )
    return resultado


def aplicar_politica_retencao_planos_d1(
    settings: Optional[dict] = None,
    *,
    excluir_run_id: Optional[str] = None,
    forcar: bool = False,
    forcar_politica: bool = False,
) -> dict:
    """Aplica política de retenção de planos D-1."""
    settings = _ensure_d1_settings(settings or {})
    out = {"removidos": [], "ignorados": [], "erros": []}

    automatico = bool(
        settings.get("limpar_planos_automatico", REPLICACAO_AUD_D1_LIMPAR_PLANOS_AUTOMATICO_DEFAULT)
    )
    if not forcar_politica and not automatico:
        return out

    run_ids = listar_run_ids_d1()
    if excluir_run_id and excluir_run_id in run_ids:
        run_ids.remove(excluir_run_id)

    manter_n = int(settings.get("manter_planos_ultimos_n", REPLICACAO_AUD_D1_MANTER_PLANOS_ULTIMOS_N_DEFAULT))
    manter_n = max(1, min(manter_n, 50))
    dias_retencao = int(settings.get("dias_retencao_planos", REPLICACAO_AUD_D1_DIAS_RETENCAO_PLANOS_DEFAULT))

    candidatos: set[str] = set()
    if len(run_ids) > manter_n:
        candidatos.update(run_ids[manter_n:])

    if dias_retencao > 0:
        limite = datetime.now().timestamp() - (dias_retencao * 86400)
        for run_id in run_ids:
            path = caminho_estado_execucao(run_id)
            if path.exists() and path.stat().st_mtime < limite:
                candidatos.add(run_id)

    if excluir_run_id:
        candidatos.discard(excluir_run_id)

    for run_id in sorted(candidatos):
        res = apagar_plano_run_d1(run_id, forcar=forcar)
        if res.get("ignorado"):
            out["ignorados"].append({"run_id": run_id, "motivo": res.get("motivo", "")})
        elif res.get("removidos"):
            out["removidos"].append(run_id)
        elif res.get("motivo"):
            out["erros"].append({"run_id": run_id, "erro": res.get("motivo", "")})

    if out["removidos"]:
        log.info("Retenção planos D-1: removidos %s", out["removidos"])
    return out


def _protocolo_meta_key(workflow: str, protocolo: str) -> str:
    norm = rap._protocolo_chave_plano(protocolo)
    try:
        from app.bots.replicacao_d1_db_bridge import ensure_django_ready

        if ensure_django_ready():
            from apps.replicacao_d1.normalization import normalize_protocolo

            norm = normalize_protocolo(protocolo) or norm
    except Exception:
        pass
    return f"{workflow}|{norm}"


def _reservar_protocolos_unicos_no_plano(
    protocolos: list[str],
    reservados: set[str],
    *,
    workflow: str,
    plano: PlanoReplicacao,
) -> list[str]:
    """Garante um protocolo por run (primeiro workflow na ordem do plano prevalece)."""
    kept: list[str] = []
    omitidos = 0
    for proto in protocolos:
        chave = rap._protocolo_chave_plano(proto)
        if not chave or chave in reservados:
            omitidos += 1
            continue
        reservados.add(chave)
        kept.append(str(proto))
    if omitidos:
        plano.warnings.append(
            f"Plano ({workflow}): {omitidos} protocolo(s) duplicado(s) omitido(s) "
            "(já selecionado em outro workflow)."
        )
    return kept


def _persistent_dict_from_settings(settings: dict) -> dict:
    snap = settings.get("_execution_snapshot")
    if isinstance(snap, dict) and isinstance(snap.get("persistent"), dict):
        return snap["persistent"]
    if isinstance(settings.get("persistent"), dict):
        return settings["persistent"]
    return {}


def _retro_workflow_alias_fields() -> tuple[str, ...]:
    return (
        "nome_d1",
        "nome_canonico",
        "nome_selenium",
        "chave_d1_normalizada",
        "chave_normalizada",
    )


def _build_retro_workflow_alias_map(settings: dict, retro: dict | None) -> dict[str, str]:
    """Alias normalizado (parquet/rotina) -> nome D-1 canônico do cadastro."""
    persistent = _persistent_dict_from_settings(settings) or {}
    retro_items = list((persistent.get("retroativo") or {}).get("workflows") or [])
    retro_ids = {item.get("id") for item in retro_items if item.get("id") is not None}
    alias_map: dict[str, str] = {}

    def _register(item: dict) -> None:
        canonical = str(item.get("nome_d1") or item.get("nome_canonico") or "").strip()
        if not canonical:
            return
        for field in _retro_workflow_alias_fields():
            alias = _normalizar_workflow(str(item.get(field) or ""))
            if alias:
                alias_map[alias] = canonical

    for wf in persistent.get("workflows") or []:
        if retro_ids and wf.get("id") not in retro_ids:
            continue
        _register(wf)
    for item in retro_items:
        _register(item)
    if retro:
        for key in retro.get("workflow_d1_keys") or []:
            norm = _normalizar_workflow(str(key))
            if norm and norm not in alias_map:
                alias_map[norm] = str(key)
    return alias_map


def _alias_keys_for_workflow(
    alias_map: dict[str, str] | None,
    workflow_d1_key: str,
) -> set[str]:
    """Chaves normalizadas de alias que pertencem ao mesmo workflow D-1."""
    norm_key = _normalizar_workflow(workflow_d1_key)
    if not alias_map:
        return {norm_key}
    canonical = alias_map.get(norm_key)
    if canonical is None:
        for alias, canon in alias_map.items():
            if _normalizar_workflow(canon) == norm_key:
                canonical = canon
                break
    if not canonical:
        return {norm_key}
    canon_norm = _normalizar_workflow(canonical)
    keys = {
        alias
        for alias, canon in alias_map.items()
        if _normalizar_workflow(canon) == canon_norm
    }
    keys.add(canon_norm)
    return keys


def _aplicar_aliases_workflow_retroativo(
    df: pd.DataFrame,
    alias_map: dict[str, str],
) -> pd.DataFrame:
    """Normaliza nomes do parquet para o nome D-1 cadastrado."""
    if df.empty or not alias_map or COLUNA_WORKFLOW_PARQUET not in df.columns:
        return df
    out = df.copy()
    keys = out[COLUNA_WORKFLOW_PARQUET].astype(str).map(_normalizar_workflow)
    canonical = keys.map(lambda k: alias_map.get(k))
    mask = canonical.notna() & (canonical.astype(str).str.len() > 0)
    out.loc[mask, COLUNA_WORKFLOW_PARQUET] = canonical[mask].astype(str)
    return out


def _workflow_dto_retroativo(
    settings: dict,
    wf_key: str,
    retro: dict | None,
) -> dict | None:
    persistent = _persistent_dict_from_settings(settings) or {}
    retro_items = list((persistent.get("retroativo") or {}).get("workflows") or [])
    retro_ids = {item.get("id") for item in retro_items if item.get("id") is not None}
    wf_key = _normalizar_workflow(wf_key)
    for wf in persistent.get("workflows") or []:
        if retro_ids and wf.get("id") not in retro_ids:
            continue
        aliases = {
            _normalizar_workflow(str(wf.get(field) or ""))
            for field in _retro_workflow_alias_fields()
        }
        if wf_key in aliases:
            return wf
    if retro:
        for _id, nome in (retro.get("workflows_nomes") or {}).items():
            if _normalizar_workflow(str(nome)) == wf_key:
                for wf in persistent.get("workflows") or []:
                    if str(wf.get("id") or "") == str(_id):
                        return wf
    return None


def _resolver_amostra_retroativa_workflow(
    *,
    wf_dto: dict | None,
    wf_calc_row: dict,
    calc_params: dict,
    total_disponivel: int,
) -> int:
    if wf_dto and wf_dto.get("amostra_100"):
        return max(0, int(total_disponivel))
    pct = wf_dto.get("amostra_pct_especial") if wf_dto else None
    if pct is not None:
        pct_int = int(pct)
        if pct_int >= 100:
            return max(0, int(total_disponivel))
        if total_disponivel <= 0:
            return 0
        return max(1, round(total_disponivel * pct_int / 100))
    amostra_df = calcular_amostras_from_meta(pd.DataFrame([wf_calc_row]), calc_params)
    if amostra_df.empty:
        return 0
    return _as_int(amostra_df.iloc[0]["amostra_diaria"])


def carregar_config_retroativa(settings: dict) -> dict | None:
    """Lê política retroativa do snapshot persistente congelado no planejamento."""
    persistent = _persistent_dict_from_settings(settings) or {}
    if not persistent:
        try:
            from app.bots.replicacao_d1_db_bridge import load_retro_config_db
            persistent = {"retroativo": load_retro_config_db() or {}}
        except Exception:
            persistent = {}
    retro = persistent.get("retroativo") or {}
    if not retro.get("retroativo_ativo"):
        return None
    workflows = [item for item in (retro.get("workflows") or []) if item.get("ativo", True)]
    inicio = str(retro.get("retroativo_data_inicio") or "").strip()
    fim = str(retro.get("retroativo_data_fim") or "").strip()
    if not workflows or not inicio or not fim:
        return None
    workflow_d1_keys: set[str] = set()
    workflow_config_keys: set[str] = set()
    for item in workflows:
        nome_d1 = str(item.get("nome_d1") or item.get("chave_d1_normalizada") or "").strip()
        if nome_d1:
            workflow_d1_keys.add(_normalizar_workflow(nome_d1))
        chave_d1 = str(item.get("chave_d1_normalizada") or "").strip()
        if chave_d1:
            workflow_d1_keys.add(_normalizar_workflow(chave_d1))
        config_key = str(item.get("chave_normalizada") or item.get("nome_canonico") or "").strip()
        if config_key:
            workflow_config_keys.add(_normalizar_workflow(config_key))
    return {
        "data_inicio": inicio,
        "data_fim": fim,
        "workflow_d1_keys": workflow_d1_keys,
        "workflow_config_keys": workflow_config_keys,
        "workflows_nomes": {
            str(item.get("id") or ""): str(item.get("nome_canonico") or item.get("nome_d1") or "")
            for item in workflows
        },
    }


def _registrar_selection_reason_plano(
    plano: PlanoReplicacao,
    workflow: str,
    protocolos: list[str],
    df: pd.DataFrame,
) -> None:
    if df.empty or "_selection_reason" not in df.columns or not protocolos:
        return
    sub = rap._filtrar_por_protocolos_exatos(df, protocolos)
    if sub.empty:
        return
    if COLUNA_DATA_ANALISE in sub.columns:
        sub[COLUNA_DATA_ANALISE] = _parse_data_analise(sub[COLUNA_DATA_ANALISE])
        if sub[COLUNA_DATA_ANALISE].notna().any():
            sub["_hora"] = sub[COLUNA_DATA_ANALISE].dt.hour
    for _, row in sub.iterrows():
        tag = str(row.get("_selection_reason") or "").strip()
        if not tag.startswith("retroativo:"):
            continue
        proto = str(row.get(COLUNA_PROTOCOLO) or "").strip()
        key = _protocolo_meta_key(workflow, proto)
        plano.selection_reason_por_protocolo[key] = tag
        hora = row.get("_hora")
        if pd.isna(hora):
            hora = None
        plano.protocolo_meta_por_chave[key] = {
            "data_analise": row.get(COLUNA_DATA_ANALISE),
            "hora": hora,
            "matricula_tipo": str(row.get("_matricula_tipo") or "desconhecido"),
        }



def _carregar_dia_parquet_tratado(
    dia: date,
    workflow_d1_set: set[str],
    plano: PlanoReplicacao,
    *,
    parquet_suffix: str = "",
    strict_missing: bool = False,
) -> pd.DataFrame:
    """Carrega um dia do parquet tratado, filtrando workflows e marcando selection_reason."""
    data_dia = datetime.combine(dia, datetime.min.time())
    esperado = f"{rap.PREFIXO_DETALHADO_FINAL}{dia.strftime('%Y%m%d')}.parquet"
    try:
        parquet_path = resolver_parquet_d1(
            data_ref=data_dia,
            fallback_ultimo=False,
        )
    except FileNotFoundError:
        if strict_missing:
            raise
        return pd.DataFrame()
    if parquet_path.name != esperado:
        plano.warnings.append(
            f"Retroativo: Parquet de {dia.isoformat()} ausente; "
            f"usando fallback {parquet_path.name}."
        )
    frame = ler_parquet(parquet_path, log=log)
    _validar_parquet(frame)
    frame = frame[
        frame[COLUNA_WORKFLOW_PARQUET]
        .astype(str)
        .map(_normalizar_workflow)
        .isin(workflow_d1_set)
    ].copy()
    if frame.empty:
        return frame
    frame["_selection_reason"] = f"retroativo:{dia.isoformat()}{parquet_suffix}"
    return frame


def _carregar_pool_retroativo_parquet(
    *,
    inicio,
    fim,
    workflow_d1_set: set[str],
    fallback_ultimo_parquet: bool,
    plano: PlanoReplicacao,
) -> pd.DataFrame:
    "Carrega um parquet para cada dia do intervalo retroativo."
    frames: list[pd.DataFrame] = []
    cache: dict[Path, pd.DataFrame] = {}
    dia = inicio
    while dia <= fim:
        if fallback_ultimo_parquet:
            data_dia = datetime.combine(dia, datetime.min.time())
            esperado = f"{rap.PREFIXO_DETALHADO_FINAL}{dia.strftime('%Y%m%d')}.parquet"
            parquet_path = resolver_parquet_d1(
                data_ref=data_dia,
                fallback_ultimo=fallback_ultimo_parquet,
            )
            if parquet_path.name != esperado:
                plano.warnings.append(
                    f"Retroativo: Parquet de {dia.isoformat()} ausente; "
                    f"usando fallback {parquet_path.name}."
                )
            if parquet_path not in cache:
                frame = ler_parquet(parquet_path, log=log)
                _validar_parquet(frame)
                cache[parquet_path] = frame
            frame = cache[parquet_path].copy()
            frame = frame[
                frame[COLUNA_WORKFLOW_PARQUET]
                .astype(str)
                .map(_normalizar_workflow)
                .isin(workflow_d1_set)
            ].copy()
            if not frame.empty:
                frame["_selection_reason"] = f"retroativo:{dia.isoformat()}"
                frames.append(frame)
        else:
            frame = _carregar_dia_parquet_tratado(
                dia,
                workflow_d1_set,
                plano,
                strict_missing=True,
            )
            if not frame.empty:
                frames.append(frame)
        dia += timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _volumetria_retroativa_workflow(
    df: pd.DataFrame,
    workflow_d1_key: str,
    *,
    alias_map: dict[str, str] | None = None,
) -> dict[str, int]:
    """Agrega volumetria manual/automático a partir do pool retroativo de um workflow."""
    alias_keys = _alias_keys_for_workflow(alias_map, workflow_d1_key)
    sub = df[
        df[COLUNA_WORKFLOW_PARQUET].astype(str).map(_normalizar_workflow).isin(alias_keys)
    ].copy()
    if "_selection_reason" in sub.columns:
        sub = sub[sub["_selection_reason"].astype(str).str.startswith("retroativo:")]
    if sub.empty:
        return {
            COLUNA_CONFIG_MANUAIS: 0,
            COLUNA_CONFIG_AUTOMATICOS: 0,
            COLUNA_CONFIG_TOTAL: 0,
        }
    vol = carregar_volumetria_d1_parquet(sub)
    if vol.empty:
        return {
            COLUNA_CONFIG_MANUAIS: 0,
            COLUNA_CONFIG_AUTOMATICOS: 0,
            COLUNA_CONFIG_TOTAL: 0,
        }
    row = vol.iloc[0]
    return {
        COLUNA_CONFIG_MANUAIS: _as_int(row.get(COLUNA_CONFIG_MANUAIS, 0)),
        COLUNA_CONFIG_AUTOMATICOS: _as_int(row.get(COLUNA_CONFIG_AUTOMATICOS, 0)),
        COLUNA_CONFIG_TOTAL: _as_int(row.get(COLUNA_CONFIG_TOTAL, 0)),
    }


def _complementar_itens_config_retroativo(
    itens_config: List[dict],
    config: pd.DataFrame,
    df: pd.DataFrame,
    *,
    settings: dict,
    retro_cfg: dict | None,
    plano: PlanoReplicacao,
) -> List[dict]:
    """Inclui workflows retroativos ausentes no D-1 de referência no loop de amostragem."""
    if not retro_cfg or df is None or df.empty:
        return itens_config

    config_d1_keys = {
        _normalizar_workflow(_workflow_d1_da_linha(row))
        for _, row in config.iterrows()
        if _workflow_d1_da_linha(row)
    }
    retro_keys = {_normalizar_workflow(k) for k in (retro_cfg.get("workflow_d1_keys") or [])}
    missing_keys = retro_keys - config_d1_keys
    if not missing_keys:
        return itens_config

    existing_d1_keys = {
        _normalizar_workflow(str(item.get("workflow_d1") or ""))
        for item in itens_config
        if item.get("workflow_d1")
    }

    from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa, load_planning_dataframes

    mapa_d1: pd.DataFrame
    calc_params: dict
    categorias: pd.DataFrame | None = None
    if is_fonte_banco_ativa(settings):
        run_id = str(settings.get("run_id") or "")
        db_data = load_planning_dataframes(settings, run_id=run_id or None)
        mapa_d1 = db_data["mapa_workflow_d1"].copy()
        calc_params = dict(db_data.get("calculadora_params") or {})
        categorias = db_data.get("categoria_clientes")
    else:
        paths = rap._resolver_paths_config(settings)
        mapa_d1 = carregar_mapa_workflow_d1(paths["default"], settings=settings)
        calc_params = dict(settings.get("_calculadora_params") or {})
        if not calc_params:
            calc_params = rap._ler_meta_calculadora_padrao(paths["default"])
        categorias = carregar_categoria_clientes(paths["categoria"], settings=settings)

    if mapa_d1.empty:
        plano.warnings.append(
            "Retroativo: cadastro de workflows indisponível para complementar config."
        )
        return itens_config

    mapa_d1 = mapa_d1.copy()
    mapa_d1["_wf_d1_key"] = mapa_d1[COLUNA_CONFIG_WORKFLOW_D1].map(_normalizar_workflow)
    if categorias is not None and not categorias.empty:
        cat_cols = [COLUNA_CONFIG_SEGMENTO, COLUNA_CONFIG_CATEGORIA, "_cli_key"]
        if COLUNA_CONFIG_META_CLIENTE in categorias.columns:
            cat_cols.insert(2, COLUNA_CONFIG_META_CLIENTE)
        mapa_d1["_cli_key"] = mapa_d1[COLUNA_CONFIG_CLIENTE].map(_normalizar_workflow)
        mapa_d1 = mapa_d1.merge(categorias[cat_cols], on="_cli_key", how="left")

    novos: List[dict] = []
    for wf_key in sorted(missing_keys):
        if wf_key in existing_d1_keys:
            continue
        mapa_rows = mapa_d1[mapa_d1["_wf_d1_key"].astype(str) == wf_key]
        if mapa_rows.empty:
            nomes = [
                str(v)
                for v in (retro_cfg.get("workflows_nomes") or {}).values()
                if _normalizar_workflow(str(v)) == wf_key
            ]
            plano.warnings.append(
                "Retroativo: workflow "
                f"({nomes[0] if nomes else wf_key}) sem cadastro; ignorado no plano."
            )
            continue

        mrow = mapa_rows.iloc[0]
        workflow = str(mrow[COLUNA_CONFIG_WORKFLOW])
        workflow_d1 = str(mrow[COLUNA_CONFIG_WORKFLOW_D1])
        alias_map = _build_retro_workflow_alias_map(settings, retro_cfg)
        vol = _volumetria_retroativa_workflow(df, wf_key, alias_map=alias_map)
        if vol[COLUNA_CONFIG_TOTAL] <= 0:
            continue

        wf_calc = {
            "_wf_key": str(mrow.get("_wf_key") or _normalizar_workflow(workflow)),
            COLUNA_CONFIG_CATEGORIA: str(mrow.get(COLUNA_CONFIG_CATEGORIA, "") or ""),
            COLUNA_CONFIG_TOTAL: vol[COLUNA_CONFIG_TOTAL],
            COLUNA_CONFIG_MANUAIS: vol[COLUNA_CONFIG_MANUAIS],
            COLUNA_CONFIG_AUTOMATICOS: vol[COLUNA_CONFIG_AUTOMATICOS],
        }
        contagens = contar_por_hora(df, workflow_d1)
        if not contagens:
            continue
        total_disponivel = sum(contagens.values())
        wf_dto = _workflow_dto_retroativo(settings, wf_key, retro_cfg)
        amostra_diaria = _resolver_amostra_retroativa_workflow(
            wf_dto=wf_dto,
            wf_calc_row=wf_calc,
            calc_params=calc_params,
            total_disponivel=total_disponivel,
        )
        if amostra_diaria <= 0:
            if wf_dto:
                plano.warnings.append(
                    f"Retroativo: {workflow} sem amostra calculada a partir da volumetria retroativa."
                )
            continue

        synthetic_row = pd.Series(
            {
                COLUNA_CONFIG_WORKFLOW: workflow,
                COLUNA_CONFIG_WORKFLOW_D1: workflow_d1,
                COLUNA_CONFIG_CLIENTE: str(mrow.get(COLUNA_CONFIG_CLIENTE, "") or ""),
                COLUNA_CONFIG_CATEGORIA: str(mrow.get(COLUNA_CONFIG_CATEGORIA, "") or ""),
                COLUNA_CONFIG_FILA: str(
                    mrow.get(COLUNA_CONFIG_FILA, REPLICACAO_FILA_G_AUDITORIA)
                    or REPLICACAO_FILA_G_AUDITORIA
                ),
                COLUNA_CONFIG_USAR_ARQUIVO_CSV: mrow.get(
                    COLUNA_CONFIG_USAR_ARQUIVO_CSV, pd.NA
                ),
                COLUNA_CONFIG_NOME_REGRA_BRFLOW: mrow.get(
                    COLUNA_CONFIG_NOME_REGRA_BRFLOW, ""
                ),
                "amostra_diaria": amostra_diaria,
                "amostra_ajustada": amostra_diaria,
                **vol,
            }
        )
        novos.append(
            {
                "workflow": workflow,
                "workflow_d1": workflow_d1,
                "amostra": amostra_diaria,
                "amostra_diaria": amostra_diaria,
                "contagens": contagens,
                "tem_d1": True,
                "cliente": str(mrow.get(COLUNA_CONFIG_CLIENTE, "") or ""),
                "categoria": str(mrow.get(COLUNA_CONFIG_CATEGORIA, "") or ""),
                "row": synthetic_row,
                "somente_retroativo": True,
            }
        )
        existing_d1_keys.add(wf_key)

    if novos:
        log.info(
            "Retroativo D-1 | %d workflow(s) só-retroativo(s) incluído(s) na amostragem",
            len(novos),
        )
    return itens_config + novos


def expandir_pool_retroativo(
    df: pd.DataFrame,
    config: pd.DataFrame,
    *,
    settings: dict,
    data_ref: datetime,
    plano: PlanoReplicacao,
    database_only: bool,
    fallback_ultimo_parquet: bool = REPLICACAO_AUD_D1_FALLBACK_PARQUET_DEFAULT,
) -> pd.DataFrame:
    retro = carregar_config_retroativa(settings)
    run_id = str(getattr(plano, "run_id", "") or settings.get("run_id") or "")
    if not retro:
        plan_log(
            log,
            logging.INFO,
            "Retroativo inativo",
            run_id=run_id,
            phase=PLANNING_RETRO,
            active=False,
        )
        return df

    data_ref_date = data_ref.date() if hasattr(data_ref, "date") else data_ref
    inicio = datetime.strptime(str(retro["data_inicio"])[:10], "%Y-%m-%d").date()
    fim = datetime.strptime(str(retro["data_fim"])[:10], "%Y-%m-%d").date()
    if fim > data_ref_date:
        plano.warnings.append(
            f"Retroativo: data fim ({fim.isoformat()}) posterior à referência D-1 "
            f"({data_ref_date.isoformat()}); intervalo ajustado."
        )
        fim = data_ref_date
    if inicio > fim:
        plano.warnings.append("Retroativo: intervalo inválido (início posterior ao fim).")
        return df

    workflow_d1_set = set(retro.get("workflow_d1_keys") or [])
    if not workflow_d1_set:
        plano.warnings.append("Retroativo: nenhum workflow elegível selecionado.")
        return df

    alias_map = _build_retro_workflow_alias_map(settings, retro)
    workflow_filter_keys = set(alias_map.keys()) if alias_map else workflow_d1_set

    # Classifica workflows presentes no D-1 de referência vs só-retroativos.
    config_d1_keys: set[str] = set()
    for _, row in config.iterrows():
        workflow_d1 = _workflow_d1_da_linha(row)
        if workflow_d1:
            config_d1_keys.add(_normalizar_workflow(workflow_d1))

    somente_retroativo = workflow_d1_set - config_d1_keys
    if somente_retroativo:
        nomes = [
            str(v)
            for v in (retro.get("workflows_nomes") or {}).values()
            if _normalizar_workflow(str(v)) in somente_retroativo
        ]
        if not nomes:
            nomes = sorted(somente_retroativo)[:5]
        plano.warnings.append(
            "Retroativo: workflow(s) sem registro no D-1 do dia "
            f"({', '.join(nomes) or '—'}); pool será carregado do intervalo retroativo."
        )

    retro_config = {
        **retro,
        "data_inicio": inicio.isoformat(),
        "data_fim": fim.isoformat(),
        "workflow_d1_keys": workflow_filter_keys,
    }
    try:
        if database_only:
            from app.bots.replicacao_d1_db_bridge import (
                fallback_parquet_dias_ausentes_ativo,
                load_retro_source_dataframe_db,
                load_retro_source_hybrid_db_parquet,
            )

            if fallback_parquet_dias_ausentes_ativo(settings):
                retro_path = "hybrid"
                retro_df = load_retro_source_hybrid_db_parquet(
                    settings, data_ref, retro_config, plano
                )
            else:
                retro_path = "db"
                retro_df = load_retro_source_dataframe_db(settings, data_ref, retro_config)
        else:
            retro_path = "parquet"
            retro_df = _carregar_pool_retroativo_parquet(
                inicio=inicio,
                fim=fim,
                workflow_d1_set=workflow_filter_keys,
                fallback_ultimo_parquet=fallback_ultimo_parquet,
                plano=plano,
            )
    except Exception as exc:
        if (
            not database_only
            and not fallback_ultimo_parquet
            and isinstance(exc, FileNotFoundError)
        ):
            raise
        plano.warnings.append(f"Retroativo: falha ao carregar intervalo ({exc}).")
        return df

    retro_df = _aplicar_aliases_workflow_retroativo(retro_df, alias_map)

    if retro_df.empty:
        plano.warnings.append(
            f"Retroativo: nenhum protocolo no intervalo {inicio.isoformat()}–{fim.isoformat()}."
        )
        return df

    existing_ref: set[tuple[str, str]] = set()
    existing_retro: set[tuple[str, str, str]] = set()
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            proto = str(row.get("_protocolo_normalizado") or row.get(COLUNA_PROTOCOLO) or "").strip()
            wf = _normalizar_workflow(row.get(COLUNA_WORKFLOW_PARQUET, ""))
            if not proto or not wf:
                continue
            if rap._is_retroativo_row(row):
                retro_day = rap._extrair_dia_retroativo(row)
                existing_retro.add((proto.casefold(), wf, retro_day))
            else:
                existing_ref.add((proto.casefold(), wf))

    canonical_keys = {_normalizar_workflow(v) for v in alias_map.values()} if alias_map else workflow_d1_set
    novos: list[dict] = []
    for _, row in retro_df.iterrows():
        wf = _normalizar_workflow(row.get(COLUNA_WORKFLOW_PARQUET, ""))
        if wf not in canonical_keys and wf not in workflow_d1_set:
            continue
        proto_norm = str(row.get("_protocolo_normalizado") or row.get(COLUNA_PROTOCOLO) or "").casefold()
        retro_day = rap._extrair_dia_retroativo(row)
        if (proto_norm, wf) in existing_ref:
            continue
        retro_key = (proto_norm, wf, retro_day)
        if retro_key in existing_retro:
            continue
        novos.append(row.to_dict())
        existing_retro.add(retro_key)

    if not novos:
        plano.warnings.append(
            "Retroativo: nenhum protocolo novo após deduplicação com o pool D-1 do dia."
        )
        return df

    extra = pd.DataFrame(novos)
    merged = pd.concat([df, extra], ignore_index=True) if df is not None and not df.empty else extra
    log.info(
        "Retroativo D-1 | intervalo=%s..%s | +%d protocolo(s) no pool",
        inicio.isoformat(),
        fim.isoformat(),
        len(novos),
    )
    plan_log(
        log,
        logging.INFO,
        "Retroativo aplicado",
        run_id=run_id,
        phase=PLANNING_RETRO,
        path=retro_path,
        intervalo=f"{inicio.isoformat()}..{fim.isoformat()}",
        added=len(novos),
    )
    return merged


def gerar_plano_replicacao_d1(
    data_ref: Optional[datetime] = None,
    seed: Optional[int] = None,
    fallback_ultimo_parquet: bool = REPLICACAO_AUD_D1_FALLBACK_PARQUET_DEFAULT,
    salvar_plano_detalhado: bool = True,
    settings: Optional[dict] = None,
) -> PlanoReplicacao:
    """Gera plano D-1: volumetria do parquet, redistribuição priorizada, CSVs por workflow."""
    settings = _ensure_d1_settings(settings)
    if seed is None:
        seed = int(settings.get("replicacao_aud_seed", REPLICACAO_AUD_D1_SEED_DEFAULT))
    if "fallback_ultimo_parquet" in settings:
        fallback_ultimo_parquet = _parse_bool_setting(
            settings["fallback_ultimo_parquet"], fallback_ultimo_parquet
        )
    if data_ref is None and settings.get("replicacao_aud_data_ref"):
        data_ref = datetime.strptime(str(settings["replicacao_aud_data_ref"]).strip(), "%Y%m%d")
    data_ref = data_ref or (datetime.now() - timedelta(days=1))

    data_exec = datetime.now()
    run_id = _resolver_run_id_d1(data_exec, settings)
    settings = {**settings, "run_id": run_id}

    from app.bots.replicacao_d1_db_bridge import (
        ensure_planned_run_db,
        fallback_parquet_dias_ausentes_ativo,
        is_fonte_banco_ativa,
        load_source_dataframe_db,
        load_source_dataframe_hybrid_db,
        try_inject_planning_flags,
    )

    settings = try_inject_planning_flags(settings)
    optimized_planning = planejamento_otimizado_ativo(settings)
    optimized_planning_shadow = _parse_bool_setting(
        settings.get("optimized_planning_shadow"), False
    )
    database_only = is_fonte_banco_ativa(settings)
    fb_parquet = fallback_parquet_dias_ausentes_ativo(settings)
    plan_log(
        log,
        logging.INFO,
        "Planejamento iniciado",
        run_id=run_id,
        phase=PLANNING_START,
        database_only=database_only,
        data_ref=data_ref.strftime("%Y-%m-%d"),
        seed=seed,
        fallback_parquet=fb_parquet,
        fallback_ultimo_parquet=fallback_ultimo_parquet,
        optimized_planning=optimized_planning,
        optimized_planning_shadow=optimized_planning_shadow,
        run_id_reason=_run_id_resolution_reason(data_exec, settings, run_id),
    )

    source_batch_id: Optional[int] = None
    source_load_warnings: list[str] = []
    source_from_parquet_fallback = False
    if database_only:
        _DATABASE_ONLY_RUN_IDS.add(run_id)
        if fallback_parquet_dias_ausentes_ativo(settings):
            source = load_source_dataframe_hybrid_db(
                data_ref,
                settings=settings,
                warnings=source_load_warnings,
            )
        else:
            source = load_source_dataframe_db(data_ref)
        df = source["dataframe"]
        source_batch_id = int(source["source_batch_id"])
        source_name = str(source["source_name"])
        parquet_path = Path(source_name)
        source_from_parquet_fallback = bool(source.get("from_parquet_fallback"))
        if source_from_parquet_fallback:
            log.info(
                "Fonte D-1 via parquet fallback: lote=%s arquivo=%s",
                source_batch_id,
                source_name,
            )
            source_kind = "parquet_fallback"
        else:
            log.info("Carregando fonte D-1 do PostgreSQL: lote=%s", source_batch_id)
            source_kind = "db"
    else:
        try:
            parquet_path = resolver_parquet_d1(
                data_ref=data_ref,
                fallback_ultimo=fallback_ultimo_parquet,
            )
        except FileNotFoundError:
            if not fallback_ultimo_parquet:
                raise
            log.warning(
                "Parquet D-1 ausente para %s; usando fallback para o último parquet disponível",
                data_ref.strftime("%Y%m%d"),
            )
            parquet_path = resolver_parquet_d1(
                data_ref=data_ref,
                fallback_ultimo=True,
            )
        log.info("Carregando parquet D-1 (volumetria + protocolos): %s", parquet_path)
        df = ler_parquet(parquet_path, log=log)
        source_name = parquet_path.name
        source_kind = "parquet"
    _validar_parquet(df)
    report_date_str = (
        data_ref.date().isoformat() if hasattr(data_ref, "date") else str(data_ref)[:10]
    )
    plan_log(
        log,
        logging.INFO,
        "Fonte D-1 carregada",
        run_id=run_id,
        phase=PLANNING_SOURCE,
        source=source_kind,
        batch_id=source_batch_id or "",
        rows=len(df),
        report_date=report_date_str,
    )

    settings = {**settings, "_parquet_ref": source_name}
    if database_only:
        ensure_planned_run_db(
            run_id,
            data_referencia_d1=data_ref.date() if hasattr(data_ref, "date") else data_ref,
            parquet_referencia="",
        )
    config_raw = carregar_config_auditoria_d1(df, settings=settings)
    config_raw = filtrar_config_por_fila(config_raw, settings=settings)
    config_raw = filtrar_config_destinos_desabilitados(config_raw, settings=settings)
    parquet_ref = str(config_raw.attrs.get("parquet_referencia", source_name))
    config_warnings = list(config_raw.attrs.get("config_warnings", []) or [])
    workflows_pendentes_config = list(config_raw.attrs.get("workflows_pendentes_config", []) or [])
    workflows_pendentes_novos = list(config_raw.attrs.get("workflows_pendentes_novos", []) or [])
    workflows_pendentes_existentes = list(config_raw.attrs.get("workflows_pendentes_existentes", []) or [])
    workflows_pendentes_falha_sync = list(config_raw.attrs.get("workflows_pendentes_falha_sync", []) or [])
    default_xlsx_path = str(config_raw.attrs.get("default_xlsx_path", "") or "")

    plan_log(
        log,
        logging.INFO,
        "Config carregada",
        run_id=run_id,
        phase=PLANNING_CONFIG,
        workflows=len(config_raw),
        pendentes_config=len(workflows_pendentes_config),
        pendentes_novos=len(workflows_pendentes_novos),
        fonte="banco" if database_only else "legado",
    )

    try:
        config, info_capacidade = aplicar_escala_auditores_config_por_fila(
            config_raw, data_ref=data_ref, settings=settings
        )
    except (FileNotFoundError, ValueError) as exc:
        if bool(settings.get("usar_escala_auditores", REPLICACAO_AUD_D1_USAR_ESCALA_DEFAULT)):
            raise
        log.warning("Escala de auditores não aplicada (D-1): %s", exc)
        config = config_raw.copy()
        config["amostra_ajustada"] = (
            pd.to_numeric(config["amostra_diaria"], errors="coerce").fillna(0).astype(int)
        )
        soma_esc = _as_int(config["amostra_diaria"].sum())
        info_capacidade = InfoCapacidade(0, 0.0, float(soma_esc), soma_esc, 1.0, False)

    if not database_only and bool(settings.get("limpar_planos_ao_gerar", REPLICACAO_AUD_D1_LIMPAR_PLANOS_AO_GERAR_DEFAULT)):
        aplicar_politica_retencao_planos_d1(settings, forcar_politica=True)

    pasta_protocolos = Path() if database_only else _pasta_saida_protocolos_d1(run_id)
    pasta_resumo = Path() if database_only else PASTA_REPLICACAO_AUD_D1_RESUMO
    if not database_only:
        pasta_protocolos.mkdir(parents=True, exist_ok=True)
        (pasta_protocolos / REPLICACAO_D1_SUBPASTA_BRFLOW).mkdir(parents=True, exist_ok=True)
        (pasta_protocolos / REPLICACAO_D1_SUBPASTA_CASE).mkdir(parents=True, exist_ok=True)

    excluir_historico = bool(
        settings.get("excluir_historico", REPLICACAO_AUD_D1_EXCLUIR_HISTORICO_DEFAULT)
    )
    dias_historico = int(settings.get("dias_historico", REPLICACAO_AUD_D1_DIAS_HISTORICO_DEFAULT))
    historico: set = set()
    if excluir_historico:
        historico = carregar_protocolos_historico(
            excluir_run_id=run_id,
            dias_historico=dias_historico,
            referencia=data_exec,
        )
        if optimized_planning:
            historico = rap.preparar_historico_planejamento(historico)

    plano = PlanoReplicacao(
        data_referencia=data_ref,
        pasta_protocolos=pasta_protocolos,
        pasta_resumo=pasta_resumo,
        run_id=run_id,
        pasta_execucao=pasta_protocolos,
        total_workflows_config=len(config),
        pool_redistribuido=0,
        estado_execucao_path=None if database_only else caminho_estado_execucao(run_id),
        auditores_ativos=info_capacidade.auditores_ativos,
        meta_produ=info_capacidade.meta_produ,
        capacidade_produtiva=info_capacidade.capacidade_produtiva,
        soma_amostra_diaria=info_capacidade.soma_amostra_diaria,
        fator_capacidade=info_capacidade.fator_capacidade,
        pasta_volumetria=parquet_ref,
        auditores_ativos_case=info_capacidade.auditores_ativos_case,
    )
    plano.pastas_fila = {} if database_only else {
        REPLICACAO_D1_SUBPASTA_BRFLOW: str(pasta_protocolos / REPLICACAO_D1_SUBPASTA_BRFLOW),
        REPLICACAO_D1_SUBPASTA_CASE: str(pasta_protocolos / REPLICACAO_D1_SUBPASTA_CASE),
    }
    plano.warnings.extend(config_warnings)
    plano.warnings.extend(source_load_warnings)
    plano.workflows_pendentes_config = workflows_pendentes_config
    plano.workflows_pendentes_novos = workflows_pendentes_novos
    plano.workflows_pendentes_existentes = workflows_pendentes_existentes
    plano.workflows_pendentes_falha_sync = workflows_pendentes_falha_sync
    plano.default_xlsx_path = default_xlsx_path

    ano_mes = resolver_ano_mes(settings, data_exec=data_exec, data_referencia_d1=data_ref)
    metas_workflow = carregar_metas_por_workflow(config)
    headroom_mensal = carregar_headroom_meta_mensal(ano_mes, metas_workflow, settings=settings)
    if metas_workflow and not database_only:
        recalcular_snapshot_mensal(ano_mes, metas_workflow, settings=settings)
    plano.warnings.extend(rap.validar_metas_workflows_config(config))
    fallback_sem_cap = bool(
        settings.get("meta_cliente_fallback_sem_cap", META_CLIENTE_FALLBACK_SEM_CAP_DEFAULT)
    )
    log.info(
        "Balanceamento D-1 | mês=%s | workflows_com_limite=%d",
        ano_mes,
        len(metas_workflow),
    )
    plan_log(
        log,
        logging.INFO,
        "Balanceamento mensal",
        run_id=run_id,
        phase=PLANNING_ALLOCATE,
        competencia=ano_mes,
        workflows_limite=len(metas_workflow),
        fallback_sem_cap=fallback_sem_cap,
        excluir_historico=excluir_historico,
        historico=len(historico),
    )

    for _, row in config.iterrows():
        wf = str(row[COLUNA_CONFIG_WORKFLOW]).strip()
        if wf:
            plano.workflow_brflow[wf] = _resolver_workflow_nome_brflow(row)
            fila = resolver_fila_linha(row)
            plano.workflow_fila[wf] = fila
            plano.workflow_modo_replicacao[wf] = resolver_modo_replicacao_linha(row, fila)
            regra = str(row.get("nome_regra_brflow", "") or "").strip()
            if regra:
                plano.workflow_regra_brflow[wf] = regra

    detalhes: List[pd.DataFrame] = []
    meta_base = {
        "RunId": run_id,
        "Parquet Referencia": "" if database_only else parquet_path.name,
        "Data Referencia D1": data_ref.strftime("%d/%m/%Y"),
        "Data Execucao": data_exec.strftime("%d/%m/%Y %H:%M"),
        "Seed Amostra": seed,
        "Fonte Volumetria": (
            "PostgreSQL (parquet fallback)"
            if database_only and source_from_parquet_fallback
            else ("PostgreSQL" if database_only else "parquet D-1")
        ),
        "Data Escala Auditores": (
            info_capacidade.data_escala.strftime("%d/%m/%Y")
            if info_capacidade.usar_escala and info_capacidade.data_escala
            else ""
        ),
        "Auditores Ativos BRFlow": info_capacidade.auditores_ativos if info_capacidade.usar_escala else "",
        "Auditores Ativos Case": info_capacidade.auditores_ativos_case if info_capacidade.usar_escala else "",
        "Auditores Ativos Bio": getattr(info_capacidade, "auditores_ativos_bio", 0) if info_capacidade.usar_escala else "",
        "Auditores Ativos Redoc": getattr(info_capacidade, "auditores_ativos_redoc", 0) if info_capacidade.usar_escala else "",
        "Meta Produ": round(info_capacidade.meta_produ, 2) if info_capacidade.usar_escala else "",
        "Capacidade Produtiva": round(info_capacidade.capacidade_produtiva, 1),
        "Soma Amostra Diaria": info_capacidade.soma_amostra_diaria,
        "Fator Capacidade": round(info_capacidade.fator_capacidade, 4),
        "Parquet Volumetria": parquet_ref,
    }

    if database_only:
        from app.bots.replicacao_d1_db_bridge import try_inject_execution_snapshot

        had_snapshot = bool(settings.get("_execution_snapshot"))
        settings = try_inject_execution_snapshot(settings)
        snap = settings.get("_execution_snapshot") or {}
        plan_log(
            log,
            logging.INFO,
            "Config congelada",
            run_id=run_id,
            phase=PLANNING_CONFIG,
            config_version=settings.get("config_version", ""),
            config_hash=truncate_hash(settings.get("config_hash")),
            competencia=settings.get("_competencia_meta_resolvida") or snap.get("competencia_resolvida", ""),
            snapshot_cache=had_snapshot,
        )
    df = expandir_pool_retroativo(
        df,
        config,
        settings=settings,
        data_ref=data_ref,
        plano=plano,
        database_only=database_only,
        fallback_ultimo_parquet=fallback_ultimo_parquet,
    )
    if optimized_planning or optimized_planning_shadow:
        prepared_df = rap.preparar_pool_planejamento(df)
        shadow_mismatches: list[str] = []
        if optimized_planning_shadow:
            workflow_d1_values = {
                str(_workflow_d1_da_linha(row))
                for _, row in config.iterrows()
                if str(_workflow_d1_da_linha(row)).strip()
            }
            shadow_mismatches = [
                workflow_d1
                for workflow_d1 in sorted(workflow_d1_values)
                if contar_por_hora(df, workflow_d1)
                != contar_por_hora(prepared_df, workflow_d1)
            ]
            plan_log(
                log,
                logging.WARNING if shadow_mismatches else logging.INFO,
                "Shadow do pool otimizado validado",
                run_id=run_id,
                phase=PLANNING_ALLOCATE,
                workflows=len(workflow_d1_values),
                mismatches=len(shadow_mismatches),
                warning=bool(shadow_mismatches),
            )
        if shadow_mismatches:
            optimized_planning = False
            plano.warnings.append(
                "Planejamento otimizado desativado nesta execução: divergência shadow "
                f"em {len(shadow_mismatches)} workflow(s)."
            )
        elif optimized_planning:
            df = prepared_df

    itens_config: List[dict] = []
    for _, row in config.iterrows():
        workflow = row[COLUNA_CONFIG_WORKFLOW]
        workflow_d1 = _workflow_d1_da_linha(row)
        amostra_diaria = _as_int(row["amostra_diaria"])
        amostra = _as_int(row["amostra_ajustada"])
        contagens = contar_por_hora(df, workflow_d1)
        itens_config.append(
            {
                "workflow": workflow,
                "workflow_d1": workflow_d1,
                "amostra": amostra,
                "amostra_diaria": amostra_diaria,
                "contagens": contagens,
                "tem_d1": bool(contagens),
                "cliente": str(row.get(COLUNA_CONFIG_CLIENTE, "") or ""),
                "categoria": str(row.get(COLUNA_CONFIG_CATEGORIA, "") or ""),
                "row": row,
            }
        )

    retro_cfg_early = carregar_config_retroativa(settings)
    itens_config = _complementar_itens_config_retroativo(
        itens_config,
        config,
        df,
        settings=settings,
        retro_cfg=retro_cfg_early,
        plano=plano,
    )

    for item in itens_config:
        workflow = str(item["workflow"])
        row = item["row"]
        fila = plano.workflow_fila.get(workflow) or resolver_fila_linha(row)
        modo = plano.workflow_modo_replicacao.get(workflow) or resolver_modo_replicacao_linha(
            row, fila
        )
        plano.workflow_fila[workflow] = fila
        plano.workflow_modo_replicacao[workflow] = modo
        item["modo_replicacao"] = modo

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
    if itens_config and len(sem_d1) / len(itens_config) > 0.5:
        plano.warnings.append(
            "Mais da metade dos workflows sem D-1 de referência; "
            "verifique sync Rotina ou ative fallback_parquet_dias_ausentes."
        )
    sem_d1_redist = [
        item
        for item in sem_d1
        if item.get("amostra_override_pct") is None
        and item.get("modo_replicacao") == REPLICACAO_MODO_PROTOCOLOS
    ]
    com_d1_redist = [item for item in com_d1 if item.get("amostra_override_pct") is None]
    pool_redistribuir = sum(item["amostra"] for item in sem_d1_redist)
    plano.pool_redistribuido = pool_redistribuir

    if pool_redistribuir > 0 and not com_d1:
        plano.warnings.append(
            f"{pool_redistribuir} protocolos sem destino: nenhum workflow com registro no D-1"
        )
    elif pool_redistribuir > 0:
        log.info(
            "Redistribuindo D-1 %d protocolos de %d workflow(s) sem D-1 (prioridade cliente/categoria)",
            pool_redistribuir,
            len(sem_d1_redist),
        )

    bonus_por_workflow, sobra_redist = redistribuir_amostra_priorizada_com_meta(
        sem_d1_redist,
        com_d1_redist,
        _reduzir_headroom_base_run(headroom_mensal, com_d1_redist),
        fallback_sem_cap=fallback_sem_cap,
    )
    plano.volume_redistribuicao_nao_alocado = int(sobra_redist)
    if sobra_redist > 0 and not fallback_sem_cap:
        plano.warnings.append(
            f"{sobra_redist} protocolo(s) de redistribuição não alocados (limites de balanceamento / fallback desativado)"
        )

    if sem_d1:
        log.info(
            "Ignorando %d workflow(s) sem registros/protocolos executáveis no plano D-1",
            len(sem_d1),
        )

    resultados_com_d1: List[dict] = []
    deficits_fontes: List[dict] = []
    usar_mix, pct_mix_manual, pct_mix_auto = _resolver_mix_manual_automatico(settings)
    if optimized_planning and usar_mix:
        df = _df_com_flag_matricula(df)
    if usar_mix:
        log.info(
            "Pref. Manual×Automático ativa | manual=%d%% | automático=%d%%",
            pct_mix_manual,
            pct_mix_auto,
        )

    retro_cfg = carregar_config_retroativa(settings)
    retro_wf_keys: set[str] = set()
    if retro_cfg:
        retro_wf_keys = {_normalizar_workflow(k) for k in (retro_cfg.get("workflow_d1_keys") or [])}

    protocolos_reservados_plano: set[str] = set()

    total_workflows_alocacao = len(com_d1)
    intervalo_progresso = max(1, (total_workflows_alocacao + 9) // 10)
    for indice_workflow, item in enumerate(com_d1, start=1):
        workflow = item["workflow"]
        workflow_d1 = item["workflow_d1"]
        if (
            indice_workflow == 1
            or indice_workflow == total_workflows_alocacao
            or indice_workflow % intervalo_progresso == 0
        ):
            plan_log(
                log,
                logging.INFO,
                f"Alocando workflows {indice_workflow}/{total_workflows_alocacao}",
                run_id=run_id,
                phase=PLANNING_ALLOCATE,
                current=indice_workflow,
                total=total_workflows_alocacao,
                workflow=str(workflow),
            )
        amostra = item["amostra"]
        contagens = item["contagens"]
        total_disponivel = sum(contagens.values())
        hist = historico if excluir_historico else None
        mix_detalhe: Optional[dict] = None
        is_retro_wf = _normalizar_workflow(workflow_d1) in retro_wf_keys
        modo_replicacao = plano.workflow_modo_replicacao.get(
            workflow, REPLICACAO_MODO_PROTOCOLOS
        )

        if modo_replicacao == REPLICACAO_MODO_QTD:
            bonus = int(bonus_por_workflow.get(workflow, 0))
            override_pct = item.get("amostra_override_pct")
            amostra_base = _calcular_amostra_base_qtd(
                amostra=amostra,
                amostra_override_pct=int(override_pct) if override_pct is not None else None,
                total_disponivel=total_disponivel,
            )
            resultados_com_d1.append(
                {
                    "item": item,
                    "workflow": workflow,
                    "workflow_d1": workflow_d1,
                    "amostra": amostra_base,
                    "bonus": bonus,
                    "amostra_efetiva": amostra_base + bonus,
                    "alocacao": {},
                    "excl_hist": 0,
                    "total_disponivel": total_disponivel,
                    "protocolos": [],
                    "qtd_salva": 0,
                    "amostra_override_pct": int(override_pct) if override_pct is not None else None,
                    "mix_matricula": None,
                    "modo_qtd": True,
                    "modo_replicacao": REPLICACAO_MODO_QTD,
                }
            )
            continue

        if item.get("amostra_override_pct") is not None:
            override_pct = int(item["amostra_override_pct"])
            if usar_mix:
                # Meta = % do disponível D-1, depois aplica mix Manual×Automático.
                target = max(1, round(total_disponivel * override_pct / 100)) if total_disponivel else 0
                if override_pct >= 100:
                    target = total_disponivel
                if is_retro_wf:
                    selecionados, excl_hist, mix_detalhe, split_avisos = (
                        selecionar_protocolos_com_mix_matricula_retroativo_split(
                            df,
                            workflow_d1,
                            target,
                            pct_referencia=RETROATIVO_SPLIT_REFERENCIA_PCT,
                            pct_manual=pct_mix_manual,
                            pct_automatico=pct_mix_auto,
                            seed=seed,
                            historico=hist,
                        )
                    )
                    plano.warnings.extend(split_avisos)
                else:
                    selecionados, excl_hist, mix_detalhe = selecionar_protocolos_com_mix_matricula(
                        df,
                        workflow_d1,
                        target,
                        pct_manual=pct_mix_manual,
                        pct_automatico=pct_mix_auto,
                        seed=seed,
                        historico=hist,
                    )
            elif is_retro_wf:
                target = max(1, round(total_disponivel * override_pct / 100)) if total_disponivel else 0
                if override_pct >= 100:
                    target = total_disponivel
                selecionados, excl_hist, split_avisos = selecionar_protocolos_retroativo_split(
                    df,
                    workflow_d1,
                    target,
                    pct_referencia=RETROATIVO_SPLIT_REFERENCIA_PCT,
                    seed=seed,
                    historico=hist,
                )
                plano.warnings.extend(split_avisos)
            else:
                selecionados, excl_hist = selecionar_protocolos_por_porcentagem(
                    df,
                    workflow_d1,
                    override_pct,
                    seed=seed,
                    historico=hist,
                )
            plano.excluidos_historico_total += excl_hist
            qtd_salva = len(selecionados)
            protocolos = (
                selecionados[COLUNA_PROTOCOLO].astype(str).tolist() if not selecionados.empty else []
            )
            _registrar_selection_reason_plano(plano, workflow, protocolos, df)
            horas_usadas = sorted(contagens.keys())
            resultados_com_d1.append(
                {
                    "item": item,
                    "workflow": workflow,
                    "workflow_d1": workflow_d1,
                    "amostra": amostra,
                    "bonus": 0,
                    "amostra_efetiva": qtd_salva,
                    "alocacao": {h: contagens[h] for h in horas_usadas},
                    "excl_hist": excl_hist,
                    "total_disponivel": total_disponivel,
                    "protocolos": protocolos,
                    "qtd_salva": qtd_salva,
                    "amostra_override_pct": override_pct,
                    "mix_matricula": mix_detalhe,
                }
            )
            continue

        bonus = int(bonus_por_workflow.get(workflow, 0))
        amostra_efetiva = amostra + bonus

        if is_retro_wf:
            if usar_mix:
                selecionados, excl_hist, mix_detalhe, split_avisos = (
                    selecionar_protocolos_com_mix_matricula_retroativo_split(
                        df,
                        workflow_d1,
                        amostra_efetiva,
                        pct_referencia=RETROATIVO_SPLIT_REFERENCIA_PCT,
                        pct_manual=pct_mix_manual,
                        pct_automatico=pct_mix_auto,
                        seed=seed,
                        historico=hist,
                    )
                )
                plano.warnings.extend(split_avisos)
            else:
                selecionados, excl_hist, split_avisos = selecionar_protocolos_retroativo_split(
                    df,
                    workflow_d1,
                    amostra_efetiva,
                    pct_referencia=RETROATIVO_SPLIT_REFERENCIA_PCT,
                    seed=seed,
                    historico=hist,
                )
                plano.warnings.extend(split_avisos)
            alocacao = {}
            if not selecionados.empty and "_hora" in selecionados.columns:
                alocacao = {
                    int(h): int(c)
                    for h, c in selecionados.groupby("_hora").size().astype(int).items()
                }
            elif contagens:
                alocacao = alocar_amostra_por_hora(contagens, amostra_efetiva)
        elif usar_mix:
            selecionados, excl_hist, mix_detalhe = selecionar_protocolos_com_mix_matricula(
                df,
                workflow_d1,
                amostra_efetiva,
                pct_manual=pct_mix_manual,
                pct_automatico=pct_mix_auto,
                seed=seed,
                historico=hist,
            )
            alocacao = {}
            if not selecionados.empty and "_hora" in selecionados.columns:
                alocacao = {
                    int(h): int(c)
                    for h, c in selecionados.groupby("_hora").size().astype(int).items()
                }
            elif contagens:
                alocacao = alocar_amostra_por_hora(contagens, amostra_efetiva)
        else:
            alocacao = alocar_amostra_por_hora(contagens, amostra_efetiva)
            selecionados, excl_hist = selecionar_protocolos(
                df,
                workflow_d1,
                alocacao,
                seed=seed,
                historico=hist,
            )
        plano.excluidos_historico_total += excl_hist
        qtd_salva = len(selecionados)
        protocolos = selecionados[COLUNA_PROTOCOLO].astype(str).tolist() if not selecionados.empty else []
        _registrar_selection_reason_plano(plano, workflow, protocolos, df)

        if qtd_salva < amostra_efetiva:
            deficits_fontes.append(
                {
                    "workflow": workflow,
                    "amostra": amostra_efetiva - qtd_salva,
                    "cliente": item["cliente"],
                    "categoria": item["categoria"],
                }
            )

        resultados_com_d1.append(
            {
                "item": item,
                "workflow": workflow,
                "workflow_d1": workflow_d1,
                "amostra": amostra,
                "bonus": bonus,
                "amostra_efetiva": amostra_efetiva,
                "alocacao": alocacao,
                "excl_hist": excl_hist,
                "total_disponivel": total_disponivel,
                "protocolos": protocolos,
                "qtd_salva": qtd_salva,
                "amostra_override_pct": None,
                "mix_matricula": mix_detalhe,
            }
        )

    bonus_deficit_total: Dict[str, int] = defaultdict(int)
    sobra_deficit = 0
    if deficits_fontes:
        bonus_deficit_total, sobra_deficit = redistribuir_amostra_priorizada_com_meta(
            deficits_fontes,
            com_d1_redist,
            dict(headroom_mensal),
            fallback_sem_cap=fallback_sem_cap,
        )
        plano.volume_redistribuicao_nao_alocado += int(sobra_deficit)

    for res in resultados_com_d1:
        workflow = res["workflow"]
        workflow_d1 = res["workflow_d1"]
        item = res["item"]
        amostra = res["amostra"]
        qtd_salva = res["qtd_salva"]
        excl_hist = res["excl_hist"]
        alocacao = res["alocacao"]
        total_disponivel = res["total_disponivel"]
        nome_csv = f"{_sanitizar_nome_arquivo(workflow)}.csv"
        horas_usadas = sorted(alocacao.keys())
        faixa = f"{horas_usadas[0]:02d}h-{horas_usadas[-1]:02d}h" if horas_usadas else ""

        if res.get("modo_replicacao") == REPLICACAO_MODO_QTD or res.get("modo_qtd"):
            bonus = res["bonus"] + int(bonus_deficit_total.get(workflow, 0))
            amostra_efetiva_final = int(res["amostra"]) + bonus
            plano.qtd_por_workflow[workflow] = amostra_efetiva_final
            amostra_solicitada = (
                formatar_amostra_override(int(res["amostra_override_pct"]))
                if res.get("amostra_override_pct") is not None
                else res["amostra"]
            )
            _append_resumo_modo_qtd(
                plano,
                workflow=workflow,
                workflow_d1=workflow_d1,
                item=item,
                amostra_efetiva_final=amostra_efetiva_final,
                amostra_solicitada=amostra_solicitada,
                amostra_redistribuida=bonus,
                total_disponivel=total_disponivel,
                meta_base=meta_base,
                info_capacidade=info_capacidade,
                parquet_ref=parquet_ref,
            )
            continue

        if res.get("amostra_override_pct") is not None:
            override_pct = int(res["amostra_override_pct"])
            protocolos = _reservar_protocolos_unicos_no_plano(
                list(res["protocolos"]),
                protocolos_reservados_plano,
                workflow=workflow,
                plano=plano,
            )
            qtd_salva = len(protocolos)
            plano.protocolos_por_workflow[workflow] = protocolos
            pct_atingido = (
                round(100 * qtd_salva / total_disponivel, 1) if total_disponivel else 0.0
            )
            obs_parts = [f"Amostra {formatar_amostra_override(override_pct)} do D-1"]
            mix_obs = _obs_mix_matricula(res.get("mix_matricula"))
            if mix_obs:
                obs_parts.append(mix_obs)
            if excl_hist > 0:
                obs_parts.append(f"{excl_hist} excluído(s) por histórico")
            plano.resumo.append(
                {
                    "Workflow": workflow,
                    "Workflow D1": workflow_d1,
                    "Amostra Diaria": item["amostra_diaria"],
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
                    "Modo Replicacao": REPLICACAO_MODO_PROTOCOLOS,
                    "Observacao": "; ".join(obs_parts),
                    "RedistribuicaoTier": "",
                    **meta_base,
                    **_meta_resumo_fila(workflow, plano, info_capacidade),
                    **_meta_colunas_config_d1(item["row"], parquet_ref),
                }
            )
            if protocolos:
                det_rows = rap._filtrar_por_protocolos_exatos(df, protocolos)
                if not det_rows.empty:
                    det_rows[COLUNA_DATA_ANALISE] = _parse_data_analise(det_rows[COLUNA_DATA_ANALISE])
                    det_rows = det_rows[det_rows[COLUNA_DATA_ANALISE].notna()].copy()
                    det_rows["_hora"] = det_rows[COLUNA_DATA_ANALISE].dt.hour
                    det = det_rows[[COLUNA_PROTOCOLO, COLUNA_WORKFLOW_PARQUET, COLUNA_DATA_ANALISE]].copy()
                    det["Hora"] = det_rows["_hora"]
                    det["WorkflowConfig"] = workflow
                    det["Canal Destino"] = resolver_canal_destino(
                        plano.workflow_fila.get(workflow, REPLICACAO_FILA_G_AUDITORIA)
                    )
                    detalhes.append(det)
            continue

        bonus = res["bonus"] + int(bonus_deficit_total.get(workflow, 0))
        amostra_efetiva_final = amostra + bonus
        protocolos = list(res["protocolos"])

        extra = int(bonus_deficit_total.get(workflow, 0))
        if extra > 0:
            ja_usados = set(protocolos)
            contagens_rest = _contagens_restantes_por_hora(
                df,
                workflow_d1,
                excluir_protocolos=ja_usados,
                historico=historico if excluir_historico else None,
            )
            aloc_extra = alocar_amostra_por_hora(contagens_rest, extra)
            hist_extra = (historico | ja_usados) if (historico and excluir_historico) else ja_usados
            extra_df, _ = selecionar_protocolos(
                df,
                workflow_d1,
                aloc_extra,
                seed=seed + 999,
                historico=hist_extra if hist_extra else None,
            )
            if not extra_df.empty:
                protocolos.extend(extra_df[COLUNA_PROTOCOLO].astype(str).tolist())
                qtd_salva = len(protocolos)
                if aloc_extra:
                    horas_extra = sorted(aloc_extra.keys())
                    faixa_extra = f"{horas_extra[0]:02d}h-{horas_extra[-1]:02d}h"
                    if faixa:
                        faixa = f"{faixa}+{faixa_extra}"
                    else:
                        faixa = faixa_extra

        protocolos = _reservar_protocolos_unicos_no_plano(
            protocolos,
            protocolos_reservados_plano,
            workflow=workflow,
            plano=plano,
        )
        qtd_salva = len(protocolos)
        plano.protocolos_por_workflow[workflow] = protocolos

        pct = round(100 * qtd_salva / amostra_efetiva_final, 1) if amostra_efetiva_final else 0.0
        obs_parts = []
        if bonus > 0:
            obs_parts.append(f"+{bonus} redistribuídos (prioridade cliente/categoria/saldo de balanceamento)")
        mix_obs = _obs_mix_matricula(res.get("mix_matricula"))
        if mix_obs:
            obs_parts.append(mix_obs)
        if qtd_salva < amostra_efetiva_final:
            obs_parts.append(f"Amostra parcial ({qtd_salva}/{amostra_efetiva_final})")

        plano.resumo.append(
            {
                "Workflow": workflow,
                "Workflow D1": workflow_d1,
                "Amostra Diaria": item["amostra_diaria"],
                "Amostra Solicitada": amostra,
                "Amostra Redistribuida": bonus,
                "Amostra Efetiva": amostra_efetiva_final,
                "Protocolos Salvos": qtd_salva,
                "Excluidos Historico": excl_hist,
                "Disponivel D1": total_disponivel,
                "Horas Utilizadas": len(horas_usadas),
                "Faixa Horaria": faixa,
                "Status": _classificar_status(amostra_efetiva_final, qtd_salva, sem_registro=False),
                "Pct Atingido": pct,
                "Arquivo CSV": nome_csv,
                "Modo Replicacao": REPLICACAO_MODO_PROTOCOLOS,
                "Observacao": "; ".join(obs_parts),
                "RedistribuicaoTier": "",
                **meta_base,
                **_meta_resumo_fila(workflow, plano, info_capacidade),
                **_meta_colunas_config_d1(item["row"], parquet_ref),
            }
        )

        if protocolos:
            det_rows = rap._filtrar_por_protocolos_exatos(df, protocolos)
            if not det_rows.empty:
                det_rows[COLUNA_DATA_ANALISE] = _parse_data_analise(det_rows[COLUNA_DATA_ANALISE])
                det_rows = det_rows[det_rows[COLUNA_DATA_ANALISE].notna()].copy()
                det_rows["_hora"] = det_rows[COLUNA_DATA_ANALISE].dt.hour
                det = det_rows[[COLUNA_PROTOCOLO, COLUNA_WORKFLOW_PARQUET, COLUNA_DATA_ANALISE]].copy()
                det["Hora"] = det_rows["_hora"]
                det["WorkflowConfig"] = workflow
                det["Canal Destino"] = resolver_canal_destino(
                    plano.workflow_fila.get(workflow, REPLICACAO_FILA_G_AUDITORIA)
                )
                det["AmostraEfetiva"] = amostra_efetiva_final
                for h, q in alocacao.items():
                    det.loc[det["Hora"] == h, "QuotaHora"] = q
                detalhes.append(det)

    for item in sem_d1:
        workflow = item["workflow"]
        if plano.workflow_modo_replicacao.get(workflow) != REPLICACAO_MODO_QTD:
            continue
        amostra = _as_int(item.get("amostra", 0))
        if amostra <= 0 or workflow in plano.qtd_por_workflow:
            continue
        plano.qtd_por_workflow[workflow] = amostra
        if workflow not in plano.workflows_sem_registro:
            plano.workflows_sem_registro.append(workflow)
        _append_resumo_modo_qtd(
            plano,
            workflow=workflow,
            workflow_d1=item["workflow_d1"],
            item=item,
            amostra_efetiva_final=amostra,
            amostra_solicitada=amostra,
            amostra_redistribuida=0,
            total_disponivel=0,
            meta_base=meta_base,
            info_capacidade=info_capacidade,
            parquet_ref=parquet_ref,
            observacao_extra="sem registros no D-1",
        )

    plano.workflows = [
        item["workflow"]
        for item in itens_config
        if plano.protocolos_por_workflow.get(item["workflow"])
        or _as_int(plano.qtd_por_workflow.get(item["workflow"], 0)) > 0
    ]
    total_protocolos_pre = sum(len(v) for v in plano.protocolos_por_workflow.values())
    total_qtd_pre = sum(plano.qtd_por_workflow.values())
    if database_only and total_protocolos_pre <= 0 and total_qtd_pre <= 0:
        detalhes_msg: list[str] = []
        if excluir_historico and plano.excluidos_historico_total > 0:
            detalhes_msg.append(
                f"{plano.excluidos_historico_total} protocolo(s) excluído(s) por histórico recente"
            )
        if plano.volume_redistribuicao_nao_alocado > 0:
            detalhes_msg.append(
                f"{plano.volume_redistribuicao_nao_alocado} protocolo(s) de redistribuição não alocados"
            )
        extra = f" ({'; '.join(detalhes_msg)})" if detalhes_msg else ""
        raise ValueError(
            f"Plano não possui protocolos ou quantidades qtd para persistir{extra}. "
            "Verifique histórico, amostra retroativa ou cadastro de workflows."
        )
    if not database_only:
        protocolos_export = _protocolos_exportaveis_por_modo(plano)
        paths_ok = exportar_protocolos_csv_por_fila(
            protocolos_export,
            pasta_protocolos,
            plano.workflow_fila,
        )
        plano.csv_paths.update(paths_ok)
    plano.seed_amostra = int(seed)

    df_det = pd.concat(detalhes, ignore_index=True) if detalhes else pd.DataFrame()
    if database_only:
        if source_batch_id is None:
            raise RuntimeError("Lote de fonte D-1 não resolvido para persistência do plano.")
        from app.bots.replicacao_d1_db_bridge import persist_plan_db

        persist_plan_db(plano, settings=settings, source_batch_id=source_batch_id)
    estado = inicializar_estado_execucao(
        plano,
        parquet_referencia="" if database_only else parquet_path.name,
        data_referencia_d1=data_ref.strftime("%d/%m/%Y"),
        data_execucao=data_exec.strftime("%d/%m/%Y %H:%M"),
        seed_amostra=int(seed),
    )
    if not database_only:
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
        "Plano D-1 gerado | run_id=%s | base=%s | protocolos=%s",
        run_id,
        "PostgreSQL" if database_only else PASTA_REPLICACAO_AUD_D1_BASE,
        "PostgreSQL" if database_only else pasta_protocolos,
    )
    total_protocolos = sum(len(v) for v in plano.protocolos_por_workflow.values())
    plan_signature = assinatura_logica_plano(plano)[:16]
    plan_log(
        log,
        logging.INFO,
        "Planejamento concluído",
        run_id=run_id,
        phase=PLANNING_DONE,
        workflows=len(plano.workflows),
        protocols=total_protocolos,
        warnings=len(plano.warnings),
        sem_registro=len(plano.workflows_sem_registro),
        source_parquet_fallback=source_from_parquet_fallback,
        backend="postgresql" if database_only else "arquivos",
        optimized_planning=optimized_planning,
        plan_signature=plan_signature,
    )
    return plano
