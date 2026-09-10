from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("controle_sla", "0005_historico_e_gap_situacao"),
    ]

    operations = [
        migrations.AddField(
            model_name="etapagap",
            name="etapa_id",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="ID da DimEtapa vinculada após cadastrar (Nova etapa).",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="etapagap",
            name="projecao_sla_id",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="ID da primeira regra Projeção SLA vinculada após cadastrar (legado).",
                null=True,
            ),
        ),
    ]
