from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.auditoria.constants import ETAPA_FALHA_AUTOMATICO
from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaFalha,
)
from apps.auditoria.services.atividade_brflow import seed_falha_defaults_from_atividade
from apps.auditoria.services.atividade_crud import ensure_atividade_mutavel
from apps.auditoria.services.atividade_protocolo_crud import sync_atividade_protocolo_metrics
from apps.auditoria.services.catalog_items import get_etapa_falha_automatico, get_valid_tipo_falha_values
from apps.auditoria.services.text_format import (
    TIPO_FALHA_AUTOMATICO,
    format_auditoria_label,
    is_tipo_falha_automatico,
)
from apps.auditoria.services.validation import (
    normalize_tipo_registro,
    validate_falha_finalize_record,
    validate_falha_payload,
)


def ensure_trilha_analise_preenchida(atividade: AuditoriaAtividade) -> None:
    parsed = atividade.brflow_parsed if isinstance(atividade.brflow_parsed, dict) else {}
    if not str(parsed.get("trilha_raw") or "").strip():
        raise ValueError("Preencha a Trilha de Análise antes de salvar ou finalizar a análise.")


def sync_atividade_falhas_metrics(atividade: AuditoriaAtividade) -> AuditoriaAtividade:
    if atividade.protocolos.exists():
        return sync_atividade_protocolo_metrics(atividade)

    total = atividade.falhas.count()
    atividade.total_protocolos = total
    if total > 0 and atividade.status == AuditoriaAtividade.STATUS_PENDENTE:
        atividade.status = AuditoriaAtividade.STATUS_EM_ANDAMENTO
    atividade.save(update_fields=["total_protocolos", "status", "updated_at"])
    return atividade


def count_falhas_pendentes(atividade: AuditoriaAtividade) -> int:
    """Para auditoria, pendente = atividade sem nenhuma falha cadastrada."""
    if atividade.tipo != AuditoriaAtividade.TIPO_AUDITORIA:
        return 0
    total = atividade.falhas.count()
    return 0 if total > 0 else 1


def _resolve_tipo_falha_catalog(preferred_label: str) -> str:
    label = format_auditoria_label(preferred_label)
    for value in get_valid_tipo_falha_values():
        if format_auditoria_label(value) == label:
            return value
    return label


