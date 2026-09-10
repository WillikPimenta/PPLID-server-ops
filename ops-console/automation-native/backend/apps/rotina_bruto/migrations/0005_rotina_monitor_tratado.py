# Generated manually for monitor tratado schema

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rotina_bruto", "0004_typed_columns"),
    ]

    operations = [
        migrations.DeleteModel(
            name="RotinaMonitorBrutoRecord",
        ),
        migrations.CreateModel(
            name="RotinaMonitorTratadoRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("report_date", models.DateField(db_index=True)),
                ("data", models.DateField(db_index=True)),
                ("hora", models.PositiveSmallIntegerField()),
                (
                    "matricula_usuario",
                    models.CharField(blank=True, db_index=True, default="", max_length=64),
                ),
                ("data_evento", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("evento", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("data_segundo_evento", models.DateTimeField(blank=True, null=True)),
                ("segundo_evento", models.CharField(blank=True, default="", max_length=255)),
            ],
            options={
                "db_table": "rotina_monitor_tratado_record",
                "ordering": ["-report_date", "-data_evento", "matricula_usuario"],
                "indexes": [
                    models.Index(
                        fields=["report_date", "matricula_usuario"],
                        name="rotina_mon_report__a1b2c3_idx",
                    ),
                    models.Index(
                        fields=["report_date", "data", "hora"],
                        name="rotina_mon_report__d4e5f6_idx",
                    ),
                ],
            },
        ),
    ]
