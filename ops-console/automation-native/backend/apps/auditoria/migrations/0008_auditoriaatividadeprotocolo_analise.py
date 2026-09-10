# Generated manually

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auditoria", "0007_auditoriaatividade"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="agente",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="analisado_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="analisado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="auditoria_protocolos_analisados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="brflow_parsed",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="brflow_raw",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="cruzamento_bases",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="etapa_falha",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="motivo_falha",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="nivel_dificuldade",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="qualidade_imagem",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="resultado_correto",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="situacao",
            field=models.CharField(
                blank=True,
                choices=[("conforme", "Conforme"), ("nao_conforme", "Não conforme")],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="tipo_documento",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="tipo_falha_analise",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="auditoriaatividadeprotocolo",
            name="uf_documento",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
    ]
