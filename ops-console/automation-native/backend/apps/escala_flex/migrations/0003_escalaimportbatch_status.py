from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("escala_flex", "0002_escala"),
    ]

    operations = [
        migrations.AddField(
            model_name="escalaimportbatch",
            name="failure_detail",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="escalaimportbatch",
            name="finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="escalaimportbatch",
            name="status",
            field=models.CharField(
                choices=[
                    ("processing", "Em andamento"),
                    ("completed", "Concluída"),
                    ("failed", "Falhou"),
                ],
                default="completed",
                max_length=20,
            ),
        ),
    ]
