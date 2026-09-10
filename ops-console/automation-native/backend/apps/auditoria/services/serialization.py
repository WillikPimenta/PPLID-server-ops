from __future__ import annotations

from apps.auditoria.models import (
    AuditoriaCatalogItem,
    AuditoriaFalhaCadastro,
    AuditoriaMotivoFalha,
    QualidadePendenteAuditoriaFalha,
)
from apps.auditoria.services.text_format import format_auditoria_label
from apps.auditoria.services.suporte_operacional_link import suporte_operacional_payload_for


def _resolve_resultado_qualidade(record) -> str:
    stored = getattr(record, "resultado_qualidade", None)
    if stored:
        return stored
    return AuditoriaFalhaCadastro.inferir_resultado_qualidade(
        status=getattr(record, "status", ""),
        tipo_falha=getattr(record, "tipo_falha", ""),
        origem=getattr(record, "origem", "") or AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        tipo_registro=getattr(record, "tipo_registro", ""),
        status_falha=getattr(record, "status_falha", ""),
        brflow_parsed=getattr(record, "brflow_parsed", None),
    )


def _resolve_analise_status(record) -> tuple[str, str]:
    labels = dict(AuditoriaFalhaCadastro.ANALISE_STATUS_CHOICES)
    if isinstance(record, AuditoriaFalhaCadastro):
        return AuditoriaFalhaCadastro.ANALISE_CONCLUIDO, labels[AuditoriaFalhaCadastro.ANALISE_CONCLUIDO]
    if isinstance(record, QualidadePendenteAuditoriaFalha):
        return (
            AuditoriaFalhaCadastro.ANALISE_NAO_ATRIBUIDO,
            labels[AuditoriaFalhaCadastro.ANALISE_NAO_ATRIBUIDO],
        )
    analise_status = getattr(record, "analise_status", None) or AuditoriaFalhaCadastro.ANALISE_NAO_ATRIBUIDO
    return analise_status, labels.get(analise_status, "Não atribuído")

