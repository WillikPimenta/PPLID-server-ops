from django.db import migrations, models


def garantir_tratados_concluidos(apps, schema_editor):
    Tratado = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    Tratado.objects.exclude(analise_status="concluido").update(analise_status="concluido")


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0038_tratados_somente_concluidos"),
    ]

    operations = [
        migrations.RunPython(garantir_tratados_concluidos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="auditoriafalhacadastro",
            name="analise_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("nao_atribuido", "Não atribuído"),
                    ("aguardando_analise", "Aguardando análise"),
                    ("em_analise", "Em análise"),
                    ("concluido", "Concluído"),
                ],
                db_index=True,
                default="concluido",
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="auditoriafalhacadastro",
            constraint=models.CheckConstraint(
                condition=models.Q(("analise_status", "concluido")),
                name="auditoria_tratado_deve_estar_concluido",
            ),
        ),
    ]
