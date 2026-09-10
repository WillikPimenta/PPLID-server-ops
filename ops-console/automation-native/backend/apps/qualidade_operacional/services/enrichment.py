# -*- coding: utf-8 -*-
"""Lookups em batch: DimCliente, DimWorkflow, Agent."""
from __future__ import annotations

from django.db.models.functions import Lower

from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.services.criticidade import (
    CLIENTE_CLARO_FORMALIZACAO_ID,
    CLIENTE_CLARO_FORMALIZACAO_NOME,
    WORKFLOW_CLARO_CONFER_ID,
    WORKFLOW_CLARO_CONFER_NOME,
    criticidade_label,
)
from apps.qualidade_operacional.services.normalize import (
    is_processual_tipificacao,
    nivel_dificuldade_efetivo,
)
from apps.workforce.models import Agent


def build_lider_responsavel_lookup(
    rows,
    *,
    date_field: str = "data",
) -> dict[int, str]:
    """Líder canônico por fato (AgentHistory na data efetiva), em lote."""
    lookup = build_responsavel_lookup(rows, date_field=date_field)
    return {
        pk: (meta.get("responsavel_nome") or "")
        for pk, meta in lookup.items()
        if meta.get("responsabilidade") == "lider"
    }


def _responsavel_payload(resolved: dict | None) -> dict[str, str | None]:
    if not resolved:
        return {
            "responsavel_nome": None,
            "responsavel_matricula": None,
            "responsabilidade": None,
            "regra_responsabilidade": None,
        }
    responsabilidade = (resolved.get("responsabilidade") or "lider").strip().lower()
    nome = (resolved.get("lider") or "").strip()
    matricula = (
        resolved.get("facilitador_matricula")
        or resolved.get("lider_matricula")
        or ""
    ).strip().lower()
    regra = (resolved.get("regra_responsabilidade") or "").strip().lower() or None
    if responsabilidade != "facilitador":
        regra = None
    return {
        "responsavel_nome": nome or None,
        "responsavel_matricula": matricula or None,
        "responsabilidade": responsabilidade,
        "regra_responsabilidade": regra,
    }


def build_responsavel_lookup(
    rows,
    *,
    date_field: str = "data",
) -> dict[int, dict[str, str | None]]:
    """Responsável HC por fato: facilitador na janela, senão líder do AgentHistory."""
    from apps.qualidade_operacional.services.workforce_scope import (
        build_leader_history_index,
        build_responsibility_index,
        responsibility_for_date,
        resolve_leader_for_date,
    )

    dates = [
        getattr(row, date_field)
        for row in rows
        if getattr(row, date_field, None) is not None
    ]
    if not dates:
        return {}
    period_start = min(dates)
    period_end = max(dates)
    leader_index = build_leader_history_index(period_start, period_end)
    responsibility_index = build_responsibility_index()
    lookup: dict[int, dict[str, str | None]] = {}
    for row in rows:
        on_date = getattr(row, date_field, None)
        matricula = (getattr(row, "matricula", None) or "").strip().lower()
        if not on_date or not matricula:
            continue
        leader_fallback = resolve_leader_for_date(
            matricula,
            on_date,
            histories_by_mat=leader_index,
            period_start=period_start,
            period_end=period_end,
        ) or {
            "matricula": matricula,
            "lider": "",
            "lider_matricula": "",
            "responsabilidade": "lider",
        }
        resolved = responsibility_for_date(
            responsibility_index,
            matricula,
            on_date,
            leader_fallback,
        )
        lookup[row.pk] = _responsavel_payload(resolved)
    return lookup


def responsavel_tipo_label(responsabilidade: str | None) -> str:
    if (responsabilidade or "").strip().lower() == "facilitador":
        return "Facilitador"
    if responsabilidade:
        return "Líder"
    return ""


def merge_responsavel_fields(payload: dict, responsavel: dict[str, str | None] | None) -> dict:
    meta = responsavel or _responsavel_payload(None)
    payload.update(meta)
    nome = meta.get("responsavel_nome")
    if nome and meta.get("responsabilidade") == "lider":
        payload["lider_responsavel"] = nome
    return payload


