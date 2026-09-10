# Generated manually for origem backfill on auditoria_falha_cadastro

from django.db import migrations
from django.db.models import Q


def fill_origem_from_tipo_registro(apps, schema_editor):
    Falha = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    for tipo in ("auditoria", "contestacao", "reinspecao"):
        Falha.objects.filter(Q(origem="") | Q(origem__isnull=True), tipo_registro=tipo).update(origem=tipo)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0035_qualidade_pendentes_origem"),
    ]

    operations = [
        migrations.RunPython(fill_origem_from_tipo_registro, noop_reverse),
    ]
