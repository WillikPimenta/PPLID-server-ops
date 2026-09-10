import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("qualidade_operacional", "0005_agente_acao_acompanhamento"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QualidadeImportBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("auditados", "Auditorias"), ("falhas", "Falhas")], db_index=True, max_length=16)),
                ("competencia", models.DateField(db_index=True)),
                ("filename", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("validating", "Validando"), ("validated", "Validado"), ("processing", "Importando"), ("completed", "Concluído"), ("restoring", "Restaurando"), ("restored", "Restaurado"), ("failed", "Falhou")], db_index=True, default="validating", max_length=16)),
                ("phase", models.CharField(blank=True, default="", max_length=128)),
                ("progress_percent", models.PositiveSmallIntegerField(default=0)),
                ("source_path", models.TextField(blank=True, default="")),
                ("backup_path", models.TextField(blank=True, default="")),
                ("file_size", models.PositiveBigIntegerField(default=0)),
                ("checksum_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("rows_total", models.PositiveIntegerField(default=0)),
                ("rows_valid", models.PositiveIntegerField(default=0)),
                ("rows_outside_period", models.PositiveIntegerField(default=0)),
                ("rows_errors", models.PositiveIntegerField(default=0)),
                ("distinct_protocols", models.PositiveIntegerField(default=0)),
                ("duplicate_protocol_rows", models.PositiveIntegerField(default=0)),
                ("previous_rows", models.PositiveIntegerField(default=0)),
                ("imported_rows", models.PositiveIntegerField(default=0)),
                ("warnings", models.JSONField(blank=True, default=list)),
                ("errors", models.JSONField(blank=True, default=list)),
                ("failure_detail", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("validated_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="qualidade_import_batches", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "qualidade_import_batch", "ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="qualidadeimportbatch",
            constraint=models.UniqueConstraint(condition=models.Q(status__in=["processing", "restoring"]), fields=("kind", "competencia"), name="qo_import_active_kind_month_uniq"),
        ),
        migrations.AddIndex(
            model_name="qualidadeimportbatch",
            index=models.Index(fields=["kind", "competencia", "status"], name="qo_import_kind_month_idx"),
        ),
    ]
