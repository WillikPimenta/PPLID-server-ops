import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditoriaFalhaCadastro",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("protocolo", models.CharField(db_index=True, max_length=100)),
                ("brflow_raw", models.TextField(blank=True, default="")),
                ("brflow_parsed", models.JSONField(blank=True, default=dict)),
                ("modulo", models.CharField(blank=True, default="", max_length=255)),
                ("demanda_url", models.URLField(blank=True, default="", max_length=500)),
                ("tipo_falha", models.CharField(choices=[("FN", "FN"), ("FP", "FP"), ("OUTROS", "OUTROS")], max_length=16)),
                ("usuario", models.CharField(max_length=255)),
                ("resultado_cliente", models.TextField(blank=True, default="")),
                ("novo_resultado", models.TextField(blank=True, default="")),
                ("sinalizacao", models.CharField(blank=True, default="", max_length=255)),
                ("motivo_falha", models.CharField(blank=True, default="", max_length=255)),
                ("etapa_falha", models.CharField(blank=True, default="", max_length=255)),
                ("nivel_dificuldade", models.CharField(blank=True, default="", max_length=100)),
                ("tipo_documento", models.CharField(blank=True, default="", max_length=255)),
                ("uf_documento", models.CharField(blank=True, default="", max_length=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="auditoria_falha_cadastros",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "auditoria_falha_cadastro",
                "ordering": ["-created_at"],
            },
        ),
    ]