def dias_entre_auditoria_e_origem(data, data_analise, *, fallback=None) -> int | None:
    """Dias entre auditoria e análise origem (auditoria − origem)."""
    if data is not None and data_analise is not None:
        return (data - data_analise).days
    return fallback


def build_dim_lookups(
    cliente_ids: set[int],
    workflow_ids: set[int],
    matriculas: set[str],
) -> tuple[dict[int, str], dict[int, str], dict[str, str]]:
    clientes: dict[int, str] = {}
    if cliente_ids:
        for row in DimCliente.objects.filter(id_cliente__in=cliente_ids).values(
            "id_cliente", "nome"
        ):
            clientes[int(row["id_cliente"])] = (row["nome"] or "").strip()
        if CLIENTE_CLARO_FORMALIZACAO_ID in cliente_ids:
            clientes[CLIENTE_CLARO_FORMALIZACAO_ID] = (
                CLIENTE_CLARO_FORMALIZACAO_NOME
            )

    workflows: dict[int, str] = {}
    if workflow_ids:
        for row in DimWorkflow.objects.filter(id_workflow__in=workflow_ids).values(
            "id_workflow", "nome"
        ):
            workflows[int(row["id_workflow"])] = (row["nome"] or "").strip()
        if WORKFLOW_CLARO_CONFER_ID in workflow_ids:
            workflows[WORKFLOW_CLARO_CONFER_ID] = WORKFLOW_CLARO_CONFER_NOME

    agents: dict[str, str] = {}
    mats = {m.strip().lower() for m in matriculas if m and m.strip()}
    if mats:
        for row in (
            Agent.objects.annotate(lan_lower=Lower("user_lan_id"))
            .filter(lan_lower__in=mats)
            .values("user_lan_id", "full_name")
        ):
            key = (row["user_lan_id"] or "").strip().lower()
            if key:
                agents[key] = (row["full_name"] or "").strip()

    return clientes, workflows, agents


def merge_observacao_auditor(falha_obs: str, atividade_obs: str) -> str:
    """Combina observações da falha e da atividade sem duplicar."""
    parts: list[str] = []
    for value in (falha_obs, atividade_obs):
        text = (value or "").strip()
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)


def build_observacao_auditor_lookup(rows) -> dict[int, str]:
    """Mapa falha_pk -> observação do auditor (somente linhas Intranet)."""
    from apps.auditoria.models import AuditoriaFalhaCadastro
    from apps.qualidade_operacional.models import QualidadeIntranetProjection
    from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE

    intranet_rows = [r for r in rows if (getattr(r, "source_file", "") or "") == INTRANET_SOURCE_FILE]
    if not intranet_rows:
        return {}

    intranet_ids = [r.pk for r in intranet_rows]
    projections = list(
        QualidadeIntranetProjection.objects.filter(falha_id__in=intranet_ids).values(
            "falha_id", "source_id"
        )
    )
    source_ids = {int(p["source_id"]) for p in projections if p.get("source_id")}
    if not source_ids:
        return {}

    sources = {
        item.pk: item
        for item in AuditoriaFalhaCadastro.objects.filter(pk__in=source_ids).select_related(
            "atividade"
        )
    }

    lookup: dict[int, str] = {}
    for proj in projections:
        falha_id = int(proj["falha_id"])
        source_id = proj.get("source_id")
        if not source_id:
            continue
        source = sources.get(int(source_id))
        if source is None:
            continue
        atividade_obs = ""
        if source.atividade_id:
            atividade_obs = getattr(source.atividade, "observacao", "") or ""
        lookup[falha_id] = merge_observacao_auditor(source.observacao, atividade_obs)
    return lookup


def build_all_agents_lookup() -> dict[str, str]:
    """Mapa matricula (lower) → nome para exportações em lote."""
    agents: dict[str, str] = {}
    for row in Agent.objects.annotate(lan_lower=Lower("user_lan_id")).values(
        "user_lan_id", "full_name"
    ):
        key = (row["user_lan_id"] or "").strip().lower()
        if key:
            agents[key] = (row["full_name"] or "").strip()
    return agents


