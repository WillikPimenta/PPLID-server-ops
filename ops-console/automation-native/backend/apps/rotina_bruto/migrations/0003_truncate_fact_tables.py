# -*- coding: utf-8 -*-
from django.db import migrations


def truncate_fact_tables(apps, schema_editor):
    apps.get_model("rotina_bruto", "RotinaDetalhadoBrutoRecord").objects.all().delete()
    apps.get_model("rotina_bruto", "RotinaProdBrutoRecord").objects.all().delete()
    apps.get_model("rotina_bruto", "RotinaMonitorBrutoRecord").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rotina_bruto", "0002_alter_cpf_max_length"),
    ]

    operations = [
        migrations.RunPython(truncate_fact_tables, migrations.RunPython.noop),
    ]
