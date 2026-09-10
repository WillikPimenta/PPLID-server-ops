# Generated manually for Bio/Redoc D-1 integration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("replicacao_d1", "0016_fallback_parquet_dias_ausentes"),
    ]

    operations = [
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="replicacao_workflow_destino_bio",
            field=models.CharField(
                blank=True,
                default="Auditoria Biometria - Auditoria Biometria",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="replicacao_workflow_cod_bio",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="replicacao_workflow_destino_redoc",
            field=models.CharField(
                blank=True,
                default="Auditoria Redoc - Auditoria Redoc",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="replicacao_workflow_cod_redoc",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="meta_produ_diaria_bio",
            field=models.DecimalField(decimal_places=2, default=300, max_digits=12),
        ),
        migrations.AddField(
            model_name="replicacaod1configgeral",
            name="meta_produ_diaria_redoc",
            field=models.DecimalField(decimal_places=2, default=300, max_digits=12),
        ),
        migrations.AddField(
            model_name="replicacaod1escaladia",
            name="auditores_bio",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="replicacaod1escaladia",
            name="auditores_redoc",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="replicacaod1workflow",
            name="nome_regra_brflow",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
