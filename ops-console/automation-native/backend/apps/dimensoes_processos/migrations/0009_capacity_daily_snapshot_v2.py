import hashlib
import json

from django.db import migrations, models


def populate_snapshot_identity(apps, schema_editor):
    snapshot_model = apps.get_model("dimensoes_processos", "CapacityDailySnapshot")
    pending = []
    for snapshot in snapshot_model.objects.all().iterator(chunk_size=500):
        payload = snapshot.payload or {}
        metric_version = (payload.get("series") or {}).get("metric_version") or "legacy"
        canonical_payload = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        snapshot.scenario_id = "planejamento"
        snapshot.metric_version = str(metric_version)[:64]
        snapshot.source_fingerprint = hashlib.sha256(
            canonical_payload.encode("utf-8")
        ).hexdigest()
        pending.append(snapshot)
        if len(pending) == 500:
            snapshot_model.objects.bulk_update(
                pending,
                ["scenario_id", "metric_version", "source_fingerprint"],
            )
            pending.clear()
    if pending:
        snapshot_model.objects.bulk_update(
            pending,
            ["scenario_id", "metric_version", "source_fingerprint"],
        )


class Migration(migrations.Migration):
    dependencies = [("dimensoes_processos", "0008_capacity_hourly_profile_snapshot")]

    operations = [
        migrations.AlterField(
            model_name="capacitydailysnapshot",
            name="calculation_date",
            field=models.DateField(),
        ),
        migrations.AddField(
            model_name="capacitydailysnapshot",
            name="scenario_id",
            field=models.CharField(
                choices=[
                    ("planejamento", "Planejamento"),
                    ("operacao", "Operacao"),
                    ("manual", "Simulacao"),
                ],
                default="planejamento",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="capacitydailysnapshot",
            name="metric_version",
            field=models.CharField(default="legacy", max_length=64),
        ),
        migrations.AddField(
            model_name="capacitydailysnapshot",
            name="source_fingerprint",
            field=models.CharField(default="legacy-unversioned", max_length=64),
        ),
        migrations.RunPython(populate_snapshot_identity, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="capacitydailysnapshot",
            constraint=models.UniqueConstraint(
                fields=(
                    "calculation_date",
                    "scenario_id",
                    "metric_version",
                    "source_fingerprint",
                ),
                name="uniq_capacity_snapshot_identity",
            ),
        ),
        migrations.AlterModelOptions(
            name="capacitydailysnapshot",
            options={
                "ordering": ["calculation_date", "scenario_id", "metric_version"]
            },
        ),
    ]
