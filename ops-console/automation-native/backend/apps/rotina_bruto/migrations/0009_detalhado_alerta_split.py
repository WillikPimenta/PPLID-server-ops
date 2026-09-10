# Generated manually for alertas table split

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("rotina_bruto", "0008_rename_rotina_conf_report__a8f1d2_idx_rotina_conf_report__ea35e6_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="RotinaDetalhadoBrutoAlerta",
            fields=[
                (
                    "record",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="alerta",
                        serialize=False,
                        to="rotina_bruto.rotinadetalhadobrutorecord",
                    ),
                ),
                ("alertas", models.TextField(blank=True, default="")),
            ],
            options={
                "db_table": "rotina_detalhado_bruto_alerta",
            },
        ),
        migrations.RemoveField(
            model_name="rotinadetalhadobrutorecord",
            name="alertas",
        ),
    ]
