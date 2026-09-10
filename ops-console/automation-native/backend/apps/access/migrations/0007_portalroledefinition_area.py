from django.db import migrations, models


ROLE_AREAS = {
    "plan_gerencia": "planejamento",
    "plan_analista": "planejamento",
    "plan_assistente": "planejamento",
    "op_gerencia": "operacao",
    "op_lider": "operacao",
    "op_agente": "operacao",
    "proc_usuario": "processos",
    "qual_usuario": "qualidade",
    "qual_auditoria_fraud": "qualidade",
    "qual_auditoria_compliance": "qualidade",
    "qual_contestacao_fraud": "qualidade",
    "qual_contestacao_compliance": "qualidade",
    "qual_capacitacao": "qualidade",
    "qual_gerencia": "qualidade",
    "adm_portal": "administracao",
}


def backfill_areas(apps, schema_editor):
    PortalRoleDefinition = apps.get_model("access", "PortalRoleDefinition")
    for row in PortalRoleDefinition.objects.all():
        area = ROLE_AREAS.get(row.role, "outros")
        if row.area != area:
            row.area = area
            row.save(update_fields=["area"])


class Migration(migrations.Migration):

    dependencies = [
        ("access", "0006_portalroledefinition_granted_routes"),
    ]

    operations = [
        migrations.AddField(
            model_name="portalroledefinition",
            name="area",
            field=models.CharField(blank=True, default="outros", max_length=32),
        ),
        migrations.RunPython(backfill_areas, migrations.RunPython.noop),
    ]
