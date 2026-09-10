# Generated manually — inverte hierarquia: Categoria (pai) → Segmento (filho)
from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


def _unique_segmento_chave(Segmento, base_chave: str) -> str:
    chave = base_chave
    suffix = 2
    while Segmento.objects.filter(chave_normalizada=chave).exists():
        chave = f"{base_chave}-{suffix}"
        suffix += 1
    return chave


def _swap_hierarchy(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'replicacao_d1_categoria' AND column_name = 'segmento_id'
                """
            )
            if cursor.fetchone() is None:
                return

    Segmento = apps.get_model("replicacao_d1", "ReplicacaoD1Segmento")
    Categoria = apps.get_model("replicacao_d1", "ReplicacaoD1Categoria")
    Cliente = apps.get_model("replicacao_d1", "ReplicacaoD1Cliente")

    old_segmentos = list(Segmento.objects.all().order_by("id"))
    old_categorias = list(Categoria.objects.select_related("segmento").all().order_by("id"))
    old_segmento_ids = {s.id for s in old_segmentos}

    seg_to_cat: dict[int, int] = {}
    for old_seg in old_segmentos:
        cat = Categoria.objects.filter(chave_normalizada=old_seg.chave_normalizada).first()
        if cat is None:
            cat = Categoria.objects.create(
                nome=old_seg.nome,
                chave_normalizada=old_seg.chave_normalizada,
                ativo=old_seg.ativo,
                created_at=old_seg.created_at,
                updated_at=old_seg.updated_at,
                created_by_id=old_seg.created_by_id,
                updated_by_id=old_seg.updated_by_id,
            )
        seg_to_cat[old_seg.id] = cat.id

    cat_to_seg: dict[int, int] = {}
    for old_cat in old_categorias:
        parent_cat_id = seg_to_cat.get(old_cat.segmento_id) if old_cat.segmento_id else None
        chave = _unique_segmento_chave(Segmento, old_cat.chave_normalizada)
        seg = Segmento.objects.filter(chave_normalizada=chave).exclude(id__in=old_segmento_ids).first()
        if seg is None:
            seg = Segmento.objects.create(
                nome=old_cat.nome,
                chave_normalizada=chave,
                categoria_id=parent_cat_id,
                ativo=old_cat.ativo,
                created_at=old_cat.created_at,
                updated_at=old_cat.updated_at,
                created_by_id=old_cat.created_by_id,
                updated_by_id=old_cat.updated_by_id,
            )
        else:
            Segmento.objects.filter(pk=seg.pk).update(categoria_id=parent_cat_id, ativo=old_cat.ativo)
            seg = Segmento.objects.get(pk=seg.pk)
        cat_to_seg[old_cat.id] = seg.id

    for cliente in Cliente.objects.all().iterator():
        new_categoria_id = seg_to_cat.get(cliente.segmento_id) if cliente.segmento_id else None
        new_segmento_id = cat_to_seg.get(cliente.categoria_id) if cliente.categoria_id else None
        categoria_nome = ""
        segmento_nome = ""
        if new_categoria_id:
            categoria_nome = Categoria.objects.filter(pk=new_categoria_id).values_list("nome", flat=True).first() or ""
        if new_segmento_id:
            segmento_nome = Segmento.objects.filter(pk=new_segmento_id).values_list("nome", flat=True).first() or ""
        Cliente.objects.filter(pk=cliente.pk).update(
            categoria_id=new_categoria_id,
            segmento_id=new_segmento_id,
            categoria_nome=categoria_nome,
            segmento_nome=segmento_nome,
        )

    if old_segmentos:
        Segmento.objects.filter(id__in=[s.id for s in old_segmentos]).update(ativo=False)
    if old_categorias:
        Categoria.objects.filter(id__in=[c.id for c in old_categorias]).update(ativo=False)


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("replicacao_d1", "0022_workflow_modo_replicacao"),
    ]

    operations = [
        migrations.AddField(
            model_name="replicacaod1segmento",
            name="categoria",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="segmentos",
                to="replicacao_d1.replicacaod1categoria",
            ),
        ),
        migrations.RunPython(_swap_hierarchy, _noop_reverse),
        migrations.RemoveField(
            model_name="replicacaod1categoria",
            name="segmento",
        ),
    ]
