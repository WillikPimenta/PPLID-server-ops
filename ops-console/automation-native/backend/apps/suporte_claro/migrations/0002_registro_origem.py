# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_claro", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="suporteclaroregistro",
            name="origem",
            field=models.CharField(
                blank=True,
                choices=[
                    ("teams", "Teams"),
                    ("email", "E-mail"),
                    ("ligacao", "Ligação"),
                ],
                db_index=True,
                default="",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="suporteclaroregistro",
            name="status",
            field=models.CharField(
                choices=[
                    ("aberto", "Não iniciado"),
                    ("em_atendimento", "Em andamento"),
                    ("concluido", "Concluído"),
                ],
                db_index=True,
                default="aberto",
                max_length=32,
            ),
        ),
    ]
