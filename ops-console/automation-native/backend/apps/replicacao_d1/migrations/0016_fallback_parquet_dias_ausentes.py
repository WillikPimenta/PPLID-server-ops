# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0015_retroativo_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="fallback_parquet_dias_ausentes",
            field=models.BooleanField(
                default=False,
                help_text="Com fonte_banco_ativa, usa parquet tratado do dia exato quando a partição Rotina estiver vazia.",
            ),
        ),
    ]
