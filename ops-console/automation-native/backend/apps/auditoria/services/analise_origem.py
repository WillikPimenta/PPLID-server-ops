from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.auditoria.models import QualidadeAnaliseOrigem


SCHEMA_VERSION = 1

ORIGIN_DATE_KEYS = {
    "protocolo_criado_em": (
        "data_criacao_origem",
        "data_criacao_protocolo",
        "data_criacao",
    ),
    "protocolo_analisado_em": (
        "data_analise_origem",
        "data_analise_protocolo",
        "data_analise",
    ),
    "protocolo_concluido_em": (
        "data_conclusao_origem",
        "data_conclusao_protocolo",
        "data_conclusao",
    ),
}

# Chaves adicionadas durante a análise e que não pertencem ao Brflow original.
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

# Metadados técnicos usados para rastrear a importação e o fluxo.
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


def separar_payload_legado(brflow_parsed: dict | None) -> tuple[dict, str, dict, dict]:
    """Separa o JSON legado sem descartar chaves desconhecidas.

    Retorna ``(brflow_parsed, trilha_raw, trilha_parsed, contexto)``. Chaves não
    reconhecidas permanecem no Brflow estruturado para preservar integralmente
    dados de versões anteriores.
    """
    source = dict(brflow_parsed) if isinstance(brflow_parsed, dict) else {}
    trilha_raw = str(source.pop("trilha_raw", "") or "")
    trilha_parsed: dict = {}
    contexto: dict = {}

    for key in list(source):
        if key in TRILHA_PARSED_KEYS:
            trilha_parsed[key] = source.pop(key)
        elif key in CONTEXTO_KEYS:
            contexto[key] = source.pop(key)

    return source, trilha_raw, trilha_parsed, contexto


def calcular_conteudo_hash(
    *,
    protocolo: str,
    brflow_raw: str,
    brflow_parsed: dict,
    trilha_raw: str,
    trilha_parsed: dict,
    contexto: dict,
    schema_version: int = SCHEMA_VERSION,
) -> str:
    canonical = json.dumps(
        {
            "protocolo": protocolo,
            "brflow_raw": brflow_raw,
            "brflow_parsed": brflow_parsed,
            "trilha_raw": trilha_raw,
            "trilha_parsed": trilha_parsed,
            "contexto": contexto,
            "schema_version": schema_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_origin_datetime(value) -> datetime | None:
    """Converte datas estruturadas sem alterar o valor original em ``brflow_parsed``."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        parsed = parse_datetime(text)
        if parsed is None:
            parsed_date = parse_date(text[:10])
            if parsed_date is None:
                for fmt in (
                    "%d/%m/%Y %H:%M:%S",
                    "%d/%m/%Y %H:%M",
                    "%d/%m/%Y",
                ):
                    try:
                        parsed = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        continue
            else:
                parsed = datetime.combine(parsed_date, time.min)
        if parsed is None:
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def extrair_datas_origem(brflow_parsed: dict | None) -> dict[str, datetime | None]:
    """Extrai criação, análise e conclusão como conceitos independentes."""
    source = brflow_parsed if isinstance(brflow_parsed, dict) else {}
    extracted: dict[str, datetime | None] = {}
    for field, keys in ORIGIN_DATE_KEYS.items():
        raw = next((source[key] for key in keys if source.get(key) not in (None, "")), None)
        extracted[field] = parse_origin_datetime(raw)
    return extracted


def get_or_create_analise_origem(
    *,
    protocolo: str,
    brflow_raw: str = "",
    brflow_parsed: dict | None = None,
) -> QualidadeAnaliseOrigem:
    protocolo = str(protocolo or "").strip()
    brflow_raw = str(brflow_raw or "")
    parsed, trilha_raw, trilha_parsed, contexto = separar_payload_legado(brflow_parsed)
    conteudo_hash = calcular_conteudo_hash(
        protocolo=protocolo,
        brflow_raw=brflow_raw,
        brflow_parsed=parsed,
        trilha_raw=trilha_raw,
        trilha_parsed=trilha_parsed,
        contexto=contexto,
    )
    defaults = {
        "protocolo": protocolo,
        "brflow_raw": brflow_raw,
        "brflow_parsed": parsed,
        "trilha_raw": trilha_raw,
        "trilha_parsed": trilha_parsed,
        "contexto": contexto,
        "schema_version": SCHEMA_VERSION,
        **extrair_datas_origem(parsed),
    }
    origem, created = QualidadeAnaliseOrigem.objects.get_or_create(
        conteudo_hash=conteudo_hash,
        defaults=defaults,
    )
    immutable_fields = (
        "protocolo",
        "brflow_raw",
        "brflow_parsed",
        "trilha_raw",
        "trilha_parsed",
        "contexto",
        "schema_version",
    )
    if not created and any(
        getattr(origem, key) != defaults[key] for key in immutable_fields
    ):
        raise RuntimeError("Colisão de hash ao registrar a origem da análise.")
    return origem
