from __future__ import annotations

import hashlib
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    AuditoriaAtividadeImportStaging,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.reinspecao_ged_finalizados import protocolos_finalizados_ged
from apps.auditoria.services.reinspecao_import import (
    TIPO_FALHA_REINSPECAO,
    ReinspecaoImportPreview,
    apply_mapping_to_preview,
    parse_datetime_cell,
    parse_reinspecao_file,
    rows_from_reinspecao_preview_payload,
)


def preview_reinspecao_import(file_bytes: bytes, filename: str) -> ReinspecaoImportPreview:
    return apply_mapping_to_preview(parse_reinspecao_file(file_bytes, filename))


def create_reinspecao_import_staging(
    *,
    user,
    file_bytes: bytes,
    filename: str,
    preview: ReinspecaoImportPreview,
    file_sha256: str,
) -> tuple[AuditoriaAtividadeImportStaging, bool]:
    from apps.auditoria.services.reinspecao_fila import fila_contexto_atual

    fingerprint = build_reinspecao_import_fingerprint(
        file_sha256=file_sha256,
        mapping_version=preview.mapping_version,
        mapping_source_hash=preview.mapping_source_hash,
    )
    expires_at = timezone.now() + timedelta(hours=2)
    with transaction.atomic():
        get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)
        existing = (
            AuditoriaAtividadeImportStaging.objects.filter(
                created_by=user,
                expires_at__gt=timezone.now(),
                preview__fila_contexto=fila_contexto_atual(),
                preview__import_fingerprint=fingerprint,
            )
            .order_by("-created_at")
            .first()
        )
        if existing:
            return existing, False

        staging = AuditoriaAtividadeImportStaging(
            nome_arquivo=filename,
            preview={
                **preview.to_dict(),
                "kind": "reinspecao",
                "fila_contexto": fila_contexto_atual(),
                "file_sha256": file_sha256,
                "import_fingerprint": fingerprint,
                "rows": [
                    {
                        "protocolo": row.protocolo,
                        "matricula_inspetor": row.matricula_inspetor,
                        "descricao_irregularidades": row.descricao_irregularidades,
                        "data_contestacao": (
                            row.data_contestacao.isoformat() if row.data_contestacao else None
                        ),
                        "excel_row": row.excel_row,
                        "codigo_irregularidade": row.codigo_irregularidade,
                        "classificacao_irregularidade": row.classificacao_irregularidade,
                        "cenario_mapeado": row.cenario_mapeado,
                        "etapa_mapeada": row.etapa_mapeada,
                        "mapping_scenario_status": row.mapping_scenario_status,
                        "mapping_stage_status": row.mapping_stage_status,
                        "mapping_version": row.mapping_version,
                        "mapping_source_hash": row.mapping_source_hash,
                        "mapping_candidates": row.mapping_candidates,
                    }
                    for row in preview.rows
                ],
            },
            created_by=user,
            expires_at=expires_at,
        )
        staging.arquivo.save(filename, ContentFile(file_bytes), save=False)
        staging.save()
        return staging, True