def ensure_falhas_from_trilha(
    user,
    atividade: AuditoriaAtividade,
    *,
    extracted_users: list[str] | None = None,
    etapas_por_usuario: dict[str, str] | None = None,
    trilha_hits: list[dict[str, str]] | None = None,
) -> list[QualidadePendenteAuditoriaFalha]:
    """Garante falha por usuário da trilha + etapa Automático.

    Não sobrescreve falhas existentes — só cria as que faltam.
    """
    ensure_atividade_mutavel(atividade)
    defaults = seed_falha_defaults_from_atividade(atividade)
    protocolo = str(defaults.get("protocolo") or "").strip()
    tipo_colaborador = _resolve_tipo_falha_catalog("Colaborador")
    tipo_automatico = _resolve_tipo_falha_catalog(TIPO_FALHA_AUTOMATICO)
    etapa_auto = format_auditoria_label(get_etapa_falha_automatico() or ETAPA_FALHA_AUTOMATICO)
    etapas = {str(k).strip(): str(v or "").strip() for k, v in (etapas_por_usuario or {}).items()}
    etapas_cf = {k.casefold(): v for k, v in etapas.items() if k}

    existing = list(atividade.falhas.all())
    rows_present = {
        (
            (item.usuario or "").strip().casefold(),
            format_auditoria_label(item.etapa_falha or "").casefold(),
        )
        for item in existing
        if (item.usuario or "").strip()
    }
    users_present = {usuario for usuario, _ in rows_present}
    has_automatico = any(is_tipo_falha_automatico(item.tipo_falha) for item in existing)

    created: list[QualidadePendenteAuditoriaFalha] = []
    source_hits = trilha_hits or [
        {
            "usuario": str(raw_user or "").strip(),
            "etapa_falha": etapas.get(str(raw_user or "").strip())
            or etapas_cf.get(str(raw_user or "").strip().casefold())
            or "",
            "tempo_analise": "",
        }
        for raw_user in extracted_users or []
    ]
    for hit in source_hits:
        usuario = str(hit.get("usuario") or "").strip()
        if not usuario:
            continue
        etapa = str(hit.get("etapa_falha") or "").strip()
        key = (usuario.casefold(), format_auditoria_label(etapa).casefold())
        if key in rows_present or (not etapa and usuario.casefold() in users_present):
            continue
        record = QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            protocolo=protocolo,
            brflow_raw=(defaults.get("brflow_raw") or atividade.brflow_raw or "").strip(),
            brflow_parsed=defaults.get("brflow_parsed")
            if isinstance(defaults.get("brflow_parsed"), dict)
            else {},
            modulo="",
            demanda_url=str(defaults.get("demanda_url") or atividade.link_demanda or "").strip(),
            tipo_falha=tipo_colaborador,
            usuario=usuario,
            resultado_cliente=str(defaults.get("resultado_cliente") or "").strip(),
            novo_resultado="",
            sinalizacao="",
            motivo_falha="",
            etapa_falha=etapa,
            tempo_analise=str(hit.get("tempo_analise") or "").strip(),
            nivel_dificuldade="",
            tipo_documento="",
            uf_documento="",
            qualidade_imagem="",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            created_by=user,
        )
        rows_present.add(key)
        users_present.add(usuario.casefold())
        created.append(record)

    if not has_automatico:
        record = QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            protocolo=protocolo,
            brflow_raw=(defaults.get("brflow_raw") or atividade.brflow_raw or "").strip(),
            brflow_parsed=defaults.get("brflow_parsed")
            if isinstance(defaults.get("brflow_parsed"), dict)
            else {},
            modulo="",
            demanda_url=str(defaults.get("demanda_url") or atividade.link_demanda or "").strip(),
            tipo_falha=tipo_automatico,
            usuario="",
            resultado_cliente=str(defaults.get("resultado_cliente") or "").strip(),
            novo_resultado="",
            sinalizacao="",
            motivo_falha="",
            etapa_falha=etapa_auto,
            tempo_analise="",
            nivel_dificuldade="",
            tipo_documento="",
            uf_documento="",
            qualidade_imagem="",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            created_by=user,
        )
        created.append(record)

    if created:
        sync_atividade_falhas_metrics(atividade)
    return created


@transaction.atomic
def update_atividade_trilha_brflow(
    atividade: AuditoriaAtividade,
    *,
    trilha_raw: str,
    extracted_users: list[str] | None = None,
    user=None,
    etapas_por_usuario: dict[str, str] | None = None,
    trilha_hits: list[dict[str, str]] | None = None,
) -> AuditoriaAtividade:
    ensure_atividade_mutavel(atividade)
    parsed = dict(atividade.brflow_parsed) if isinstance(atividade.brflow_parsed, dict) else {}
    parsed["trilha_raw"] = (trilha_raw or "").strip()
    if extracted_users is not None:
        parsed["extracted_users"] = [
            str(item or "").strip() for item in extracted_users if str(item or "").strip()
        ]
    atividade.brflow_parsed = parsed
    if atividade.status == AuditoriaAtividade.STATUS_PENDENTE:
        atividade.status = AuditoriaAtividade.STATUS_EM_ANDAMENTO
        atividade.save(update_fields=["brflow_parsed", "status", "updated_at"])
    else:
        atividade.save(update_fields=["brflow_parsed", "updated_at"])

    if user is not None and (extracted_users is not None or trilha_hits is not None):
        ensure_falhas_from_trilha(
            user,
            atividade,
            extracted_users=extracted_users,
            etapas_por_usuario=etapas_por_usuario,
            trilha_hits=trilha_hits,
        )
    return atividade


