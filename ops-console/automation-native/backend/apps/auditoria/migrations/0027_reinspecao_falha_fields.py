from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0026_atividade_data_recepcao_datetime"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditoriaatividade",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("contestacao", "Contestação"),
                    ("auditoria", "Auditoria"),
                    ("reinspecao", "Reinspeção"),
                ],
                db_index=True,
                default="contestacao",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="auditoriafalhacadastro",
            name="tipo_registro",
            field=models.CharField(
                choices=[
                    ("auditoria", "Auditoria"),
                    ("contestacao", "Contestação"),
                    ("reinspecao", "Reinspeção"),
                ],
                db_index=True,
                default="auditoria",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="data_contestacao",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="descricao_irregularidades",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="cliente",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="status",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="observacao",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="auditor",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="data_resposta",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
