# -*- coding: utf-8 -*-
"""Relatório pré-remoção de colunas legadas em replicacao_d1_run."""
from django.db import migrations


def report_legacy_run_columns(apps, schema_editor):
    Run = apps.get_model("replicacao_d1", "ReplicacaoD1Run")
    counts = {
        "source_file": Run.objects.exclude(source_file="").count(),
        "config_version": Run.objects.filter(config_version__isnull=False).count(),
        "config_hash": Run.objects.exclude(config_hash="").count(),
        "total_runs": Run.objects.count(),
    }
    print(f"replicacao_d1_run legacy column report: {counts}")


class Migration(migrations.Migration):
    dependencies = [
        ("replicacao_d1", "0007_bot_data_ingestion_and_dashboard_fields"),
    ]

    operations = [
        migrations.RunPython(report_legacy_run_columns, migrations.RunPython.noop),
        migrations.RemoveField(model_name="replicacaod1run", name="source_file"),
        migrations.RemoveField(model_name="replicacaod1run", name="config_version"),
        migrations.RemoveField(model_name="replicacaod1run", name="config_hash"),
    ]
