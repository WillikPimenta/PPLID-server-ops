from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def clear_anonymous_likes(apps, schema_editor):
    NewsLike = apps.get_model("communication", "NewsLike")
    NewsLike.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("communication", "0003_newslike"),
    ]

    operations = [
        migrations.RunPython(clear_anonymous_likes, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="newslike",
            name="news_like_unique_client",
        ),
        migrations.RemoveField(
            model_name="newslike",
            name="client_key",
        ),
        migrations.AddField(
            model_name="newslike",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="news_likes",
                to=settings.AUTH_USER_MODEL,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="newslike",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="news_likes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="newslike",
            constraint=models.UniqueConstraint(
                fields=("news", "user"),
                name="news_like_unique_user",
            ),
        ),
        migrations.AddIndex(
            model_name="newslike",
            index=models.Index(fields=["user"], name="news_like_user_id_idx"),
        ),
    ]
