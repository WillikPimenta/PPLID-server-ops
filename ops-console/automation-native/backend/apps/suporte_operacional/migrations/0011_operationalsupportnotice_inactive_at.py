from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_operacional", "0010_operationalsupportnotice"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsupportnotice",
            name="inactive_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
