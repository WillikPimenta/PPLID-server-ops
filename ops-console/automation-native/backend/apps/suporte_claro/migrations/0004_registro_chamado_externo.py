# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_claro", "0003_historico"),
    ]

    operations = [
        migrations.AddField(
            model_name="suporteclaroregistro",
            name="chamado_sistema",
            field=models.CharField(
                blank=True,
                choices=[("jira", "Jira"), ("service", "ServiceNow")],
                db_index=True,
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="suporteclaroregistro",
            name="chamado_codigo",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="suporteclaroregistro",
            name="chamado_url",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
