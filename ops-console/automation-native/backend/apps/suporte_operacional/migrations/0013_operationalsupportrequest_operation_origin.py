from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_operacional", "0012_presence_presencial"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="operation_origin",
            field=models.CharField(
                choices=[("fraud", "Fraud"), ("confer", "Confer")],
                db_index=True,
                default="fraud",
                max_length=16,
            ),
        ),
    ]
