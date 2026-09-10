# Incremental sync indexes for auditoria_falha_cadastro (PostgreSQL concurrent when possible).

from django.db import migrations, models


SYNC_UPDATED_INDEX = (
    "aud_falha_sync_upd_id_idx",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS aud_falha_sync_upd_id_idx "
    "ON auditoria_falha_cadastro (updated_at, id)",
    "DROP INDEX CONCURRENTLY IF EXISTS aud_falha_sync_upd_id_idx",
    ["updated_at", "id"],
)


def _eligibility_index(connection):
    """Compatível com schema antes e depois de resultado_qualidade (0045)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'auditoria_falha_cadastro'
              AND column_name IN ('analise_status', 'resultado_qualidade')
            """
        )
        columns = {row[0] for row in cursor.fetchall()}

    if "resultado_qualidade" in columns:
        fields = ["resultado_qualidade", "tipo_registro", "origem"]
    elif "analise_status" in columns:
        fields = ["analise_status", "tipo_registro", "origem"]
    else:
        fields = ["tipo_registro", "origem"]

    columns_sql = ", ".join(fields)
    return (
        "aud_falha_elig_idx",
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS aud_falha_elig_idx "
        f"ON auditoria_falha_cadastro ({columns_sql})",
        "DROP INDEX CONCURRENTLY IF EXISTS aud_falha_elig_idx",
        fields,
    )


def _create_indexes(apps, schema_editor):
    indexes = [SYNC_UPDATED_INDEX, _eligibility_index(schema_editor.connection)]
    if schema_editor.connection.vendor == "postgresql":
        for _name, create_sql, _drop, _fields in indexes:
            schema_editor.execute(create_sql)
        return
    AuditoriaFalhaCadastro = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    for name, _create, _drop, fields in indexes:
        schema_editor.add_index(
            AuditoriaFalhaCadastro,
            models.Index(fields=fields, name=name),
        )


def _drop_indexes(apps, schema_editor):
    indexes = [SYNC_UPDATED_INDEX, _eligibility_index(schema_editor.connection)]
    if schema_editor.connection.vendor == "postgresql":
        for _name, _create, drop_sql, _fields in indexes:
            schema_editor.execute(drop_sql)
        return
    AuditoriaFalhaCadastro = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    for name, _create, _drop, fields in indexes:
        schema_editor.remove_index(
            AuditoriaFalhaCadastro,
            models.Index(fields=fields, name=name),
        )


class Migration(migrations.Migration):
    atomic = False

    replaces = [
        ("auditoria", "0046_qualidade_sync_indexes"),
    ]

    dependencies = [
        ("auditoria", "0046_resultado_qualidade"),
    ]

    operations = [
        migrations.RunPython(_create_indexes, _drop_indexes),
    ]
