# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0005_meta_produ_diaria_case"),
    ]

    operations = [
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="usar_amostra_mix_manual_automatico",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="amostra_pct_manual",
            field=models.PositiveSmallIntegerField(default=70),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="amostra_pct_automatico",
            field=models.PositiveSmallIntegerField(default=30),
        ),
    ]
