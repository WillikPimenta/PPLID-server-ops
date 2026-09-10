from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("escala_flex", "0020_merge_schedule_swap_kind_and_index_rename"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduletoday",
            name="observation",
            field=models.TextField(blank=True, verbose_name="observação"),
        ),
    ]
