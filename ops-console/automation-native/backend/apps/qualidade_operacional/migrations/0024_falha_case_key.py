# Generated manually for case_key on qualidade_falha.

from django.db import migrations, models


def backfill_falha_case_keys(apps, schema_editor):
    QualidadeFalha = apps.get_model("qualidade_operacional", "QualidadeFalha")
    from apps.qualidade_operacional.services.case_key import build_case_key_str

    batch: list = []
    for falha in QualidadeFalha.objects.all().iterator(chunk_size=500):
        key = build_case_key_str(falha.protocolo, falha.matricula)
        if falha.case_key == key:
            continue
        falha.case_key = key
        batch.append(falha)
        if len(batch) >= 500:
            QualidadeFalha.objects.bulk_update(batch, ["case_key"])
            batch.clear()
    if batch:
        QualidadeFalha.objects.bulk_update(batch, ["case_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("qualidade_operacional", "0023_localidade_documento"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadefalha",
            name="case_key",
            field=models.CharField(blank=True, db_index=True, default="", max_length=180),
        ),
        migrations.RunPython(backfill_falha_case_keys, migrations.RunPython.noop),
    ]