def serialize_catalog_item(item: AuditoriaCatalogItem) -> dict:
    label = format_auditoria_label(item.label or item.value)
    return {
        "id": item.id,
        "catalog": item.catalog,
        "value": item.value,
        "label": label,
        "active": item.active,
        "sort_order": item.sort_order,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def serialize_motivo_falha(item: AuditoriaMotivoFalha) -> dict:
    return {
        "id": item.id,
        "motivo": item.motivo,
        "criticidade": item.criticidade,
        "segmentos": item.segmentos,
        "subsegmento": item.subsegmento,
        "active": item.active,
        "sort_order": item.sort_order,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _isoformat_or_none(value) -> str | None:
    return value.isoformat() if value else None


def serialize_falha_cadastro(record: AuditoriaFalhaCadastro) -> dict:
    responsavel = record.responsavel
    responsavel_nome = None
    if responsavel is not None:
        full = (responsavel.get_full_name() or "").strip()
        responsavel_nome = full or responsavel.username

    analise_status, analise_status_label = _resolve_analise_status(record)
    resultado_qualidade = _resolve_resultado_qualidade(record)
    resultado_labels = dict(AuditoriaFalhaCadastro.RESULTADO_QUALIDADE_CHOICES)
    agente_ref = record.agente_ref
    auditor_ref = record.auditor_ref

    return {
        "id": record.id,
        "atividade_id": record.atividade_id,
        "analise_origem_id": record.analise_origem_id,
        "protocolo_origem_id": record.protocolo_origem_id,
        "etapa_origem_id": record.etapa_origem_id,
        "etapa_chave": record.etapa_chave,
        "ordem_etapa": record.ordem_etapa,
        "etapa_criada_em": _isoformat_or_none(record.etapa_criada_em),
        "etapa_atualizada_em": _isoformat_or_none(record.etapa_atualizada_em),
        "protocolo": record.protocolo,
        "brflow_raw": record.brflow_raw,
        "brflow_parsed": record.brflow_parsed or {},
        "modulo": record.modulo,
        "demanda_url": record.demanda_url,
        "tipo_falha": record.tipo_falha,
        "usuario": record.usuario,
        "agente_id": (
            str(record.agente_ref_id) if record.agente_ref_id else None
        ),
        "agente_nome": agente_ref.full_name if agente_ref else "",
        "agente_ativo": agente_ref.active if agente_ref else None,
        "resultado_cliente": record.resultado_cliente,
        "novo_resultado": record.novo_resultado,
        "sinalizacao": record.sinalizacao,
        "motivo_falha": record.motivo_falha,
        "etapa_falha": record.etapa_falha,
        "codigo_irregularidade": record.codigo_irregularidade,
        "mapping_scenario_status": record.mapping_scenario_status,
        "mapping_stage_status": record.mapping_stage_status,
        "mapping_version": record.mapping_version,
        "mapping_source_hash": record.mapping_source_hash,
        "tempo_analise": record.tempo_analise or "",
        "cruzamento_bases": record.cruzamento_bases or "",
        "nivel_dificuldade": record.nivel_dificuldade,
        "tipo_documento": record.tipo_documento,
        "uf_documento": record.uf_documento,
        "qualidade_imagem": record.qualidade_imagem or "",
        "data_contestacao": _isoformat_or_none(record.data_contestacao),
        "data_analise": _isoformat_or_none(record.data_analise),
        "data_analise_intranet": _isoformat_or_none(record.data_analise_intranet),
        "data_recepcao_contestacao": _isoformat_or_none(record.data_recepcao_contestacao),
        "data_encerramento_atividade_intranet": _isoformat_or_none(
            record.data_encerramento_atividade_intranet
        ),
        "descricao_irregularidades": record.descricao_irregularidades or "",
        "cliente": record.cliente or "",
        "status": record.status or "",
        "observacao": record.observacao or "",
        "auditor": record.auditor or "",
        "auditor_id": (
            str(record.auditor_ref_id) if record.auditor_ref_id else None
        ),
        "auditor_nome": auditor_ref.full_name if auditor_ref else "",
        "auditor_ativo": auditor_ref.active if auditor_ref else None,
        "data_resposta": _isoformat_or_none(record.data_resposta),
        "analise_status": analise_status,
        "analise_status_label": analise_status_label,
        "resultado_qualidade": resultado_qualidade,
        "resultado_qualidade_label": resultado_labels.get(
            resultado_qualidade,
            "Não classificado",
        ),
        "responsavel_id": str(record.responsavel_id) if record.responsavel_id else None,
        "responsavel_nome": responsavel_nome,
        "atribuido_em": _isoformat_or_none(record.atribuido_em),
        "analise_iniciada_em": _isoformat_or_none(record.analise_iniciada_em),
        "analise_concluida_em": _isoformat_or_none(record.analise_concluida_em),
        "tipo_registro": record.tipo_registro,
        "origem": record.origem or "",
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "created_by": record.created_by.username if record.created_by else None,
        "suporte_operacional": suporte_operacional_payload_for(record),
    }


def serialize_falha_pendente_auditoria(
    record: QualidadePendenteAuditoriaFalha,
) -> dict:
    """Serializa somente o contrato do rascunho da Auditoria Fraud."""
    analise_status = AuditoriaFalhaCadastro.ANALISE_NAO_ATRIBUIDO
    analise_status_label = dict(AuditoriaFalhaCadastro.ANALISE_STATUS_CHOICES)[
        analise_status
    ]
    resultado_qualidade = _resolve_resultado_qualidade(record)
    resultado_label = dict(AuditoriaFalhaCadastro.RESULTADO_QUALIDADE_CHOICES).get(
        resultado_qualidade,
        "Não classificado",
    )

    return {
        "id": record.id,
        "atividade_id": record.atividade_id,
        "analise_origem_id": None,
        "protocolo_origem_id": None,
        "etapa_origem_id": None,
        "etapa_chave": None,
        "ordem_etapa": None,
        "etapa_criada_em": None,
        "etapa_atualizada_em": None,
        "protocolo": record.protocolo,
        "brflow_raw": record.brflow_raw,
        "brflow_parsed": record.brflow_parsed or {},
        "modulo": record.modulo,
        "demanda_url": record.demanda_url,
        "tipo_falha": record.tipo_falha,
        "usuario": record.usuario,
        "agente_id": None,
        "agente_nome": "",
        "agente_ativo": None,
        "resultado_cliente": record.resultado_cliente,
        "novo_resultado": record.novo_resultado,
        "sinalizacao": record.sinalizacao,
        "motivo_falha": record.motivo_falha,
        "etapa_falha": record.etapa_falha,
        "tempo_analise": record.tempo_analise or "",
        "cruzamento_bases": "",
        "nivel_dificuldade": record.nivel_dificuldade,
        "tipo_documento": record.tipo_documento,
        "uf_documento": record.uf_documento,
        "qualidade_imagem": record.qualidade_imagem or "",
        "data_contestacao": None,
        "data_analise": None,
        "data_analise_intranet": None,
        "data_recepcao_contestacao": None,
        "data_encerramento_atividade_intranet": None,
        "descricao_irregularidades": "",
        "cliente": "",
        "status": "",
        "observacao": "",
        "auditor": "",
        "auditor_id": None,
        "auditor_nome": "",
        "auditor_ativo": None,
        "data_resposta": None,
        "analise_status": analise_status,
        "analise_status_label": analise_status_label,
        "resultado_qualidade": resultado_qualidade,
        "resultado_qualidade_label": resultado_label,
        "responsavel_id": None,
        "responsavel_nome": None,
        "atribuido_em": None,
        "analise_iniciada_em": None,
        "analise_concluida_em": None,
        "tipo_registro": record.tipo_registro,
        "origem": "",
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "created_by": record.created_by.username if record.created_by else None,
    }
