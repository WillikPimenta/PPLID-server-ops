# -*- coding: utf-8 -*-
"""Reconcilia o tipo da coluna id quando a 0017 legada (BigAutoField) chegou a rodar."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0017_alter_replicacaod1retroativoconfig_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="replicacaod1retroativoconfig",
            name="id",
            field=models.PositiveSmallIntegerField(
                default=1,
                editable=False,
                primary_key=True,
                serialize=False,
            ),
        ),
    ]
