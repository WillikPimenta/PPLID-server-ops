# Generated manually — resultado_origem no fact para matriz origem×destino

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("produtividade_case", "0007_fact_cadastro_destino"),
    ]

    operations = [
        migrations.AddField(
            model_name="caseconsolidadofact",
            name="resultado_origem",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
    ]
