# Generated manually for carga retroativa / upload retomável

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("qualidade_operacional", "0006_qualidadeimportbatch"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="qualidadeimportbatch",
            name="qo_import_active_kind_month_uniq",
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="import_mode",
            field=models.CharField(
                choices=[("monthly", "Carga mensal"), ("retroactive", "Carga retroativa")],
                db_index=True,
                default="monthly",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="qualidadeimportbatch",
            name="competencia",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name="qualidadeimportbatch",
            name="status",
            field=models.CharField(
                choices=[
                    ("uploading", "Enviando"),
                    ("validating", "Validando"),
                    ("validated", "Validado"),
                    ("processing", "Importando"),
                    ("completed", "Concluído"),
                    ("restoring", "Restaurando"),
                    ("restored", "Restaurado"),
                    ("failed", "Falhou"),
                ],
                db_index=True,
                default="validating",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="chunks_expected",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="chunks_received",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="bytes_received",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="upload_complete",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="expected_checksum",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="month_plan",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="current_competencia",
            field=models.CharField(blank=True, default="", max_length=7),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="months_done",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="months_total",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddConstraint(
            model_name="qualidadeimportbatch",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    import_mode="monthly",
                    status__in=["processing", "restoring"],
                    competencia__isnull=False,
                ),
                fields=("kind", "competencia"),
                name="qo_import_active_kind_month_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="qualidadeimportbatch",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    import_mode="retroactive",
                    status__in=["uploading", "validating", "processing", "restoring"],
                ),
                fields=("kind",),
                name="qo_import_active_retro_kind_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="qualidadeimportbatch",
            index=models.Index(
                fields=["import_mode", "status"],
                name="qo_import_mode_status_idx",
            ),
        ),
        migrations.CreateModel(
            name="QualidadeImportChunk",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("index", models.PositiveIntegerField()),
                ("size", models.PositiveIntegerField(default=0)),
                ("checksum_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("path", models.TextField(blank=True, default="")),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="qualidade_operacional.qualidadeimportbatch",
                    ),
                ),
            ],
            options={
                "db_table": "qualidade_import_chunk",
                "ordering": ["index"],
            },
        ),
        migrations.AddConstraint(
            model_name="qualidadeimportchunk",
            constraint=models.UniqueConstraint(
                fields=("batch", "index"),
                name="qo_import_chunk_batch_idx_uniq",
            ),
        ),
    ]
