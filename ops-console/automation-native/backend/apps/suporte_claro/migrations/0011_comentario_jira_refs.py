# Generated manually: store Jira comment ids for mirrored delete.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_claro", "0010_comentario_visibilidade"),
    ]

    operations = [
        migrations.AddField(
            model_name="suporteclarocomentarioetapa",
            name="formalizado_jira_refs",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
