from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ops_monitoring", "0003_api_traffic_buckets"),
    ]

    operations = [
        migrations.AddField(
            model_name="apirequestmetric",
            name="request_params",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
