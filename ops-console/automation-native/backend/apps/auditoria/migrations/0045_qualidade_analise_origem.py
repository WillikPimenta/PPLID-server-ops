import hashlib
import json

import django.db.models.deletion
from django.db import migrations, models


SCHEMA_VERSION = 1
TRILHA_PARSED_KEYS = frozenset(
    {
        "consideracoes_finais",
        "cruzamento_bases",
        "etapas",
        "etapas_por_usuario",
        "extracted_users",
        "qualidade_imagem",
        "reanalisado",
        "resultado_contestado",
        "situacao",
        "tempo_analise",
        "tipo_conclusao",
    }
)
CONTEXTO_KEYS = frozenset(
    {
        "atividade_id",
        "excel_row",
        "fila_contexto",
        "pendente_reinspecao_id",
        "protocolo_origem_id",
        "source_file",
    }
)


def separar_payload_legado(brflow_parsed):
    source = dict(brflow_parsed) if isinstance(brflow_parsed, dict) else {}
    trilha_raw = str(source.pop("trilha_raw", "") or "")
    trilha_parsed = {}
    contexto = {}
    for key in list(source):
        if key in TRILHA_PARSED_KEYS:
            trilha_parsed[key] = source.pop(key)
        elif key in CONTEXTO_KEYS:
            contexto[key] = source.pop(key)
    return source, trilha_raw, trilha_parsed, contexto


def calcular_hash(*, protocolo, brflow_raw, brflow_parsed, trilha_raw, trilha_parsed, contexto):
    canonical = json.dumps(
        {
            "protocolo": protocolo,
            "brflow_raw": brflow_raw,
            "brflow_parsed": brflow_parsed,
            "trilha_raw": trilha_raw,
            "trilha_parsed": trilha_parsed,
            "contexto": contexto,
            "schema_version": SCHEMA_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def backfill_analise_origem(apps, schema_editor):
    Tratado = apps.get_model("auditoria", "AuditoriaFalhaCadastro")
    Origem = apps.get_model("auditoria", "QualidadeAnaliseOrigem")
    alias = schema_editor.connection.alias

    qs = Tratado.objects.using(alias).filter(analise_origem_id__isnull=True).order_by("id")
    for tratado in qs.iterator(chunk_size=500):
        protocolo = str(tratado.protocolo or "").strip()
        brflow_raw = str(tratado.brflow_raw or "")
        brflow_parsed, trilha_raw, trilha_parsed, contexto = separar_payload_legado(
            tratado.brflow_parsed
        )
        conteudo_hash = calcular_hash(
            protocolo=protocolo,
            brflow_raw=brflow_raw,
            brflow_parsed=brflow_parsed,
            trilha_raw=trilha_raw,
            trilha_parsed=trilha_parsed,
            contexto=contexto,
        )
        defaults = {
            "protocolo": protocolo,
            "brflow_raw": brflow_raw,
            "brflow_parsed": brflow_parsed,
            "trilha_raw": trilha_raw,
            "trilha_parsed": trilha_parsed,
            "contexto": contexto,
            "schema_version": SCHEMA_VERSION,
        }
        origem, created = Origem.objects.using(alias).get_or_create(
            conteudo_hash=conteudo_hash,
            defaults=defaults,
        )
        if not created and any(getattr(origem, key) != value for key, value in defaults.items()):
            raise RuntimeError("Colisão de hash ao migrar a origem da análise.")
        Tratado.objects.using(alias).filter(pk=tratado.pk).update(analise_origem_id=origem.pk)

    if Tratado.objects.using(alias).filter(analise_origem_id__isnull=True).exists():
        raise RuntimeError("Existem tratados sem origem após o backfill.")


class Migration(migrations.Migration):

    replaces = [
        ("auditoria", "0044_qualidade_analise_origem"),
    ]

    dependencies = [
        ("auditoria", "0044_alter_auditoriacatalogitem_catalog"),
    ]

    operations = [
        migrations.CreateModel(
            name="QualidadeAnaliseOrigem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("protocolo", models.CharField(blank=True, db_index=True, default="", max_length=100)),
                ("brflow_raw", models.TextField(blank=True, default="")),
                ("brflow_parsed", models.JSONField(blank=True, default=dict)),
                ("trilha_raw", models.TextField(blank=True, default="")),
                ("trilha_parsed", models.JSONField(blank=True, default=dict)),
                ("contexto", models.JSONField(blank=True, default=dict)),
                ("conteudo_hash", models.CharField(max_length=64, unique=True)),
                ("schema_version", models.PositiveSmallIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qualidade_analise_origem",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="auditoriafalhacadastro",
            name="analise_origem",
            field=models.ForeignKey(
                blank=True,
                help_text="Fonte compartilhada do Brflow e da trilha desta análise.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tratados",
                to="auditoria.qualidadeanaliseorigem",
            ),
        ),
        migrations.RunPython(backfill_analise_origem, migrations.RunPython.noop),
    ]
