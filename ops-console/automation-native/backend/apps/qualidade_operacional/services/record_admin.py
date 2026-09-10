from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeGAuditoriaProjection,
    QualidadeIntranetProjection,
    QualidadeRegistroEstado,
    QualidadeRegistroHistorico,
)
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.qualidade_operacional.services.source_config import (
    G_AUDITORIA_SOURCE_FILE,
    INTRANET_SOURCE_FILE,
)

PREVIEW_SALT = "qualidade.record-admin.preview.v1"
PREVIEW_MAX_AGE_SECONDS = 600
VALID_KINDS = {"auditado", "falha"}
VALID_ACTIONS = {"update", "suppress", "restore"}

EDITABLE_FIELDS = {
    "auditado": {
        "id_cliente", "id_workflow", "tipo_analise",
        "matricula_auditor", "cenario", "etapa", "status",
        "irregularidades_apontadas", "tipo_conclusao", "localidade_documento",
    },
    "falha": {
        "id_cliente", "id_workflow", "tipo_analise", "modulo", "cenario",
        "usuario_auditor", "etapa", "tipo_falha",
        "uf", "tipo_documento", "nivel_dificuldade", "des_problemas", "tendencia",
        "novo_resultado", "categoria_falha", "localidade", "localidade_documento",
        "lider", "tipo_falha_oficial", "nivel_dificuldade_confer", "sub_segmento",
        "segmento", "condicao_metrica",
    },
}

FIELD_LABELS = {
    "id_cliente": "ID cliente", "id_workflow": "ID workflow", "tipo_analise": "Tipo de análise",
    "matricula_auditor": "Matrícula do auditor", "usuario_auditor": "Auditor",
    "data_analise": "Data da análise", "tipo_falha": "Tipo de falha",
    "tipo_conclusao": "Tipo de conclusão", "localidade_documento": "UF do documento",
}


def model_for_kind(kind: str):
    if kind == "auditado":
        return QualidadeAuditado
    if kind == "falha":
        return QualidadeFalha
    raise ValidationError({"kind": "Tipo de registro inválido."})


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def record_snapshot(instance, kind: str) -> dict[str, Any]:
    return {
        name: _jsonable(getattr(instance, name))
        for name in sorted(EDITABLE_FIELDS[kind])
    }


def identity_for(instance, kind: str) -> str:
    if instance.admin_identity:
        return instance.admin_identity
    source_file = str(instance.source_file or "")
    if source_file == INTRANET_SOURCE_FILE:
        lookup = {"auditado": "auditado_id", "falha": "falha_id"}[kind]
        projection = QualidadeIntranetProjection.objects.filter(**{lookup: instance.pk}).first()
        if projection:
            return f"intranet:{projection.source_id}:{kind}"
    if kind == "auditado" and source_file == G_AUDITORIA_SOURCE_FILE:
        projection = QualidadeGAuditoriaProjection.objects.filter(auditado_id=instance.pk).first()
        if projection:
            return f"g-auditoria:{projection.staging_id}:auditado"
    # TSV não possui chave externa universal. O digest sem nome do arquivo é estável
    # entre reimportações idênticas e, conservadoramente, agrupa duplicatas idênticas.
    identity_payload = {
        field.name: _jsonable(getattr(instance, field.name))
        for field in instance._meta.concrete_fields
        if field.name not in {
            "id", "source_file", "imported_at", "admin_identity",
            "admin_suppressed", "admin_revision",
        }
    }
    raw = json.dumps(identity_payload, sort_keys=True, ensure_ascii=False, default=str)
    return f"tsv:{kind}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def source_kind(instance) -> str:
    if instance.source_file == INTRANET_SOURCE_FILE:
        return "intranet"
    if instance.source_file == G_AUDITORIA_SOURCE_FILE:
        return "g_auditoria"
    return "tsv"


