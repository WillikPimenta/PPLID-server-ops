# -*- coding: utf-8 -*-
from django.db import migrations, models


def seed_aliases(apps, schema_editor):
    QualidadeDimAlias = apps.get_model("qualidade_operacional", "QualidadeDimAlias")
    rows = [
        ("cliente", "PICPAY", 10, "56.573 ocorrências coocorrentes workflow Picpay"),
        ("cliente", "BRADESCO", 6, "79.246 ocorrências Segurança Corporativa"),
        ("cliente", "BMG", 7, "33.926 ocorrências Banco BMG"),
        ("cliente", "MERCANTIL", 34, "Dois workflows históricos consistentes"),
        ("cliente", "BRB", 35, "Dois workflows históricos consistentes"),
        ("cliente", "VIVO", 4, "434.280 ocorrências Telefônica Brasil / Vivo"),
        ("cliente", "SERASA PME", 17, "Provável Serasa Experian — validar com negócio"),
        (
            "workflow",
            "TIM BRASIL - DOCUMENTOSCOPIA ESPECIALIZADA",
            623,
            "ID canônico; 836 permanece duplicata inativa no catálogo",
        ),
    ]
    for kind, alias_key, target_id, evidence in rows:
        QualidadeDimAlias.objects.get_or_create(
            kind=kind,
            alias_key=alias_key,
            defaults={"target_id": target_id, "evidence": evidence, "active": True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("qualidade_operacional", "0013_intranet_filter_dimensions"),
    ]

    operations = [
        migrations.CreateModel(
            name="QualidadeDimAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "kind",
                    models.CharField(
                        choices=[("cliente", "Cliente"), ("workflow", "Workflow")],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("alias_key", models.CharField(db_index=True, max_length=255)),
                ("target_id", models.IntegerField()),
                ("evidence", models.TextField(blank=True, default="")),
                ("active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qualidade_dim_alias",
                "indexes": [
                    models.Index(fields=["kind", "active"], name="qo_dim_alias_kind_act"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("kind", "alias_key"),
                        name="qo_dim_alias_kind_key_uniq",
                    ),
                ],
            },
        ),
        migrations.RunPython(seed_aliases, migrations.RunPython.noop),
    ]
