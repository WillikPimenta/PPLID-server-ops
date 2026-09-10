# Generated manually: editable conclusion / retorno timestamp.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_claro", "0011_comentario_jira_refs"),
    ]

    operations = [
        migrations.AddField(
            model_name="suporteclaroregistro",
            name="retorno_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
