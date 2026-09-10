# -*- coding: utf-8 -*-
"""Alinha o estado de migrations do singleton retroativo ao PK fixo (pk=1).

A versão anterior desta migration alterava o PK para BigAutoField; foi revertida
no código, mas ambientes que já a aplicaram precisam do arquivo no grafo e de
0018 para reconciliar o tipo da coluna no banco.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0016_fallback_parquet_dias_ausentes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
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
            ],
        ),
    ]
