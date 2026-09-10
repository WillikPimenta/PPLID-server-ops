# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rotina_bruto", "0009_detalhado_alerta_split"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rotinagedirregularidadetratadorecord",
            name="msisdn",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
