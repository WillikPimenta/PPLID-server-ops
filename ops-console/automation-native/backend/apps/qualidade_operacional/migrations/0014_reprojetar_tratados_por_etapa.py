from django.db import migrations


def reprojetar_tratados_por_etapa(apps, schema_editor):
    columns = {
        column.name
        for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(),
            "qualidade_intranet_projection",
        )
    }
    # Os campos G Auditoria sÃ³ sÃ£o criados na migration seguinte. A
    # reprojeÃ§Ã£o Ã© executada novamente em 0016, apÃ³s o schema completo.
    if "g_auditoria_projection_id" not in columns:
        return

    # Usa o serviço idempotente oficial depois que o schema de ambas as apps já
    # está no estado esperado. `force=True` também corrige projeções legadas
    # mesmo quando a fonte Intranet ainda está em modo de transição.
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
        ("auditoria", "0050_normalizar_tratados_por_etapa"),
        ("qualidade_operacional", "0013_intranet_filter_dimensions"),
    ]

    operations = [
        migrations.RunPython(reprojetar_tratados_por_etapa, migrations.RunPython.noop),
    ]
