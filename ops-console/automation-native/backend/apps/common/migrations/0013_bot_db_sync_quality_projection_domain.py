from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("common", "0012_bot_db_sync_reinspecao_ged_domain")]

    operations = [
        migrations.AlterField(
            model_name="botdbsyncjob",
            name="domain",
            field=models.CharField(
                choices=[
                    ("produtividade", "Produtividade"),
                    ("monitor_eventos", "Monitor eventos"),
                    ("rotina_bruto", "Rotina bruto"),
                    ("falhas_criticas", "Falhas críticas"),
                    ("replicacao_d1", "Replicação D-1"),
                    ("produtividade_case", "Produtividade Case Manager"),
                    ("prioridades_nh", "Prioridades por nível hierárquico"),
                    ("monitoramento_sla", "Monitoramento SLA útil"),
                    ("reinspecao_ged", "Reinspeção GED irregularidade"),
                    ("qualidade_projection", "Projeção da Qualidade"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
