from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("suporte_operacional", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="difficulty_level",
            field=models.CharField(
                blank=True,
                choices=[("Fácil", "Fácil"), ("Médio", "Médio"), ("Difícil", "Difícil")],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="document_type",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="operationalsupportrequest",
            name="document_uf",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
