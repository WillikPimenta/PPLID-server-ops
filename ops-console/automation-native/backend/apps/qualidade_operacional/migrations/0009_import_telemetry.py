# Generated manually for telemetria / heartbeat da carga retroativa

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qualidade_operacional", "0008_alter_qualidadeimportchunk_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="rows_processed",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="bytes_processed",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="heartbeat_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="processing_rate",
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="estimated_seconds_remaining",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="current_stage",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="progress_indeterminate",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="qualidadeimportbatch",
            name="stage_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
