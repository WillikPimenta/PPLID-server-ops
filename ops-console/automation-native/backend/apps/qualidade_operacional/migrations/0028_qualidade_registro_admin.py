import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("qualidade_operacional", "0027_qualidade_import_case_key_conflicts"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadeauditado",
            name="admin_identity",
            field=models.CharField(blank=True, db_index=True, default="", max_length=96),
        ),
        migrations.AddField(
            model_name="qualidadeauditado",
            name="admin_revision",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="qualidadeauditado",
            name="admin_suppressed",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="qualidadefalha",
            name="admin_identity",
            field=models.CharField(blank=True, db_index=True, default="", max_length=96),
        ),
        migrations.AddField(
            model_name="qualidadefalha",
            name="admin_revision",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="qualidadefalha",
            name="admin_suppressed",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.CreateModel(
            name="QualidadeRegistroEstado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("auditado", "Auditado"), ("falha", "Falha")], max_length=16)),
                ("identity_key", models.CharField(max_length=96)),
                ("current_object_id", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("suppressed", models.BooleanField(db_index=True, default=False)),
                ("overrides", models.JSONField(blank=True, default=dict)),
                ("revision", models.PositiveIntegerField(default=0)),
                ("suppression_parent_key", models.CharField(blank=True, default="", max_length=96)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="qualidade_registros_atualizados", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "qualidade_registro_estado"},
        ),
        migrations.CreateModel(
            name="QualidadeRegistroHistorico",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("revision", models.PositiveIntegerField()),
                ("action", models.CharField(choices=[("update", "Editar"), ("suppress", "Suprimir"), ("restore", "Restaurar")], max_length=16)),
                ("before", models.JSONField(default=dict)),
                ("after", models.JSONField(default=dict)),
                ("changes", models.JSONField(blank=True, default=dict)),
                ("reason", models.TextField()),
                ("ticket", models.CharField(max_length=128)),
                ("idempotency_key", models.UUIDField(editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="qualidade_registros_historico", to=settings.AUTH_USER_MODEL)),
                ("estado", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="historico", to="qualidade_operacional.qualidaderegistroestado")),
            ],
            options={"db_table": "qualidade_registro_historico", "ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="qualidaderegistroestado",
            constraint=models.UniqueConstraint(fields=("kind", "identity_key"), name="qo_reg_estado_kind_ident_uniq"),
        ),
        migrations.AddIndex(
            model_name="qualidaderegistroestado",
            index=models.Index(fields=["kind", "suppressed"], name="qo_reg_estado_kind_sup_idx"),
        ),
        migrations.AddConstraint(
            model_name="qualidaderegistrohistorico",
            constraint=models.UniqueConstraint(fields=("estado", "revision"), name="qo_reg_hist_estado_rev_uniq"),
        ),
    ]
