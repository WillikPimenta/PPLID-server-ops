from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0064_qualidadeconfiguracaoalteracao"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualidadeconfiguracaoalteracao",
            name="reverte",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reversoes",
                to="auditoria.qualidadeconfiguracaoalteracao",
            ),
        ),
    ]
