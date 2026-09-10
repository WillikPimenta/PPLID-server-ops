from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("auditoria", "0055_merge_auditor_responsavel_agent_links")]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="resultado_qualidade_override",
            field=models.CharField(
                blank=True,
                choices=[
                    ("com_falha", "Com falha"),
                    ("sem_falha", "Sem falha"),
                    ("nao_classificado", "N\u00e3o classificado"),
                ],
                default=None,
                help_text="Resultado definido manualmente por uma altera\u00e7\u00e3o auditada.",
                max_length=32,
                null=True,
            ),
        ),
    ]
