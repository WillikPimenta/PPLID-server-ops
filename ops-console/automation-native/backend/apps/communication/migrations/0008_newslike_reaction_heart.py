from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communication", "0007_newslike_reaction"),
    ]

    operations = [
        migrations.AlterField(
            model_name="newslike",
            name="reaction",
            field=models.CharField(
                choices=[
                    ("heart", "Coração"),
                    ("rocket", "Foguete"),
                    ("clap", "Palmas"),
                    ("party", "Festa"),
                ],
                default="heart",
                max_length=16,
            ),
        ),
    ]
