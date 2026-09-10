from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0039_auditoria_tratado_concluido_constraint"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="auditoriaatividade",
            index=models.Index(
                fields=["tipo", "created_at"],
                name="aud_ativ_tipo_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditoriacontroleregistro",
            index=models.Index(
                fields=["created_at"],
                name="aud_ctrl_created_idx",
            ),
        ),
    ]
