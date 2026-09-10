# Generated manually for produtividade_case domain

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0002_bot_db_sync_replicacao_d1_domain"),
    ]

    operations = [
        migrations.AlterField(
            model_name="botdbsyncjob",
            name="domain",
            field=models.CharField(
                choices=[
                    ("produtividade", "Produtividade"),
                    ("monitor_eventos", "Monitor eventos"),
                    ("rotina_bruto", "Rotina bruto"),
                    ("falhas_criticas", "Falhas críticas"),
                    ("replicacao_d1", "Replicação D-1"),
                    ("produtividade_case", "Produtividade Case Manager"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
