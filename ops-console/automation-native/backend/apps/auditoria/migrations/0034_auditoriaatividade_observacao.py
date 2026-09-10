from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0033_falha_public_id_prefix"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividade",
            name="observacao",
            field=models.TextField(blank=True, default=""),
        ),
    ]
