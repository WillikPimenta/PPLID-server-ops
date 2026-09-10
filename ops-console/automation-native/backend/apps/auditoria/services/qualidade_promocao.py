from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaAtividadeProtocoloEtapa,
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteAuditoria,
    QualidadePendenteAuditoriaFalha,
    QualidadePendenteContestacao,
    QualidadePendenteReinspecao,
    ReinspecaoAuditorPresence,
)
from apps.auditoria.services.analise_origem import get_or_create_analise_origem
from apps.auditoria.services.agent_links import resolve_agent_for_user, resolve_agent_reference
from apps.auditoria.services.suporte_operacional_link import resolve_and_link
from apps.auditoria.services.text_format import is_tipo_falha_sem_falha
from apps.qualidade_operacional.services.normalize import clean_text


def tratados_qs(*, origem: str | None = None) -> QuerySet[AuditoriaFalhaCadastro]:
    """Queryset da tabela central de tratados (`auditoria_falha_cadastro`).

    Filtra por ``origem`` quando informado. Aceita legado reinspeção com
    ``origem`` vazio e ``tipo_registro=reinspecao``.
    """
    qs = AuditoriaFalhaCadastro.objects.all()
    if not origem:
        return qs
    if origem == AuditoriaFalhaCadastro.ORIGEM_REINSPECAO:
        return qs.filter(
            Q(origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO)
            | Q(origem="", tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO)
        )
    if origem == AuditoriaFalhaCadastro.ORIGEM_AUDITORIA:
        return qs.filter(
            Q(origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA)
            | Q(origem="", tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA)
        )
    if origem == AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO:
        return qs.filter(
            Q(origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO)
            | Q(origem="", tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO)
        )
    return qs.filter(origem=origem)


def auditoria_fraud_tratados_qs() -> QuerySet[AuditoriaFalhaCadastro]:
    """Tratados pertencentes à Auditoria Fraud, sem resultados de Compliance."""
    compliance_context = ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE
    return tratados_qs(origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA).filter(
        Q(brflow_parsed__fila_contexto__isnull=True)
        | ~Q(brflow_parsed__fila_contexto=compliance_context),
        Q(analise_origem__isnull=True)
        | Q(analise_origem__contexto__fila_contexto__isnull=True)
        | ~Q(analise_origem__contexto__fila_contexto=compliance_context),
    )


def status_inicial_auditoria_fraud(tipo_falha: str | None) -> str:
    """Mantém ``Sem Falha`` contabilizável e envia falhas Fraud para revisão."""
    if is_tipo_falha_sem_falha(tipo_falha or ""):
        return AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA
    return AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO


def _mapping_metadata_for_promotion(
    pendente: QualidadePendenteReinspecao | QualidadePendenteAuditoriaCompliance,
) -> dict[str, object]:
    """Explicita a diferença de contrato entre Reinspeção e Compliance."""
    if isinstance(pendente, QualidadePendenteReinspecao):
        return {
            "codigo_irregularidade": pendente.codigo_irregularidade or "",
            "mapping_scenario_status": pendente.mapping_scenario_status or "",
            "mapping_stage_status": pendente.mapping_stage_status or "",
            "mapping_version": pendente.mapping_version or "",
            "mapping_source_hash": pendente.mapping_source_hash or "",
            "mapping_applied_at": pendente.mapping_applied_at,
        }
    return {
        "codigo_irregularidade": "",
        "mapping_scenario_status": "",
        "mapping_stage_status": "",
        "mapping_version": "",
        "mapping_source_hash": "",
        "mapping_applied_at": None,
    }


