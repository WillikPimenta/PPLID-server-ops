# Generated manually — remove public_id (P…/F…/A…) columns

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0036_backfill_falha_origem"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="auditoriafalhacadastro",
            name="public_id",
        ),
        migrations.RemoveField(
            model_name="qualidadependenteauditoria",
            name="public_id",
        ),
        migrations.RemoveField(
            model_name="qualidadependentecontestacao",
            name="public_id",
        ),
        migrations.RemoveField(
            model_name="qualidadependentereinspecao",
            name="public_id",
        ),
    ]
