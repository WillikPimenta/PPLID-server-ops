from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("escala_flex", "0008_rename_descanso_to_pessoal"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="escala",
            name="escala_sheet_n_d7dd89_idx",
        ),
        migrations.RemoveField(
            model_name="escala",
            name="sheet_name",
        ),
    ]