def apply_state_to_instance(instance, kind: str, *, state: QualidadeRegistroEstado | None = None):
    identity = identity_for(instance, kind)
    instance.admin_identity = identity
    if state is None:
        state = QualidadeRegistroEstado.objects.filter(kind=kind, identity_key=identity).first()
    if state:
        for key, value in (state.overrides or {}).items():
            if key not in EDITABLE_FIELDS[kind]:
                continue
            field = instance._meta.get_field(key)
            setattr(instance, key, field.to_python(value))
        instance.admin_suppressed = state.suppressed
        instance.admin_revision = state.revision
    return instance


def apply_states_to_instances(instances: Iterable, kind: str) -> list:
    rows = list(instances)
    by_identity: dict[str, list] = {}
    for row in rows:
        identity = identity_for(row, kind)
        row.admin_identity = identity
        by_identity.setdefault(identity, []).append(row)
    states = {
        state.identity_key: state
        for state in QualidadeRegistroEstado.objects.filter(
            kind=kind, identity_key__in=list(by_identity)
        )
    }
    for identity, matches in by_identity.items():
        for row in matches:
            apply_state_to_instance(row, kind, state=states.get(identity))
    return rows


def apply_state_to_fields(fields: dict[str, Any], kind: str) -> dict[str, Any]:
    """Reaplica o ledger antes de um bulk_create/update de projeção."""
    instance = model_for_kind(kind)(**fields)
    apply_state_to_instance(instance, kind)
    output = dict(fields)
    for key in EDITABLE_FIELDS[kind]:
        if key in output:
            output[key] = getattr(instance, key)
    output.update(
        admin_identity=instance.admin_identity,
        admin_suppressed=instance.admin_suppressed,
        admin_revision=instance.admin_revision,
    )
    return output


def _state_for(instance, kind: str, *, lock: bool = False) -> QualidadeRegistroEstado | None:
    identity = identity_for(instance, kind)
    qs = QualidadeRegistroEstado.objects
    if lock:
        qs = qs.select_for_update()
    return qs.filter(kind=kind, identity_key=identity).first()


def revision_for(instance, kind: str) -> int:
    state = _state_for(instance, kind)
    return int(state.revision if state else instance.admin_revision or 0)


def _related_failures(auditado: QualidadeAuditado, *, reject_ambiguity: bool = True):
    origin = source_kind(auditado)
    if origin == "intranet":
        projection = QualidadeIntranetProjection.objects.filter(auditado_id=auditado.pk).first()
        if projection and projection.falha_id:
            return QualidadeFalha.all_objects.filter(pk=projection.falha_id)
        return QualidadeFalha.all_objects.none()
    if origin == "g_auditoria":
        g_projection = QualidadeGAuditoriaProjection.objects.filter(auditado_id=auditado.pk).first()
        if not g_projection:
            return QualidadeFalha.all_objects.none()
        from apps.qualidade_operacional.models import QualidadeGAuditoriaFailureReconciliation

        return QualidadeFalha.all_objects.filter(
            g_auditoria_reconciliation__projection_id=g_projection.pk
        )
    if origin != "tsv":
        return QualidadeFalha.all_objects.none()
    # TSV: só uma correspondência inequívoca no mesmo recorte semântico pode
    # sofrer cascata. Protocolo isolado nunca é suficiente.
    if not auditado.protocolo or not auditado.matricula or not auditado.data:
        if reject_ambiguity:
            raise ValidationError(
                {"related_failures": "O auditado TSV não possui protocolo, matrícula e data suficientes para provar a cascata."}
            )
        return QualidadeFalha.all_objects.none()
    qs = QualidadeFalha.all_objects.filter(protocolo=auditado.protocolo)
    qs = qs.filter(
        matricula__iexact=str(auditado.matricula).strip(),
        data=auditado.data,
        origem_tratado=auditado.origem_tratado,
    )
    if reject_ambiguity and qs.count() > 1:
        raise ValidationError(
            {"related_failures": "Mais de uma falha TSV corresponde ao auditado; refine/corrija a origem antes da supressão."}
        )
    return qs


