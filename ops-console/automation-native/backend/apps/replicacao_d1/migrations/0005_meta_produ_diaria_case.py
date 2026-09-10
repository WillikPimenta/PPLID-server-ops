# -*- coding: utf-8 -*-
from django.db import migrations, models


def copiar_meta_brflow_para_case(apps, schema_editor):
    """Ambientes existentes: Case herda a meta única até o usuário separar."""
    ConfigGeral = apps.get_model("replicacao_d1", "ReplicacaoD1ConfigGeral")
    for row in ConfigGeral.objects.all():
        row.meta_produ_diaria_case = row.meta_produ_diaria
        row.save(update_fields=["meta_produ_diaria_case"])


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0004_repair_chaves_e_nomes_legados"),
    ]

    operations = [
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="meta_produ_diaria_case",
            field=models.DecimalField(decimal_places=2, default=300, max_digits=12),
        ),
        migrations.RunPython(copiar_meta_brflow_para_case, migrations.RunPython.noop),
    ]
