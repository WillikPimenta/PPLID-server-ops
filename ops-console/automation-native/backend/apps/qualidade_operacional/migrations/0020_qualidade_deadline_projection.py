from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("qualidade_operacional", "0019_qualidade_deadline_partial_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="QualidadeDeadlineGeneration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("building", "Building"), ("active", "Active"), ("superseded", "Superseded"), ("failed", "Failed")], db_index=True, max_length=16)),
                ("rules_version", models.CharField(default="deadline-v1", max_length=32)),
                ("source_fingerprint", models.CharField(blank=True, default="", max_length=64)),
                ("scope_keys_built", models.JSONField(blank=True, default=list)),
                ("auditado_scope_count", models.PositiveBigIntegerField(default=0)),
                ("falha_scope_count", models.PositiveBigIntegerField(default=0)),
                ("protocol_count", models.PositiveBigIntegerField(default=0)),
                ("build_duration_ms", models.PositiveIntegerField(default=0)),
                ("build_reason", models.CharField(blank=True, default="", max_length=128)),
                ("error_detail", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                (
                    "previous_generation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="successors",
                        to="qualidade_operacional.qualidadedeadlinegeneration",
                    ),
                ),
            ],
            options={
                "db_table": "qualidade_deadline_generation",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="QualidadeDeadlineAuditadoScope",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scope_key", models.CharField(db_index=True, max_length=64)),
                ("protocolo", models.CharField(db_index=True, max_length=100)),
                (
                    "auditado",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deadline_scopes",
                        to="qualidade_operacional.qualidadeauditado",
                    ),
                ),
                (
                    "generation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="auditado_scopes",
                        to="qualidade_operacional.qualidadedeadlinegeneration",
                    ),
                ),
            ],
            options={
                "db_table": "qualidade_deadline_auditado_scope",
            },
        ),
        migrations.CreateModel(
            name="QualidadeDeadlineFalhaScope",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scope_key", models.CharField(db_index=True, max_length=64)),
                (
                    "match_reason",
                    models.CharField(
                        choices=[
                            ("protocolo", "Protocolo"),
                            ("intranet", "Intranet"),
                            ("g_auditoria_projection", "G Auditoria projeção"),
                            ("g_auditoria_reconciliation", "G Auditoria reconciliação"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "falha",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deadline_scopes",
                        to="qualidade_operacional.qualidadefalha",
                    ),
                ),
                (
                    "generation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="falha_scopes",
                        to="qualidade_operacional.qualidadedeadlinegeneration",
                    ),
                ),
            ],
            options={
                "db_table": "qualidade_deadline_falha_scope",
            },
        ),
        migrations.AddIndex(
            model_name="qualidadedeadlineauditadoscope",
            index=models.Index(fields=["generation", "scope_key", "protocolo"], name="qo_deadline_aud_prot_idx"),
        ),
        migrations.AddIndex(
            model_name="qualidadedeadlineauditadoscope",
            index=models.Index(fields=["generation", "scope_key", "auditado"], name="qo_deadline_aud_id_idx"),
        ),
        migrations.AddIndex(
            model_name="qualidadedeadlinefalhascope",
            index=models.Index(fields=["generation", "scope_key", "falha"], name="qo_deadline_fal_id_idx"),
        ),
        migrations.AddConstraint(
            model_name="qualidadedeadlineauditadoscope",
            constraint=models.UniqueConstraint(fields=("generation", "scope_key", "auditado"), name="qo_deadline_aud_scope_uniq"),
        ),
        migrations.AddConstraint(
            model_name="qualidadedeadlinefalhascope",
            constraint=models.UniqueConstraint(fields=("generation", "scope_key", "falha"), name="qo_deadline_fal_scope_uniq"),
        ),
    ]
