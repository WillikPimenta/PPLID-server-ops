# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qualidade_operacional", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadeauditado",
            name="tipo_conclusao",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddIndex(
            model_name="qualidadeauditado",
            index=models.Index(fields=["tipo_conclusao"], name="qo_aud_tipo_conc_idx"),
        ),
    ]
