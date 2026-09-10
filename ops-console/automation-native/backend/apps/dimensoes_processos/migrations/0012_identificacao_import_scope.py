from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dimensoes_processos", "0011_identificacao_processos_import"),
    ]

    operations = [
        migrations.AddField(
            model_name="identificacaoprocessosimportrun",
            name="import_scope",
            field=models.CharField(
                choices=[
                    ("cadastros", "Só cadastros"),
                    ("projecao_sla", "Cadastros + Projeção SLA"),
                    ("full", "Completo"),
                ],
                default="full",
                max_length=16,
            ),
        ),
    ]
