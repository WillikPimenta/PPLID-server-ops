import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0052_merge_20260814_1009"),
        ("auditoria", "0049_contestacao_protocolo_espelho"),
        ("workforce", "0012_agenthistory_sharepoint_item_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="agente_ref",
            field=models.ForeignKey(
                blank=True,
                db_column="agente_id",
                help_text="Agente relacionado ao valor legado de usuario, inclusive inativos e SISTEMA.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="falhas_atribuidas",
                to="workforce.agent",
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="auditor_ref",
            field=models.ForeignKey(
                blank=True,
                db_column="auditor_id",
                help_text="Auditor que efetivamente finalizou e registrou a analise.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="falhas_auditadas",
                to="workforce.agent",
            ),
        ),
        migrations.CreateModel(
            name="AuditoriaFalhaAlteracao",
            fields=[
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                (
                    "origem",
                    models.CharField(
                        choices=[
                            ("auditoria", "Auditoria"),
                            ("contestacao", "Contesta\u00e7\u00e3o"),
                            ("reinspecao", "Reinspe\u00e7\u00e3o"),
                            ("intranet", "Intranet"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("versao", models.PositiveIntegerField()),
                ("dados_anteriores", models.JSONField(default=dict)),
                ("dados_alterados", models.JSONField(default=dict)),
                ("campos_alterados", models.JSONField(default=list)),
                ("justificativa", models.TextField()),
                ("schema_version", models.PositiveSmallIntegerField(default=1)),
                (
                    "idempotency_key",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "alterado_por",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="alteracoes_de_falha",
                        to="workforce.agent",
                    ),
                ),
                (
                    "falha",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="alteracoes",
                        to="auditoria.auditoriafalhacadastro",
                    ),
                ),
            ],
            options={
                "db_table": "auditoria_falha_alteracao",
                "ordering": ["-created_at", "-versao"],
            },
        ),
        migrations.AddConstraint(
            model_name="auditoriafalhaalteracao",
            constraint=models.UniqueConstraint(
                fields=("falha", "versao"),
                name="uniq_aud_falha_alteracao_versao",
            ),
        ),
        migrations.AddIndex(
            model_name="auditoriafalhaalteracao",
            index=models.Index(
                fields=["falha", "created_at"],
                name="aud_fal_alt_fal_dt_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditoriafalhaalteracao",
            index=models.Index(
                fields=["alterado_por", "created_at"],
                name="aud_fal_alt_aut_dt_idx",
            ),
        ),
    ]
