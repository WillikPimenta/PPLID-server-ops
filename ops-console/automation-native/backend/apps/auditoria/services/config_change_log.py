from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable

from django.db import transaction

from apps.auditoria.models import (
    AuditoriaCatalogItem,
    AuditoriaMotivoFalha,
    QualidadeConfiguracaoAlteracao,
)


class ConfigChangeUndoError(Exception):
    pass


CONFIG_TARGETS = {
    "auditoria_catalog_item": (
        AuditoriaCatalogItem,
        {"catalog", "value", "label", "sort_order", "active"},
    ),
    "auditoria_motivo_falha": (
        AuditoriaMotivoFalha,
        {"motivo", "criticidade", "segmentos", "subsegmento", "sort_order", "active"},
    ),
}


def snapshot_fields(instance, fields: Iterable[str]) -> dict[str, Any]:
    return {field: getattr(instance, field) for field in fields}


def build_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        field: {"de": before.get(field), "para": value}
        for field, value in after.items()
        if before.get(field) != value
    }


def log_config_change(
    *, instance, changes: dict, user, reverte: QualidadeConfiguracaoAlteracao | None = None
) -> QualidadeConfiguracaoAlteracao | None:
    if not changes:
        return None
    return QualidadeConfiguracaoAlteracao.objects.create(
        tabela_origem=instance._meta.db_table,
        registro_id=str(instance.pk or ""),
        mudancas=changes,
        usuario=user if getattr(user, "is_authenticated", False) else None,
        reverte=reverte,
    )


def _remove_automatic_seed_conflict(*, change, target, field: str, previous_value) -> bool:
    if not isinstance(target, AuditoriaMotivoFalha) or field != "motivo":
        return False
    conflict = (
        AuditoriaMotivoFalha.objects.select_for_update()
        .exclude(pk=target.pk)
        .filter(motivo=previous_value)
        .first()
    )
    if conflict is None:
        return False
    if not (change.criado_em <= conflict.created_at <= change.criado_em + timedelta(seconds=5)):
        return False
    if QualidadeConfiguracaoAlteracao.objects.filter(
        tabela_origem=conflict._meta.db_table,
        registro_id=str(conflict.pk),
    ).exists():
        return False
    comparable_fields = ("criticidade", "segmentos", "subsegmento", "active")
    if any(getattr(conflict, name) != getattr(target, name) for name in comparable_fields):
        return False
    conflict.delete()
    return True


@transaction.atomic
def undo_config_change(*, change_id: int, user) -> QualidadeConfiguracaoAlteracao:
    change = (
        QualidadeConfiguracaoAlteracao.objects.select_for_update()
        .filter(pk=change_id)
        .first()
    )
    if change is None:
        raise ConfigChangeUndoError("Alteracao nao encontrada.")
    if change.reverte_id or change.reversoes.exists():
        raise ConfigChangeUndoError("Esta alteracao nao pode mais ser desfeita.")

    target_config = CONFIG_TARGETS.get(change.tabela_origem)
    if target_config is None:
        raise ConfigChangeUndoError("A tabela de origem nao permite desfazer.")
    model, allowed_fields = target_config
    changes = change.mudancas if isinstance(change.mudancas, dict) else {}
    if not changes or not set(changes).issubset(allowed_fields):
        raise ConfigChangeUndoError("O log possui campos invalidos para reversao.")

    target = model.objects.select_for_update().filter(pk=change.registro_id).first()
    if target is None:
        raise ConfigChangeUndoError("O registro de origem nao existe mais.")

    for field, values in changes.items():
        if not isinstance(values, dict) or "de" not in values or "para" not in values:
            raise ConfigChangeUndoError("O log nao possui um DE/PARA valido.")
        if getattr(target, field) != values["para"]:
            raise ConfigChangeUndoError(
                "O registro recebeu alteracoes posteriores e nao pode ser desfeito com seguranca."
            )
        model_field = model._meta.get_field(field)
        if (
            model_field.unique
            and values["de"] is not None
            and model.objects.exclude(pk=target.pk).filter(**{field: values["de"]}).exists()
        ):
            if not _remove_automatic_seed_conflict(
                change=change,
                target=target,
                field=field,
                previous_value=values["de"],
            ):
                raise ConfigChangeUndoError(
                    f'Nao e possivel desfazer: o valor anterior de "{model_field.verbose_name}" '
                    "ja esta sendo utilizado por outro registro. Desfaca primeiro a alteracao posterior."
                )

    reverse_changes = {
        field: {"de": values["para"], "para": values["de"]}
        for field, values in changes.items()
    }
    is_creation = all(values["de"] is None for values in changes.values())
    if is_creation:
        reverse_log = log_config_change(
            instance=target,
            changes=reverse_changes,
            user=user,
            reverte=change,
        )
        target.delete()
    else:
        for field, values in changes.items():
            setattr(target, field, values["de"])
        target.save(update_fields=[*changes.keys(), "updated_at"])
        reverse_log = log_config_change(
            instance=target,
            changes=reverse_changes,
            user=user,
            reverte=change,
        )

    if reverse_log is None:
        raise ConfigChangeUndoError("Nao foi possivel registrar a reversao.")
    return reverse_log
