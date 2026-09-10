# -*- coding: utf-8 -*-
"""DTOs tipados para configuração permanente, opções de run e snapshot de execução."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

CALCULADORA_DEFAULTS: dict[str, float] = {
    "confianca": 0.99,
    "margin_high": 0.0115,
    "margin_low": 0.0178,
}


def normalizar_calculadora_params(raw: dict | None = None) -> dict[str, float]:
    """Mantem somente os parametros amostrais suportados pela calculadora D-1."""
    merged = dict(CALCULADORA_DEFAULTS)
    for key in CALCULADORA_DEFAULTS:
        try:
            if key in (raw or {}):
                merged[key] = float(raw[key])
        except (TypeError, ValueError):
            continue
    return merged


@dataclass(frozen=True)
class ClienteDTO:
    id: int | None
    nome: str
    chave_normalizada: str
    segmento_nome: str
    categoria_nome: str
    meta_mensal: int | None
    ativo: bool


@dataclass(frozen=True)
class WorkflowDTO:
    id: int | None
    nome_canonico: str
    nome_d1: str
    nome_selenium: str
    nome_regra_brflow: str
    fila: str
    cliente_nome: str
    cliente_chave: str
    chave_normalizada: str
    chave_d1_normalizada: str
    workflow_origem_id: int | None
    status: str
    amostra_pct_especial: int | None
    amostra_100: bool
    usar_arquivo_csv: bool
    ativo: bool


@dataclass(frozen=True)
class EscalaDiaDTO:
    data: str
    auditores_brflow: int
    auditores_case: int
    auditores_bio: int
    auditores_redoc: int


@dataclass(frozen=True)
class RetroativoWorkflowDTO:
    id: int
    nome_canonico: str
    nome_d1: str
    chave_normalizada: str
    chave_d1_normalizada: str
    cliente_nome: str
    ativo: bool


@dataclass(frozen=True)
class RetroativoConfigDTO:
    retroativo_ativo: bool
    retroativo_data_inicio: str
    retroativo_data_fim: str
    workflows: tuple[RetroativoWorkflowDTO, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PersistentConfig:
    """Configuração permanente — sem campos efêmeros de execução."""

    fonte_banco_ativa: bool
    config_version: int
    config_hash: str
    agendamento_ativo: bool
    agendamento_hora_planejamento: str
    agendamento_hora_execucao: str
    path_config_base: str
    path_default_xlsx: str
    path_categoria_xlsx: str
    path_escala_csv: str
    limpar_planos_automatico: bool
    limpar_planos_ao_gerar: bool
    limpar_planos_apos_conclusao: bool
    manter_planos_ultimos_n: int
    dias_retencao_planos: int
    replicacao_cliente_destino: str
    replicacao_cliente_cod: str
    replicacao_workflow_destino: str
    replicacao_workflow_cod: str
    replicacao_workflow_destino_31: str
    replicacao_workflow_cod_31: str
    replicacao_workflow_destino_bio: str
    replicacao_workflow_cod_bio: str
    replicacao_workflow_destino_redoc: str
    replicacao_workflow_cod_redoc: str
    replicacao_destino_brflow_ativo: bool
    replicacao_destino_case31_ativo: bool
    replicacao_destino_bio_ativo: bool
    replicacao_destino_redoc_ativo: bool
    meta_produ_diaria: float
    meta_produ_diaria_case: float
    meta_produ_diaria_bio: float
    meta_produ_diaria_redoc: float
    usar_escala_auditores: bool
    excluir_historico: bool
    dias_historico: int
    sobrescrever: bool
    fallback_ultimo_parquet: bool
    fallback_parquet_dias_ausentes: bool
    seed: int
    sincronizar_workflow_d1: bool
    apenas_ativos: bool
    meta_cliente_ano_mes_ref: str
    calculadora_params: dict[str, float]
    workflows_amostra_100: list[str]
    workflows_amostra_pct: dict[str, int]
    usar_amostra_mix_manual_automatico: bool
    amostra_pct_manual: int
    amostra_pct_automatico: int
    clientes: tuple[ClienteDTO, ...] = field(default_factory=tuple)
    workflows: tuple[WorkflowDTO, ...] = field(default_factory=tuple)
    workflows_pendentes: tuple[WorkflowDTO, ...] = field(default_factory=tuple)
    escala: tuple[EscalaDiaDTO, ...] = field(default_factory=tuple)
    retroativo: RetroativoConfigDTO | None = None

    def to_dict(self) -> dict[str, Any]:
        return _deep_freeze_dict(asdict(self))


@dataclass(frozen=True)
class RunOptions:
    """Opções temporárias de uma execução — não persistidas como config global."""

    run_id: str = ""
    data_ref: str = ""
    apenas_planejamento: bool = False
    gerar_novo_plano: bool = False
    forcar_reexecucao: bool = False
    apenas_pendentes: bool = True
    competencia_meta: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Snapshot imutável entregue ao planejamento/execução."""

    config_version: int
    config_hash: str
    persistent: dict[str, Any]
    run_options: dict[str, Any]
    consumo_mensal: dict[str, int] = field(default_factory=dict)
    competencia_resolvida: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "config_hash": self.config_hash,
            "persistent": copy.deepcopy(self.persistent),
            "run_options": copy.deepcopy(self.run_options),
            "consumo_mensal": dict(self.consumo_mensal),
            "competencia_resolvida": self.competencia_resolvida,
            "created_at": self.created_at,
        }


def _deep_freeze_dict(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _deep_freeze_dict(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, list):
        return [_deep_freeze_dict(v) for v in value]
    if isinstance(value, tuple):
        return [_deep_freeze_dict(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value
