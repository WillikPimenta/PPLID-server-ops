from django.db import migrations, models


def rename_mid_lane(apps, schema_editor):
    BotDbSyncJob = apps.get_model("common", "BotDbSyncJob")
    BotDbSyncJob.objects.filter(lane="mid").update(lane="middle")


def rename_middle_lane_back(apps, schema_editor):
    BotDbSyncJob = apps.get_model("common", "BotDbSyncJob")
    BotDbSyncJob.objects.filter(lane="middle").update(lane="mid")


class Migration(migrations.Migration):
    dependencies = [("common", "0015_bot_db_sync_mid_lane")]

    operations = [
        migrations.AlterField(
            model_name="botdbsyncjob",
            name="lane",
            field=models.CharField(
                choices=[
                    ("high", "Alta (pesado)"),
                    ("middle", "Média (peso médio)"),
                    ("low", "Baixa (leve)"),
                ],
                db_index=True,
                default="low",
                help_text="Filas high, middle e low; processadas em paralelo.",
                max_length=8,
            ),
        ),
        migrations.RunPython(rename_mid_lane, rename_middle_lane_back),
    ]
