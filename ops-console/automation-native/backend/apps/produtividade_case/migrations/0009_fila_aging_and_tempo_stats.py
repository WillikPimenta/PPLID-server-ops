# Generated manually — aging pré-calculado na fila + tempo stats no consolidado

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("produtividade_case", "0008_fact_resultado_origem"),
    ]

    operations = [
        migrations.AddField(
            model_name="casefilasnapshot",
            name="aging_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="casefilasnapshot",
            name="aging_medio_seconds",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="casefilasnapshot",
            name="aging_mediano_seconds",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="casefilasnapshot",
            name="aging_p90_seconds",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="caseconsolidadosnapshot",
            name="tempo_stats_json",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
