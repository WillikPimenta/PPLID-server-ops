# -*- coding: utf-8 -*-
"""Projeção idempotente de `auditoria_falha_cadastro` → fatos EO."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.auditoria.models import AuditoriaAtividade, AuditoriaFalhaCadastro, AuditoriaMotivoFalha
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeIntranetProjection,
)
from apps.qualidade_operacional.services.case_key import (
    SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA,
    attach_case_key_to_falha_fields,
    delete_non_canonical_falhas,
    find_conflicting_falha_case,
    is_case_key_eligible,
    resolve_falha_conflict,
)
from apps.qualidade_operacional.services.dim_aliases import DimAliasIndex, load_dim_alias_index
from apps.qualidade_operacional.services.localidade_documento import (
    normalize_localidade_documento,
)
from apps.qualidade_operacional.services.normalize import (
    clean_text,
    fold_ascii_upper,
    is_processual_tipificacao,
    normalize_matricula,
    normalize_nome_key_punct_variant,
    normalize_tipo_conclusao,
    parse_date_br,
)
from apps.qualidade_operacional.services.intranet_metric_rules import (
    classify_source_record,
    is_falha_efetiva,
    is_sem_falha,
    normalize_origem_tratado,
    normalize_procedencia,
    normalize_tipo_falha_original,
    normalize_tipo_registro,
)
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.qualidade_operacional.services.source_config import (
    G_AUDITORIA_SOURCE_FILE,
    INTRANET_SOURCE_FILE,
    MAPPING_VERSION,
    SOURCE_MODE_HYBRID,
    SOURCE_MODE_INTRANET,
    SOURCE_MODE_LEGACY,
    effective_source_active,
    g_auditoria_projection_enabled,
    get_cutover_date,
    get_source_mode,
    intranet_source_enabled,
    should_project_date,
)
from apps.workforce.models import Agent, AgentHistory

logger = logging.getLogger(__name__)

TZ_SP = ZoneInfo("America/Sao_Paulo")
SEM_FALHA_FOLDED = "SEM FALHA"
STATUS_RETIRADA = AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA
DEFAULT_TIPO_ANALISE = "G Auditoria"
CLARO_CONFER_CLIENT_ID = 83
CLARO_CONFER_WORKFLOW_ID = 450
CLARO_CONFER_NAMES = {"CLARO", "CLARO CONFER"}


@lru_cache(maxsize=1)
def _missing_projection_db_fields() -> tuple[str, ...]:
    """Compatibilidade com migrations que executam antes dos campos novos."""
    try:
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor,
                    QualidadeIntranetProjection._meta.db_table,
                )
            }
    except Exception:  # noqa: BLE001 - a query normal deve seguir sem o fallback
        return ()
    return tuple(
        field.name
        for field in QualidadeIntranetProjection._meta.concrete_fields
        if field.column not in columns
    )


@dataclass
class SyncReport:
    processed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0
    errors: int = 0
    falhas_created: int = 0
    falhas_removed: int = 0
    falhas_replaced: int = 0
    falhas_skipped_duplicate: int = 0
    inconsistent: int = 0
    warnings: list[str] = field(default_factory=list)
    by_skip_reason: dict[str, int] = field(default_factory=dict)

    def bump_skip(self, reason: str) -> None:
        self.skipped += 1
        self.by_skip_reason[reason] = self.by_skip_reason.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "skipped": self.skipped,
            "errors": self.errors,
            "falhas_created": self.falhas_created,
            "falhas_removed": self.falhas_removed,
            "falhas_replaced": self.falhas_replaced,
            "falhas_skipped_duplicate": self.falhas_skipped_duplicate,
            "inconsistent": self.inconsistent,
            "by_skip_reason": dict(self.by_skip_reason),
            "warnings": self.warnings[:50],
            "source_mode": get_source_mode(),
            "intranet_enabled": intranet_source_enabled(),
            "cutover": get_cutover_date().isoformat() if get_cutover_date() else None,
            "mapping_version": MAPPING_VERSION,
        }


@dataclass
class LookupCaches:
    clientes: dict[str, list[int]] = field(default_factory=dict)
    workflows: dict[str, list[int]] = field(default_factory=dict)
    aliases: DimAliasIndex = field(default_factory=DimAliasIndex)
    motivos: dict[str, AuditoriaMotivoFalha] = field(default_factory=dict)
    agent_histories: dict[str, list[AgentHistory]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> LookupCaches:
        return cls()


@dataclass(frozen=True)
class AnalysisOriginPayload:
    """Dados canônicos ligados por ``analise_origem_id`` e fallback legado."""

    linked: bool
    brflow: dict[str, Any] = field(default_factory=dict)
    trilha: dict[str, Any] = field(default_factory=dict)
    contexto: dict[str, Any] = field(default_factory=dict)
    legacy: dict[str, Any] = field(default_factory=dict)
    protocolo_criado_em: datetime | None = None
    protocolo_analisado_em: datetime | None = None
    protocolo_concluido_em: datetime | None = None

    @staticmethod
    def _present(value: Any) -> bool:
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    def origin_value(self, *keys: str, context_first: bool = False) -> Any:
        payloads = (
            (self.contexto, self.brflow, self.trilha)
            if context_first
            else (self.brflow, self.trilha, self.contexto)
        )
        for payload in payloads:
            for key in keys:
                value = payload.get(key)
                if self._present(value):
                    return value
        return None

    def value(self, *keys: str, context_first: bool = False) -> Any:
        value = self.origin_value(*keys, context_first=context_first)
        if self._present(value):
            return value
        for key in keys:
            legacy_value = self.legacy.get(key)
            if self._present(legacy_value):
                return legacy_value
        return None


def analysis_origin_payload(source: AuditoriaFalhaCadastro) -> AnalysisOriginPayload:
    legacy = _brflow(source)
    origin = getattr(source, "analise_origem", None)
    if origin is None:
        return AnalysisOriginPayload(linked=False, legacy=legacy)

    def as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    return AnalysisOriginPayload(
        linked=True,
        brflow=as_dict(origin.brflow_parsed),
        trilha=as_dict(origin.trilha_parsed),
        contexto=as_dict(origin.contexto),
        legacy=legacy,
        protocolo_criado_em=origin.protocolo_criado_em,
        protocolo_analisado_em=origin.protocolo_analisado_em,
        protocolo_concluido_em=origin.protocolo_concluido_em,
    )


def _origin_date(
    payload: AnalysisOriginPayload,
    structured_value: datetime | None,
    *legacy_keys: str,
) -> date | None:
    return (
        to_sp_date(structured_value)
        or parse_flexible_date(payload.origin_value(*legacy_keys))
        or parse_flexible_date(next(
            (payload.legacy.get(key) for key in legacy_keys if payload.legacy.get(key)),
            None,
        ))
    )


def _origin_analysis_date(
    source: AuditoriaFalhaCadastro,
    payload: AnalysisOriginPayload,
) -> date | None:
    origem = clean_text(source.origem or source.tipo_registro).lower()
    if origem == AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO:
        origin = getattr(source, "analise_origem", None)
        raw = clean_text(getattr(origin, "brflow_raw", "")) if origin else ""
        match = re.search(
            r"Data\s+de\s+Cria.{0,8}:\s*(\d{2}/\d{2}/\d{4})",
            raw,
            flags=re.IGNORECASE,
        )
        # Em Contestação, não usar conclusão nem data da própria auditoria
        # como substituto silencioso da criação do protocolo de origem.
        return parse_flexible_date(match.group(1)) if match else None

    fila_contexto = clean_text(payload.value("fila_contexto", context_first=True)).lower()
    legacy_structured = (
        to_sp_date(source.data_analise)
        if origem == AuditoriaFalhaCadastro.ORIGEM_AUDITORIA
        and fila_contexto == "auditoria_compliance"
        else None
    )
    return _origin_date(
        payload,
        payload.protocolo_analisado_em or legacy_structured,
        "data_analise_origem",
        "data_analise_protocolo",
        "data_analise",
    )


def _intranet_analysis_date(source: AuditoriaFalhaCadastro) -> date | None:
    """Data operacional da Intranet; nunca usa datas do protocolo de origem."""
    return (
        to_sp_date(source.data_analise_intranet)
        or to_sp_date(source.analise_concluida_em)
    )


def _is_claro_confer(
    source: AuditoriaFalhaCadastro,
    payload: AnalysisOriginPayload,
) -> bool:
    origem = clean_text(source.origem or source.tipo_registro).lower()
    fila_contexto = clean_text(
        payload.value("fila_contexto", context_first=True)
    ).lower()
    is_compliance_flow = (
        origem == AuditoriaFalhaCadastro.ORIGEM_REINSPECAO
        or (
            origem == AuditoriaFalhaCadastro.ORIGEM_AUDITORIA
            and fila_contexto == "auditoria_compliance"
        )
    )
    cliente = normalize_nome_key(
        payload.origin_value("cliente") or source.cliente or payload.legacy.get("cliente")
    )
    return is_compliance_flow and cliente in CLARO_CONFER_NAMES


def normalize_nome_key(nome: str | None) -> str:
    return " ".join(fold_ascii_upper(clean_text(nome)).split())


def _homolog_trilha_enrichment(brflow: dict) -> dict[str, str]:
    """Extrai campos estruturados da trilha apenas em imports de homologação."""
    if not clean_text(brflow.get("homolog_import_source")):
        return {}
    trilha = clean_text(brflow.get("trilha_raw") or "")
    if not trilha:
        return {}
    import re

    out: dict[str, str] = {}
    if not clean_text(brflow.get("workflow")):
        match = re.search(
            r"N[ií]vel Hier[aá]rquico:\s*(.+?)\s+Status:",
            trilha,
            flags=re.IGNORECASE,
        )
        if match:
            out["workflow"] = match.group(1).strip()
    if not clean_text(brflow.get("data_analise")):
        match = re.search(
            r"Data de Conclus[aã]o:\s*(\d{2}/\d{2}/\d{4})",
            trilha,
            flags=re.IGNORECASE,
        )
        if match:
            out["data_analise"] = match.group(1)
    if not clean_text(brflow.get("cliente")) and "TIM" in fold_ascii_upper(trilha):
        out["cliente"] = "TIM Brasil"
    return out


def to_sp_date(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt.astimezone(TZ_SP).date()
    if isinstance(value, date):
        return value
    return None


def parse_flexible_date(value: object | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_sp_date(value)
    if isinstance(value, date):
        return value
    text = clean_text(value)
    if not text:
        return None
    parsed = parse_date_br(text)
    if parsed:
        return parsed
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            sample = text.replace("Z", "+00:00")
            if "%z" in fmt and len(sample) >= 19:
                return to_sp_date(datetime.strptime(sample[:25], fmt))
            return to_sp_date(datetime.strptime(sample[:19], fmt.replace("%z", "")))
        except ValueError:
            continue
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return to_sp_date(datetime.fromisoformat(text))
    except ValueError:
        return None


def resolve_audit_date(source: AuditoriaFalhaCadastro) -> date | None:
    """Data canônica do fato para contagem EO (campo ``data``).

    Usa somente a análise/conclusão da auditoria na Intranet. Datas do protocolo
    de origem, recepção e encerramento da atividade nunca substituem esse eixo.
    """
    origem = clean_text(source.origem or source.tipo_registro).lower()
    if origem == AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO:
        return to_sp_date(source.data_contestacao) or _intranet_analysis_date(source)
    return _intranet_analysis_date(source)


def _brflow(source: AuditoriaFalhaCadastro) -> dict:
    parsed = source.brflow_parsed
    return parsed if isinstance(parsed, dict) else {}


def eligibility_result(source: AuditoriaFalhaCadastro) -> tuple[bool, str]:
    """Elegível quando o resultado e a data canônica estão classificados."""
    cls = classify_source_record(source)
    if not cls.is_auditado:
        return False, cls.skip_reason or "resultado_qualidade_nao_classificado"
    if resolve_audit_date(source) is None:
        return False, "data_origem_ausente"
    return True, "ok"


def eligible_sources_qs() -> QuerySet[AuditoriaFalhaCadastro]:
    """Todas as fontes — classificação auditado/falha ocorre em sync_one."""
    return AuditoriaFalhaCadastro.objects.all()


def build_lookup_caches(
    sources: Iterable[AuditoriaFalhaCadastro],
) -> LookupCaches:
    cliente_names: set[str] = set()
    workflow_names: set[str] = set()
    motivos: set[str] = set()
    matriculas: set[str] = set()

    for source in sources:
        analysis = analysis_origin_payload(source)
        atividade = getattr(source, "atividade", None)
        for raw in (
            analysis.origin_value("cliente"),
            source.cliente,
            getattr(atividade, "cliente", None) if atividade else None,
            analysis.legacy.get("cliente"),
        ):
            key = normalize_nome_key(raw)
            if key:
                cliente_names.add(key)
        for raw in (
            analysis.origin_value("workflow"),
            getattr(atividade, "workflow", None) if atividade else None,
            analysis.legacy.get("workflow"),
        ):
            key = normalize_nome_key(raw)
            if key:
                workflow_names.add(key)
        motivo = clean_text(source.motivo_falha)
        if motivo:
            motivos.add(motivo)
        mat = normalize_matricula(source.usuario)
        if mat:
            matriculas.add(mat)

    caches = LookupCaches()
    caches.aliases = load_dim_alias_index()
    if cliente_names:
        for row in DimCliente.objects.all().only("id_cliente", "nome").iterator(chunk_size=2000):
            key = normalize_nome_key(row.nome)
            if key in cliente_names:
                caches.clientes.setdefault(key, []).append(int(row.id_cliente))
    if workflow_names:
        for row in DimWorkflow.objects.all().only("id_workflow", "nome").iterator(chunk_size=2000):
            key = normalize_nome_key(row.nome)
            if key in workflow_names:
                caches.workflows.setdefault(key, []).append(int(row.id_workflow))
    if motivos:
        for row in AuditoriaMotivoFalha.objects.filter(motivo__in=motivos):
            caches.motivos[clean_text(row.motivo)] = row
        # fallback case-insensitive via fold
        if len(caches.motivos) < len(motivos):
            folded_need = {normalize_nome_key(m): m for m in motivos if m not in caches.motivos}
            for row in AuditoriaMotivoFalha.objects.all().only(
                "motivo", "criticidade", "segmentos", "subsegmento"
            ).iterator(chunk_size=1000):
                key = normalize_nome_key(row.motivo)
                if key in folded_need:
                    caches.motivos[folded_need[key]] = row

    if matriculas:
        from django.db.models.functions import Lower

        agents = {
            (row.user_lan_id or "").strip().lower(): row
            for row in Agent.objects.annotate(lan=Lower("user_lan_id")).filter(
                lan__in=matriculas
            )
        }
        if agents:
            histories = (
                AgentHistory.objects.filter(agent_id__in=[a.pk for a in agents.values()])
                .select_related("agent", "leader")
                .order_by("agent_id", "-start_date")
            )
            mat_by_agent = {a.pk: mat for mat, a in agents.items()}
            for hist in histories:
                mat = mat_by_agent.get(hist.agent_id)
                if mat:
                    caches.agent_histories.setdefault(mat, []).append(hist)
    return caches


def _resolve_dim_id(
    names: list[str | None],
    index: dict[str, list[int]],
    *,
    aliases: dict[str, int],
    warnings: list[str],
    label: str,
) -> int | None:
    saw_name = False
    for raw in names:
        key = normalize_nome_key(raw)
        if not key:
            continue
        saw_name = True
        ids = index.get(key) or []
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            alias_id = aliases.get(key)
            if alias_id is not None:
                warnings.append(f"{label}_alias:{clean_text(raw)[:80]}→{alias_id}")
                return alias_id
            warnings.append(f"{label}_ambiguo:{clean_text(raw)[:80]}")
            return None

        alias_id = aliases.get(key)
        if alias_id is not None:
            warnings.append(f"{label}_alias:{clean_text(raw)[:80]}→{alias_id}")
            return alias_id

        punct_key = normalize_nome_key_punct_variant(key)
        if punct_key and punct_key != key:
            punct_ids = index.get(punct_key) or []
            if len(punct_ids) == 1:
                warnings.append(f"{label}_punct_fallback:{clean_text(raw)[:80]}→{punct_ids[0]}")
                return punct_ids[0]
            if len(punct_ids) > 1:
                alias_id = aliases.get(punct_key)
                if alias_id is not None:
                    warnings.append(f"{label}_alias:{clean_text(raw)[:80]}→{alias_id}")
                    return alias_id
                warnings.append(f"{label}_ambiguo_punct:{clean_text(raw)[:80]}")
                return None

        warnings.append(f"{label}_nao_encontrado:{clean_text(raw)[:80]}")
    if saw_name and not any(w.startswith(f"{label}_") for w in warnings):
        warnings.append(f"{label}_nao_encontrado")
    return None


def _resolve_agent_snapshot(
    caches: LookupCaches,
    matricula: str,
    on_date: date | None,
    warnings: list[str],
) -> tuple[str, str, str]:
    if not matricula or on_date is None:
        if matricula and on_date is None:
            warnings.append("agent_history_sem_data")
        elif matricula:
            warnings.append("agent_history_ausente")
        return "", "", ""
    histories = caches.agent_histories.get(matricula) or []
    if not histories:
        warnings.append("agent_history_ausente")
        return "", "", ""
    chosen: AgentHistory | None = None
    for hist in histories:
        start = hist.start_date
        end = hist.final_date
        if start and start > on_date:
            continue
        if end is not None and end < on_date:
            continue
        chosen = hist
        break
    if chosen is None:
        warnings.append("agent_history_fora_janela")
        return "", "", ""
    localidade = clean_text(chosen.location, max_len=128)
    lider = ""
    if chosen.leader_id:
        lider = clean_text(
            getattr(chosen.leader, "full_name", None)
            or getattr(chosen.leader, "user_lan_id", None),
            max_len=256,
        )
    agent = chosen.agent
    ativo = "Sim" if (agent and agent.active and chosen.active) else "Não"
    return localidade, lider, ativo


def _normalize_uf(raw: str | None, warnings: list[str]) -> str:
    return normalize_localidade_documento(raw, warnings=warnings)


def _tipo_conclusao_from_source(
    source: AuditoriaFalhaCadastro,
    analysis: AnalysisOriginPayload,
) -> str:
    raw = analysis.value("tipo_conclusao")
    if clean_text(raw):
        return normalize_tipo_conclusao(raw)
    return normalize_tipo_conclusao(source.tipo_falha)


def map_source_to_payloads(
    source: AuditoriaFalhaCadastro,
    caches: LookupCaches,
    *,
    synced_at: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str], str]:
    """Retorna (auditado_fields, falha_fields|None, warnings, fingerprint)."""
    warnings: list[str] = []
    metric = classify_source_record(source)
    warnings.extend(metric.warnings)
    analysis = analysis_origin_payload(source)
    brflow = dict(analysis.legacy)
    homolog_extra = _homolog_trilha_enrichment(brflow)
    if homolog_extra:
        brflow.update(homolog_extra)
        warnings.append("homolog_trilha_enrichment")
    atividade = getattr(source, "atividade", None)
    now = synced_at or timezone.now()

    data = resolve_audit_date(source)
    data_analise_intranet = _intranet_analysis_date(source)
    data_analise_origem = _origin_analysis_date(source, analysis)
    if data_analise_origem is None and homolog_extra.get("data_analise"):
        data_analise_origem = parse_flexible_date(homolog_extra["data_analise"])
    data_criacao_origem = _origin_date(
        analysis,
        analysis.protocolo_criado_em,
        "data_criacao_origem",
        "data_criacao_protocolo",
        "data_criacao",
    )
    data_conclusao_origem = _origin_date(
        analysis,
        analysis.protocolo_concluido_em,
        "data_conclusao_origem",
        "data_conclusao_protocolo",
        "data_conclusao",
    )
    data_recepcao_contestacao = to_sp_date(
        source.data_recepcao_contestacao or source.data_contestacao
    )
    data_encerramento_atividade_intranet = to_sp_date(
        source.data_encerramento_atividade_intranet
        or (getattr(atividade, "encerrado_em", None) if atividade else None)
    )
    raw_data_analise_origem = (
        analysis.value(
            "data_analise_origem",
            "data_analise_protocolo",
            "data_analise",
        )
        or homolog_extra.get("data_analise")
    )
    if raw_data_analise_origem not in (None, "") and data_analise_origem is None:
        warnings.append("data_analise_origem_invalida")
    origem_norm = clean_text(source.origem or source.tipo_registro).lower()
    if (
        data_analise_origem is None
        and origem_norm != AuditoriaFalhaCadastro.ORIGEM_REINSPECAO
    ):
        warnings.append("data_analise_ausente")

    if _is_claro_confer(source, analysis):
        id_cliente = CLARO_CONFER_CLIENT_ID
        id_workflow = CLARO_CONFER_WORKFLOW_ID
    else:
        id_cliente = _resolve_dim_id(
            [
                analysis.origin_value("cliente"),
                source.cliente,
                getattr(atividade, "cliente", None) if atividade else None,
                brflow.get("cliente"),
            ],
            caches.clientes,
            aliases=caches.aliases.clientes,
            warnings=warnings,
            label="cliente",
        )
        id_workflow = _resolve_dim_id(
            [
                analysis.origin_value("workflow"),
                getattr(atividade, "workflow", None) if atividade else None,
                brflow.get("workflow"),
            ],
            caches.workflows,
            aliases=caches.aliases.workflows,
            warnings=warnings,
            label="workflow",
        )

    protocolo = clean_text(source.protocolo)
    if len(protocolo) > 100:
        raise ValueError(f"protocolo_excede_100:{protocolo[:30]}")

    matricula = normalize_matricula(source.usuario)
    auditor = ""
    if source.auditor_ref_id:
        auditor = clean_text(
            getattr(source.auditor_ref, "user_lan_id", ""),
            max_len=64,
        )
    elif source.auditor_responsavel_id:
        auditor = clean_text(
            getattr(source.auditor_responsavel, "username", ""),
            max_len=64,
        )
    if not auditor:
        auditor = clean_text(source.auditor, max_len=64)

    tipo_analise = clean_text(source.modulo, max_len=128) or DEFAULT_TIPO_ANALISE
    tipo_conclusao = _tipo_conclusao_from_source(source, analysis)
    resultado_origem = clean_text(
        analysis.origin_value("resultado_analise", "resultado_contestado")
        or source.resultado_cliente
        or analysis.value("resultado_analise", "resultado_contestado"),
        max_len=128,
    )
    resultado_destino = clean_text(source.novo_resultado, max_len=128)

    meta_fields = {
        "tipo_registro": metric.tipo_registro,
        "origem_tratado": metric.origem,
        "tipo_falha_original": metric.tipo_falha_original,
        "procedencia": metric.procedencia,
    }
    localidade_documento = _normalize_uf(source.uf_documento, warnings)

    auditado_fields: dict[str, Any] = {
        "data": data,
        # Contrato das duas colunas do Detalhado: ``data`` é a auditoria
        # Intranet e ``data_analise`` é a análise do protocolo de origem.
        "data_analise": data_analise_origem,
        "data_analise_intranet": data_analise_intranet,
        "data_analise_origem": data_analise_origem,
        "data_criacao_origem": data_criacao_origem,
        "data_conclusao_origem": data_conclusao_origem,
        "data_recepcao_contestacao": data_recepcao_contestacao,
        "data_encerramento_atividade_intranet": data_encerramento_atividade_intranet,
        "id_cliente": id_cliente,
        "id_workflow": id_workflow,
        "tipo_analise": tipo_analise,
        "matricula": matricula,
        "matricula_auditor": auditor,
        "protocolo": protocolo,
        "cenario": clean_text(source.motivo_falha, max_len=512),
        "etapa": clean_text(source.etapa_falha, max_len=256),
        "status": clean_text(source.status_falha, max_len=128),
        "irregularidades_apontadas": clean_text(source.descricao_irregularidades),
        "cadastrado_anteriormente": "Intranet",
        "id_operations": None,
        "resultado_origem": resultado_origem,
        "resultado_destino": resultado_destino,
        "protocolo_destino": "",
        "tipo_conclusao": tipo_conclusao,
        "localidade_documento": localidade_documento,
        **meta_fields,
        "source_file": INTRANET_SOURCE_FILE,
        "admin_identity": f"intranet:{source.pk}:auditado",
        "imported_at": now,
    }

    falha_fields: dict[str, Any] | None = None
    if metric.is_falha:
        motivo = caches.motivos.get(clean_text(source.motivo_falha))
        if motivo is None and clean_text(source.motivo_falha):
            key = normalize_nome_key(source.motivo_falha)
            for m_key, m_row in caches.motivos.items():
                if normalize_nome_key(m_key) == key:
                    motivo = m_row
                    break
        if clean_text(source.motivo_falha) and motivo is None:
            warnings.append("motivo_desconhecido")

        tipo_falha = normalize_tipo_conclusao(source.tipo_falha)
        falha_matricula = matricula
        if is_processual_tipificacao(tipo_falha):
            falha_matricula = ""
            localidade, lider, agente_ativo = "", "", ""
        else:
            localidade, lider, agente_ativo = _resolve_agent_snapshot(
                caches, matricula, data, warnings
            )
        modulo = clean_text(source.modulo, max_len=128)
        des_problemas = clean_text(source.descricao_irregularidades) or clean_text(
            source.motivo_falha
        )
        resultado_analise = resultado_origem
        qualidade_imagem = clean_text(
            analysis.origin_value("qualidade_imagem")
            or source.qualidade_imagem
            or analysis.value("qualidade_imagem"),
            max_len=128,
        )
        categoria_falha = clean_text(motivo.criticidade, max_len=128) if motivo else ""
        if metric.tipo_registro == "reinspecao":
            categoria_falha = "Procedimento"

        falha_fields = {
            "nome_origem": "Intranet",
            "protocolo": protocolo,
            "id_cliente": id_cliente,
            "id_workflow": id_workflow,
            "id_operations": None,
            "tipo_analise": tipo_analise,
            "modulo": modulo,
            "cenario": clean_text(source.motivo_falha),
            "data": data,
            "usuario_auditor": auditor,
            "matricula": falha_matricula,
            "data_analise": data_analise_origem,
            "data_analise_intranet": data_analise_intranet,
            "data_analise_origem": data_analise_origem,
            "data_criacao_origem": data_criacao_origem,
            "data_conclusao_origem": data_conclusao_origem,
            "data_recepcao_contestacao": data_recepcao_contestacao,
            "data_encerramento_atividade_intranet": data_encerramento_atividade_intranet,
            "etapa": clean_text(source.etapa_falha, max_len=256),
            "tipo_falha": tipo_falha,
            "tipo_falha_oficial": clean_text(source.tipo_falha, max_len=128),
            "uf": localidade_documento,
            "localidade_documento": localidade_documento,
            "tipo_documento": clean_text(source.tipo_documento, max_len=128),
            "nivel_dificuldade": clean_text(source.nivel_dificuldade, max_len=128),
            "des_problemas": des_problemas,
            "novo_resultado": clean_text(source.novo_resultado, max_len=128),
            "resultado_analise": resultado_analise,
            "qualidade_imagem": qualidade_imagem,
            "origem_analise": "Intranet",
            "tipo_modulo": modulo,
            "tipo_modulo_2": modulo,
            "categoria_falha": categoria_falha,
            "segmento": clean_text(motivo.segmentos, max_len=128) if motivo else "",
            "sub_segmento": clean_text(motivo.subsegmento, max_len=128) if motivo else "",
            "condicao_metrica": clean_text(source.status_falha, max_len=128),
            "localidade": localidade,
            "lider": lider,
            "agente_ativo": agente_ativo,
            **meta_fields,
            "source_file": INTRANET_SOURCE_FILE,
            "admin_identity": f"intranet:{source.pk}:falha",
            "imported_at": now,
        }

    fingerprint = _fingerprint(auditado_fields, falha_fields)
    return auditado_fields, falha_fields, warnings, fingerprint


def _fingerprint(auditado: dict, falha: dict | None) -> str:
    payload = {
        "a": {k: _jsonable(v) for k, v in auditado.items() if k != "imported_at"},
        "f": (
            {k: _jsonable(v) for k, v in falha.items() if k != "imported_at"}
            if falha
            else None
        ),
        "v": MAPPING_VERSION,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _apply_fields(instance, fields: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for key, value in fields.items():
        if getattr(instance, key) != value:
            setattr(instance, key, value)
            changed.append(key)
    return changed


def _delete_projection_facts(projection: QualidadeIntranetProjection) -> tuple[int, int]:
    removed_a = removed_f = 0
    if projection.falha_id:
        QualidadeFalha.all_objects.filter(pk=projection.falha_id).delete()
        removed_f = 1
        projection.falha = None
    if projection.auditado_id:
        QualidadeAuditado.all_objects.filter(pk=projection.auditado_id).delete()
        removed_a = 1
        projection.auditado = None
    return removed_a, removed_f


@transaction.atomic
def sync_one(
    source: AuditoriaFalhaCadastro,
    *,
    caches: LookupCaches | None = None,
    dry_run: bool = False,
    force: bool = False,
    report: SyncReport | None = None,
    bump_cache: bool = True,
) -> QualidadeIntranetProjection | None:
    report = report or SyncReport()
    report.processed += 1
    if caches is None:
        caches = build_lookup_caches([source])

    ok, reason = eligibility_result(source)
    if reason == "inconsistent":
        report.inconsistent += 1
        if "inconsistent" not in report.warnings:
            report.warnings.append("registros_inconsistentes_encontrados")

    projection_qs = (
        QualidadeIntranetProjection.objects.select_for_update()
        .filter(source_id=source.pk)
    )
    missing_projection_fields = _missing_projection_db_fields()
    if missing_projection_fields:
        projection_qs = projection_qs.defer(*missing_projection_fields)
    projection = projection_qs.first()

    if not ok:
        report.bump_skip(reason)
        if projection and not dry_run:
            _delete_projection_facts(projection)
            projection.delete()
            report.removed += 1
            if bump_cache:
                bump_quality_cache_version()
        return None

    data = resolve_audit_date(source)
    if (
        not force
        and effective_source_active()
        and not should_project_date(data)
    ):
        report.bump_skip("fora_do_corte")
        if projection and not dry_run:
            _delete_projection_facts(projection)
            projection.delete()
            report.removed += 1
            if bump_cache:
                bump_quality_cache_version()
        return None

    if not force and not effective_source_active():
        report.bump_skip("fonte_desabilitada")
        return projection

    try:
        auditado_fields, falha_fields, warnings, fingerprint = map_source_to_payloads(
            source, caches
        )
        from apps.qualidade_operacional.services.record_admin import apply_state_to_fields

        auditado_fields = apply_state_to_fields(auditado_fields, "auditado")
        if falha_fields is not None:
            falha_fields = apply_state_to_fields(falha_fields, "falha")
    except ValueError as exc:
        report.errors += 1
        report.warnings.append(str(exc)[:200])
        if projection and not dry_run:
            projection.sync_status = QualidadeIntranetProjection.STATUS_ERROR
            projection.sync_error = str(exc)[:1000]
            projection.synced_at = timezone.now()
            projection.warnings = []
            projection.save(
                update_fields=["sync_status", "sync_error", "synced_at", "warnings"]
            )
        return projection

    if (
        projection
        and not force
        and projection.source_fingerprint == fingerprint
        and projection.mapping_version == MAPPING_VERSION
        and projection.sync_status == QualidadeIntranetProjection.STATUS_OK
    ):
        report.unchanged += 1
        return projection

    duplicate_falha_conflict = None
    replace_existing_falha = False
    if falha_fields is not None:
        falha_fields = attach_case_key_to_falha_fields(falha_fields)
        if is_case_key_eligible(
            falha_fields.get("protocolo"),
            falha_fields.get("matricula"),
        ):
            duplicate_falha_conflict = find_conflicting_falha_case(
                falha_fields.get("protocolo"),
                falha_fields.get("matricula"),
                exclude_falha_id=projection.falha_id if projection else None,
            )
            if duplicate_falha_conflict:
                action = resolve_falha_conflict(falha_fields, duplicate_falha_conflict)
                if action == "replace":
                    replace_existing_falha = True
                else:
                    replace_existing_falha = False

    if dry_run:
        if projection is None:
            report.created += 1
        else:
            report.updated += 1
        if falha_fields and duplicate_falha_conflict and not replace_existing_falha:
            report.falhas_skipped_duplicate += 1
            report.bump_skip(SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA)
            if SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA not in report.warnings:
                report.warnings.append(SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA)
        elif falha_fields and replace_existing_falha:
            report.falhas_replaced += 1
        elif falha_fields and (projection is None or not projection.falha_id):
            report.falhas_created += 1
        if projection and projection.falha_id and (
            falha_fields is None or (duplicate_falha_conflict and not replace_existing_falha)
        ):
            report.falhas_removed += 1
        return projection

    created = projection is None
    if created:
        projection = QualidadeIntranetProjection(source=source)
        report.created += 1
    else:
        report.updated += 1

    if duplicate_falha_conflict and replace_existing_falha:
        conflict_id = duplicate_falha_conflict.pk
        if projection.falha_id != conflict_id:
            delete_non_canonical_falhas([conflict_id])
            report.falhas_replaced += 1
        duplicate_falha_conflict = None
    elif duplicate_falha_conflict:
        report.falhas_skipped_duplicate += 1
        report.bump_skip(SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA)
        if SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA not in report.warnings:
            report.warnings.append(SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA)
        falha_fields = None

    # Auditado
    if projection.auditado_id:
        auditado = QualidadeAuditado.all_objects.filter(pk=projection.auditado_id).first()
        if auditado is None:
            auditado = QualidadeAuditado(**auditado_fields)
            auditado.save()
            projection.auditado = auditado
        else:
            _apply_fields(auditado, auditado_fields)
            auditado.save()
    else:
        auditado = QualidadeAuditado(**auditado_fields)
        auditado.save()
        projection.auditado = auditado

    # Falha
    if falha_fields is None:
        if projection.falha_id:
            QualidadeFalha.all_objects.filter(pk=projection.falha_id).delete()
            projection.falha = None
            report.falhas_removed += 1
    else:
        if projection.falha_id:
            falha = QualidadeFalha.all_objects.filter(pk=projection.falha_id).first()
            if falha is None:
                falha = QualidadeFalha(**falha_fields)
                falha.save()
                projection.falha = falha
                report.falhas_created += 1
            else:
                _apply_fields(falha, falha_fields)
                falha.save()
        else:
            falha = QualidadeFalha(**falha_fields)
            falha.save()
            projection.falha = falha
            report.falhas_created += 1

    projection.mapping_version = MAPPING_VERSION
    projection.source_updated_at = source.updated_at
    projection.source_fingerprint = fingerprint
    projection.synced_at = timezone.now()
    if duplicate_falha_conflict and not replace_existing_falha:
        projection.sync_status = QualidadeIntranetProjection.STATUS_SKIPPED
        projection.sync_error = (
            f"{SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA}: "
            f"falha_id={duplicate_falha_conflict.pk}"
        )[:1000]
    else:
        projection.sync_status = QualidadeIntranetProjection.STATUS_OK
        projection.sync_error = ""
    projection.warnings = warnings
    projection.save()

    if g_auditoria_projection_enabled():
        # O Parquet fornece auditados; a falha continua sendo criada somente
        # acima, a partir da classificação canônica da Intranet.
        from apps.qualidade_operacional.services.g_auditoria import (
            reconcile_failures,
            reconcile_intranet_auditados,
        )

        reconcile_intranet_auditados(
            intranet_projection_ids=[projection.pk],
        )
        if projection.falha_id:
            reconcile_failures(
                failure_ids=[projection.falha_id],
                bump_cache=False,
            )

    if bump_cache:
        bump_quality_cache_version()
    return projection


@transaction.atomic
def remove_projection_for_source_id(source_id: int, *, bump_cache: bool = True) -> bool:
    projection = (
        QualidadeIntranetProjection.objects.select_for_update()
        .filter(source_id=source_id)
        .first()
    )
    if projection is None:
        return False
    _delete_projection_facts(projection)
    projection.delete()
    if bump_cache:
        bump_quality_cache_version()
    return True


def sync_batch(
    sources: list[AuditoriaFalhaCadastro],
    *,
    dry_run: bool = False,
    force: bool = False,
    report: SyncReport | None = None,
    bump_cache: bool = False,
) -> SyncReport:
    report = report or SyncReport()
    if not sources:
        return report
    caches = build_lookup_caches(sources)
    changed = False
    for source in sources:
        before = report.created + report.updated + report.removed + report.falhas_removed
        sync_one(
            source,
            caches=caches,
            dry_run=dry_run,
            force=force,
            report=report,
            bump_cache=False,
        )
        after = report.created + report.updated + report.removed + report.falhas_removed
        if after > before:
            changed = True
    if changed and bump_cache and not dry_run:
        bump_quality_cache_version()
    return report


def sync_queryset(
    qs: QuerySet[AuditoriaFalhaCadastro],
    *,
    batch_size: int = 200,
    dry_run: bool = False,
    force: bool = False,
    from_id: int | None = None,
    bump_cache: bool = True,
) -> SyncReport:
    report = SyncReport()
    if from_id is not None:
        qs = qs.filter(pk__gte=from_id)
    qs = qs.select_related(
        "atividade", "auditor_ref", "auditor_responsavel", "analise_origem"
    ).order_by("pk")
    batch: list[AuditoriaFalhaCadastro] = []
    for source in qs.iterator(chunk_size=batch_size):
        batch.append(source)
        if len(batch) >= batch_size:
            sync_batch(batch, dry_run=dry_run, force=force, report=report, bump_cache=False)
            batch = []
    if batch:
        sync_batch(batch, dry_run=dry_run, force=force, report=report, bump_cache=False)
    if not dry_run and bump_cache:
        bump_quality_cache_version()
    return report


def reconcile_orphans(*, dry_run: bool = False) -> SyncReport:
    """Remove projeções cuja origem sumiu ou deixou de ser elegível."""
    report = SyncReport()
    qs = QualidadeIntranetProjection.objects.select_related(
        "source", "source__analise_origem"
    ).order_by("pk")
    for projection in qs.iterator(chunk_size=200):
        source = projection.source
        if source is None:
            report.processed += 1
            if not dry_run:
                _delete_projection_facts(projection)
                projection.delete()
            report.removed += 1
            continue
        ok, reason = eligibility_result(source)
        data = resolve_audit_date(source)
        should = ok and (should_project_date(data) or not effective_source_active())
        # Em modo ativo, fora do corte ou inelegível → remove
        if effective_source_active() and (not ok or not should_project_date(data)):
            report.processed += 1
            report.bump_skip(reason if not ok else "fora_do_corte")
            if not dry_run:
                _delete_projection_facts(projection)
                projection.delete()
            report.removed += 1
            continue
        del should
    if not dry_run and report.removed:
        bump_quality_cache_version()
    return report


def apply_source_mode_filter(qs: QuerySet, *, date_field: str = "data") -> QuerySet:
    """Aplica corte temporal TSV × Intranet nos querysets de fatos EO."""
    mode = get_source_mode()
    enabled = intranet_source_enabled()
    cutover = get_cutover_date()
    intranet_q = Q(source_file=INTRANET_SOURCE_FILE)
    g_auditoria_q = Q(source_file=G_AUDITORIA_SOURCE_FILE)
    legacy_q = ~(intranet_q | g_auditoria_q)
    is_auditado = qs.model is QualidadeAuditado

    if is_auditado and g_auditoria_projection_enabled():
        qs = qs.exclude(
            intranet_projection__g_auditoria_match_status="matched"
        )
        g_clause = g_auditoria_q & Q(g_auditoria_projection__is_active=True)
    else:
        qs = qs.exclude(g_auditoria_q)
        g_clause = Q(pk__in=[])

    if not enabled or mode == SOURCE_MODE_LEGACY:
        return qs.filter(legacy_q | g_clause)

    if mode == SOURCE_MODE_INTRANET:
        return qs.filter(intranet_q | g_clause)

    # hybrid
    if cutover is None:
        return qs.filter(legacy_q | g_clause)

    return qs.filter(
        (intranet_q & Q(**{f"{date_field}__gte": cutover}))
        | (
            legacy_q
            & (
                Q(**{f"{date_field}__lt": cutover})
                | Q(**{f"{date_field}__isnull": True})
            )
        )
        | g_clause
    )


def _classify_cascade_bucket(source: AuditoriaFalhaCadastro) -> str:
    """Bucket exclusivo por registro fonte."""
    metric = classify_source_record(source)
    if not metric.is_auditado:
        return "resultado_nao_classificado_rows"

    data = resolve_audit_date(source)
    if data is None:
        return "missing_data_origem_rows"
    if effective_source_active() and not should_project_date(data):
        return "outside_cutover_rows"

    return "eligible_source_rows"


def build_cascade_reconciliation(*, prefix: str = "") -> dict[str, Any]:
    """Reconcilia resultado canônico, data por origem e fatos projetados."""
    qs = AuditoriaFalhaCadastro.objects.all().select_related(
        "atividade", "auditor_responsavel", "analise_origem"
    )
    if prefix:
        qs = qs.filter(protocolo__startswith=prefix)

    buckets: dict[str, int] = {
        "total_sources": 0,
        "status_nao_ativo_rows": 0,
        "status_retirada_rows": 0,
        "status_mantida_rows": 0,
        "resultado_nao_classificado_rows": 0,
        "missing_data_origem_rows": 0,
        "outside_cutover_rows": 0,
        "eligible_source_rows": 0,
        "classified_auditados": 0,
        "expected_auditados": 0,
        "expected_falhas": 0,
        "expected_nao_falhas": 0,
        "reinspecao_procedente_rows": 0,
        "reinspecao_improcedente_rows": 0,
        "reinspecao_procedencia_desconhecida": 0,
        "tipo_falha_vazio_rows": 0,
        "missing_data_auditoria": 0,
        "missing_data_analise": 0,
    }
    eligible_ids: list[int] = []

    for source in qs.iterator(chunk_size=500):
        buckets["total_sources"] += 1
        metric = classify_source_record(source)
        bucket = _classify_cascade_bucket(source)
        buckets[bucket] = buckets.get(bucket, 0) + 1

        status_falha = clean_text(source.status_falha).lower()
        if status_falha == AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA:
            buckets["status_retirada_rows"] += 1
        elif status_falha == AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA:
            buckets["status_mantida_rows"] += 1
        elif status_falha not in {"ativa", "ativo"}:
            buckets["status_nao_ativo_rows"] += 1

        if resolve_audit_date(source) is None:
            buckets["missing_data_auditoria"] += 1
        if _intranet_analysis_date(source) is None:
            buckets["missing_data_analise"] += 1

        if metric.is_auditado:
            buckets["classified_auditados"] += 1
            if fold_ascii_upper(metric.tipo_falha_original) == "REINSPECAO":
                if metric.procedencia == "Procedente":
                    buckets["reinspecao_procedente_rows"] += 1
                elif metric.procedencia == "Improcedente":
                    buckets["reinspecao_improcedente_rows"] += 1
                else:
                    buckets["reinspecao_procedencia_desconhecida"] += 1
            if not metric.tipo_falha_original:
                buckets["tipo_falha_vazio_rows"] += 1

        if bucket == "eligible_source_rows":
            eligible_ids.append(source.pk)
            buckets["expected_auditados"] += 1
            if metric.is_falha:
                buckets["expected_falhas"] += 1
            else:
                buckets["expected_nao_falhas"] += 1

    eligible_qs = AuditoriaFalhaCadastro.objects.filter(pk__in=eligible_ids)
    try:
        projection_qs = QualidadeIntranetProjection.objects.filter(source_id__in=eligible_ids)
        projected_rows = projection_qs.count()
        projected_falhas = projection_qs.filter(falha_id__isnull=False).count()
        sync_errors_pending = projection_qs.filter(
            sync_status=QualidadeIntranetProjection.STATUS_ERROR
        ).count()
        unmapped = projection_qs.filter(
            Q(warnings__icontains="cliente_nao_encontrado")
            | Q(warnings__icontains="workflow_nao_encontrado")
            | Q(warnings__icontains="cliente_ambiguo")
            | Q(warnings__icontains="workflow_ambiguo")
        ).count()
    except Exception:  # noqa: BLE001 — ProgrammingError se migration ausente
        projected_rows = projected_falhas = sync_errors_pending = unmapped = 0

    auditados_generated = QualidadeAuditado.objects.filter(
        source_file=INTRANET_SOURCE_FILE,
        protocolo__in=eligible_qs.values("protocolo"),
    ).count()
    if prefix:
        auditados_generated = QualidadeAuditado.objects.filter(
            source_file=INTRANET_SOURCE_FILE,
            protocolo__startswith=prefix,
        ).count()
        falhas_generated = QualidadeFalha.objects.filter(
            source_file=INTRANET_SOURCE_FILE,
            protocolo__startswith=prefix,
        ).count()
    else:
        falhas_generated = QualidadeFalha.objects.filter(
            source_file=INTRANET_SOURCE_FILE,
            protocolo__in=eligible_qs.values("protocolo"),
        ).count()

    input_sum = (
        buckets["resultado_nao_classificado_rows"]
        + buckets["missing_data_origem_rows"]
        + buckets["outside_cutover_rows"]
        + buckets["eligible_source_rows"]
    )
    unreconciled_difference = buckets["total_sources"] - input_sum

    return {
        **buckets,
        "total_concluido": buckets["total_sources"],
        "ignored_by_tipo_registro": 0,
        "ignored_by_origem": 0,
        "inconsistent_source_rows": 0,
        "projected_rows": projected_rows,
        "projected_falhas": projected_falhas,
        "sync_errors_pending": sync_errors_pending,
        "unmapped_cliente_workflow": unmapped,
        "auditados_generated": auditados_generated,
        "falhas_generated": falhas_generated,
        "unreconciled_difference": unreconciled_difference,
    }


def source_meta_payload() -> dict[str, Any]:
    from django.db.models import Count, Max
    from django.db.utils import ProgrammingError

    cutover = get_cutover_date()
    cascade = build_cascade_reconciliation()
    try:
        projections = QualidadeIntranetProjection.objects.all()
        agg = projections.aggregate(
            total=Count("id"),
            with_falha=Count("falha_id"),
            last_sync=Max("synced_at"),
            errors=Count(
                "id", filter=Q(sync_status=QualidadeIntranetProjection.STATUS_ERROR)
            ),
        )
        unmapped = projections.filter(
            Q(warnings__icontains="cliente_nao_encontrado")
            | Q(warnings__icontains="workflow_nao_encontrado")
            | Q(warnings__icontains="cliente_ambiguo")
            | Q(warnings__icontains="workflow_ambiguo")
        ).count()
    except ProgrammingError:
        agg = {"total": 0, "with_falha": 0, "last_sync": None, "errors": 0}
        unmapped = cascade.get("unmapped_cliente_workflow", 0)

    last_sync = agg["last_sync"]
    lag_seconds = None
    if last_sync:
        lag_seconds = max(0, int((timezone.now() - last_sync).total_seconds()))

    return {
        "source_mode": get_source_mode(),
        "intranet_enabled": intranet_source_enabled(),
        "cutover_date": cutover.isoformat() if cutover else None,
        "last_sync_at": last_sync.isoformat() if last_sync else None,
        "sync_lag_seconds": lag_seconds,
        "intranet_projected_rows": agg["total"] or 0,
        "intranet_projected_falhas": agg["with_falha"] or 0,
        "eligible_source_rows": cascade["eligible_source_rows"],
        "expected_auditados": cascade.get("expected_auditados", 0),
        "expected_falhas": cascade.get("expected_falhas", 0),
        "outside_cutover_rows": cascade["outside_cutover_rows"],
        "status_nao_ativo_rows": cascade.get("status_nao_ativo_rows", 0),
        "missing_data_analise": cascade.get("missing_data_analise", 0),
        "projected_rows": cascade["projected_rows"],
        "projected_falhas": cascade["projected_falhas"],
        "sync_errors_pending": agg["errors"] or cascade["sync_errors_pending"],
        "unmapped_cliente_workflow": unmapped,
        "unreconciled_difference": cascade["unreconciled_difference"],
        "mapping_version": MAPPING_VERSION,
        "official_metric_cutover": "2026-07-01",
        "impact_weight_cutover": "2026-08-01",
    }


def build_reconciliation_report() -> dict[str, Any]:
    """Relatório somente leitura TSV × Intranet (pré-ativação do corte)."""
    from django.db.models import Count, Max, Min
    from django.db.models.functions import TruncMonth

    tsv_aud = QualidadeAuditado.objects.exclude(source_file=INTRANET_SOURCE_FILE)
    tsv_fal = QualidadeFalha.objects.exclude(source_file=INTRANET_SOURCE_FILE)
    intra = AuditoriaFalhaCadastro.objects.filter(
        analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
    )

    def _range(qs, field: str) -> dict:
        agg = qs.aggregate(mn=Min(field), mx=Max(field))
        mn, mx = agg["mn"], agg["mx"]
        return {
            "min": mn.isoformat() if mn else None,
            "max": mx.isoformat() if mx else None,
        }

    tsv_by_month = list(
        tsv_aud.exclude(data__isnull=True)
        .annotate(month=TruncMonth("data"))
        .values("month")
        .annotate(rows=Count("id"), protocols=Count("protocolo", distinct=True))
        .order_by("month")[:36]
    )
    for row in tsv_by_month:
        month = row.get("month")
        if month is not None:
            row["month"] = month.date().isoformat() if hasattr(month, "date") else month.isoformat()

    tipo_falha_counts = list(
        intra.values("tipo_falha").annotate(c=Count("id")).order_by("-c")[:30]
    )
    status_counts = list(
        intra.values("status_falha").annotate(c=Count("id")).order_by("-c")
    )
    sem_protocolo = intra.filter(Q(protocolo="") | Q(protocolo__isnull=True)).count()
    retiradas = intra.filter(status_falha=STATUS_RETIRADA).count()

    return {
        "generated_at": timezone.now().isoformat(),
        "tsv_auditados": {
            "count": tsv_aud.count(),
            "date_range": _range(tsv_aud, "data"),
            "by_month_sample": tsv_by_month,
        },
        "tsv_falhas": {
            "count": tsv_fal.count(),
            "date_range": _range(tsv_fal, "data"),
        },
        "intranet_tratados": {
            "count": intra.count(),
            "sem_protocolo": sem_protocolo,
            "retiradas": retiradas,
            "tipo_falha": tipo_falha_counts,
            "status_falha": status_counts,
            "created_range": _range(intra, "created_at"),
            "analise_concluida_range": _range(intra, "analise_concluida_em"),
        },
        "config": source_meta_payload(),
        "cascade": build_cascade_reconciliation(),
        "note": (
            "Não escolher corte apenas pelo menor created_at da Intranet. "
            "Compare sobreposição diária/mensal e volumes por fonte em homologação."
        ),
    }
