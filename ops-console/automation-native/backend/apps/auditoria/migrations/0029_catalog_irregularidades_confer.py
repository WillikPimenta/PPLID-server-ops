import json
from pathlib import Path

from django.db import migrations, models


def seed_irregularidades_confer(apps, schema_editor):
    AuditoriaCatalogItem = apps.get_model("auditoria", "AuditoriaCatalogItem")
    catalog = "irregularidades_confer"
    if AuditoriaCatalogItem.objects.filter(catalog=catalog).exists():
        return
    seed_path = Path(__file__).resolve().parent.parent / "data" / "irregularidades_confer_seed.json"
    if not seed_path.is_file():
        return
    rows = json.loads(seed_path.read_text(encoding="utf-8"))
    items = []
    seen: set[str] = set()
    for row in rows:
        value = str(row.get("value") or "").strip()
        if not value or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        items.append(
            AuditoriaCatalogItem(
                catalog=catalog,
                value=value,
                label=value,
                sort_order=int(row.get("sort_order") or len(items)),
                active=bool(row.get("active", True)),
            )
        )
    if items:
        AuditoriaCatalogItem.objects.bulk_create(items, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("auditoria", "0028_reinspecao_fila"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditoriacatalogitem",
            name="catalog",
            field=models.CharField(
                choices=[
                    ("modulo", "Módulo"),
                    ("tipo_falha", "Tipo de falha"),
                    ("novo_resultado", "Novo resultado"),
                    ("sinalizacao", "Sinalização"),
                    ("etapa_falha", "Etapa da falha"),
                    ("nivel_dificuldade", "Nível de dificuldade"),
                    ("tipo_documento", "Tipo de documento"),
                    ("uf_documento", "UF do documento"),
                    ("cruzamento_bases", "Cruzamento de bases"),
                    ("qualidade_imagem", "Qualidade da imagem"),
                    ("tipo_acao_controle", "Tipo de ação (controles)"),
                    ("motivo_base_negativa", "Motivo (base negativa)"),
                    ("irregularidades_confer", "Irregularidades Confer"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.RunPython(seed_irregularidades_confer, migrations.RunPython.noop),
    ]
