# Generated manually for comentários passo a passo (portal-only preview).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("suporte_claro", "0008_chamado_natureza"),
    ]

    operations = [
        migrations.CreateModel(
            name="SuporteClaroComentarioEtapa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("texto", models.TextField()),
                (
                    "etapa_status",
                    models.CharField(
                        choices=[
                            ("aberto", "Não iniciado"),
                            ("em_atendimento", "Em andamento"),
                            ("concluido", "Concluído"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="suporte_claro_comentarios_etapa",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "registro",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="comentarios_etapa",
                        to="suporte_claro.suporteclaroregistro",
                    ),
                ),
            ],
            options={
                "db_table": "suporte_claro_comentario_etapa",
                "ordering": ["created_at", "id"],
            },
        ),
    ]
