from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auditoria", "0027_reinspecao_falha_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="analise_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("nao_atribuido", "Não atribuído"),
                    ("aguardando_analise", "Aguardando análise"),
                    ("em_analise", "Em análise"),
                    ("concluido", "Concluído"),
                ],
                db_index=True,
                default="nao_atribuido",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="responsavel",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reinspecao_protocolos_responsavel",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="atribuido_em",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="analise_iniciada_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="analise_concluida_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="ReinspecaoAuditorPresence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("online", "Online"),
                            ("offline", "Offline"),
                            ("ausente", "Ausente"),
                        ],
                        db_index=True,
                        default="offline",
                        max_length=16,
                    ),
                ),
                ("status_changed_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("last_assigned_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reinspecao_presence",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "reinspecao_auditor_presence",
                "ordering": ["user__username"],
            },
        ),
        migrations.CreateModel(
            name="ReinspecaoFilaHistorico",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "tipo",
                    models.CharField(
                        choices=[
                            ("atribuicao_auto", "Atribuição automática"),
                            ("direcionamento_manual", "Direcionamento manual"),
                            ("reatribuicao", "Reatribuição"),
                            ("liberacao_status", "Liberação por status"),
                            ("inicio_analise", "Início da análise"),
                            ("conclusao", "Conclusão"),
                            ("status_auditor", "Alteração de status do auditor"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("justificativa", models.TextField(blank=True, default="")),
                ("detalhe", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reinspecao_historico_actor",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "auditor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reinspecao_historico_auditor",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "falha",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reinspecao_historico",
                        to="auditoria.auditoriafalhacadastro",
                    ),
                ),
            ],
            options={
                "db_table": "reinspecao_fila_historico",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