def build_reinspecao_import_fingerprint(
    *,
    file_sha256: str,
    mapping_version: str = "",
    mapping_source_hash: str = "",
) -> str:
    raw = (
        f"{file_sha256.lower()}:reinspecao:"
        f"{mapping_version}:{mapping_source_hash.lower()}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def reinspecao_preview_from_staging(staging: AuditoriaAtividadeImportStaging) -> dict:
    internal_keys = {"rows", "fila_contexto", "file_sha256", "import_fingerprint"}
    return {
        key: value
        for key, value in (staging.preview or {}).items()
        if key not in internal_keys
    }


def is_falha_realizada(falha) -> bool:
    """Tratados (F) estão na tabela de falhas; pendentes nunca estão 'realizados'."""
    if isinstance(falha, QualidadePendenteReinspecao):
        return False
    if isinstance(falha, AuditoriaFalhaCadastro):
        return True
    return bool((getattr(falha, "status", None) or "").strip() or getattr(falha, "data_resposta", None))


def realizada_q():
    """Q legado — preferir consultar reinspecao_tratados_qs()."""
    from django.db.models import Q

    return Q(data_resposta__isnull=False) | ~Q(status="")


@transaction.atomic
def bulk_delete_reinspecao_falhas(*, ids: list[int]) -> dict:
    """Exclui protocolos pendentes. Ignora IDs inexistentes ou já tratados."""
    clean_ids = sorted({int(i) for i in ids if str(i).isdigit() or isinstance(i, int)})
    if not clean_ids:
        return {"deleted": 0, "skipped": 0, "requested": 0}

    from apps.auditoria.services.reinspecao_fila import fila_contexto_atual

    qs = QualidadePendenteReinspecao.objects.filter(
        pk__in=clean_ids,
        contexto=fila_contexto_atual(),
    )
    found = list(qs)
    deleted = 0
    if found:
        QualidadePendenteReinspecao.objects.filter(
            pk__in=[f.pk for f in found],
            contexto=fila_contexto_atual(),
        ).delete()
        deleted = len(found)
    skipped = len(clean_ids) - deleted
    return {
        "deleted": deleted,
        "skipped": max(0, skipped),
        "requested": len(clean_ids),
    }


@transaction.atomic
def confirm_reinspecao_import(
    *,
    staging: AuditoriaAtividadeImportStaging,
    user,
) -> dict:
    """Registra protocolos na tabela de pendentes. Tratados só ao finalizar a análise."""
    from apps.auditoria.models import QualidadePendenteReinspecao
    from apps.auditoria.services.reinspecao_fila import fila_contexto_atual

    staging_contexto = str((staging.preview or {}).get("fila_contexto") or "reinspecao")
    if staging_contexto != fila_contexto_atual():
        raise ValueError("A prévia pertence a outro fluxo de fila. Valide o arquivo novamente.")

    rows = rows_from_reinspecao_preview_payload(staging.preview)
    if not rows:
        raise ValueError("Não há protocolos válidos para importar.")

    from apps.auditoria.services.reinspecao_mapping import ReinspecaoMappingResolver

    active_resolver = ReinspecaoMappingResolver.from_active_mapping()
    preview_version = str((staging.preview or {}).get("mapping_version") or "").strip()
    preview_hash = str((staging.preview or {}).get("mapping_source_hash") or "").strip()
    if (
        not preview_version
        or not preview_hash
        or preview_version != active_resolver.mapping_version
        or preview_hash.lower() != active_resolver.source_hash.lower()
    ):
        raise ValueError(
            "A matriz de Reinspeção mudou após a prévia. Valide o arquivo novamente."
        )

    contexto = fila_contexto_atual()
    candidatos = {(row.protocolo or "").strip() for row in rows if (row.protocolo or "").strip()}
    skip_finalized_ged = protocolos_finalizados_ged(candidatos)

    source_file = staging.nome_arquivo or ""
    from apps.auditoria.services.reinspecao_import_dedupe import (
        dedupe_key_for_reinspecao_row,
        dedupe_key_hash,
        partition_rows_for_persist,
    )

    import_rows, skipped_existing, skipped_finalized_ged = partition_rows_for_persist(
        rows,
        contexto=contexto,
        skip_finalized_ged=skip_finalized_ged,
    )

    from apps.auditoria.services.reinspecao_ocorrencias import (
        link_reinspecao_occurrence,
        reserve_reinspecao_occurrence,
    )

    pendentes = []
    occurrence_reservations = []
    skipped_ledger = 0
    mapping_applied_at = timezone.now()
    for row in import_rows:
        protocolo = (row.protocolo or "").strip()
        dedupe_key = dedupe_key_for_reinspecao_row(row, contexto=contexto)
        reservation = reserve_reinspecao_occurrence(
            contexto=contexto,
            protocolo=protocolo,
            descricao_irregularidades=row.descricao_irregularidades,
            data_contestacao=row.data_contestacao,
            source="cadastro_manual",
            source_file=source_file,
            source_hash=str((staging.preview or {}).get("file_sha256") or ""),
            observed_at=mapping_applied_at,
        )
        if not reservation.acquired:
            skipped_ledger += 1
            continue
        pendentes.append(
            QualidadePendenteReinspecao(
                protocolo=protocolo,
                usuario=row.matricula_inspetor,
                descricao_irregularidades=row.descricao_irregularidades,
                data_contestacao=row.data_contestacao,
                data_analise=row.data_analise,
                codigo_irregularidade=row.codigo_irregularidade,
                motivo_falha=row.cenario_mapeado,
                etapa_falha=row.etapa_mapeada,
                mapping_scenario_status=row.mapping_scenario_status,
                mapping_stage_status=row.mapping_stage_status,
                mapping_version=row.mapping_version,
                mapping_source_hash=row.mapping_source_hash,
                mapping_applied_at=mapping_applied_at,
                tipo_falha=(
                    "auditoria"
                    if contexto == "auditoria_compliance"
                    else TIPO_FALHA_REINSPECAO
                ),
                contexto=contexto,
                brflow_parsed={
                    "source_file": source_file,
                    "excel_row": row.excel_row,
                    "fila_contexto": contexto,
                    "import_dedupe_key": dedupe_key_hash(dedupe_key),
                    "reinspecao_mapping": {
                        "classification": row.classificacao_irregularidade,
                        "code": row.codigo_irregularidade,
                        "scenario_status": row.mapping_scenario_status,
                        "stage_status": row.mapping_stage_status,
                        "version": row.mapping_version,
                        "source_hash": row.mapping_source_hash,
                        "candidates": row.mapping_candidates,
                    },
                    **(
                        {"nome_inspetor": row.nome_inspetor}
                        if (row.nome_inspetor or "").strip()
                        else {}
                    ),
                },
                cliente="",
                modulo="",
                status="",
                observacao="",
                auditor="",
                created_by=user,
            )
        )
        occurrence_reservations.append(reservation)

    if not pendentes:
        raise ValueError(
            "Nenhum protocolo novo para importar "
            f"({skipped_existing} já existente(s), {skipped_finalized_ged} finalizado(s) no GED)."
        )

    created = QualidadePendenteReinspecao.objects.bulk_create(pendentes)
    for reservation, pendente in zip(occurrence_reservations, created, strict=True):
        link_reinspecao_occurrence(reservation.ocorrencia, pendente=pendente)

    from apps.auditoria.services.compliance_import_archive import archive_compliance_import_file

    archive_compliance_import_file(
        staging=staging,
        user=user,
        contexto=contexto,
        protocolos_importados=len(created),
    )

    staging.delete()
    from apps.auditoria.services.reinspecao_fila import distribuir_protocolos

    distribution = distribuir_protocolos(actor=user)
    results = []
    if created and getattr(created[0], "pk", None):
        from apps.auditoria.services.reinspecao_fila import serialize_pendente_reinspecao

        results = [serialize_pendente_reinspecao(item) for item in created]
    return {
        "created": len(created),
        "skipped_existing": skipped_existing,
        "skipped_finalized_ged": skipped_finalized_ged,
        "skipped_ledger": skipped_ledger,
        "source_file": source_file,
        "results": results,
        "distribution": distribution,
    }


def list_reinspecao_falhas(
    *,
    protocolo: str = "",
    auditor_id=None,
    atribuicao: str = "",
    analise_status: str = "",
    andamento: str = "",
    page: int = 1,
    page_size: int = 50,
    column_filters: dict | None = None,
    sort_key: str = "",
    sort_dir: str = "asc",
    filter_column: str = "",
) -> dict:
    from apps.auditoria.services.reinspecao_fila import list_reinspecao_falhas_enriched

    return list_reinspecao_falhas_enriched(
        protocolo=protocolo,
        auditor_id=auditor_id,
        atribuicao=atribuicao,
        analise_status=analise_status,
        andamento=andamento,
        page=page,
        page_size=page_size,
        column_filters=column_filters,
        sort_key=sort_key,
        sort_dir=sort_dir,
        filter_column=filter_column,
    )


def update_reinspecao_falha(falha: QualidadePendenteReinspecao, payload: dict) -> QualidadePendenteReinspecao:
    if is_falha_realizada(falha):
        raise ValueError("Protocolo já respondido — alteração não permitida.")
    if "cliente" in payload:
        falha.cliente = str(payload.get("cliente") or "").strip()
    if "modulo" in payload:
        falha.modulo = str(payload.get("modulo") or "").strip()
    if "status" in payload:
        falha.status = str(payload.get("status") or "").strip()
    if "observacao" in payload:
        falha.observacao = str(payload.get("observacao") or "").strip()
    if "auditor" in payload:
        falha.auditor = str(payload.get("auditor") or "").strip()
    if "data_resposta" in payload:
        falha.data_resposta = parse_datetime_cell(payload.get("data_resposta"))
    falha.save()
    return falha
