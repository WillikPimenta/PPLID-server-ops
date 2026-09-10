# Cancelamento seguro / fencing da carga retroativa

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("qualidade_operacional", "0010_qualidade_covering_indexes"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="qualidadeimportbatch",
            name="qo_import_active_retro_kind_uniq",
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
                    ("cancel_requested", "Cancelamento solicitado"),
                    ("cancelling", "Cancelando"),
                    ("cancelled", "Cancelado"),
                    ("completed", "Concluído"),
                    ("restoring", "Restaurando"),
                    ("restored", "Restaurado"),
                    ("failed", "Falhou"),
                    ("rollback_failed", "Rollback falhou"),
                ],
                db_index=True,
                default="validating",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="failure_code",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="last_error_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="cancel_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="cancelled_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="qualidade_import_cancellations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="cancellation_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="rollback_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "—"),
                    ("pending", "Pendente"),
                    ("in_progress", "Em andamento"),
                    ("completed", "Concluído"),
                    ("failed", "Falhou"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="rollback_detail",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="worker_token",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddConstraint(
            model_name="qualidadeimportbatch",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    import_mode="retroactive",
                    status__in=[
                        "uploading",
                        "validating",
                        "processing",
                        "restoring",
                        "cancel_requested",
                        "cancelling",
                    ],
                ),
                fields=("kind",),
                name="qo_import_active_retro_kind_uniq",
            ),
        ),
    ]
