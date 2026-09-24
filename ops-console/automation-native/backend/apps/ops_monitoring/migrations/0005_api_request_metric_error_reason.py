from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ops_monitoring", "0004_api_request_metric_params"),
    ]

    operations = [
        migrations.AddField(
            model_name="apirequestmetric",
            name="error_reason",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
    ]
