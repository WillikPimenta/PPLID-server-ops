from django.db import migrations, models

from apps.planejamento_demandas.services.status_utils import classify_status_name


def backfill_status_kind(apps, schema_editor):
    JiraDemanda = apps.get_model("planejamento_demandas", "JiraDemanda")
    batch: list = []
    for obj in JiraDemanda.objects.all().iterator(chunk_size=500):
        kind = classify_status_name(obj.status_name)
        if obj.status_kind != kind:
            obj.status_kind = kind
            batch.append(obj)
        if len(batch) >= 500:
            JiraDemanda.objects.bulk_update(batch, ["status_kind"])
            batch.clear()
    if batch:
        JiraDemanda.objects.bulk_update(batch, ["status_kind"])


class Migration(migrations.Migration):
    dependencies = [
        ("planejamento_demandas", "0002_jirademanda_in_team_queue_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="jirademanda",
            name="status_kind",
            field=models.CharField(
                choices=[
                    ("open", "Aberta"),
                    ("done", "Concluída"),
                    ("cancelled", "Cancelada"),
                ],
                db_index=True,
                default="open",
                max_length=16,
            ),
        ),
        migrations.RunPython(backfill_status_kind, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="jirademanda",
            index=models.Index(
                fields=["project_key", "status_kind", "is_open"],
                name="planejamen_project_6a8f0d_idx",
            ),
        ),
    ]
