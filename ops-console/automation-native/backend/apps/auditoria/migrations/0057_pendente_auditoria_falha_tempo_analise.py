from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0056_qualidade_compliance_import_arquivo"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadependenteauditoriafalha",
            name="tempo_analise",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
