from django.db import migrations, models
from django.utils import timezone


def _stamp(created_at) -> str:
    if not created_at:
        return ""
    local = timezone.localtime(created_at)
    return f"{local.day:02d}{local.month:02d}{local.hour:02d}"


def backfill_falha_public_ids(apps, schema_editor):
    Falha = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    batch: list = []
    for falha in Falha.objects.all().only("id", "created_at", "public_id").iterator(chunk_size=500):
        stamp = _stamp(falha.created_at)
        expected = f"F{stamp}-{falha.id}" if stamp else f"F{falha.id}"
        if falha.public_id == expected:
            continue
        falha.public_id = expected
        batch.append(falha)
        if len(batch) >= 500:
            Falha.objects.bulk_update(batch, ["public_id"])
            batch = []
    if batch:
        Falha.objects.bulk_update(batch, ["public_id"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0032_auditoriafalhacadastro_qualidade_imagem"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditoriafalhacadastro",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="ID compartilhável da falha: FDDMMHH-{id} (timezone local).",
                max_length=32,
            ),
        ),
        migrations.RunPython(backfill_falha_public_ids, noop_reverse),
    ]
