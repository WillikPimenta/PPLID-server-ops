from __future__ import annotations

from collections import defaultdict

from django.db import migrations
from django.utils.dateparse import parse_datetime


STAGE_JSON_KEYS = {
    "etapas",
    "tempo_analise",
    "cruzamento_bases",
    "qualidade_imagem",
    "situacao",
}


def _clean_stage_json(value):
    parsed = dict(value) if isinstance(value, dict) else {}
    for key in STAGE_JSON_KEYS:
        parsed.pop(key, None)
    return parsed


def _as_datetime(value):
    if value is None or hasattr(value, "tzinfo"):
        return value
    if isinstance(value, str):
        return parse_datetime(value)
    return None


def _stage_from_model(item):
    return {
        "source_id": item.id,
        "ordem": item.ordem,
        "resultado_correto": item.resultado_correto,
        "nivel_dificuldade": item.nivel_dificuldade,
        "tipo_documento": item.tipo_documento,
        "uf_documento": item.uf_documento,
        "agente": item.agente,
        "tipo_falha": item.tipo_falha,
        "etapa_falha": item.etapa_falha,
        "tempo_analise": item.tempo_analise,
        "cruzamento_bases": item.cruzamento_bases,
        "qualidade_imagem": item.qualidade_imagem,
        "situacao": item.situacao,
        "motivo_falha": item.motivo_falha,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _stage_from_json(item, ordem):
    return {
        "source_id": None,
        "ordem": item.get("ordem", ordem),
        "resultado_correto": item.get("resultado_correto", ""),
        "nivel_dificuldade": item.get("nivel_dificuldade", ""),
        "tipo_documento": item.get("tipo_documento", ""),
        "uf_documento": item.get("uf_documento", ""),
        "agente": item.get("agente", ""),
        "tipo_falha": item.get("tipo_falha", ""),
        "etapa_falha": item.get("etapa_falha", ""),
        "tempo_analise": item.get("tempo_analise", ""),
        "cruzamento_bases": item.get("cruzamento_bases", ""),
        "qualidade_imagem": item.get("qualidade_imagem", ""),
        "situacao": item.get("situacao", ""),
        "motivo_falha": item.get("motivo_falha", ""),
        "created_at": _as_datetime(item.get("created_at")),
        "updated_at": _as_datetime(item.get("updated_at")),
    }


def _resolve_protocol(row, Protocolo, AnaliseOrigem):
    protocolo = Protocolo.objects.filter(tratado_id=row.pk).first()
    if protocolo is not None:
        return protocolo

    parsed = row.brflow_parsed if isinstance(row.brflow_parsed, dict) else {}
    protocolo_id = parsed.get("protocolo_origem_id")
    if not protocolo_id and row.analise_origem_id:
        contexto = (
            AnaliseOrigem.objects.filter(pk=row.analise_origem_id)
            .values_list("contexto", flat=True)
            .first()
        )
        if isinstance(contexto, dict):
            protocolo_id = contexto.get("protocolo_origem_id")
    if protocolo_id:
        protocolo = Protocolo.objects.filter(pk=protocolo_id).first()
        if protocolo is not None:
            return protocolo

    if row.atividade_id:
        return Protocolo.objects.filter(
            atividade_id=row.atividade_id,
            protocolo=row.protocolo,
        ).first()
    return None


def _stage_payload(row, stage, *, key, protocolo_id, clean_json):
    legacy_parsed = row.brflow_parsed if isinstance(row.brflow_parsed, dict) else {}
    situacao = str(stage.get("situacao") or "").strip().lower()
    if situacao == "procedente":
        resultado_qualidade = "com_falha"
    elif situacao == "improcedente":
        resultado_qualidade = "sem_falha"
    else:
        resultado_qualidade = row.resultado_qualidade

    return {
        "protocolo_origem_id": protocolo_id,
        "etapa_origem_id": stage.get("source_id"),
        "etapa_chave": key,
        "ordem_etapa": stage.get("ordem"),
        "etapa_criada_em": stage.get("created_at"),
        "etapa_atualizada_em": stage.get("updated_at"),
        "usuario": str(stage.get("agente") or row.usuario or "").strip(),
        "tipo_falha": str(stage.get("tipo_falha") or "").strip()
        or row.tipo_falha
        or "contestacao",
        "etapa_falha": str(stage.get("etapa_falha") or row.etapa_falha or "").strip(),
        "motivo_falha": str(stage.get("motivo_falha") or row.motivo_falha or "").strip(),
        "novo_resultado": str(stage.get("resultado_correto") or "").strip()
        or row.novo_resultado,
        "nivel_dificuldade": str(
            stage.get("nivel_dificuldade") or row.nivel_dificuldade or ""
        ).strip(),
        "tipo_documento": str(stage.get("tipo_documento") or row.tipo_documento or "").strip(),
        "uf_documento": str(stage.get("uf_documento") or row.uf_documento or "").strip(),
        "qualidade_imagem": str(
            stage.get("qualidade_imagem")
            or row.qualidade_imagem
            or legacy_parsed.get("qualidade_imagem")
            or ""
        ).strip(),
        "tempo_analise": str(
            stage.get("tempo_analise")
            or row.tempo_analise
            or legacy_parsed.get("tempo_analise")
            or ""
        ).strip(),
        "cruzamento_bases": str(
            stage.get("cruzamento_bases")
            or row.cruzamento_bases
            or legacy_parsed.get("cruzamento_bases")
            or ""
        ).strip(),
        "status": situacao or row.status,
        "resultado_qualidade": resultado_qualidade,
        "brflow_parsed": clean_json,
    }


def _clone_values(row):
    skip = {
        "id",
        "protocolo_origem",
        "etapa_origem",
        "etapa_chave",
        "ordem_etapa",
        "etapa_criada_em",
        "etapa_atualizada_em",
        "tempo_analise",
        "cruzamento_bases",
    }
    values = {}
    for field in row._meta.concrete_fields:
        if field.name in skip or field.primary_key:
            continue
        values[field.attname] = getattr(row, field.attname)
    return values


def _normalize_single_row(row, Tratado, counters):
    origem = (row.origem or row.tipo_registro or "intranet").strip().lower()
    parsed = row.brflow_parsed if isinstance(row.brflow_parsed, dict) else {}
    technical_id = parsed.get("pendente_reinspecao_id")
    if origem == "reinspecao" and technical_id:
        key = f"reinspecao:pendente:{technical_id}"
    else:
        key = f"{origem}:legado:{row.pk}"
    if Tratado.objects.filter(etapa_chave=key).exclude(pk=row.pk).exists():
        key = f"{key}:legado:{row.pk}"
    group = (origem, row.atividade_id, row.protocolo)
    ordem = counters[group]
    counters[group] += 1
    Tratado.objects.filter(pk=row.pk).update(
        etapa_chave=key,
        ordem_etapa=ordem,
        tempo_analise=str(parsed.get("tempo_analise") or "").strip(),
        cruzamento_bases=str(parsed.get("cruzamento_bases") or "").strip(),
        qualidade_imagem=row.qualidade_imagem
        or str(parsed.get("qualidade_imagem") or "").strip(),
        brflow_parsed=_clean_stage_json(parsed),
    )


def normalizar_tratados(apps, schema_editor):
    Tratado = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    Protocolo = apps.get_model("auditoria", "AuditoriaAtividadeProtocolo")
    Etapa = apps.get_model("auditoria", "AuditoriaAtividadeProtocoloEtapa")
    AnaliseOrigem = apps.get_model("auditoria", "QualidadeAnaliseOrigem")

    counters = defaultdict(int)
    rows = list(Tratado.objects.filter(etapa_chave__isnull=True).order_by("id"))
    for row in rows:
        origem = (row.origem or row.tipo_registro or "").strip().lower()
        if origem != "contestacao":
            _normalize_single_row(row, Tratado, counters)
            continue

        protocolo = _resolve_protocol(row, Protocolo, AnaliseOrigem)
        stages = []
        if protocolo is not None:
            stages = [
                _stage_from_model(item)
                for item in Etapa.objects.filter(protocolo_id=protocolo.pk).order_by("ordem", "id")
            ]
        if not stages:
            parsed = row.brflow_parsed if isinstance(row.brflow_parsed, dict) else {}
            raw_stages = parsed.get("etapas") if isinstance(parsed.get("etapas"), list) else []
            stages = [
                _stage_from_json(item, ordem)
                for ordem, item in enumerate(raw_stages)
                if isinstance(item, dict)
            ]
        if not stages:
            _normalize_single_row(row, Tratado, counters)
            continue

        canonical_index = next(
            (
                index
                for index, stage in enumerate(stages)
                if str(stage.get("situacao") or "").strip().lower() == "procedente"
            ),
            0,
        )
        ordered = [stages[canonical_index]] + [
            stage for index, stage in enumerate(stages) if index != canonical_index
        ]
        clean_json = _clean_stage_json(row.brflow_parsed)
        protocolo_id = protocolo.pk if protocolo is not None else None
        base_values = _clone_values(row)

        for position, stage in enumerate(ordered):
            if stage.get("source_id"):
                key = f"contestacao:etapa:{stage['source_id']}"
            else:
                key = f"contestacao:legado:{row.pk}:ordem:{stage.get('ordem', position)}"
            if Tratado.objects.filter(etapa_chave=key).exclude(pk=row.pk).exists():
                key = f"contestacao:legado:{row.pk}:etapa:{stage.get('source_id') or position}"
            payload = _stage_payload(
                row,
                stage,
                key=key,
                protocolo_id=protocolo_id,
                clean_json=clean_json,
            )
            if position == 0:
                Tratado.objects.filter(pk=row.pk).update(**payload)
                continue

            clone = {**base_values, **payload, "status_falha": "ativa"}
            created = Tratado.objects.create(**clone)
            Tratado.objects.filter(pk=created.pk).update(
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    if Tratado.objects.filter(etapa_chave__isnull=True).exists():
        raise RuntimeError("Backfill por etapa terminou com tratados sem chave de etapa.")


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0049_tratado_por_etapa"),
    ]

    operations = [
        migrations.RunPython(normalizar_tratados, migrations.RunPython.noop),
    ]