@transaction.atomic
def _promover_pendente_fila(
    pendente: QualidadePendenteReinspecao | QualidadePendenteAuditoriaCompliance,
    *,
    fluxo: str,
    finalizador=None,
    origem: str = AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
    ordem_etapa: int | None = None,
    user=None,
) -> AuditoriaFalhaCadastro:
    """Move uma etapa concluída da fila para a tabela única de tratados."""
    pendente_pk = pendente.pk
    is_auditoria_compliance = fluxo == "auditoria_compliance"
    pendente_key = (
        "pendente_auditoria_compliance_id"
        if is_auditoria_compliance
        else "pendente_reinspecao_id"
    )
    parsed = {
        **(pendente.brflow_parsed if isinstance(pendente.brflow_parsed, dict) else {}),
        pendente_key: pendente_pk,
        "fila_contexto": fluxo,
    }
    analise_origem = get_or_create_analise_origem(
        protocolo=pendente.protocolo,
        brflow_parsed=parsed,
    )
    etapa_chave = f"{fluxo}:pendente:{pendente_pk}"
    agente_ref = resolve_agent_reference(pendente.usuario).agent
    auditor_ref = resolve_agent_for_user(user) or resolve_agent_reference(pendente.auditor).agent
    mapping_metadata = _mapping_metadata_for_promotion(pendente)
    resolve_and_link(pendente)
    falha = AuditoriaFalhaCadastro.objects.create(
        atividade=None,
        analise_origem=analise_origem,
        etapa_chave=etapa_chave,
        ordem_etapa=ordem_etapa,
        etapa_criada_em=pendente.created_at,
        etapa_atualizada_em=pendente.updated_at,
        protocolo=pendente.protocolo,
        usuario=pendente.usuario or "",
        agente_ref=agente_ref,
        descricao_irregularidades=pendente.descricao_irregularidades or "",
        **mapping_metadata,
        data_contestacao=pendente.data_contestacao,
        data_analise=pendente.data_analise,
        data_analise_intranet=pendente.analise_concluida_em,
        data_recepcao_contestacao=pendente.data_contestacao,
        cliente=pendente.cliente or "",
        modulo=pendente.modulo or "",
        status=pendente.status or "",
        observacao=pendente.observacao or "",
        auditor=pendente.auditor or "",
        auditor_ref=auditor_ref,
        tipo_falha=(pendente.tipo_falha or "reinspecao").strip() or "reinspecao",
        etapa_falha=pendente.etapa_falha or "",
        motivo_falha=pendente.motivo_falha or "",
        nivel_dificuldade=pendente.nivel_dificuldade or "",
        tipo_documento=pendente.tipo_documento or "",
        uf_documento=pendente.uf_documento or "",
        novo_resultado=pendente.novo_resultado or "",
        tempo_analise=str(parsed.get("tempo_analise") or "").strip(),
        cruzamento_bases=str(parsed.get("cruzamento_bases") or "").strip(),
        qualidade_imagem=str(parsed.get("qualidade_imagem") or "").strip(),
        brflow_parsed=parsed,
        data_resposta=pendente.data_resposta,
        analise_iniciada_em=pendente.analise_iniciada_em,
        analise_concluida_em=pendente.analise_concluida_em,
        atribuido_em=pendente.atribuido_em,
        responsavel=pendente.responsavel,
        fila_origem=pendente.fila_origem or "",
        tipo_registro=(
            AuditoriaFalhaCadastro.REGISTRO_AUDITORIA
            if is_auditoria_compliance
            else AuditoriaFalhaCadastro.REGISTRO_REINSPECAO
        ),
        origem=(
            origem
            or (
                AuditoriaFalhaCadastro.ORIGEM_AUDITORIA
                if is_auditoria_compliance
                else AuditoriaFalhaCadastro.ORIGEM_REINSPECAO
            )
        ),
        created_by=pendente.created_by,
        auditor_responsavel=finalizador,
        operational_support_request_id=pendente.operational_support_request_id,
    )
    if is_auditoria_compliance:
        pendente.auditoria_compliance_historico.update(falha=falha, pendente=None)
    else:
        pendente.reinspecao_historico.update(falha=falha, pendente=None)
        from apps.auditoria.models import ReinspecaoOcorrencia
        from apps.auditoria.services.reinspecao_ocorrencias import (
            link_reinspecao_occurrence,
        )

        ocorrencia = (
            ReinspecaoOcorrencia.objects.select_for_update()
            .filter(pendente=pendente)
            .first()
        )
        if ocorrencia is not None:
            link_reinspecao_occurrence(ocorrencia, tratado=falha)
    pendente.delete()
    return falha


def promover_pendente_reinspecao(
    pendente: QualidadePendenteReinspecao,
    *,
    finalizador=None,
    origem: str = AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
    ordem_etapa: int | None = None,
    user=None,
) -> AuditoriaFalhaCadastro:
    return _promover_pendente_fila(
        pendente,
        fluxo="reinspecao",
        finalizador=finalizador,
        origem=origem,
        ordem_etapa=ordem_etapa,
        user=user,
    )


