import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workforce", "0007_rename_agent_active_idx_agent_active_c8098c_idx"),
        ("escala_flex", "0017_absence_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationalOccurrenceExtension",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "extra_seconds",
                    models.PositiveIntegerField(
                        help_text="Tempo adicional solicitado em segundos (HH:MM).",
                        verbose_name="tempo adicional (segundos)",
                    ),
                ),
                ("description", models.TextField(blank=True, verbose_name="motivo")),
                ("approved", models.BooleanField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("approval_notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_occurrence_extensions",
                        to="workforce.agent",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_occurrence_extensions",
                        to="workforce.agent",
                    ),
                ),
                (
                    "occurrence",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="extensions",
                        to="escala_flex.operationaloccurrence",
                    ),
                ),
            ],
            options={
                "db_table": "ef_operational_occurrence_extension",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="operationaloccurrenceextension",
            index=models.Index(fields=["occurrence", "approved"], name="ef_occ_ext_occ_appr_idx"),
        ),
    ]
