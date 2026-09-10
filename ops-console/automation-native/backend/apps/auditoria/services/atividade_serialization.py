from __future__ import annotations

from django.db.models import Count, Q

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaAtividadeProtocoloEtapa,
)
from apps.auditoria.services.contestacao_duplicada import serialize_duplicado_anterior
from apps.auditoria.services.atividade_sla import compute_sla_seconds, format_sla_label
from apps.auditoria.services.suporte_operacional_link import suporte_operacional_payload_for
from apps.falhas_criticas.services.user_display import resolve_user_display_name
from apps.workforce.models import Agent


def serialize_user_display_name(user) -> str | None:
    if not user:
        return None

    agent = Agent.objects.filter(user_lan_id__iexact=user.username).first()
    if agent and agent.full_name.strip():
        return agent.full_name.strip()

    display = resolve_user_display_name(user)
    if display and display != user.username:
        return display

    first_name = (user.first_name or "").strip()
    last_name = (user.last_name or "").strip()
    full_name = f"{first_name} {last_name}".strip()
    return full_name or None


def count_protocolos_tratamento_pendentes(atividade: AuditoriaAtividade) -> int:
    annotated = getattr(atividade, "protocolos_tratamento_pendentes", None)
    if annotated is not None:
        return int(annotated)
    return atividade.protocolos.exclude(
        status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
    ).count()


def count_protocolos_espelhados(atividade: AuditoriaAtividade) -> int:
    annotated = getattr(atividade, "protocolos_espelhados", None)
    if annotated is not None:
        return int(annotated)
    prefetched = getattr(atividade, "_prefetched_objects_cache", {}).get("protocolos")
    if prefetched is not None:
        return sum(1 for item in prefetched if item.tratado_referencia_id)
    return atividade.protocolos.filter(tratado_referencia_id__isnull=False).count()

def serialize_protocolo_etapa(etapa: AuditoriaAtividadeProtocoloEtapa) -> dict:
    return {
        "id": etapa.id,
        "ordem": etapa.ordem,
        "resultado_correto": etapa.resultado_correto,
        "nivel_dificuldade": etapa.nivel_dificuldade,
        "tipo_documento": etapa.tipo_documento,
        "uf_documento": etapa.uf_documento,
        "agente": etapa.agente,
        "tipo_falha": etapa.tipo_falha,
        "etapa_falha": etapa.etapa_falha,
        "tempo_analise": etapa.tempo_analise,
        "cruzamento_bases": etapa.cruzamento_bases,
        "qualidade_imagem": etapa.qualidade_imagem,
        "situacao": etapa.situacao,
        "motivo_falha": etapa.motivo_falha,
        "created_at": etapa.created_at.isoformat() if etapa.created_at else None,
        "updated_at": etapa.updated_at.isoformat() if etapa.updated_at else None,
    }


def resolve_atividade_protocolo(atividade: AuditoriaAtividade) -> str:
    parsed = atividade.brflow_parsed if isinstance(atividade.brflow_parsed, dict) else {}
    protocolo = str(parsed.get("protocolo") or "").strip()
    if protocolo:
        return protocolo

    prefetched = getattr(atividade, "_prefetched_objects_cache", {}).get("protocolos")
    if prefetched is not None:
        values = [(item.protocolo or "").strip() for item in prefetched if (item.protocolo or "").strip()]
    else:
        values = list(
            atividade.protocolos.order_by("excel_row", "id").values_list("protocolo", flat=True)[:8]
        )
        values = [str(item or "").strip() for item in values if str(item or "").strip()]

    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) <= 3:
        return ", ".join(values)
    return f"{', '.join(values[:3])} (+{len(values) - 3})"


