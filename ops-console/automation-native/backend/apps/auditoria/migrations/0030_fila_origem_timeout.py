from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0029_catalog_irregularidades_confer"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="fila_origem",
            field=models.CharField(
                blank=True,
                choices=[
                    ("auto", "Atribuição automática"),
                    ("direcionado", "Direcionamento manual"),
                ],
                db_index=True,
                default="",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="reinspecaofilahistorico",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("atribuicao_auto", "Atribuição automática"),
                    ("direcionamento_manual", "Direcionamento manual"),
                    ("reatribuicao", "Reatribuição"),
                    ("liberacao_status", "Liberação por status"),
                    ("liberacao_timeout", "Liberação por timeout"),
                    ("inicio_analise", "Início da análise"),
                    ("conclusao", "Conclusão"),
                    ("status_auditor", "Alteração de status do auditor"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
