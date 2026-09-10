from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0010_route_status_and_menu_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="portalmenuoption",
            name="parent_menu_key",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="portalmenuoption",
            name="sort_order",
            field=models.IntegerField(default=0),
        ),
    ]