def serialize_atividade(atividade: AuditoriaAtividade) -> dict:
    sla_seconds = compute_sla_seconds(atividade)
    return {
        "id": atividade.id,
        "tipo": atividade.tipo,
        "nome": atividade.nome,
        "nome_arquivo_original": atividade.nome_arquivo_original,
        "arquivo_original_url": atividade.arquivo_original.url if atividade.arquivo_original else None,
        "workflow": atividade.workflow,
        "nivel_hierarquico": atividade.nivel_hierarquico,
        "cliente": atividade.cliente,
        "protocolo": resolve_atividade_protocolo(atividade),
        "link_demanda": atividade.link_demanda,
        "brflow_raw": atividade.brflow_raw or "",
        "brflow_parsed": atividade.brflow_parsed or {},
        "observacao": atividade.observacao or "",
        "status": atividade.status,
        "data_recepcao": atividade.data_recepcao.isoformat() if atividade.data_recepcao else None,
        "encerrado_em": atividade.encerrado_em.isoformat() if atividade.encerrado_em else None,
        "sla_segundos": sla_seconds,
        "sla_label": format_sla_label(sla_seconds),
        "total_protocolos": atividade.total_protocolos,
        "protocolos_tratamento_pendentes": count_protocolos_tratamento_pendentes(atividade),
        "protocolos_espelhados": count_protocolos_espelhados(atividade),
        "created_by": serialize_user_display_name(atividade.created_by),
        "responsavel_id": str(atividade.responsavel_id) if atividade.responsavel_id else None,
        "responsavel": (
            (atividade.responsavel.full_name or "").strip() or None
            if atividade.responsavel_id
            else None
        ),
        "created_at": atividade.created_at.isoformat() if atividade.created_at else None,
        "updated_at": atividade.updated_at.isoformat() if atividade.updated_at else None,
    }


def serialize_atividade_protocolo(protocolo: AuditoriaAtividadeProtocolo) -> dict:
    etapas = list(protocolo.etapas.all()) if hasattr(protocolo, "etapas") else []
    return {
        "id": protocolo.id,
        "atividade_id": protocolo.atividade_id,
        "protocolo": protocolo.protocolo,
        "workflow": protocolo.workflow,
        "nivel_hierarquico": protocolo.nivel_hierarquico,
        "resultado_contestado": protocolo.resultado_contestado,
        "numero_contrato": protocolo.numero_contrato,
        "resultado_pos_auditoria": protocolo.resultado_pos_auditoria,
        "tipo_conclusao": protocolo.tipo_conclusao,
        "tipo_falha": protocolo.tipo_falha,
        "cenario": protocolo.cenario,
        "detalhamento": protocolo.detalhamento,
        "conclusao_contestacao": protocolo.conclusao_contestacao,
        "status": protocolo.status,
        "excel_row": protocolo.excel_row,
        "brflow_raw": protocolo.brflow_raw,
        "brflow_parsed": protocolo.brflow_parsed or {},
        "situacao": protocolo.situacao,
        "reanalisado": bool(protocolo.reanalisado),
        "consideracoes_finais": protocolo.consideracoes_finais,
        "analisado_por": serialize_user_display_name(protocolo.analisado_por),
        "analisado_em": protocolo.analisado_em.isoformat() if protocolo.analisado_em else None,
        "finalizado_em": protocolo.finalizado_em.isoformat() if protocolo.finalizado_em else None,
        "tratado_id": protocolo.tratado_id,
        "promovido": bool(protocolo.tratado_id),
        "tratado_referencia_id": protocolo.tratado_referencia_id,
        "espelhado": bool(protocolo.tratado_referencia_id),
        "duplicado_anterior": (
            serialize_duplicado_anterior(protocolo.tratado_referencia)
            if protocolo.tratado_referencia_id and protocolo.tratado_referencia is not None
            else None
        ),
        "etapas": [serialize_protocolo_etapa(item) for item in etapas],
        "created_at": protocolo.created_at.isoformat() if protocolo.created_at else None,
        "updated_at": protocolo.updated_at.isoformat() if protocolo.updated_at else None,
        "suporte_operacional": suporte_operacional_payload_for(protocolo),
    }
