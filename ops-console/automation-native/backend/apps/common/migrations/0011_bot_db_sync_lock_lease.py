from django.db import migrations, models
import django.utils.timezone
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0010_ingestion_attempt_identity"),
    ]

    operations = [
        migrations.CreateModel(
            name="BotDbSyncLockLease",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("lane", models.CharField(max_length=8, unique=True)),
                (
                    "owner_token",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("backend_pid", models.PositiveIntegerField(blank=True, null=True)),
                ("process_pid", models.PositiveIntegerField(blank=True, null=True)),
                ("label", models.CharField(blank=True, default="", max_length=128)),
                (
                    "acquired_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "heartbeat_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                    ),
                ),
            ],
            options={
                "db_table": "common_bot_db_sync_lock_lease",
            },
        ),
    ]
