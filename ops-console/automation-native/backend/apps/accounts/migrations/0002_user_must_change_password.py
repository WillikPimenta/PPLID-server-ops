from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(
                default=False,
                help_text="True quando a conta usa senha padrão do seed e precisa definir senha pessoal.",
                verbose_name="deve trocar senha",
            ),
        ),
    ]
