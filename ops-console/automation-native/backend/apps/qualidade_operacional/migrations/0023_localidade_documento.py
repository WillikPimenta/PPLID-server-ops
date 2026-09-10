# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qualidade_operacional", "0022_remove_deadline_filter"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadeauditado",
            name="localidade_documento",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="qualidadefalha",
            name="localidade_documento",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddIndex(
            model_name="qualidadeauditado",
            index=models.Index(
                fields=["localidade_documento"],
                name="qo_aud_loc_doc_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="qualidadefalha",
            index=models.Index(
                fields=["localidade_documento"],
                name="qo_fal_loc_doc_idx",
            ),
        ),
    ]
