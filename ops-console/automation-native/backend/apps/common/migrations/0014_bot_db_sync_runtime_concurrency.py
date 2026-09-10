from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0013_bot_db_sync_quality_projection_domain"),
    ]

    operations = [
        migrations.AddField(
            model_name="botdbsyncruntimeconfig",
            name="high_concurrency",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="botdbsyncruntimeconfig",
            name="low_concurrency",
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]
