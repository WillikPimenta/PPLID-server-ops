# Generated manually — cadastro origem/destino no fact consolidado

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("produtividade_case", "0006_fila_sample_origem_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="caseconsolidadofact",
            name="cadastro_origem_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="caseconsolidadofact",
            name="cadastro_destino_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddIndex(
            model_name="caseconsolidadofact",
            index=models.Index(
                fields=["periodo_mes", "cadastro_destino_at"],
                name="case_manage_periodo_cdest_idx",
            ),
        ),
    ]