def promover_pendente_auditoria_compliance(
    pendente: QualidadePendenteAuditoriaCompliance,
    *,
    finalizador=None,
    origem: str = AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
    ordem_etapa: int | None = None,
    user=None,
) -> AuditoriaFalhaCadastro:
    return _promover_pendente_fila(
        pendente,
        fluxo="auditoria_compliance",
        finalizador=finalizador,
        origem=origem,
        ordem_etapa=ordem_etapa,
        user=user,
    )


@transaction.atomic
def promover_protocolo_contestacao(
    protocolo: AuditoriaAtividadeProtocolo,
    *,
    user=None,
) -> AuditoriaFalhaCadastro:
    """Promove cada etapa concluída para uma linha tratada idempotente."""
    if protocolo.status != AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO:
        raise ValueError("Somente protocolos concluídos podem ser promovidos a tratados.")

    protocolo = (
        AuditoriaAtividadeProtocolo.objects.select_for_update()
        .get(pk=protocolo.pk)
    )
    atividade = protocolo.atividade
    etapas = list(protocolo.etapas.order_by("ordem", "id"))
    if not etapas:
        raise ValueError("O protocolo concluído não possui etapas para promover.")
    auditor_name = ""
    if protocolo.analisado_por_id:
        auditor_name = (protocolo.analisado_por.username or "").strip()
    elif user is not None:
        auditor_name = (getattr(user, "username", None) or "").strip()
    auditor_ref = resolve_agent_for_user(protocolo.analisado_por or user)

    parsed = protocolo.brflow_parsed if isinstance(protocolo.brflow_parsed, dict) else {}
    now = timezone.now()
    data_analise_raw = clean_text(parsed.get("data_analise") or "")
    data_analise_dt = protocolo.finalizado_em or protocolo.analisado_em or now
    if data_analise_raw:
        from apps.qualidade_operacional.services.intranet_source import parse_flexible_date

        parsed_date = parse_flexible_date(data_analise_raw)
        if parsed_date is not None:
            data_analise_dt = timezone.make_aware(
                datetime.combine(parsed_date, datetime.min.time()),
                timezone.get_current_timezone(),
            )

    parsed = {
        **parsed,
        "consideracoes_finais": protocolo.consideracoes_finais or "",
        "tipo_conclusao": protocolo.tipo_conclusao or "",
        "reanalisado": bool(protocolo.reanalisado),
        "situacao": protocolo.situacao or "",
        "atividade_id": atividade.id if atividade else None,
        "protocolo_origem_id": protocolo.pk,
        "resultado_contestado": protocolo.resultado_contestado or "",
        "numero_contrato": protocolo.numero_contrato or "",
        "workflow": protocolo.workflow or "",
        "nivel_hierarquico": protocolo.nivel_hierarquico or "",
        "data_analise": data_analise_dt.date().isoformat() if data_analise_dt else "",
    }

    etapa_chaves = [f"contestacao:etapa:{etapa.pk}" for etapa in etapas]
    existentes = list(
        AuditoriaFalhaCadastro.objects.filter(etapa_chave__in=etapa_chaves)
        .select_related("analise_origem")
        .order_by("ordem_etapa", "id")
    )
    analise_origem = next(
        (item.analise_origem for item in existentes if item.analise_origem_id),
        None,
    )
    if analise_origem is None:
        analise_origem = get_or_create_analise_origem(
            protocolo=protocolo.protocolo,
            brflow_raw=protocolo.brflow_raw or "",
            brflow_parsed=parsed,
        )
    promovidos: list[AuditoriaFalhaCadastro] = []
    resolve_and_link(protocolo)
    for etapa in etapas:
        etapa_chave = f"contestacao:etapa:{etapa.pk}"
        falha, _created = AuditoriaFalhaCadastro.objects.get_or_create(
            etapa_chave=etapa_chave,
            defaults={
                "atividade": atividade,
                "protocolo_origem": protocolo,
                "etapa_origem": etapa,
                "analise_origem": analise_origem,
                "ordem_etapa": etapa.ordem,
                "etapa_criada_em": etapa.created_at,
                "etapa_atualizada_em": etapa.updated_at,
                "protocolo": (protocolo.protocolo or "").strip(),
                "brflow_raw": "",
                "brflow_parsed": {},
                "modulo": "Contestação",
                "demanda_url": (atividade.link_demanda if atividade else "") or "",
                "tipo_falha": (etapa.tipo_falha or "").strip() or "contestacao",
                "usuario": (etapa.agente or "").strip(),
                "agente_ref": resolve_agent_reference(etapa.agente).agent,
                "resultado_cliente": protocolo.resultado_contestado or "",
                "novo_resultado": (etapa.resultado_correto or "").strip()
                or protocolo.resultado_pos_auditoria
                or "",
                "sinalizacao": "",
                "motivo_falha": (etapa.motivo_falha or "").strip(),
                "etapa_falha": (etapa.etapa_falha or "").strip(),
                "tempo_analise": (etapa.tempo_analise or "").strip(),
                "cruzamento_bases": (etapa.cruzamento_bases or "").strip(),
                "nivel_dificuldade": (etapa.nivel_dificuldade or "").strip(),
                "tipo_documento": (etapa.tipo_documento or "").strip(),
                "uf_documento": (etapa.uf_documento or "").strip(),
                "qualidade_imagem": (etapa.qualidade_imagem or "").strip(),
                "data_contestacao": atividade.data_recepcao if atividade else None,
                "data_analise_intranet": protocolo.finalizado_em or now,
                "data_recepcao_contestacao": (
                    atividade.data_recepcao if atividade else None
                ),
                "data_encerramento_atividade_intranet": (
                    atividade.encerrado_em if atividade else None
                ),
                "descricao_irregularidades": protocolo.detalhamento or "",
                "cliente": (atividade.cliente if atividade else "") or "",
                "status": (etapa.situacao or "").strip().lower(),
                "observacao": protocolo.consideracoes_finais or "",
                "auditor": auditor_name,
                "auditor_ref": auditor_ref,
                "data_resposta": protocolo.finalizado_em or protocolo.analisado_em or now,
                "analise_iniciada_em": protocolo.analisado_em,
                "analise_concluida_em": protocolo.finalizado_em or now,
                "data_analise": data_analise_dt,
                "atribuido_em": protocolo.analisado_em,
                "responsavel": protocolo.analisado_por or user,
                "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
                "origem": AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
                "created_by": protocolo.analisado_por
                or (atividade.created_by if atividade else None)
                or user,
                "auditor_responsavel": user,
            },
        )
        if not _created and falha.auditor_responsavel_id is None and user is not None:
            falha.auditor_responsavel = user
            falha.save(update_fields=["auditor_responsavel", "updated_at"])
        resolve_and_link(falha)
        promovidos.append(falha)

    canonical = next(
        (item for item in promovidos if (item.status or "").strip().lower() == "procedente"),
        promovidos[0],
    )
    if protocolo.tratado_id != canonical.id:
        protocolo.tratado = canonical
        protocolo.save(update_fields=["tratado", "updated_at"])

    if atividade is not None:
        _sync_pendente_contestacao_after_promocao(atividade)

    return canonical


