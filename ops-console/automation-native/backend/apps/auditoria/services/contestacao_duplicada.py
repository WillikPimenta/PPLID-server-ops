from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaAtividadeProtocolo,
    AuditoriaAtividadeProtocoloEtapa,
    AuditoriaFalhaCadastro,
)
from apps.auditoria.services.contestacao_import import (
    ContestacaoImportPreview,
    ParsedProtocolRow,
    cell_to_str,
    normalize_text,
)
from apps.auditoria.services.text_format import is_tipo_falha_automatico

ContestacaoIdentity = tuple[str, str, str]


def normalize_protocolo(value: Any) -> str:
    return normalize_text(cell_to_str(value))


def contestacao_row_identity(
    protocolo: str,
    workflow: str,
    resultado_contestado: str,
) -> ContestacaoIdentity:
    """Chave composta normalizada: protocolo + workflow + resultado contestado."""
    return (
        normalize_protocolo(protocolo),
        normalize_text(workflow),
        normalize_text(resultado_contestado),
    )


def tratado_contestacao_identity(tratado: AuditoriaFalhaCadastro) -> ContestacaoIdentity:
    parsed = tratado.brflow_parsed if isinstance(tratado.brflow_parsed, dict) else {}
    resultado = (tratado.resultado_cliente or "").strip() or str(parsed.get("resultado_contestado") or "")
    workflow = str(parsed.get("workflow") or "").strip()
    if not workflow and tratado.atividade_id and tratado.atividade is not None:
        workflow = (tratado.atividade.workflow or "").strip()
    return contestacao_row_identity(tratado.protocolo, workflow, resultado)


def lookup_prior_tratados_contestacao(
    rows: list[ParsedProtocolRow],
) -> dict[ContestacaoIdentity, AuditoriaFalhaCadastro]:
    """Retorna tratado de contestação mais recente por chave composta normalizada."""
    from apps.auditoria.services.qualidade_promocao import tratados_qs

    if not rows:
        return {}

    wanted_identities = {
        contestacao_row_identity(row.protocolo, row.workflow, row.resultado_contestado)
        for row in rows
    }
    protocolos_sql = sorted({(row.protocolo or "").strip() for row in rows if (row.protocolo or "").strip()})
    if not protocolos_sql:
        return {}

    qs = (
        tratados_qs(origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO)
        .filter(protocolo__in=protocolos_sql)
        .select_related("atividade")
        .order_by("-analise_concluida_em", "-data_resposta", "-id")
    )
    result: dict[ContestacaoIdentity, AuditoriaFalhaCadastro] = {}
    for item in qs:
        identity = tratado_contestacao_identity(item)
        if identity not in wanted_identities or identity in result:
            continue
        result[identity] = item
    return result


def serialize_duplicado_anterior(tratado: AuditoriaFalhaCadastro) -> dict[str, Any]:
    atividade = tratado.atividade
    link_demanda = (tratado.demanda_url or "").strip()
    if not link_demanda and atividade is not None:
        link_demanda = (atividade.link_demanda or "").strip()
    data_ref = tratado.analise_concluida_em or tratado.data_resposta
    parsed = tratado.brflow_parsed if isinstance(tratado.brflow_parsed, dict) else {}
    workflow = str(parsed.get("workflow") or "").strip()
    if not workflow and atividade is not None:
        workflow = (atividade.workflow or "").strip()
    resultado = (tratado.resultado_cliente or "").strip() or str(parsed.get("resultado_contestado") or "")
    return {
        "tratado_id": tratado.id,
        "atividade_id": atividade.id if atividade is not None else None,
        "atividade_nome": (atividade.nome if atividade is not None else "") or "",
        "link_demanda": link_demanda,
        "analise_concluida_em": data_ref.isoformat() if data_ref else None,
        "workflow": workflow,
        "resultado_contestado": resultado,
    }


def enrich_preview_with_tratados_anteriores(preview: ContestacaoImportPreview) -> None:
    """Enriquece a prévia com protocolos já tratados em contestações anteriores."""
    prior_map = lookup_prior_tratados_contestacao(preview.rows)
    if not prior_map:
        return

    ja_tratados: list[dict[str, Any]] = []
    amostra_by_excel_row = {
        int(item.get("excel_row") or 0): item
        for item in preview.amostra
        if isinstance(item, dict) and item.get("excel_row")
    }

    for row in preview.rows:
        identity = contestacao_row_identity(row.protocolo, row.workflow, row.resultado_contestado)
        prior = prior_map.get(identity)
        if prior is None:
            continue
        info = serialize_duplicado_anterior(prior)
        ja_tratados.append(
            {
                "protocolo": row.protocolo,
                "workflow": row.workflow,
                "resultado_contestado": row.resultado_contestado,
                **info,
            }
        )
        amostra_item = amostra_by_excel_row.get(row.excel_row)
        if amostra_item is not None:
            amostra_item["duplicado_anterior"] = info

    preview.protocolos_ja_tratados = ja_tratados


def format_duplicado_detalhamento_note(tratado: AuditoriaFalhaCadastro) -> str:
    link = (tratado.demanda_url or "").strip()
    atividade = tratado.atividade
    if not link and atividade is not None:
        link = (atividade.link_demanda or "").strip()

    atividade_nome = (atividade.nome if atividade is not None else "") or ""
    data_ref = tratado.analise_concluida_em or tratado.data_resposta
    data_label = timezone.localtime(data_ref).strftime("%d/%m/%Y") if data_ref else ""

    message = "Protocolo duplicado. Auditoria já realizada"
    if link:
        message = f"{message} em {link}"
    if atividade_nome or data_label:
        detail = atividade_nome or "atividade anterior"
        if data_label:
            detail = f"{detail} — {data_label}"
        message = f"{message} (atividade: {detail})"
    return f"{message}."