def serialize_auditado(
    row,
    clientes: dict[int, str],
    workflows: dict[int, str],
    agents: dict[str, str],
    *,
    source_meta: dict | None = None,
    responsavel: dict[str, str | None] | None = None,
) -> dict:
    mat = (row.matricula or "").strip().lower()
    aud = (row.matricula_auditor or "").strip().lower()
    meta = source_meta or {}
    payload = {
        "id": row.pk,
        "data": row.data.isoformat() if row.data else None,
        "data_analise": row.data_analise.isoformat() if row.data_analise else None,
        "data_analise_intranet": (
            row.data_analise_intranet.isoformat() if row.data_analise_intranet else None
        ),
        "data_analise_origem": (
            row.data_analise_origem.isoformat() if row.data_analise_origem else None
        ),
        "data_criacao_origem": (
            row.data_criacao_origem.isoformat() if row.data_criacao_origem else None
        ),
        "data_conclusao_origem": (
            row.data_conclusao_origem.isoformat() if row.data_conclusao_origem else None
        ),
        "data_recepcao_contestacao": (
            row.data_recepcao_contestacao.isoformat()
            if row.data_recepcao_contestacao
            else None
        ),
        "data_encerramento_atividade_intranet": (
            row.data_encerramento_atividade_intranet.isoformat()
            if row.data_encerramento_atividade_intranet
            else None
        ),
        "id_cliente": row.id_cliente,
        "cliente_nome": clientes.get(row.id_cliente) if row.id_cliente else None,
        "id_workflow": row.id_workflow,
        "workflow_nome": workflows.get(row.id_workflow) if row.id_workflow else None,
        "tipo_analise": row.tipo_analise,
        "matricula": row.matricula or "",
        "agente_nome": agents.get(mat) if mat else None,
        "matricula_auditor": row.matricula_auditor or "",
        "auditor_nome": agents.get(aud) if aud else None,
        "protocolo": row.protocolo,
        "cenario": row.cenario,
        "etapa": row.etapa,
        "status": row.status,
        "irregularidades_apontadas": row.irregularidades_apontadas,
        "cadastrado_anteriormente": row.cadastrado_anteriormente,
        "id_operations": row.id_operations,
        "resultado_origem": row.resultado_origem,
        "resultado_destino": row.resultado_destino,
        "protocolo_destino": row.protocolo_destino,
        "tipo_conclusao": row.tipo_conclusao,
        "source_kind": meta.get("source_kind", "tsv"),
        "source_id": meta.get("source_id"),
        "source_label": meta.get("source_label", "Arquivo TSV"),
        "prazo_status": meta.get("prazo_status"),
        "dias_prazo": dias_entre_auditoria_e_origem(
            row.data,
            row.data_analise,
            fallback=meta.get("dias_prazo"),
        ),
    }
    return merge_responsavel_fields(payload, responsavel)


