# Generated manually — dimensões de filtro Intranet (tipo_registro, origem, etc.)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qualidade_operacional", "0012_intranet_projection_and_protocolo"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadeauditado",
            name="tipo_registro",
            field=models.CharField(blank=True, db_index=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="qualidadeauditado",
            name="origem_tratado",
            field=models.CharField(blank=True, db_index=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="qualidadeauditado",
            name="tipo_falha_original",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="qualidadeauditado",
            name="procedencia",
            field=models.CharField(blank=True, db_index=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="qualidadefalha",
            name="tipo_registro",
            field=models.CharField(blank=True, db_index=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="qualidadefalha",
            name="origem_tratado",
            field=models.CharField(blank=True, db_index=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="qualidadefalha",
            name="tipo_falha_original",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="qualidadefalha",
            name="procedencia",
            field=models.CharField(blank=True, db_index=True, default="", max_length=32),
        ),
    ]
