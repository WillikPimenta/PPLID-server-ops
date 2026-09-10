# Generated manually — protocolo_origem + cadastro_origem_at na fila

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("produtividade_case", "0005_case_fact_and_fila_sample"),
    ]

    operations = [
        migrations.AddField(
            model_name="casefilasampleitem",
            name="protocolo_origem",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="casefilasampleitem",
            name="cadastro_origem_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
