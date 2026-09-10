import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("communication", "0004_newslike_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="news",
            name="is_critical",
            field=models.BooleanField(default=False, verbose_name="crítica"),
        ),
        migrations.AddField(
            model_name="news",
            name="source_url",
            field=models.URLField(blank=True, default="", max_length=500, verbose_name="fonte"),
        ),
        migrations.CreateModel(
            name="NewsAcknowledgment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "acknowledged_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="aceite em"),
                ),
                (
                    "news",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="acknowledgments",
                        to="communication.news",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="news_acknowledgments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "aceite de notícia",
                "verbose_name_plural": "aceites de notícias",
                "db_table": "news_acknowledgment",
                "indexes": [
                    models.Index(fields=["news"], name="news_ack_news_idx"),
                    models.Index(fields=["user"], name="news_ack_user_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("news", "user"),
                        name="news_ack_unique_user",
                    )
                ],
            },
        ),
    ]
