# -*- coding: utf-8 -*-
"""Data repair: recalcular chaves e sincronizar nomes legados a partir dos FKs."""

from django.db import migrations

from apps.replicacao_d1.normalization import normalize_key


def repair_normalized_keys_and_names(apps, schema_editor):
    Segmento = apps.get_model("replicacao_d1", "ReplicacaoD1Segmento")
    Categoria = apps.get_model("replicacao_d1", "ReplicacaoD1Categoria")
    Cliente = apps.get_model("replicacao_d1", "ReplicacaoD1Cliente")
    Workflow = apps.get_model("replicacao_d1", "ReplicacaoD1Workflow")

    for obj in Segmento.objects.all().iterator():
        chave = normalize_key(obj.nome)
        if obj.chave_normalizada != chave:
            obj.chave_normalizada = chave
            obj.save(update_fields=["chave_normalizada"])

    for obj in Categoria.objects.all().iterator():
        chave = normalize_key(obj.nome)
        if obj.chave_normalizada != chave:
            obj.chave_normalizada = chave
            obj.save(update_fields=["chave_normalizada"])

    for obj in Cliente.objects.select_related("segmento", "categoria").iterator():
        updates = []
        chave = normalize_key(obj.nome)
        if obj.chave_normalizada != chave:
            obj.chave_normalizada = chave
            updates.append("chave_normalizada")
        if obj.segmento_id and obj.segmento and obj.segmento_nome != obj.segmento.nome:
            obj.segmento_nome = obj.segmento.nome
            updates.append("segmento_nome")
        if obj.categoria_id and obj.categoria and obj.categoria_nome != obj.categoria.nome:
            obj.categoria_nome = obj.categoria.nome
            updates.append("categoria_nome")
        if updates:
            obj.save(update_fields=updates)

    for obj in Workflow.objects.all().iterator():
        updates = []
        chave = normalize_key(obj.nome_canonico)
        if obj.chave_normalizada != chave:
            obj.chave_normalizada = chave
            updates.append("chave_normalizada")
        chave_d1 = normalize_key(obj.nome_d1) if obj.nome_d1 else ""
        if obj.chave_d1_normalizada != chave_d1:
            obj.chave_d1_normalizada = chave_d1
            updates.append("chave_d1_normalizada")
        if updates:
            obj.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [
        ("replicacao_d1", "0003_config_banco"),
    ]

    operations = [
        migrations.RunPython(repair_normalized_keys_and_names, migrations.RunPython.noop),
    ]