def _identity_peers(instance, kind: str) -> list:
    identity = identity_for(instance, kind)
    if source_kind(instance) != "tsv":
        return [instance]
    model = model_for_kind(kind)
    candidates = model.all_objects.filter(
        protocolo=instance.protocolo,
        matricula__iexact=instance.matricula,
        data=instance.data,
        data_analise=instance.data_analise,
    )
    return [row for row in candidates if identity_for(row, kind) == identity]


def _cascade_failures(instance, state: QualidadeRegistroEstado, action: str):
    if action == "suppress":
        return _related_failures(instance).select_for_update()
    child_ids = QualidadeRegistroEstado.objects.filter(
        kind="falha",
        suppressed=True,
        suppression_parent_key=state.identity_key,
        current_object_id__isnull=False,
    ).values_list("current_object_id", flat=True)
    return QualidadeFalha.all_objects.select_for_update().filter(pk__in=child_ids)


def editable_field_definitions(kind: str) -> list[dict[str, Any]]:
    model = model_for_kind(kind)
    result = []
    for name in sorted(EDITABLE_FIELDS[kind]):
        field = model._meta.get_field(name)
        field_type = "text"
        if field.get_internal_type() in {"DateField", "DateTimeField"}:
            field_type = "date" if field.get_internal_type() == "DateField" else "datetime"
        elif field.get_internal_type() in {"IntegerField", "BigIntegerField"}:
            field_type = "number"
        elif field.get_internal_type() == "TextField":
            field_type = "textarea"
        result.append({"key": name, "label": FIELD_LABELS.get(name, name.replace("_", " ").title()), "type": field_type, "editable": True})
    return result


def _summary(instance, kind: str, state: QualidadeRegistroEstado | None = None) -> dict[str, Any]:
    suppressed = state.suppressed if state else bool(instance.admin_suppressed)
    revision = state.revision if state else int(instance.admin_revision or 0)
    effective = apply_state_to_instance(instance, kind, state=state)
    return {
        "kind": kind,
        "id": effective.pk,
        "protocolo": effective.protocolo,
        "matricula": effective.matricula,
        "source_kind": source_kind(effective),
        "source_label": {"tsv": "Arquivo TSV", "intranet": "Portal Onboarding Protection", "g_auditoria": "G Auditoria"}[source_kind(effective)],
        "data_analise": _jsonable(getattr(effective, "data_analise", None)),
        "status": getattr(effective, "status", None) if kind == "auditado" else getattr(effective, "resultado_analise", None),
        "suppressed": suppressed,
        "revision": str(revision),
        "updated_at": _jsonable(getattr(effective, "imported_at", None)),
    }


def serialize_history(item: QualidadeRegistroHistorico) -> dict[str, Any]:
    actor_name = item.actor.get_full_name() or item.actor.get_username()
    return {
        "id": str(item.pk), "action": item.action, "reason": item.reason, "ticket": item.ticket,
        "actor_name": actor_name, "actor_username": item.actor.get_username(),
        "created_at": item.created_at.isoformat(), "changes": item.changes, "status": "completed",
    }


def record_detail(instance, kind: str, *, can_manage: bool) -> dict[str, Any]:
    state = _state_for(instance, kind)
    return {
        "record": _summary(instance, kind, state),
        "revision": str(state.revision if state else instance.admin_revision or 0),
        "current": record_snapshot(apply_state_to_instance(instance, kind, state=state), kind),
        "fields": editable_field_definitions(kind),
        "history": [serialize_history(row) for row in (state.historico.select_related("actor").all() if state else [])],
        "can_manage": can_manage,
        "warnings": (["Identidade TSV por conteúdo: duplicatas integralmente idênticas recebem o mesmo controle."] if source_kind(instance) == "tsv" else []),
    }


