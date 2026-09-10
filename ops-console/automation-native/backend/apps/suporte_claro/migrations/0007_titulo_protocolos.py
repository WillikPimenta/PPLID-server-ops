# -*- coding: utf-8 -*-
from django.db import migrations, models


def backfill_titulo_and_protocolos(apps, schema_editor):
    Registro = apps.get_model("suporte_claro", "SuporteClaroRegistro")
    Protocolo = apps.get_model("suporte_claro", "SuporteClaroProtocolo")
    for registro in Registro.objects.all().iterator():
        if not (registro.titulo or "").strip():
            registro.titulo = registro.protocolo
            registro.save(update_fields=["titulo"])
        if registro.protocolo and not Protocolo.objects.filter(registro_id=registro.id).exists():
            Protocolo.objects.create(
                registro_id=registro.id,
                numero=registro.protocolo,
                comentario="",
                ordem=0,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_claro", "0006_import_ref"),
    ]

    operations = [
        migrations.AddField(
            model_name="suporteclaroregistro",
            name="titulo",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.CreateModel(
            name="SuporteClaroProtocolo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("numero", models.CharField(max_length=64)),
                ("comentario", models.TextField(blank=True, default="")),
                ("ordem", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "registro",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="protocolos",
                        to="suporte_claro.suporteclaroregistro",
                    ),
                ),
            ],
            options={
                "db_table": "suporte_claro_protocolo",
                "ordering": ["ordem", "id"],
            },
        ),
        migrations.RunPython(backfill_titulo_and_protocolos, migrations.RunPython.noop),
    ]
