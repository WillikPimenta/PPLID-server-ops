from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communication", "0006_rename_news_ack_news_idx_news_acknow_news_id_5e38a5_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="newslike",
            name="reaction",
            field=models.CharField(
                choices=[
                    ("rocket", "Foguete"),
                    ("clap", "Palmas"),
                    ("party", "Festa"),
                ],
                default="rocket",
                max_length=16,
            ),
        ),
    ]
