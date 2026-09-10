from django.db import migrations, models


def forwards_status_em_andamento(apps, schema_editor):
    Protocolo = apps.get_model("auditoria", "AuditoriaAtividadeProtocolo")
    Protocolo.objects.filter(status="em_analise").update(status="em_andamento")


def backwards_status_em_analise(apps, schema_editor):
    Protocolo = apps.get_model("auditoria", "AuditoriaAtividadeProtocolo")
    Protocolo.objects.filter(status="em_andamento").update(status="em_analise")


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0011_auditoriaatividade_cliente_link"),
    ]

    operations = [
        migrations.RunPython(forwards_status_em_andamento, backwards_status_em_analise),
        migrations.AlterField(
            model_name="auditoriaatividadeprotocolo",
            name="status",
            field=models.CharField(
                choices=[
                    ("pendente", "Pendente"),
                    ("em_andamento", "Em andamento"),
                    ("concluido", "Concluído"),
                ],
                db_index=True,
                default="pendente",
                max_length=20,
            ),
        ),
    ]
