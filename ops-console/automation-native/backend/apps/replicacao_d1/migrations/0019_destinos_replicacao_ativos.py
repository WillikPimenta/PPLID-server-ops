# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0018_workflow_origem_duplicate"),
    ]

    operations = [
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="replicacao_destino_brflow_ativo",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="replicacao_destino_case31_ativo",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="replicacao_destino_bio_ativo",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="replicacao_destino_redoc_ativo",
            field=models.BooleanField(default=True),
        ),
    ]
