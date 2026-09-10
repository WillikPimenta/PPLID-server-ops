# Generated manually for comentário visibilidade / formalização externa.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suporte_claro", "0009_comentario_etapa"),
    ]

    operations = [
        migrations.AddField(
            model_name="suporteclarocomentarioetapa",
            name="visibilidade",
            field=models.CharField(
                choices=[("interno", "Interno"), ("externo", "Externo (formalizado)")],
                db_index=True,
                default="interno",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="suporteclarocomentarioetapa",
            name="formalizado_externo",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="suporteclarocomentarioetapa",
            name="formalizado_issue_keys",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
