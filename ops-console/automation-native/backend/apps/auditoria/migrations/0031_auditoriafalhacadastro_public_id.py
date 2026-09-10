from __future__ import annotations

from django.db import migrations, models
from django.utils import timezone


def _format_public_id(pk: int, created_at) -> str:
    if not created_at:
        return str(pk)
    local = timezone.localtime(created_at)
    return f"{local.day:02d}{local.month:02d}{local.hour:02d}-{pk}"


def backfill_public_ids(apps, schema_editor):
    Falha = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    batch: list = []
    for falha in Falha.objects.all().only("id", "created_at", "public_id").iterator(chunk_size=500):
        expected = _format_public_id(falha.id, falha.created_at)
        if falha.public_id == expected:
            continue
        falha.public_id = expected
        batch.append(falha)
        if len(batch) >= 500:
            Falha.objects.bulk_update(batch, ["public_id"])
            batch.clear()
    if batch:
        Falha.objects.bulk_update(batch, ["public_id"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0030_fila_origem_timeout"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="ID compartilhável no formato DDMMHH-{id}, igual às atividades.",
                max_length=32,
            ),
        ),
        migrations.RunPython(backfill_public_ids, noop_reverse),
    ]