def append_duplicado_note_to_detalhamento(existing: str, note: str) -> str:
    base = (existing or "").strip()
    note = (note or "").strip()
    if not note:
        return base
    if not base:
        return note
    if note.casefold() in base.casefold():
        return base
    return f"{base}\n\n{note}"


def build_etapas_from_tratado(
    protocolo: AuditoriaAtividadeProtocolo,
    tratado: AuditoriaFalhaCadastro,
) -> list[AuditoriaAtividadeProtocoloEtapa]:
    parsed = tratado.brflow_parsed if isinstance(tratado.brflow_parsed, dict) else {}
    etapas_data = parsed.get("etapas") if isinstance(parsed.get("etapas"), list) else []
    if not etapas_data:
        # Tratados da tabela central representam uma etapa por linha. Registros
        # promovidos recentemente não carregam mais a lista redundante no JSON.
        etapas_data = [{
            "nivel_dificuldade": tratado.nivel_dificuldade,
            "tipo_documento": tratado.tipo_documento,
            "uf_documento": tratado.uf_documento,
            "agente": tratado.usuario,
            "tipo_falha": tratado.tipo_falha,
            "etapa_falha": tratado.etapa_falha,
            "tempo_analise": tratado.tempo_analise,
            "cruzamento_bases": tratado.cruzamento_bases,
            "qualidade_imagem": tratado.qualidade_imagem,
            "situacao": tratado.status,
            "motivo_falha": tratado.motivo_falha,
        }]
    created: list[AuditoriaAtividadeProtocoloEtapa] = []

    for index, item in enumerate(etapas_data):
        if not isinstance(item, dict):
            continue
        tipo_falha = (item.get("tipo_falha") or "").strip()
        etapa = AuditoriaAtividadeProtocoloEtapa(
            protocolo=protocolo,
            ordem=index,
            resultado_correto=(item.get("resultado_correto") or "").strip(),
            nivel_dificuldade=(item.get("nivel_dificuldade") or "").strip(),
            tipo_documento=(item.get("tipo_documento") or "").strip(),
            uf_documento=(item.get("uf_documento") or "").strip(),
            agente="" if is_tipo_falha_automatico(tipo_falha) else (item.get("agente") or "").strip(),
            tipo_falha=tipo_falha,
            etapa_falha=(item.get("etapa_falha") or "").strip(),
            tempo_analise=(item.get("tempo_analise") or "").strip(),
            cruzamento_bases=(item.get("cruzamento_bases") or "").strip(),
            qualidade_imagem=(item.get("qualidade_imagem") or "").strip(),
            situacao=(item.get("situacao") or "").strip().lower(),
            motivo_falha=(item.get("motivo_falha") or "").strip(),
        )
        tracked = (
            etapa.resultado_correto,
            etapa.nivel_dificuldade,
            etapa.tipo_documento,
            etapa.uf_documento,
            etapa.agente,
            etapa.tipo_falha,
            etapa.etapa_falha,
            etapa.tempo_analise,
            etapa.cruzamento_bases,
            etapa.qualidade_imagem,
            etapa.situacao,
            etapa.motivo_falha,
        )
        if any(tracked):
            created.append(etapa)
    return created


def apply_espelho_from_tratado(
    protocolo: AuditoriaAtividadeProtocolo,
    tratado: AuditoriaFalhaCadastro,
) -> list[AuditoriaAtividadeProtocoloEtapa]:
    """Preenche protocolo espelhado com snapshot do tratado anterior (somente leitura)."""
    parsed = tratado.brflow_parsed if isinstance(tratado.brflow_parsed, dict) else {}
    now = timezone.now()

    protocolo.tratado_referencia = tratado
    protocolo.brflow_raw = tratado.brflow_raw or ""
    protocolo.brflow_parsed = {**parsed, "_espelho": True, "_espelho_tratado_id": tratado.id}
    protocolo.consideracoes_finais = (
        (tratado.observacao or "").strip()
        or str(parsed.get("consideracoes_finais") or "").strip()
    )
    protocolo.tipo_conclusao = str(parsed.get("tipo_conclusao") or "")
    protocolo.reanalisado = bool(parsed.get("reanalisado"))
    protocolo.situacao = (tratado.status or parsed.get("situacao") or "").strip().lower()
    protocolo.numero_contrato = str(parsed.get("numero_contrato") or protocolo.numero_contrato or "")
    protocolo.resultado_pos_auditoria = (tratado.novo_resultado or "").strip()
    protocolo.tipo_falha = (tratado.tipo_falha or "").strip()
    protocolo.cenario = (tratado.motivo_falha or "").strip()
    protocolo.detalhamento = (tratado.descricao_irregularidades or "").strip()
    protocolo.conclusao_contestacao = protocolo.conclusao_contestacao or ""
    protocolo.status = AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
    protocolo.analisado_por = tratado.responsavel
    protocolo.analisado_em = tratado.analise_iniciada_em or tratado.analise_concluida_em or now
    protocolo.finalizado_em = tratado.analise_concluida_em or tratado.data_resposta or now

    if protocolo.situacao == AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE:
        protocolo.conclusao_contestacao = "PROCEDENTE"
    elif protocolo.situacao == AuditoriaAtividadeProtocolo.SITUACAO_IMPROCEDENTE:
        protocolo.conclusao_contestacao = "IMPROCEDENTE"

    return build_etapas_from_tratado(protocolo, tratado)
