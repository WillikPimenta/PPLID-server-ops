from django.db import migrations, models


def move_quality_projection_to_mid(apps, schema_editor):
    BotDbSyncJob = apps.get_model("common", "BotDbSyncJob")
    # Só movimenta a fila aguardando processamento. Um job running pode ainda
    # estar sob o lock low de um worker da release anterior durante o deploy;
    # trocar sua lane nesse instante permitiria que a reconciliação o tratasse
    # como órfão e o executasse em duplicidade.
    BotDbSyncJob.objects.filter(
        domain="qualidade_projection",
        status="pending",
    ).update(lane="mid")


def move_quality_projection_back_to_low(apps, schema_editor):
    BotDbSyncJob = apps.get_model("common", "BotDbSyncJob")
    BotDbSyncJob.objects.filter(
        domain="qualidade_projection",
        lane="mid",
        status="pending",
    ).update(lane="low")


class Migration(migrations.Migration):
    dependencies = [("common", "0014_bot_db_sync_runtime_concurrency")]

    operations = [
        migrations.AlterField(
            model_name="botdbsyncjob",
            name="lane",
            field=models.CharField(
                choices=[
                    ("high", "Alta (pesado)"),
                    ("mid", "Média (Projeção da Qualidade)"),
                    ("low", "Baixa (leve)"),
                ],
                db_index=True,
                default="low",
                help_text="Fila high, mid ou low; processadas em paralelo.",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="botdbsyncruntimeconfig",
            name="mid_concurrency",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.RunPython(
            move_quality_projection_to_mid,
            move_quality_projection_back_to_low,
        ),
    ]
