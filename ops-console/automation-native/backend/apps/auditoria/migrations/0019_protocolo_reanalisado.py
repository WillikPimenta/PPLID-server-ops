from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0018_atividade_arquivo_optional"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="reanalisado",
            field=models.BooleanField(default=False),
        ),
    ]