def serialize_falha(
    row,
    clientes: dict[int, str],
    workflows: dict[int, str],
    agents: dict[str, str],
    *,
    source_meta: dict | None = None,
    include_reconciliation: bool = False,
    lider_responsavel: str | None = None,
    responsavel: dict[str, str | None] | None = None,
) -> dict:
    processual = is_processual_tipificacao(row.tipo_falha)
    mat = "" if processual else (row.matricula or "").strip().lower()
    aud = (row.usuario_auditor or "").strip().lower()
    meta = source_meta or {}
    payload = {
        "id": row.pk,
        "data": row.data.isoformat() if row.data else None,
        "data_analise": row.data_analise.isoformat() if row.data_analise else None,
        "data_analise_intranet": (
            row.data_analise_intranet.isoformat() if row.data_analise_intranet else None
        ),
        "data_analise_origem": (
            row.data_analise_origem.isoformat() if row.data_analise_origem else None
        ),
        "data_criacao_origem": (
            row.data_criacao_origem.isoformat() if row.data_criacao_origem else None
        ),
        "data_conclusao_origem": (
            row.data_conclusao_origem.isoformat() if row.data_conclusao_origem else None
        ),
        "data_recepcao_contestacao": (
            row.data_recepcao_contestacao.isoformat()
            if row.data_recepcao_contestacao
            else None
        ),
        "data_encerramento_atividade_intranet": (
            row.data_encerramento_atividade_intranet.isoformat()
            if row.data_encerramento_atividade_intranet
            else None
        ),
        "protocolo": row.protocolo,
        "id_cliente": row.id_cliente,
        "cliente_nome": clientes.get(row.id_cliente) if row.id_cliente else None,
        "id_workflow": row.id_workflow,
        "workflow_nome": workflows.get(row.id_workflow) if row.id_workflow else None,
        "id_operations": row.id_operations,
        "tipo_analise": row.tipo_analise,
        "modulo": row.modulo,
        "cenario": row.cenario,
        "matricula": "" if processual else (row.matricula or ""),
        "agente_nome": agents.get(mat) if mat else None,
        "usuario_auditor": row.usuario_auditor or "",
        "auditor_nome": agents.get(aud) if aud else None,
        "etapa": row.etapa,
        "tipo_falha": row.tipo_falha,
        "tipo_falha_oficial": row.tipo_falha_oficial,
        "categoria_falha": criticidade_label(
            row.categoria_falha,
            id_cliente=row.id_cliente,
            tipo_registro=row.tipo_registro,
        ),
        "localidade": row.localidade,
        "localidade_documento": row.localidade_documento,
        "lider": row.lider,
        "lider_responsavel": lider_responsavel,
        "agente_ativo": row.agente_ativo,
        "uf": row.uf,
        "tipo_documento": row.tipo_documento,
        "nivel_dificuldade": row.nivel_dificuldade,
        "nivel_dificuldade_confer": row.nivel_dificuldade_confer,
        "nivel_dificuldade_efetiva": nivel_dificuldade_efetivo(
            row.nivel_dificuldade_confer,
            row.nivel_dificuldade,
        ),
        "resultado_analise": row.resultado_analise,
        "novo_resultado": row.novo_resultado,
        "tendencia": row.tendencia,
        "des_problemas": row.des_problemas,
        "segmento": row.segmento,
        "sub_segmento": row.sub_segmento,
        "condicao_metrica": row.condicao_metrica,
        "qualidade_imagem": row.qualidade_imagem,
        "origem_analise": row.origem_analise,
        "data_diferenca": row.data_diferenca,
        "source_kind": meta.get("source_kind", "tsv"),
        "source_id": meta.get("source_id"),
        "source_label": meta.get("source_label", "Arquivo TSV"),
        "prazo_status": meta.get("prazo_status"),
        "dias_prazo": dias_entre_auditoria_e_origem(
            row.data,
            row.data_analise,
            fallback=meta.get("dias_prazo"),
        ),
        "observacao_auditor": (meta.get("observacao_auditor") or "").strip(),
    }
    if include_reconciliation:
        payload.update(
            {
                "reconciliation_status": meta.get("reconciliation_status"),
                "reconciliation_observation": meta.get("reconciliation_observation"),
                "reconciliation_differences": meta.get(
                    "reconciliation_differences", {}
                ),
            }
        )
    return merge_responsavel_fields(payload, responsavel)