@transaction.atomic
def finalizar_atividade_auditoria(
    atividade: AuditoriaAtividade,
    *,
    finalizador,
) -> AuditoriaAtividade:
    if atividade.tipo != AuditoriaAtividade.TIPO_AUDITORIA:
        raise ValueError("Somente atividades de auditoria podem ser finalizadas por este fluxo.")
    ensure_trilha_analise_preenchida(atividade)
    if not atividade.falhas.exists():
        raise ValueError("Cadastre ao menos uma falha antes de finalizar a análise.")

    invalid = []
    for record in atividade.falhas.order_by("id"):
        errors = validate_falha_finalize_record(record)
        if errors:
            invalid.append(f"Falha {record.pk}: {', '.join(errors.values())}")
    if invalid:
        raise ValueError("Não foi possível finalizar. " + " ".join(invalid))

    from apps.auditoria.services.qualidade_promocao import selar_tratados_auditoria

    atividade.status = AuditoriaAtividade.STATUS_CONCLUIDA
    if atividade.encerrado_em is None:
        atividade.encerrado_em = timezone.now()
    atividade.save(update_fields=["status", "encerrado_em", "updated_at"])

    now = timezone.now()
    atividade.protocolos.exclude(
        status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
    ).update(
        status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
        finalizado_em=now,
        updated_at=now,
    )

    promovidos = selar_tratados_auditoria(atividade, finalizador=finalizador)
    atividade._promovidos_count = len(promovidos)
    return atividade


@transaction.atomic
def create_falha_for_atividade(
    user,
    atividade: AuditoriaAtividade,
    payload: dict,
) -> QualidadePendenteAuditoriaFalha:
    ensure_atividade_mutavel(atividade)
    ensure_trilha_analise_preenchida(atividade)
    errors = validate_falha_payload(payload)
    if errors:
        raise ValueError(errors)

    brflow_parsed = payload.get("brflow_parsed")
    if not isinstance(brflow_parsed, dict):
        brflow_parsed = atividade.brflow_parsed if isinstance(atividade.brflow_parsed, dict) else {}

    protocolo_num = (payload.get("protocolo") or "").strip()
    record = QualidadePendenteAuditoriaFalha.objects.create(
        atividade=atividade,
        protocolo=protocolo_num,
        brflow_raw=(payload.get("brflow_raw") or atividade.brflow_raw or "").strip(),
        brflow_parsed=brflow_parsed,
        modulo=(payload.get("modulo") or "").strip(),
        demanda_url=(payload.get("demanda_url") or atividade.link_demanda or "").strip(),
        tipo_falha=(payload.get("tipo_falha") or "").strip(),
        usuario=(payload.get("usuario") or "").strip(),
        resultado_cliente=(payload.get("resultado_cliente") or "").strip(),
        novo_resultado=(payload.get("novo_resultado") or "").strip(),
        sinalizacao=(payload.get("sinalizacao") or "").strip(),
        motivo_falha=(payload.get("motivo_falha") or "").strip(),
        etapa_falha=(payload.get("etapa_falha") or "").strip(),
        tempo_analise=(payload.get("tempo_analise") or "").strip(),
        nivel_dificuldade=(payload.get("nivel_dificuldade") or "").strip(),
        tipo_documento=(payload.get("tipo_documento") or "").strip(),
        uf_documento=(payload.get("uf_documento") or "").strip(),
        qualidade_imagem=(payload.get("qualidade_imagem") or "").strip(),
        observacao=(payload.get("observacao") or "").strip(),
        tipo_registro=normalize_tipo_registro(
            payload.get("tipo_registro") or AuditoriaFalhaCadastro.REGISTRO_AUDITORIA
        ),
        created_by=user,
    )

    if protocolo_num:
        proto = atividade.protocolos.filter(protocolo=protocolo_num).first()
        if proto and proto.status != AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO:
            proto.status = AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
            proto.analisado_por = user
            proto.analisado_em = timezone.now()
            proto.finalizado_em = timezone.now()
            proto.save(
                update_fields=[
                    "status",
                    "analisado_por",
                    "analisado_em",
                    "finalizado_em",
                    "updated_at",
                ]
            )

    sync_atividade_falhas_metrics(atividade)
    return record


