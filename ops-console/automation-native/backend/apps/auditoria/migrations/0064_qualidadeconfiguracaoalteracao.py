from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0063_contestacaooperacional_cenario_original_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QualidadeConfiguracaoAlteracao",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tabela_origem", models.CharField(db_index=True, max_length=128)),
                ("registro_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("mudancas", models.JSONField(default=dict)),
                ("criado_em", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "usuario",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="qualidade_configuracao_alteracoes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "qualidade_configuracao_alteracao",
                "ordering": ["-criado_em", "-id"],
            },
        ),
    ]
