from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0005_auditoriamotivofalha"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="tipo_registro",
            field=models.CharField(
                choices=[("auditoria", "Auditoria"), ("contestacao", "Contestação")],
                db_index=True,
                default="auditoria",
                max_length=16,
            ),
        ),
    ]
