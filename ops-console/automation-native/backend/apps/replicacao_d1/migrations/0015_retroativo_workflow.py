# -*- coding: utf-8 -*-
from django.db import migrations, models
import django.db.models.deletion


def _migrate_cliente_links_to_workflows(apps, schema_editor):
    RetroativoConfig = apps.get_model("replicacao_d1", "ReplicacaoD1RetroativoConfig")
    RetroativoCliente = apps.get_model("replicacao_d1", "ReplicacaoD1RetroativoCliente")
    RetroativoWorkflow = apps.get_model("replicacao_d1", "ReplicacaoD1RetroativoWorkflow")
    Workflow = apps.get_model("replicacao_d1", "ReplicacaoD1Workflow")

    retro = RetroativoConfig.objects.filter(pk=1).first()
    if retro is None:
        return

    workflow_ids: set[int] = set(
        RetroativoWorkflow.objects.filter(config=retro, ativo=True).values_list("workflow_id", flat=True)
    )
    for link in RetroativoCliente.objects.filter(config=retro, ativo=True).select_related("cliente"):
        qs = Workflow.objects.filter(
            cliente_id=link.cliente_id,
            ativo=True,
            status="ATIVO",
        )
        for wf_id in qs.values_list("pk", flat=True):
            if wf_id in workflow_ids:
                continue
            RetroativoWorkflow.objects.create(config=retro, workflow_id=wf_id, ativo=True)
            workflow_ids.add(wf_id)


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0014_retroativo_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReplicacaoD1RetroativoWorkflow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ativo", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "config",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="workflows",
                        to="replicacao_d1.replicacaod1retroativoconfig",
                    ),
                ),
                (
                    "workflow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="retroativo_vinculos",
                        to="replicacao_d1.replicacaod1workflow",
                    ),
                ),
            ],
            options={
                "db_table": "replicacao_d1_retroativo_workflow",
            },
        ),
        migrations.AddConstraint(
            model_name="replicacaod1retroativoworkflow",
            constraint=models.UniqueConstraint(
                fields=("config", "workflow"),
                name="replicacao_d1_retroativo_wf_uniq",
            ),
        ),
        migrations.RunPython(_migrate_cliente_links_to_workflows, migrations.RunPython.noop),
    ]
