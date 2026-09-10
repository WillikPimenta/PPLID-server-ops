# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0003_bot_db_sync_job_lane"),
    ]

    operations = [
        migrations.CreateModel(
            name="BotDbSyncRuntimeConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chunk_size", models.PositiveIntegerField(default=2000)),
                ("batch_size", models.PositiveIntegerField(default=2000)),
                ("stale_minutes", models.PositiveIntegerField(default=45)),
                ("drain_max_jobs", models.PositiveIntegerField(default=10)),
                ("queue_wait_s", models.PositiveIntegerField(default=900)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "common_bot_db_sync_runtime_config",
            },
        ),
    ]
