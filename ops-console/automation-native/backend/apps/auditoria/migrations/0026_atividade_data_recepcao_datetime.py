from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0025_atividade_tipo_falha_atividade"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditoriaatividade",
            name="data_recepcao",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
