from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("qualidade_operacional", "0020_qualidade_deadline_projection"),
    ]

    operations = [
        migrations.CreateModel(
            name="QualidadeDeadlineProtocoloScope",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scope_key", models.CharField(db_index=True, max_length=64)),
                ("protocolo", models.CharField(max_length=100)),
                (
                    "generation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="protocolo_scopes",
                        to="qualidade_operacional.qualidadedeadlinegeneration",
                    ),
                ),
            ],
            options={
                "db_table": "qualidade_deadline_protocolo_scope",
            },
        ),
        migrations.CreateModel(
            name="QualidadeEoMonthlyRollup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scope_key", models.CharField(db_index=True, max_length=64)),
                ("year_month", models.DateField(db_index=True)),
                ("deadline_on", models.BooleanField(db_index=True)),
                ("date_axis", models.CharField(db_index=True, max_length=16)),
                ("grain", models.CharField(db_index=True, max_length=16)),
                ("auditados", models.PositiveBigIntegerField(default=0)),
                ("falhas", models.PositiveBigIntegerField(default=0)),
                ("impacto_ponderado", models.FloatField(default=0.0)),
                ("daily_auditados", models.JSONField(blank=True, default=dict)),
                ("daily_falhas", models.JSONField(blank=True, default=dict)),
                ("daily_impacto", models.JSONField(blank=True, default=dict)),
                (
                    "generation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="monthly_rollups",
                        to="qualidade_operacional.qualidadedeadlinegeneration",
                    ),
                ),
            ],
            options={
                "db_table": "qualidade_eo_monthly_rollup",
            },
        ),
        migrations.AddIndex(
            model_name="qualidadedeadlineprotocoloscope",
            index=models.Index(fields=["generation", "scope_key", "protocolo"], name="qo_deadline_prot_idx"),
        ),
        migrations.AddConstraint(
            model_name="qualidadedeadlineprotocoloscope",
            constraint=models.UniqueConstraint(fields=("generation", "scope_key", "protocolo"), name="qo_deadline_prot_scope_uniq"),
        ),
        migrations.AddIndex(
            model_name="qualidadeeomonthlyrollup",
            index=models.Index(fields=["generation", "scope_key", "deadline_on", "date_axis", "grain"], name="qo_eo_rollup_lookup_idx"),
        ),
        migrations.AddConstraint(
            model_name="qualidadeeomonthlyrollup",
            constraint=models.UniqueConstraint(
                fields=("generation", "scope_key", "year_month", "deadline_on", "date_axis", "grain"),
                name="qo_eo_monthly_rollup_uniq",
            ),
        ),
    ]