def build_source_meta_for_auditados(rows) -> dict[int, dict]:
    """Resolve ponte Intranet em lote para IDs de auditados da página."""
    from apps.qualidade_operacional.models import (
        QualidadeGAuditoriaProjection,
        QualidadeIntranetProjection,
    )
    from apps.qualidade_operacional.services.source_config import (
        G_AUDITORIA_SOURCE_FILE,
        G_AUDITORIA_SOURCE_KIND,
        G_AUDITORIA_SOURCE_LABEL,
        INTRANET_SOURCE_FILE,
        INTRANET_SOURCE_KIND,
        INTRANET_SOURCE_LABEL,
        TSV_SOURCE_KIND,
        TSV_SOURCE_LABEL,
    )

    result: dict[int, dict] = {}
    intranet_ids = [r.pk for r in rows if (r.source_file or "") == INTRANET_SOURCE_FILE]
    g_auditoria_ids = [
        r.pk for r in rows if (r.source_file or "") == G_AUDITORIA_SOURCE_FILE
    ]
    for row in rows:
        if row.pk not in intranet_ids and row.pk not in g_auditoria_ids:
            result[row.pk] = {
                "source_kind": TSV_SOURCE_KIND,
                "source_id": None,
                "source_label": TSV_SOURCE_LABEL,
            }
    if intranet_ids:
        for proj in QualidadeIntranetProjection.objects.filter(
            auditado_id__in=intranet_ids
        ).values("auditado_id", "source_id"):
            result[int(proj["auditado_id"])] = {
                "source_kind": INTRANET_SOURCE_KIND,
                "source_id": proj["source_id"],
                "source_label": INTRANET_SOURCE_LABEL,
            }
        for pk in intranet_ids:
            result.setdefault(
                pk,
                {
                    "source_kind": INTRANET_SOURCE_KIND,
                    "source_id": None,
                    "source_label": INTRANET_SOURCE_LABEL,
                },
            )
    if g_auditoria_ids:
        for projection in QualidadeGAuditoriaProjection.objects.filter(
            auditado_id__in=g_auditoria_ids
        ).select_related("staging"):
            result[int(projection.auditado_id)] = {
                "source_kind": G_AUDITORIA_SOURCE_KIND,
                "source_id": projection.staging_id,
                "source_label": G_AUDITORIA_SOURCE_LABEL,
                "prazo_status": projection.staging.prazo_status,
                "dias_prazo": projection.staging.dias_prazo,
            }
        for pk in g_auditoria_ids:
            result.setdefault(
                pk,
                {
                    "source_kind": G_AUDITORIA_SOURCE_KIND,
                    "source_id": None,
                    "source_label": G_AUDITORIA_SOURCE_LABEL,
                },
            )
    return result


def build_source_meta_for_falhas(rows) -> dict[int, dict]:
    from apps.qualidade_operacional.models import (
        QualidadeGAuditoriaFailureReconciliation,
        QualidadeIntranetProjection,
    )
    from apps.qualidade_operacional.services.source_config import (
        INTRANET_SOURCE_FILE,
        INTRANET_SOURCE_KIND,
        INTRANET_SOURCE_LABEL,
        TSV_SOURCE_KIND,
        TSV_SOURCE_LABEL,
    )

    result: dict[int, dict] = {}
    intranet_ids = [r.pk for r in rows if (r.source_file or "") == INTRANET_SOURCE_FILE]
    for row in rows:
        if row.pk not in intranet_ids:
            result[row.pk] = {
                "source_kind": TSV_SOURCE_KIND,
                "source_id": None,
                "source_label": TSV_SOURCE_LABEL,
            }
    if intranet_ids:
        for proj in QualidadeIntranetProjection.objects.filter(
            falha_id__in=intranet_ids
        ).values("falha_id", "source_id"):
            result[int(proj["falha_id"])] = {
                "source_kind": INTRANET_SOURCE_KIND,
                "source_id": proj["source_id"],
                "source_label": INTRANET_SOURCE_LABEL,
            }
        for pk in intranet_ids:
            result.setdefault(
                pk,
                {
                    "source_kind": INTRANET_SOURCE_KIND,
                    "source_id": None,
                    "source_label": INTRANET_SOURCE_LABEL,
                },
            )
        for reconciliation in QualidadeGAuditoriaFailureReconciliation.objects.filter(
            falha_id__in=intranet_ids
        ).select_related("projection__staging"):
            meta = result.setdefault(int(reconciliation.falha_id), {})
            meta.update(
                {
                    "reconciliation_status": reconciliation.status,
                    "reconciliation_observation": reconciliation.observation,
                    "reconciliation_differences": reconciliation.differences,
                }
            )
            if reconciliation.projection_id:
                staging = reconciliation.projection.staging
                meta["prazo_status"] = staging.prazo_status
                meta["dias_prazo"] = staging.dias_prazo
    observacao_lookup = build_observacao_auditor_lookup(rows)
    for falha_id, observacao in observacao_lookup.items():
        meta = result.setdefault(falha_id, {})
        meta["observacao_auditor"] = observacao
    return result
