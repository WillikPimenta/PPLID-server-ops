# -*- coding: utf-8 -*-
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("dimensoes_processos", "0003_derivacao_etapa_megazord"),
    ]

    operations = [
        migrations.AddField(
            model_name="dimnomealias",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="derivacaoetapaimportrun",
            name="period_from",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="derivacaoetapaimportrun",
            name="period_to",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="derivacaoetapaimportrun",
            name="source_file_filter",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="derivacaoetapaimportrun",
            name="source_manifest",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="derivacaoetapaimportrun",
            name="source_fingerprint",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="derivacaoetapaimportrun",
            name="reviewed_scan",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="import_runs",
                to="dimensoes_processos.derivacaoetapaimportrun",
            ),
        ),
    ]