def _apply_falha_fields(
    record: QualidadePendenteAuditoriaFalha,
    atividade: AuditoriaAtividade,
    payload: dict,
) -> None:
    brflow_parsed = payload.get("brflow_parsed")
    if not isinstance(brflow_parsed, dict):
        brflow_parsed = atividade.brflow_parsed if isinstance(atividade.brflow_parsed, dict) else {}

    record.protocolo = (payload.get("protocolo") or "").strip()
    record.brflow_raw = (payload.get("brflow_raw") or atividade.brflow_raw or "").strip()
    record.brflow_parsed = brflow_parsed
    record.modulo = (payload.get("modulo") or "").strip()
    record.demanda_url = (payload.get("demanda_url") or atividade.link_demanda or "").strip()
    record.tipo_falha = (payload.get("tipo_falha") or "").strip()
    record.usuario = (payload.get("usuario") or "").strip()
    record.resultado_cliente = (payload.get("resultado_cliente") or "").strip()
    record.novo_resultado = (payload.get("novo_resultado") or "").strip()
    record.sinalizacao = (payload.get("sinalizacao") or "").strip()
    record.motivo_falha = (payload.get("motivo_falha") or "").strip()
    record.etapa_falha = (payload.get("etapa_falha") or "").strip()
    record.tempo_analise = (payload.get("tempo_analise") or "").strip()
    record.nivel_dificuldade = (payload.get("nivel_dificuldade") or "").strip()
    record.tipo_documento = (payload.get("tipo_documento") or "").strip()
    record.uf_documento = (payload.get("uf_documento") or "").strip()
    record.qualidade_imagem = (payload.get("qualidade_imagem") or "").strip()
    record.observacao = (payload.get("observacao") or "").strip()
    record.tipo_registro = normalize_tipo_registro(
        payload.get("tipo_registro") or AuditoriaFalhaCadastro.REGISTRO_AUDITORIA
    )


@transaction.atomic
def update_falha_for_atividade(
    atividade: AuditoriaAtividade,
    falha_id: int,
    payload: dict,
) -> QualidadePendenteAuditoriaFalha | None:
    ensure_atividade_mutavel(atividade)
    record = atividade.falhas.filter(pk=falha_id).first()
    if not record:
        return None
    ensure_trilha_analise_preenchida(atividade)

    errors = validate_falha_payload(payload)
    if errors:
        raise ValueError(errors)

    _apply_falha_fields(record, atividade, payload)
    record.save()
    sync_atividade_falhas_metrics(atividade)
    return record


@transaction.atomic
def delete_falha_for_atividade(atividade: AuditoriaAtividade, falha_id: int) -> bool:
    ensure_atividade_mutavel(atividade)
    deleted, _ = atividade.falhas.filter(pk=falha_id).delete()
    if deleted:
        sync_atividade_falhas_metrics(atividade)
        return True
    return False


@transaction.atomic
def save_falhas_batch(
    user,
    atividade: AuditoriaAtividade,
    payloads: list[dict],
    *,
    finalizar: bool = False,
) -> tuple[AuditoriaAtividade, list[QualidadePendenteAuditoriaFalha]]:
    """Salva a grade inteira e, opcionalmente, finaliza em uma unica transacao."""
    atividade = AuditoriaAtividade.objects.select_for_update().get(pk=atividade.pk)
    saved: list[QualidadePendenteAuditoriaFalha] = []
    for raw_payload in payloads:
        payload = dict(raw_payload)
        falha_id = payload.pop("id", None)
        if falha_id:
            record = update_falha_for_atividade(atividade, int(falha_id), payload)
            if record is None:
                raise ValueError(f"Falha {falha_id} nao encontrada nesta atividade.")
        else:
            record = create_falha_for_atividade(user, atividade, payload)
        saved.append(record)

    if finalizar:
        atividade = finalizar_atividade_auditoria(atividade, finalizador=user)
        saved = []
    return atividade, saved
