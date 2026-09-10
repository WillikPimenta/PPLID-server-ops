from django.db import migrations


def reprojetar_apos_g_auditoria(apps, schema_editor):
    def columns(table):
        with schema_editor.connection.cursor() as cursor:
            return {
                column.name
                for column in schema_editor.connection.introspection.get_table_description(
                    cursor, table
                )
            }

    # O serviço usa os modelos atuais. Em instalações que percorrem 0016 antes
    # das migrations de datas explícitas, retornar evita consultar colunas que
    # ainda não existem. A reprojeção é feita pelo comando operacional após o
    # schema completo, com dry-run e reconciliação.
    if "data_analise_intranet" not in columns("auditoria_falha_cadastro"):
        return
    if "data_analise_intranet" not in columns("qualidade_auditado"):
        return

    from apps.auditoria.models import AuditoriaFalhaCadastro
    from apps.qualidade_operacional.services.intranet_source import sync_queryset

    sync_queryset(
        AuditoriaFalhaCadastro.objects.all(),
        batch_size=200,
        force=True,
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("qualidade_operacional", "0015_g_auditoria_projection"),
    ]

    operations = [
        migrations.RunPython(reprojetar_apos_g_auditoria, migrations.RunPython.noop),
    ]
