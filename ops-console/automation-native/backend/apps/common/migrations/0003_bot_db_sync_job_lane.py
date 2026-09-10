# -*- coding: utf-8 -*-
from django.db import migrations, models


def _backfill_lanes(apps, schema_editor):
    BotDbSyncJob = apps.get_model("common", "BotDbSyncJob")
    high_domains = {"falhas_criticas"}
    high_pairs = {
        ("rotina_bruto", "detalhado"),
        ("rotina_bruto", "prod"),
        ("rotina_bruto", "ged_detalhado"),
        ("rotina_bruto", "ged_irregularidade"),
    }
    for job in BotDbSyncJob.objects.all().iterator():
        domain = (job.domain or "").strip()
        report_type = (job.report_type or "").strip()
        lane = "high" if domain in high_domains or (domain, report_type) in high_pairs else "low"
        if job.lane != lane:
            job.lane = lane
            job.save(update_fields=["lane"])


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0002_bot_db_sync_replicacao_d1_domain"),
    ]

    operations = [
        migrations.AddField(
            model_name="botdbsyncjob",
            name="lane",
            field=models.CharField(
                choices=[("high", "Alta (pesado)"), ("low", "Baixa (leve)")],
                db_index=True,
                default="low",
                help_text="Fila high (pesado) ou low (leve); 1 sync por fila em paralelo.",
                max_length=8,
            ),
        ),
        migrations.AddIndex(
            model_name="botdbsyncjob",
            index=models.Index(
                fields=["lane", "status", "created_at"],
                name="bot_db_sync_lane_status",
            ),
        ),
        migrations.RunPython(_backfill_lanes, migrations.RunPython.noop),
    ]
