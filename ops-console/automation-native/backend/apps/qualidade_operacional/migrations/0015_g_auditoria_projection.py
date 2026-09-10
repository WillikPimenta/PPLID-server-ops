import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("qualidade_operacional", "0014_reprojetar_tratados_por_etapa"),
        ("rotina_bruto", "0012_g_auditoria_staging"),
    ]

    operations = [
        migrations.CreateModel(
            name="QualidadeGAuditoriaProjection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("mapping_version", models.PositiveSmallIntegerField(default=1)),
                (
                    "source_fingerprint",
                    models.CharField(blank=True, db_index=True, default="", max_length=64),
                ),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("warnings", models.JSONField(blank=True, default=list)),
                (
                    "projected_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "auditado",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="g_auditoria_projection",
                        to="qualidade_operacional.qualidadeauditado",
                    ),
                ),
                (
                    "staging",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qualidade_projection",
                        to="rotina_bruto.rotinagauditoriarecord",
                    ),
                ),
            ],
            options={
                "db_table": "qualidade_g_auditoria_projection",
                "indexes": [
                    models.Index(
                        fields=["is_active", "projected_at"],
                        name="qo_gaud_active_proj_idx",
                    ),
                    models.Index(fields=["mapping_version"], name="qo_gaud_mapver_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="qualidadeintranetprojection",
            name="g_auditoria_match_observation",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="qualidadeintranetprojection",
            name="g_auditoria_match_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("unmatched", "Não localizado"),
                    ("matched", "Localizado"),
                    ("ambiguous", "Ambíguo"),
                ],
                db_index=True,
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="qualidadeintranetprojection",
            name="g_auditoria_projection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="intranet_audit_matches",
                to="qualidade_operacional.qualidadegauditoriaprojection",
            ),
        ),
        migrations.CreateModel(
            name="QualidadeGAuditoriaFailureReconciliation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("matched", "Localizada"),
                            ("unmatched", "Não localizada"),
                            ("ambiguous", "Correspondência ambígua"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("observation", models.CharField(blank=True, default="", max_length=128)),
                ("mapping_version", models.PositiveSmallIntegerField(default=1)),
                ("original_payload", models.JSONField(blank=True, default=dict)),
                ("differences", models.JSONField(blank=True, default=dict)),
                ("candidate_count", models.PositiveIntegerField(default=0)),
                (
                    "reconciled_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "falha",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="g_auditoria_reconciliation",
                        to="qualidade_operacional.qualidadefalha",
                    ),
                ),
                (
                    "projection",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="failure_reconciliations",
                        to="qualidade_operacional.qualidadegauditoriaprojection",
                    ),
                ),
            ],
            options={
                "db_table": "qualidade_g_auditoria_failure_reconciliation",
                "indexes": [
                    models.Index(
                        fields=["status", "reconciled_at"],
                        name="qo_gaud_fail_rec_idx",
                    ),
                    models.Index(fields=["mapping_version"], name="qo_gaud_fail_map_idx"),
                ],
            },
        ),
    ]
