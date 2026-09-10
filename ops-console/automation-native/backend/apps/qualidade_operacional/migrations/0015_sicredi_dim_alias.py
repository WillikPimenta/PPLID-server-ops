# -*- coding: utf-8 -*-
from django.db import migrations, models


def seed_sicredi_alias(apps, schema_editor):
    QualidadeDimAlias = apps.get_model("qualidade_operacional", "QualidadeDimAlias")
    QualidadeDimAlias.objects.get_or_create(
        kind="cliente",
        alias_key="SICREDI",
        defaults={
            "target_id": 21,
            "evidence": "Nome genérico + WF746 SICREDI 15; Confederação (id 21) — validar com negócio",
            "active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("qualidade_operacional", "0014_qualidade_dim_alias"),
    ]

    operations = [
        migrations.RunPython(seed_sicredi_alias, migrations.RunPython.noop),
    ]
