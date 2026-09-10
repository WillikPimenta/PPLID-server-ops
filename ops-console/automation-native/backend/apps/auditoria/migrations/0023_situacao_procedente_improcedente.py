# Generated manually: situacao conforme/nao_conforme -> improcedente/procedente

from django.db import migrations, models


def forwards_situacao(apps, schema_editor):
    Protocolo = apps.get_model("auditoria", "AuditoriaAtividadeProtocolo")
    Etapa = apps.get_model("auditoria", "AuditoriaAtividadeProtocoloEtapa")
    Protocolo.objects.filter(situacao="conforme").update(situacao="improcedente")
    Protocolo.objects.filter(situacao="nao_conforme").update(situacao="procedente")
    Etapa.objects.filter(situacao="conforme").update(situacao="improcedente")
    Etapa.objects.filter(situacao="nao_conforme").update(situacao="procedente")


def backwards_situacao(apps, schema_editor):
    Protocolo = apps.get_model("auditoria", "AuditoriaAtividadeProtocolo")
    Etapa = apps.get_model("auditoria", "AuditoriaAtividadeProtocoloEtapa")
    Protocolo.objects.filter(situacao="improcedente").update(situacao="conforme")
    Protocolo.objects.filter(situacao="procedente").update(situacao="nao_conforme")
    Etapa.objects.filter(situacao="improcedente").update(situacao="conforme")
    Etapa.objects.filter(situacao="procedente").update(situacao="nao_conforme")


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0022_atividade_responsavel"),
    ]

    operations = [
        migrations.RunPython(forwards_situacao, backwards_situacao),
        migrations.AlterField(
            model_name="auditoriaatividadeprotocolo",
            name="situacao",
            field=models.CharField(
                blank=True,
                choices=[("improcedente", "Improcedente"), ("procedente", "Procedente")],
                default="",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="auditoriaatividadeprotocoloetapa",
            name="situacao",
            field=models.CharField(
                blank=True,
                choices=[("improcedente", "Improcedente"), ("procedente", "Procedente")],
                default="",
                max_length=20,
            ),
        ),
    ]
