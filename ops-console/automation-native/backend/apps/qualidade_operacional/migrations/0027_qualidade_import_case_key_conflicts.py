# Generated manually for case_key conflict preview on import batches.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qualidade_operacional", "0026_auditado_contestacao_reception_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="case_key_conflicts",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="rows_importable",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
