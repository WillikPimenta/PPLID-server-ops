# -*- coding: utf-8 -*-
from django.db import migrations, models


def copy_legacy_chamados(apps, schema_editor):
    Registro = apps.get_model("suporte_claro", "SuporteClaroRegistro")
    Chamado = apps.get_model("suporte_claro", "SuporteClaroChamadoExterno")
    for registro in Registro.objects.exclude(chamado_sistema=""):
        Chamado.objects.create(
            registro_id=registro.id,
            sistema=registro.chamado_sistema,
            codigo=registro.chamado_codigo or "",
            url=registro.chamado_url or "",
            ordem=0,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_claro", "0004_registro_chamado_externo"),
    ]

    operations = [
        migrations.CreateModel(
            name="SuporteClaroChamadoExterno",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "sistema",
                    models.CharField(
                        choices=[("jira", "Jira"), ("service", "ServiceNow")],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("codigo", models.CharField(blank=True, default="", max_length=128)),
                ("url", models.CharField(blank=True, default="", max_length=512)),
                ("ordem", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "registro",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="chamados_externos",
                        to="suporte_claro.suporteclaroregistro",
                    ),
                ),
            ],
            options={
                "db_table": "suporte_claro_chamado_externo",
                "ordering": ["ordem", "id"],
            },
        ),
        migrations.RunPython(copy_legacy_chamados, migrations.RunPython.noop),
    ]
