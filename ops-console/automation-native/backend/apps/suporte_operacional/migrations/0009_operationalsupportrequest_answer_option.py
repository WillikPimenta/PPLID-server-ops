from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_operacional", "0008_queue_priority"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="answer_option",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
