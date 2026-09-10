from django.db import migrations


def populate_filter_catalog(apps, schema_editor):
    Auditado = apps.get_model("qualidade_operacional", "QualidadeAuditado")
    Falha = apps.get_model("qualidade_operacional", "QualidadeFalha")
    Opcao = apps.get_model("qualidade_operacional", "QualidadeFiltroOpcao")

    sources = (
        (
            "tipo_analise",
            Auditado.objects.exclude(tipo_analise="").values_list(
                "tipo_analise", flat=True
            ).distinct(),
        ),
        (
            "tipo_falha",
            Falha.objects.exclude(tipo_falha="").values_list(
                "tipo_falha", flat=True
            ).distinct(),
        ),
        (
            "localidade",
            Falha.objects.exclude(localidade="").values_list(
                "localidade", flat=True
            ).distinct(),
        ),
        (
            "etapa",
            Auditado.objects.exclude(etapa="").values_list("etapa", flat=True).distinct(),
        ),
        (
            "etapa",
            Falha.objects.exclude(etapa="").values_list("etapa", flat=True).distinct(),
        ),
    )
    rows = {
        (dimensao, str(valor).strip())
        for dimensao, values in sources
        for valor in values
        if str(valor or "").strip()
    }
    Opcao.objects.bulk_create(
        [Opcao(dimensao=dimensao, valor=valor) for dimensao, valor in sorted(rows)],
        ignore_conflicts=True,
        batch_size=1000,
    )


class Migration(migrations.Migration):
    dependencies = [("qualidade_operacional", "0003_performance_catalog")]
    operations = [migrations.RunPython(populate_filter_catalog, migrations.RunPython.noop)]