def _sync_pendente_contestacao_after_promocao(atividade: AuditoriaAtividade) -> None:
    """Atualiza ou remove o lote pendente quando não houver mais protocolos abertos."""
    pendente = QualidadePendenteContestacao.objects.filter(atividade=atividade).first()
    abertos = atividade.protocolos.exclude(
        status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
    ).count()
    total = atividade.protocolos.count()
    if pendente is None:
        return
    if abertos == 0:
        # A atividade e seus protocolos permanecem; somente a ponte de fila sai.
        pendente.delete()
    else:
        pendente.total_protocolos = total
        pendente.status = QualidadePendenteContestacao.STATUS_EM_ANDAMENTO
        pendente.save(update_fields=["total_protocolos", "status", "updated_at"])


@transaction.atomic
def selar_tratados_auditoria(
    atividade: AuditoriaAtividade,
    *,
    finalizador,
) -> list[AuditoriaFalhaCadastro]:
    """Promove rascunhos da auditoria e esvazia suas tabelas intermediárias."""
    if atividade.tipo != AuditoriaAtividade.TIPO_AUDITORIA:
        raise ValueError("Somente atividades de auditoria podem ser seladas por este fluxo.")

    now = timezone.now()
    rascunhos = list(
        QualidadePendenteAuditoriaFalha.objects.select_for_update()
        .filter(atividade=atividade)
        .order_by("id")
    )
    promovidos: list[AuditoriaFalhaCadastro] = []
    auditor_ref = resolve_agent_for_user(finalizador)
    from apps.qualidade_operacional.signals import defer_quality_projection_syncs

    with defer_quality_projection_syncs():
        for ordem, item in enumerate(rascunhos):
            item_pk = item.pk
            parsed = item.brflow_parsed if isinstance(item.brflow_parsed, dict) else {}
            analise_origem = get_or_create_analise_origem(
                protocolo=item.protocolo,
                brflow_raw=item.brflow_raw,
                brflow_parsed=item.brflow_parsed,
            )
            falha = AuditoriaFalhaCadastro.objects.create(
                atividade=atividade,
                analise_origem=analise_origem,
                etapa_chave=f"auditoria:rascunho:{item_pk}",
                ordem_etapa=ordem,
                etapa_criada_em=item.created_at,
                etapa_atualizada_em=item.updated_at,
                protocolo=item.protocolo,
                brflow_raw="",
                brflow_parsed={"workflow": atividade.workflow or ""},
                modulo=item.modulo,
                demanda_url=item.demanda_url,
                tipo_falha=item.tipo_falha,
                usuario=item.usuario,
                agente_ref=resolve_agent_reference(item.usuario).agent,
                resultado_cliente=item.resultado_cliente,
                novo_resultado=item.novo_resultado,
                sinalizacao=item.sinalizacao,
                motivo_falha=item.motivo_falha,
                etapa_falha=item.etapa_falha,
                tempo_analise=item.tempo_analise,
                cruzamento_bases=str(parsed.get("cruzamento_bases") or "").strip(),
                nivel_dificuldade=item.nivel_dificuldade,
                tipo_documento=item.tipo_documento,
                uf_documento=item.uf_documento,
                qualidade_imagem=item.qualidade_imagem,
                cliente=atividade.cliente or "",
                data_resposta=now,
                analise_concluida_em=now,
                data_analise_intranet=now,
                data_recepcao_contestacao=atividade.data_recepcao,
                data_encerramento_atividade_intranet=atividade.encerrado_em,
                observacao=(item.observacao or atividade.observacao or "").strip(),
                auditor=(getattr(finalizador, "username", "") or "").strip(),
                auditor_ref=auditor_ref,
                tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
                origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
                status_falha=status_inicial_auditoria_fraud(item.tipo_falha),
                created_by=item.created_by,
                auditor_responsavel=finalizador,
            )
            resolve_and_link(falha)
            promovidos.append(falha)

    if rascunhos:
        QualidadePendenteAuditoriaFalha.objects.filter(
            pk__in=[item.pk for item in rascunhos]
        ).delete()
    # Na auditoria os protocolos servem apenas como fila operacional. Depois da
    # promoção, a fonte histórica passa a ser a tabela central de tratados.
    atividade.protocolos.all().delete()
    QualidadePendenteAuditoria.objects.filter(atividade=atividade).delete()
    return promovidos


