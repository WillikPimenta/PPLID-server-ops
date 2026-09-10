from django.db import migrations, models
import json
from pathlib import Path


def seed_motivos_falha(apps, schema_editor):
    AuditoriaMotivoFalha = apps.get_model("auditoria", "AuditoriaMotivoFalha")
    if AuditoriaMotivoFalha.objects.exists():
        return

    seed_path = Path(__file__).resolve().parent.parent / "data" / "motivos_falha_seed.json"
    if not seed_path.is_file():
        return

    rows = json.loads(seed_path.read_text(encoding="utf-8"))
    AuditoriaMotivoFalha.objects.bulk_create(
        [
            AuditoriaMotivoFalha(
                motivo=row["motivo"].strip(),
                criticidade=row["criticidade"].strip(),
                segmentos=row["segmentos"].strip(),
                subsegmento=row["subsegmento"].strip(),
                sort_order=index,
                active=True,
            )
            for index, row in enumerate(rows)
            if row.get("motivo", "").strip()
        ]
    )


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0004_reformat_catalog_labels"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditoriaMotivoFalha",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("motivo", models.CharField(max_length=255, unique=True)),
                ("criticidade", models.CharField(max_length=64)),
                ("segmentos", models.CharField(max_length=128)),
                ("subsegmento", models.CharField(max_length=128)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "auditoria_motivo_falha",
                "ordering": ["sort_order", "motivo"],
            },
        ),
        migrations.RunPython(seed_motivos_falha, migrations.RunPython.noop),
    ]
