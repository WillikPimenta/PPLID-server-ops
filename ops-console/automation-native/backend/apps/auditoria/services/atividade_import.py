from __future__ import annotations



from datetime import timedelta



from django.core.files.base import ContentFile

from django.db import transaction

from django.utils import timezone



from apps.auditoria.models import (

    AuditoriaAtividade,

    AuditoriaAtividadeImportStaging,

    AuditoriaAtividadeProtocolo,

    AuditoriaAtividadeProtocoloEtapa,

    QualidadePendenteContestacao,

)

from apps.auditoria.services.atividade_protocolo_crud import sync_atividade_protocolo_metrics

from apps.auditoria.services.contestacao_duplicada import (
    apply_espelho_from_tratado,
    contestacao_row_identity,
    enrich_preview_with_tratados_anteriores,
    lookup_prior_tratados_contestacao,
)

from apps.auditoria.services.contestacao_import import (

    ContestacaoImportPreview,

    ParsedProtocolRow,

    parse_contestacao_file,

    parse_import_filename_metadata,

    rows_from_preview_payload,

)





def build_default_activity_name(filename: str) -> str:

    base = (filename or "Importação").rsplit(".", 1)[0].strip()

    return base or "Atividade de auditoria"





def preview_import(file_bytes: bytes, filename: str) -> ContestacaoImportPreview:

    preview = parse_contestacao_file(file_bytes, filename)

    metadata = parse_import_filename_metadata(filename)

    if metadata["cliente"]:

        preview.cliente = metadata["cliente"]

    preview.link_demanda = metadata["link_demanda"]

    enrich_preview_with_tratados_anteriores(preview)

    return preview





def create_import_staging(

    *,

    user,

    file_bytes: bytes,

    filename: str,

    preview: ContestacaoImportPreview,

) -> AuditoriaAtividadeImportStaging:

    expires_at = timezone.now() + timedelta(hours=2)

    staging = AuditoriaAtividadeImportStaging(

        nome_arquivo=filename,

        preview={

            **preview.to_dict(),

            "rows": [

                {

                    "protocolo": row.protocolo,

                    "workflow": row.workflow,

                    "nivel_hierarquico": row.nivel_hierarquico,

                    "resultado_contestado": row.resultado_contestado,

                    "numero_contrato": row.numero_contrato,

                    "resultado_pos_auditoria": row.resultado_pos_auditoria,

                    "tipo_conclusao": row.tipo_conclusao,

                    "tipo_falha": row.tipo_falha,

                    "cenario": row.cenario,

                    "detalhamento": row.detalhamento,

                    "conclusao_contestacao": row.conclusao_contestacao,
                    "data_analise": row.data_analise,
                    "excel_row": row.excel_row,

                }

                for row in preview.rows

            ],

        },

        created_by=user,

        expires_at=expires_at,

    )

    staging.arquivo.save(filename, ContentFile(file_bytes), save=False)

    staging.save()

    return staging





def _sync_pendente_after_import(atividade: AuditoriaAtividade) -> None:

    pendente = QualidadePendenteContestacao.objects.filter(atividade=atividade).first()

    if pendente is None:

        return

    abertos = atividade.protocolos.exclude(

        status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,

    ).count()

    if abertos == 0:

        pendente.delete()

        return

    pendente.total_protocolos = atividade.total_protocolos or 0

    pendente.status = QualidadePendenteContestacao.STATUS_EM_ANDAMENTO

    pendente.save(update_fields=["total_protocolos", "status", "updated_at"])





@transaction.atomic

def confirm_import(

    *,

    staging: AuditoriaAtividadeImportStaging,

    user,

    nome: str | None = None,

    cliente: str | None = None,

    link_demanda: str | None = None,

    data_recepcao=None,

    responsavel=None,

) -> AuditoriaAtividade:

    rows = rows_from_preview_payload(staging.preview)

    if not rows:

        raise ValueError("Não há protocolos para importar.")



    unique_rows: list[ParsedProtocolRow] = []

    seen_protocolos: set[str] = set()

    for row in rows:

        if row.protocolo in seen_protocolos:

            continue

        seen_protocolos.add(row.protocolo)

        unique_rows.append(row)

    rows = unique_rows



    prior_map = lookup_prior_tratados_contestacao(rows)



    activity_name = (nome or "").strip() or build_default_activity_name(staging.nome_arquivo)

    first_row = rows[0]



    atividade = AuditoriaAtividade(

        tipo=AuditoriaAtividade.TIPO_CONTESTACAO,

        nome=activity_name,

        nome_arquivo_original=staging.nome_arquivo,

        workflow=first_row.workflow,

        nivel_hierarquico=first_row.nivel_hierarquico,

        cliente=(cliente or "").strip(),

        link_demanda=(link_demanda or "").strip(),

        data_recepcao=data_recepcao,

        status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,

        total_protocolos=len(rows),

        created_by=user,

        responsavel=responsavel,

    )

    atividade.save()



    if staging.arquivo:

        with staging.arquivo.open("rb") as handle:

            atividade.arquivo_original.save(

                staging.nome_arquivo,

                ContentFile(handle.read()),

                save=True,

            )



    protocolos: list[AuditoriaAtividadeProtocolo] = []

    espelho_tratados: dict[int, object] = {}

    for row in rows:
        identity = contestacao_row_identity(row.protocolo, row.workflow, row.resultado_contestado)
        prior = prior_map.get(identity)

        protocolo = AuditoriaAtividadeProtocolo(

            atividade=atividade,

            protocolo=row.protocolo,

            workflow=row.workflow,

            nivel_hierarquico=row.nivel_hierarquico,

            resultado_contestado=row.resultado_contestado,

            numero_contrato=row.numero_contrato,

            resultado_pos_auditoria=row.resultado_pos_auditoria,

            tipo_conclusao=row.tipo_conclusao,

            tipo_falha=row.tipo_falha,

            cenario=row.cenario,

            detalhamento=row.detalhamento,

            conclusao_contestacao=row.conclusao_contestacao,
            brflow_parsed={"data_analise": row.data_analise} if row.data_analise else {},
            status=(
                AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
                if prior is not None
                else AuditoriaAtividadeProtocolo.STATUS_PENDENTE
            ),
            excel_row=row.excel_row,

        )

        if prior is not None:
            espelho_tratados[row.excel_row] = prior

        protocolos.append(protocolo)



    created_protocolos = AuditoriaAtividadeProtocolo.objects.bulk_create(protocolos)



    etapas: list[AuditoriaAtividadeProtocoloEtapa] = []

    for protocolo in created_protocolos:

        prior = espelho_tratados.get(protocolo.excel_row)

        if prior is None:

            continue

        etapas.extend(apply_espelho_from_tratado(protocolo, prior))

        protocolo.save()



    if etapas:

        AuditoriaAtividadeProtocoloEtapa.objects.bulk_create(etapas)



    staging.delete()

    from apps.auditoria.services.qualidade_promocao import link_pendente_contestacao_from_atividade



    link_pendente_contestacao_from_atividade(atividade)

    sync_atividade_protocolo_metrics(atividade)

    _sync_pendente_after_import(atividade)

    return atividade