def link_pendente_auditoria_from_atividade(atividade) -> QualidadePendenteAuditoria:
    """Cria/atualiza pendente de auditoria (P) vinculado à atividade avulsa."""
    parsed = atividade.brflow_parsed if isinstance(atividade.brflow_parsed, dict) else {}
    protocolo = str(parsed.get("protocolo") or "").strip()
    pendente = QualidadePendenteAuditoria.objects.filter(atividade=atividade).first()
    if pendente is None:
        pendente = QualidadePendenteAuditoria(atividade=atividade, created_by=atividade.created_by)
    pendente.protocolo = protocolo
    pendente.nome = atividade.nome or ""
    pendente.cliente = atividade.cliente or ""
    pendente.workflow = atividade.workflow or ""
    pendente.nivel_hierarquico = atividade.nivel_hierarquico or ""
    pendente.link_demanda = atividade.link_demanda or ""
    pendente.brflow_raw = atividade.brflow_raw or ""
    pendente.brflow_parsed = parsed
    pendente.status = QualidadePendenteAuditoria.STATUS_EM_ANDAMENTO
    pendente.save()
    return pendente


def link_pendente_contestacao_from_atividade(atividade) -> QualidadePendenteContestacao:
    """Cria/atualiza pendente de contestação (A) vinculado ao lote."""
    pendente = QualidadePendenteContestacao.objects.filter(atividade=atividade).first()
    if pendente is None:
        pendente = QualidadePendenteContestacao(atividade=atividade, created_by=atividade.created_by)
    pendente.nome = atividade.nome or ""
    pendente.cliente = atividade.cliente or ""
    pendente.nome_arquivo_original = atividade.nome_arquivo_original or ""
    pendente.data_recepcao = atividade.data_recepcao
    pendente.status = QualidadePendenteContestacao.STATUS_EM_ANDAMENTO
    pendente.total_protocolos = atividade.total_protocolos or 0
    pendente.save()
    return pendente
