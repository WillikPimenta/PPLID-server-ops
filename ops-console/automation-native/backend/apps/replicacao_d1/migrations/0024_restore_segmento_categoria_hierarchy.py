# Restaura hierarquia: Segmento (pai) → Categoria (filha)
from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


def _unique_categoria_chave(Categoria, base_chave: str) -> str:
    chave = base_chave
    suffix = 2
    while Categoria.objects.filter(chave_normalizada=chave).exists():
        chave = f"{base_chave}-{suffix}"
        suffix += 1
    return chave


def _restore_hierarchy(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'replicacao_d1_segmento' AND column_name = 'categoria_id'
                """
            )
            if cursor.fetchone() is None:
                return

    Segmento = apps.get_model("replicacao_d1", "ReplicacaoD1Segmento")
    Categoria = apps.get_model("replicacao_d1", "ReplicacaoD1Categoria")
    Cliente = apps.get_model("replicacao_d1", "ReplicacaoD1Cliente")

    old_categorias = list(Categoria.objects.all().order_by("id"))
    old_segmentos = list(Segmento.objects.select_related("categoria").all().order_by("id"))
    old_categoria_ids = {c.id for c in old_categorias}

    cat_to_seg: dict[int, int] = {}
    for old_cat in old_categorias:
        seg = Segmento.objects.filter(chave_normalizada=old_cat.chave_normalizada).first()
        if seg is None:
            seg = Segmento.objects.create(
                nome=old_cat.nome,
                chave_normalizada=old_cat.chave_normalizada,
                ativo=old_cat.ativo,
                created_at=old_cat.created_at,
                updated_at=old_cat.updated_at,
                created_by_id=old_cat.created_by_id,
                updated_by_id=old_cat.updated_by_id,
            )
        cat_to_seg[old_cat.id] = seg.id

    seg_to_cat: dict[int, int] = {}
    for old_seg in old_segmentos:
        parent_seg_id = cat_to_seg.get(old_seg.categoria_id) if old_seg.categoria_id else None
        chave = _unique_categoria_chave(Categoria, old_seg.chave_normalizada)
        cat = Categoria.objects.filter(chave_normalizada=chave).exclude(id__in=old_categoria_ids).first()
        if cat is None:
            cat = Categoria.objects.create(
                nome=old_seg.nome,
                chave_normalizada=chave,
                segmento_id=parent_seg_id,
                ativo=old_seg.ativo,
                created_at=old_seg.created_at,
                updated_at=old_seg.updated_at,
                created_by_id=old_seg.created_by_id,
                updated_by_id=old_seg.updated_by_id,
            )
        else:
            Categoria.objects.filter(pk=cat.pk).update(segmento_id=parent_seg_id, ativo=old_seg.ativo)
            cat = Categoria.objects.get(pk=cat.pk)
        seg_to_cat[old_seg.id] = cat.id

    for cliente in Cliente.objects.all().iterator():
        new_segmento_id = cat_to_seg.get(cliente.categoria_id) if cliente.categoria_id else None
        new_categoria_id = seg_to_cat.get(cliente.segmento_id) if cliente.segmento_id else None
        segmento_nome = ""
        categoria_nome = ""
        if new_segmento_id:
            segmento_nome = Segmento.objects.filter(pk=new_segmento_id).values_list("nome", flat=True).first() or ""
        if new_categoria_id:
            categoria_nome = Categoria.objects.filter(pk=new_categoria_id).values_list("nome", flat=True).first() or ""
        Cliente.objects.filter(pk=cliente.pk).update(
            segmento_id=new_segmento_id,
            categoria_id=new_categoria_id,
            segmento_nome=segmento_nome,
            categoria_nome=categoria_nome,
        )

    if old_categorias:
        Categoria.objects.filter(id__in=[c.id for c in old_categorias]).update(ativo=False)
    if old_segmentos:
        Segmento.objects.filter(id__in=[s.id for s in old_segmentos]).update(ativo=False)


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("replicacao_d1", "0023_invert_categoria_segmento_hierarchy"),
    ]

    operations = [
        migrations.AddField(
            model_name="replicacaod1categoria",
            name="segmento",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="categorias",
                to="replicacao_d1.replicacaod1segmento",
            ),
        ),
        migrations.RunPython(_restore_hierarchy, _noop_reverse),
        migrations.RemoveField(
            model_name="replicacaod1segmento",
            name="categoria",
        ),
    ]
