# Generated manually

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0008_auditoriaatividadeprotocolo_analise"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditoriaAtividadeProtocoloEtapa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ordem", models.PositiveIntegerField(default=0)),
                ("resultado_correto", models.TextField(blank=True, default="")),
                ("nivel_dificuldade", models.CharField(blank=True, default="", max_length=100)),
                ("tipo_documento", models.CharField(blank=True, default="", max_length=255)),
                ("uf_documento", models.CharField(blank=True, default="", max_length=50)),
                ("agente", models.CharField(blank=True, default="", max_length=255)),
                ("tipo_falha", models.CharField(blank=True, default="", max_length=32)),
                ("etapa_falha", models.CharField(blank=True, default="", max_length=255)),
                ("cruzamento_bases", models.CharField(blank=True, default="", max_length=255)),
                ("qualidade_imagem", models.CharField(blank=True, default="", max_length=255)),
                (
                    "situacao",
                    models.CharField(
                        blank=True,
                        choices=[("conforme", "Conforme"), ("nao_conforme", "Não conforme")],
                        default="",
                        max_length=20,
                    ),
                ),
                ("motivo_falha", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "protocolo",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="etapas",
                        to="auditoria.auditoriaatividadeprotocolo",
                    ),
                ),
            ],
            options={
                "db_table": "auditoria_atividade_protocolo_etapa",
                "ordering": ["ordem", "id"],
            },
        ),
    ]
