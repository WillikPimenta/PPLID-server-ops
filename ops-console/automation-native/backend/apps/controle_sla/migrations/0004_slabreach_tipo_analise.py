from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("controle_sla", "0003_breach_unique_by_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="slabreach",
            name="tipo_analise",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Manual ou Automática (DimEtapa.manual por nome da etapa).",
                max_length=16,
            ),
        ),
    ]
