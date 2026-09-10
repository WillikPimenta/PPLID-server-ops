from django.db import migrations, models
import django.db.models.deletion
import uuid


DEFAULT_OCCURRENCE_TYPES = [
    (1, "Acesso aos Portais"),
    (2, "Sistema Indisponível"),
    (3, "Treinamento"),
    (4, "Reunião"),
    (5, "Suporte Técnico"),
    (6, "Outros"),
]


def seed_occurrence_types(apps, schema_editor):
    OccurrenceType = apps.get_model("escala_flex", "OccurrenceType")
    for pk, name in DEFAULT_OCCURRENCE_TYPES:
        OccurrenceType.objects.update_or_create(
            id=pk,
            defaults={"name": name, "active": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("workforce", "0001_initial"),
        ("escala_flex", "0009_remove_escala_sheet_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="OccurrenceType",
            fields=[
                ("id", models.PositiveSmallIntegerField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=128, verbose_name="nome")),
                ("active", models.BooleanField(default=True)),
                (
                    "sharepoint_id",
                    models.PositiveIntegerField(blank=True, null=True, unique=True),
                ),
            ],
            options={
                "verbose_name": "tipo de ocorrência",
                "verbose_name_plural": "tipos de ocorrência",
                "db_table": "ef_occurrence_type",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="OperationalOccurrence",
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
                ("date", models.DateField(verbose_name="data")),
                (
                    "forecast_seconds",
                    models.PositiveIntegerField(
                        help_text="Duração prevista em segundos (HH:MM).",
                        verbose_name="previsão (segundos)",
                    ),
                ),
                ("description", models.TextField(blank=True, verbose_name="descrição")),
                ("approved", models.BooleanField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("approval_notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="operational_occurrences",
                        to="workforce.agent",
                    ),
                ),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_operational_occurrences",
                        to="workforce.agent",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_operational_occurrences",
                        to="workforce.agent",
                    ),
                ),
                (
                    "leader",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="team_operational_occurrences",
                        to="workforce.agent",
                    ),
                ),
                (
                    "occurrence_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="occurrences",
                        to="escala_flex.occurrencetype",
                    ),
                ),
                (
                    "schedule_today",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="operational_occurrences",
                        to="escala_flex.scheduletoday",
                    ),
                ),
            ],
            options={
                "db_table": "ef_operational_occurrence",
                "ordering": ["-date", "agent__full_name"],
            },
        ),
        migrations.AddIndex(
            model_name="operationaloccurrence",
            index=models.Index(fields=["date", "agent"], name="ef_operatio_date_0a8f0d_idx"),
        ),
        migrations.AddIndex(
            model_name="operationaloccurrence",
            index=models.Index(fields=["approved"], name="ef_operatio_approve_6d2f1a_idx"),
        ),
        migrations.AddIndex(
            model_name="operationaloccurrence",
            index=models.Index(fields=["leader"], name="ef_operatio_leader__f4a2b1_idx"),
        ),
        migrations.RunPython(seed_occurrence_types, migrations.RunPython.noop),
    ]
