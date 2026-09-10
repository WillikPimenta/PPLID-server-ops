from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("produtividade_case", "0010_rename_case_manage_periodo_8g9h0i_idx_case_manage_periodo_677477_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="casefilasampleitem",
            name="cliente_origem",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddIndex(
            model_name="casefilasampleitem",
            index=models.Index(
                fields=["snapshot", "cliente_origem"],
                name="case_manage_snapsho_15eb28_idx",
            ),
        ),
    ]