def search_records(params) -> dict[str, Any]:
    requested = str(params.get("kind") or "").strip()
    kinds = [requested] if requested in VALID_KINDS else ["auditado", "falha"]
    q = str(params.get("q") or "").strip()
    protocolo = str(params.get("protocolo") or "").strip()
    matricula = str(params.get("matricula") or "").strip()
    if not any((q, protocolo, matricula)):
        raise ValidationError({"filters": "Informe protocolo, matrícula ou busca textual."})
    if max(len(q), len(protocolo), len(matricula)) < 2:
        raise ValidationError({"filters": "O filtro de busca deve possuir ao menos 2 caracteres."})
    source_filter = str(params.get("source_kind") or "").strip()
    state_filter = str(params.get("state") or "").strip()
    try:
        page = max(1, int(params.get("page") or 1)); page_size = min(100, max(1, int(params.get("page_size") or 25)))
    except (TypeError, ValueError):
        page, page_size = 1, 25
    fetch_limit = page * page_size
    rows: list[tuple[str, Any]] = []
    total = 0
    for kind in kinds:
        qs = model_for_kind(kind).all_objects.all()
        if q:
            qs = qs.filter(Q(protocolo__icontains=q) | Q(matricula__icontains=q))
        if protocolo:
            qs = qs.filter(protocolo__icontains=protocolo)
        if matricula:
            qs = qs.filter(matricula__icontains=matricula)
        if state_filter == "active":
            qs = qs.filter(admin_suppressed=False)
        elif state_filter == "suppressed":
            qs = qs.filter(admin_suppressed=True)
        if source_filter == "intranet":
            qs = qs.filter(source_file=INTRANET_SOURCE_FILE)
        elif source_filter == "g_auditoria":
            qs = qs.filter(source_file=G_AUDITORIA_SOURCE_FILE)
        elif source_filter == "tsv":
            qs = qs.exclude(source_file__in=[INTRANET_SOURCE_FILE, G_AUDITORIA_SOURCE_FILE])
        total += qs.count()
        rows.extend((kind, row) for row in qs.order_by("-id")[:fetch_limit])
    rows.sort(key=lambda item: item[1].pk, reverse=True)
    start = (page - 1) * page_size
    selected = rows[start:start + page_size]
    return {"results": [_summary(row, kind, _state_for(row, kind)) for kind, row in selected], "count": total, "page": page, "page_size": page_size, "total_pages": max(1, (total + page_size - 1) // page_size)}


def _normalized_changes(instance, kind: str, changes: Any) -> dict[str, Any]:
    if not isinstance(changes, dict) or not changes:
        raise ValidationError({"changes": "Informe ao menos um campo para editar."})
    unknown = sorted(set(changes) - EDITABLE_FIELDS[kind])
    if unknown:
        raise ValidationError({"changes": f"Campos não permitidos: {', '.join(unknown)}."})
    normalized = {}
    for key, value in changes.items():
        try:
            field = instance._meta.get_field(key)
            normalized[key] = _jsonable(field.clean(value, instance))
        except (TypeError, ValueError, ValidationError) as exc:
            raise ValidationError({"changes": {key: str(exc)}}) from exc
    return normalized


def build_preview(instance, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "")
    if action not in VALID_ACTIONS:
        raise ValidationError({"action": "Ação inválida."})
    reason = str(payload.get("reason") or "").strip(); ticket = str(payload.get("ticket") or "").strip()
    if len(reason) < 10:
        raise ValidationError({"reason": "Informe uma justificativa com ao menos 10 caracteres."})
    if not ticket:
        raise ValidationError({"ticket": "Informe o chamado/ticket da solicitação."})
    if len(ticket) > 128:
        raise ValidationError({"ticket": "O chamado/ticket deve ter no máximo 128 caracteres."})
    revision = revision_for(instance, kind)
    if str(payload.get("expected_revision")) != str(revision):
        raise ValidationError({"expected_revision": "Registro alterado por outra operação. Recarregue."})
    changes = _normalized_changes(instance, kind, payload.get("changes")) if action == "update" else {}
    current_state = _state_for(instance, kind)
    if action == "restore" and current_state and current_state.suppression_parent_key:
        raise ValidationError({"action": "Restaure primeiro o auditado que originou esta supressão."})
    related_count = _related_failures(instance).filter(admin_suppressed=False).count() if kind == "auditado" and action == "suppress" else 0
    identity_peer_count = len(_identity_peers(instance, kind))
    confirmation = f"CONFIRMAR {kind.upper()} {instance.pk}"
    token_payload = {"kind": kind, "id": instance.pk, "action": action, "revision": revision, "changes": changes, "reason": reason, "ticket": ticket, "confirmation": confirmation}
    token = signing.dumps(token_payload, salt=PREVIEW_SALT, compress=True)
    impact = [{"key": "record", "label": "Registro selecionado", "value": f"{kind} #{instance.pk}", "tone": "danger" if action == "suppress" else "info"}]
    if kind == "auditado" and action == "suppress":
        impact.append({"key": "related_failures", "label": "Falhas relacionadas retiradas dos indicadores", "value": related_count, "tone": "danger"})
    if identity_peer_count > 1:
        impact.append({"key": "identity_peers", "label": "Fatos TSV com a mesma identidade materializados", "value": identity_peer_count, "tone": "warning"})
    return {"preview_token": token, "revision": str(revision), "action": action, "impact": impact, "warnings": (["O auditado e suas falhas relacionadas deixarão de compor todos os indicadores e exports."] if related_count else []), "confirmation_phrase": confirmation, "expires_at": (timezone.now() + timedelta(seconds=PREVIEW_MAX_AGE_SECONDS)).isoformat(), "changes": {key: {"before": _jsonable(getattr(instance, key)), "after": value} for key, value in changes.items()}}


def _ensure_state(instance, kind: str, user, *, lock: bool = True) -> QualidadeRegistroEstado:
    identity = identity_for(instance, kind)
    qs = QualidadeRegistroEstado.objects.select_for_update() if lock else QualidadeRegistroEstado.objects
    state = qs.filter(kind=kind, identity_key=identity).first()
    if state is None:
        state = QualidadeRegistroEstado.objects.create(kind=kind, identity_key=identity, current_object_id=instance.pk, updated_by=user)
    return state


def _write_history(state, action, before, after, changes, reason, ticket, user, idem):
    return QualidadeRegistroHistorico.objects.create(estado=state, revision=state.revision, action=action, before=before, after=after, changes=changes, reason=reason, ticket=ticket, actor=user, idempotency_key=idem)


def _materialize(instance, kind: str, state: QualidadeRegistroEstado) -> None:
    apply_state_to_instance(instance, kind, state=state)
    instance.save(update_fields=[*sorted(state.overrides), "admin_identity", "admin_suppressed", "admin_revision", "imported_at"])


@transaction.atomic
def execute_action(instance, kind: str, payload: dict[str, Any], user):
    try:
        idem = uuid.UUID(str(payload.get("idempotency_key") or ""))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError({"idempotency_key": "Chave de idempotência inválida."}) from exc
    existing = QualidadeRegistroHistorico.objects.select_related("estado", "actor").filter(idempotency_key=idem).first()
    if existing:
        if existing.estado.kind != kind or existing.estado.identity_key != identity_for(instance, kind):
            raise ValidationError({"idempotency_key": "Chave já utilizada para outro registro."})
        current = model_for_kind(kind).all_objects.get(pk=instance.pk)
        return current, existing, False
    try:
        signed = signing.loads(str(payload.get("preview_token") or ""), salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE_SECONDS)
    except signing.BadSignature as exc:
        raise ValidationError({"preview_token": "Preview inválido ou expirado."}) from exc
    if signed.get("kind") != kind or int(signed.get("id") or 0) != int(instance.pk):
        raise ValidationError({"preview_token": "O preview pertence a outro registro."})
    normalized_payload_changes = (
        _normalized_changes(instance, kind, payload.get("changes"))
        if signed.get("action") == "update"
        else {}
    )
    for key in ("action", "revision", "changes", "reason", "ticket", "confirmation"):
        expected = payload.get("confirmation") if key == "confirmation" else payload.get("expected_revision") if key == "revision" else payload.get(key)
        if key == "changes":
            expected = normalized_payload_changes
        if signed.get(key) != expected and str(signed.get(key)) != str(expected):
            raise ValidationError({"preview_token": "A confirmação diverge do preview aprovado."})
    model = model_for_kind(kind)
    instance = model.all_objects.select_for_update().get(pk=instance.pk)
    state = _ensure_state(instance, kind, user)
    if str(state.revision) != str(payload.get("expected_revision")):
        raise ValidationError({"expected_revision": "Registro alterado por outra operação. Recarregue."})
    action = signed["action"]; before = record_snapshot(apply_state_to_instance(instance, kind, state=state), kind)
    before["_admin_suppressed"] = state.suppressed
    before["_admin_revision"] = state.revision
    changes = signed.get("changes") or {}
    if action == "update":
        state.overrides = {**(state.overrides or {}), **changes}
    elif action == "suppress":
        if state.suppressed:
            raise ValidationError({"action": "Registro já está suprimido."})
        state.suppressed = True; state.suppression_parent_key = ""
    elif action == "restore":
        if not state.suppressed:
            raise ValidationError({"action": "Registro não está suprimido."})
        if state.suppression_parent_key:
            raise ValidationError({"action": "Restaure primeiro o auditado que originou esta supressão."})
        state.suppressed = False; state.suppression_parent_key = ""
    state.revision += 1; state.current_object_id = instance.pk; state.updated_by = user
    state.save()
    peers = _identity_peers(instance, kind)
    for peer in peers:
        _materialize(peer, kind, state)
    # `_identity_peers` consulta novas instâncias para TSV; mantenha o alvo em
    # memória alinhado ao estado aplicado para que o snapshot DE/PARA seja fiel.
    apply_state_to_instance(instance, kind, state=state)
    if kind == "auditado" and action in {"suppress", "restore"}:
        cascade_effects = []
        for failure in _cascade_failures(instance, state, action):
            child = _ensure_state(failure, "falha", user)
            child_before = record_snapshot(apply_state_to_instance(failure, "falha", state=child), "falha")
            child_before["_admin_suppressed"] = child.suppressed
            child_before["_admin_revision"] = child.revision
            if action == "suppress" and not child.suppressed:
                child.suppressed = True; child.suppression_parent_key = state.identity_key
            elif action == "restore" and child.suppressed and child.suppression_parent_key == state.identity_key:
                child.suppressed = False; child.suppression_parent_key = ""
            else:
                continue
            child.revision += 1; child.current_object_id = failure.pk; child.updated_by = user; child.save()
            for peer in _identity_peers(failure, "falha"):
                _materialize(peer, "falha", child)
            child_after = record_snapshot(failure, "falha")
            child_after["_admin_suppressed"] = child.suppressed
            child_after["_admin_revision"] = child.revision
            child_idem = uuid.uuid5(idem, f"cascade:{action}:{child.identity_key}")
            _write_history(
                child, action, child_before, child_after,
                {"cascade_parent": {"before": child_before.get("_admin_suppressed"), "after": child_after.get("_admin_suppressed"), "parent_identity": state.identity_key}},
                signed["reason"], signed["ticket"], user, child_idem,
            )
            cascade_effects.append({"kind": "falha", "id": failure.pk, "identity_key": child.identity_key, "suppressed": child.suppressed, "revision": child.revision})
    after = record_snapshot(instance, kind)
    after["_admin_suppressed"] = state.suppressed
    after["_admin_revision"] = state.revision
    history_changes = {key: {"before": before.get(key), "after": after.get(key)} for key in set(before) | set(after) if before.get(key) != after.get(key)}
    if kind == "auditado" and action in {"suppress", "restore"}:
        history_changes["cascade_failures"] = {"before": [], "after": cascade_effects}
    history = _write_history(state, action, before, after, history_changes, signed["reason"], signed["ticket"], user, idem)
    transaction.on_commit(bump_quality_cache_version)
    return instance, history, True


def summary(instance, kind: str) -> dict[str, Any]:
    return _summary(instance, kind, _state_for(instance, kind))
