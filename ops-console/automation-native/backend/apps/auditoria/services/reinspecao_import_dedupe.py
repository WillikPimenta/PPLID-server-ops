"""Chave composta de deduplicação para importação reinspeção / auditoria compliance."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from typing import Any, Callable, Iterable, TypeVar

from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.reinspecao_fila import (
    FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
    fila_contexto_atual,
    reinspecao_tratados_qs,
)
from apps.auditoria.services.reinspecao_import import (
    ParsedReinspecaoRow,
    normalize_irregularidade,
    resolve_matricula_inspetor,
)
from apps.replicacao_d1.normalization import normalize_protocolo

T = TypeVar("T")

MOTIVO_DUPLICADO_ARQUIVO = "duplicado_arquivo"
MOTIVO_JA_NA_FILA = "ja_na_fila"
MOTIVO_JA_ANALISADO = "ja_analisado"

MOTIVO_LABELS = {
    MOTIVO_DUPLICADO_ARQUIVO: "Duplicado no arquivo",
    MOTIVO_JA_NA_FILA: "Já na fila do portal",
    MOTIVO_JA_ANALISADO: "Já analisado no portal",
}


def normalize_texto(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def normalize_date_day(value: datetime | None) -> str:
    if value is None:
        return ""
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt).strftime("%Y-%m-%d")


def normalize_date_minute(value: datetime | None) -> str:
    if value is None:
        return ""
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


ReinspecaoOccurrenceKey = tuple[str, str, str, str]
DedupeKey = tuple[str, ...]


def normalize_datetime_full(value: datetime | None) -> str:
    """Normaliza o instante completo para UTC, sem descartar hora ou segundos."""
    if value is None:
        return ""
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.astimezone(datetime_timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def build_reinspecao_occurrence_key(
    *,
    contexto: str,
    protocolo: str,
    descricao_irregularidades: str,
    data_contestacao: datetime | None,
) -> ReinspecaoOccurrenceKey:
    """Identidade canônica de uma contestação de Reinspeção."""
    return (
        normalize_texto(contexto),
        normalize_protocolo(protocolo),
        normalize_irregularidade(descricao_irregularidades),
        normalize_datetime_full(data_contestacao),
    )


def reinspecao_occurrence_key_hash(key: ReinspecaoOccurrenceKey) -> str:
    """SHA-256 estável da identidade canônica, adequado para unicidade persistida."""
    payload = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_reinspecao_dedupe_key(
    *,
    contexto: str,
    protocolo: str,
    matricula_inspetor: str,
    descricao_irregularidades: str,
    data_contestacao: datetime | None,
) -> DedupeKey:
    """Compatibilidade: a matrícula não participa mais da identidade."""
    del matricula_inspetor
    return build_reinspecao_occurrence_key(
        contexto=contexto,
        protocolo=protocolo,
        descricao_irregularidades=descricao_irregularidades,
        data_contestacao=data_contestacao,
    )


def build_compliance_dedupe_key(
    *,
    contexto: str,
    protocolo: str,
    matricula_inspetor: str,
    tipo_status: str,
    data_conferencia: datetime | None,
) -> DedupeKey:
    return (
        contexto,
        normalize_protocolo(protocolo),
        resolve_matricula_inspetor(matricula_inspetor),
        normalize_texto(tipo_status or "Conferência"),
        normalize_date_minute(data_conferencia),
    )


def dedupe_key_hash(key: DedupeKey) -> str:
    payload = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _key_from_pendente(
    pendente: QualidadePendenteReinspecao | QualidadePendenteAuditoriaCompliance,
) -> DedupeKey:
    if pendente.contexto == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        return build_compliance_dedupe_key(
            contexto=pendente.contexto,
            protocolo=pendente.protocolo,
            matricula_inspetor=pendente.usuario,
            tipo_status=pendente.descricao_irregularidades,
            data_conferencia=pendente.data_analise,
        )
    return build_reinspecao_dedupe_key(
        contexto=pendente.contexto,
        protocolo=pendente.protocolo,
        matricula_inspetor=pendente.usuario,
        descricao_irregularidades=pendente.descricao_irregularidades,
        data_contestacao=pendente.data_contestacao,
    )


def _key_from_tratado(tratado: AuditoriaFalhaCadastro, *, contexto: str) -> DedupeKey | None:
    parsed = tratado.brflow_parsed if isinstance(tratado.brflow_parsed, dict) else {}
    fila_ctx = str(parsed.get("fila_contexto") or "").strip()
    if contexto == FILA_CONTEXTO_AUDITORIA_COMPLIANCE and fila_ctx and fila_ctx != contexto:
        return None
    if contexto == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        return build_compliance_dedupe_key(
            contexto=contexto,
            protocolo=tratado.protocolo,
            matricula_inspetor=tratado.usuario,
            tipo_status=tratado.descricao_irregularidades,
            data_conferencia=tratado.data_analise,
        )
    return build_reinspecao_dedupe_key(
        contexto=contexto,
        protocolo=tratado.protocolo,
        matricula_inspetor=tratado.usuario,
        descricao_irregularidades=tratado.descricao_irregularidades,
        data_contestacao=tratado.data_contestacao,
    )


def load_portal_dedupe_index(*, contexto: str | None = None) -> dict[DedupeKey, str]:
    """Retorna mapa key → motivo para registros já existentes no portal."""
    ctx = contexto or fila_contexto_atual()
    index: dict[DedupeKey, str] = {}

    if ctx == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        pendente_qs = QualidadePendenteAuditoriaCompliance.objects.only(
            "protocolo",
            "usuario",
            "descricao_irregularidades",
            "data_contestacao",
            "data_analise",
        )
        from apps.auditoria.services.auditoria_compliance_fila import (
            reinspecao_tratados_qs as tratados_contexto_qs,
        )
    else:
        pendente_qs = QualidadePendenteReinspecao.objects.filter(contexto=ctx).only(
            "contexto",
            "protocolo",
            "usuario",
            "descricao_irregularidades",
            "data_contestacao",
            "data_analise",
        )
        tratados_contexto_qs = reinspecao_tratados_qs
    for pendente in pendente_qs.iterator(chunk_size=500):
        index[_key_from_pendente(pendente)] = MOTIVO_JA_NA_FILA

    tratado_qs = tratados_contexto_qs().only(
        "protocolo",
        "usuario",
        "descricao_irregularidades",
        "data_contestacao",
        "data_analise",
        "brflow_parsed",
        "origem",
        "tipo_registro",
    )
    for tratado in tratado_qs.iterator(chunk_size=500):
        key = _key_from_tratado(tratado, contexto=ctx)
        if key is None:
            continue
        index.setdefault(key, MOTIVO_JA_ANALISADO)

    return index


def dedupe_key_for_reinspecao_row(row: ParsedReinspecaoRow, *, contexto: str) -> DedupeKey:
    protocolo = (row.protocolo or "").strip()
    if contexto == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        return build_compliance_dedupe_key(
            contexto=contexto,
            protocolo=protocolo,
            matricula_inspetor=row.matricula_inspetor,
            tipo_status=row.descricao_irregularidades,
            data_conferencia=row.data_analise,
        )
    return build_reinspecao_dedupe_key(
        contexto=contexto,
        protocolo=protocolo,
        matricula_inspetor=row.matricula_inspetor,
        descricao_irregularidades=row.descricao_irregularidades,
        data_contestacao=row.data_contestacao,
    )


def partition_rows_for_persist(
    rows: list[ParsedReinspecaoRow],
    *,
    contexto: str,
    skip_finalized_ged: set[str],
    portal_index: dict[DedupeKey, str] | None = None,
) -> tuple[list[ParsedReinspecaoRow], int, int]:
    """Retorna linhas novas para gravar (uma pendente por irregularidade distinta)."""
    portal = portal_index if portal_index is not None else load_portal_dedupe_index(contexto=contexto)
    finalized_occurrences: set[ReinspecaoOccurrenceKey] = set()
    if contexto != FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        from apps.auditoria.services.reinspecao_ged_finalizados import (
            ocorrencias_finalizadas_ged,
        )

        finalized_occurrences = ocorrencias_finalizadas_ged(rows, contexto=contexto)

    # Compatibilidade de assinatura: o conjunto legado contém apenas protocolos
    # e não possui granularidade suficiente para bloquear uma ocorrência.
    del skip_finalized_ged
    kept: list[ParsedReinspecaoRow] = []
    skipped_existing = 0
    skipped_finalized_ged = 0
    seen_file: set[DedupeKey] = set()

    for row in rows:
        protocolo = (row.protocolo or "").strip()
        if not protocolo:
            continue

        key = dedupe_key_for_reinspecao_row(row, contexto=contexto)
        if key in seen_file:
            continue
        seen_file.add(key)

        if key in portal:
            skipped_existing += 1
            continue

        if key in finalized_occurrences:
            skipped_finalized_ged += 1
            continue

        kept.append(row)

    return kept, skipped_existing, skipped_finalized_ged


@dataclass
class ImportRegistroIgnorado:
    protocolo: str
    matricula_inspetor: str
    descricao: str
    data: str | None
    excel_row: int
    motivo: str
    dedupe_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocolo": self.protocolo,
            "matricula_inspetor": self.matricula_inspetor,
            "descricao": self.descricao,
            "data": self.data,
            "excel_row": self.excel_row,
            "motivo": self.motivo,
            "motivo_label": MOTIVO_LABELS.get(self.motivo, self.motivo),
            "dedupe_key": self.dedupe_key,
        }


@dataclass
class ReinspecaoPortalOccurrence:
    pendente: QualidadePendenteReinspecao | None = None
    tratado: AuditoriaFalhaCadastro | None = None


def load_reinspecao_portal_occurrences(
    *,
    contexto: str,
) -> dict[ReinspecaoOccurrenceKey, ReinspecaoPortalOccurrence]:
    """Carrega os objetos exatos do portal pela identidade canônica."""
    index: dict[ReinspecaoOccurrenceKey, ReinspecaoPortalOccurrence] = {}
    pendentes = QualidadePendenteReinspecao.objects.filter(contexto=contexto).only(
        "contexto",
        "protocolo",
        "descricao_irregularidades",
        "data_contestacao",
    )
    for pendente in pendentes.iterator(chunk_size=500):
        key = build_reinspecao_occurrence_key(
            contexto=contexto,
            protocolo=pendente.protocolo,
            descricao_irregularidades=pendente.descricao_irregularidades,
            data_contestacao=pendente.data_contestacao,
        )
        index.setdefault(key, ReinspecaoPortalOccurrence()).pendente = pendente

    tratados = reinspecao_tratados_qs().only(
        "protocolo",
        "descricao_irregularidades",
        "data_contestacao",
        "brflow_parsed",
        "origem",
        "tipo_registro",
    )
    for tratado in tratados.iterator(chunk_size=500):
        key = build_reinspecao_occurrence_key(
            contexto=contexto,
            protocolo=tratado.protocolo,
            descricao_irregularidades=tratado.descricao_irregularidades,
            data_contestacao=tratado.data_contestacao,
        )
        index.setdefault(key, ReinspecaoPortalOccurrence()).tratado = tratado
    return index


def filter_import_rows(
    rows: list[T],
    *,
    contexto: str,
    key_builder: Callable[..., DedupeKey],
    row_snapshot: Callable[[T], dict[str, Any]],
    portal_index: dict[DedupeKey, str] | None = None,
) -> tuple[list[T], list[ImportRegistroIgnorado], list[ImportRegistroIgnorado]]:
    """Remove duplicatas no arquivo e no portal; mantém a primeira ocorrência do arquivo."""
    portal = portal_index if portal_index is not None else load_portal_dedupe_index(contexto=contexto)
    kept: list[T] = []
    duplicados_arquivo: list[ImportRegistroIgnorado] = []
    ignorados_portal: list[ImportRegistroIgnorado] = []
    seen_file: set[DedupeKey] = set()

    for row in rows:
        snapshot = row_snapshot(row)
        key = key_builder(contexto=contexto, **snapshot)
        dedupe_hash = dedupe_key_hash(key)
        ignorado_base = ImportRegistroIgnorado(
            protocolo=str(snapshot.get("protocolo") or ""),
            matricula_inspetor=str(snapshot.get("matricula_inspetor") or ""),
            descricao=str(snapshot.get("descricao") or ""),
            data=snapshot.get("data_display"),
            excel_row=int(snapshot.get("excel_row") or 0),
            motivo="",
            dedupe_key=dedupe_hash,
        )

        if key in seen_file:
            ignorado = ImportRegistroIgnorado(
                **{**ignorado_base.__dict__, "motivo": MOTIVO_DUPLICADO_ARQUIVO}
            )
            duplicados_arquivo.append(ignorado)
            continue
        seen_file.add(key)

        portal_motivo = portal.get(key)
        if portal_motivo:
            ignorado = ImportRegistroIgnorado(
                **{**ignorado_base.__dict__, "motivo": portal_motivo}
            )
            ignorados_portal.append(ignorado)
            continue

        kept.append(row)

    return kept, duplicados_arquivo, ignorados_portal


def reinspecao_row_snapshot(row: ParsedReinspecaoRow) -> dict[str, Any]:
    return {
        "protocolo": row.protocolo,
        "matricula_inspetor": row.matricula_inspetor,
        "descricao": row.descricao_irregularidades,
        "data_display": row.data_contestacao.isoformat() if row.data_contestacao else None,
        "excel_row": row.excel_row,
        "descricao_irregularidades": row.descricao_irregularidades,
        "data_contestacao": row.data_contestacao,
    }


def compliance_row_snapshot(row: Any) -> dict[str, Any]:
    data = row.data_conferencia
    return {
        "protocolo": row.protocolo,
        "matricula_inspetor": row.matricula_inspetor,
        "descricao": row.tipo_status or "Conferência",
        "data_display": data.isoformat() if data else None,
        "excel_row": row.excel_row,
        "tipo_status": row.tipo_status,
        "data_conferencia": row.data_conferencia,
    }


def apply_reinspecao_import_dedupe(
    rows: list[ParsedReinspecaoRow],
    *,
    contexto: str | None = None,
) -> tuple[list[ParsedReinspecaoRow], list[ImportRegistroIgnorado], list[ImportRegistroIgnorado]]:
    ctx = contexto or fila_contexto_atual()
    return filter_import_rows(
        rows,
        contexto=ctx,
        key_builder=lambda **kwargs: build_reinspecao_dedupe_key(
            contexto=kwargs["contexto"],
            protocolo=kwargs["protocolo"],
            matricula_inspetor=kwargs["matricula_inspetor"],
            descricao_irregularidades=kwargs["descricao_irregularidades"],
            data_contestacao=kwargs["data_contestacao"],
        ),
        row_snapshot=reinspecao_row_snapshot,
    )


def apply_compliance_import_dedupe(
    rows: list[Any],
    *,
    contexto: str | None = None,
) -> tuple[list[Any], list[ImportRegistroIgnorado], list[ImportRegistroIgnorado]]:
    ctx = contexto or fila_contexto_atual()
    return filter_import_rows(
        rows,
        contexto=ctx,
        key_builder=lambda **kwargs: build_compliance_dedupe_key(
            contexto=kwargs["contexto"],
            protocolo=kwargs["protocolo"],
            matricula_inspetor=kwargs["matricula_inspetor"],
            tipo_status=kwargs["tipo_status"],
            data_conferencia=kwargs["data_conferencia"],
        ),
        row_snapshot=compliance_row_snapshot,
    )


def ignorados_to_preview(
    duplicados_arquivo: Iterable[ImportRegistroIgnorado],
    ignorados_portal: Iterable[ImportRegistroIgnorado],
) -> dict[str, Any]:
    dup_list = list(duplicados_arquivo)
    portal_list = list(ignorados_portal)
    return {
        "protocolos_duplicados_arquivo": sorted({item.protocolo for item in dup_list if item.protocolo}),
        "protocolos_ignorados": [item.to_dict() for item in (*dup_list, *portal_list)],
        "total_ignorados_duplicata": len(dup_list) + len(portal_list),
    }


def build_registros_importacao(
    *,
    kept_rows: Iterable[Any],
    duplicados_arquivo: Iterable[ImportRegistroIgnorado],
    ignorados_portal: Iterable[ImportRegistroIgnorado],
    row_to_registro: Callable[[Any], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Lista unificada para a tela de importação (OK + duplicados), ordenada por linha."""
    registros: list[dict[str, Any]] = []
    for row in kept_rows:
        base = row_to_registro(row)
        registros.append(
            {
                **base,
                "status_importacao": "ok",
                "motivo": None,
                "motivo_label": None,
            }
        )
    for ignorado in (*duplicados_arquivo, *ignorados_portal):
        registros.append(
            {
                "status_importacao": "duplicado",
                "protocolo": ignorado.protocolo,
                "matricula_inspetor": ignorado.matricula_inspetor,
                "descricao": ignorado.descricao,
                "data": ignorado.data,
                "excel_row": ignorado.excel_row,
                "motivo": ignorado.motivo,
                "motivo_label": MOTIVO_LABELS.get(ignorado.motivo, ignorado.motivo),
                "dedupe_key": ignorado.dedupe_key,
            }
        )
    registros.sort(
        key=lambda item: (
            int(item.get("excel_row") or 0),
            str(item.get("protocolo") or ""),
        )
    )
    return registros


def reinspecao_row_registro(row: ParsedReinspecaoRow) -> dict[str, Any]:
    return {
        "protocolo": row.protocolo,
        "matricula_inspetor": row.matricula_inspetor,
        "descricao": row.descricao_irregularidades,
        "data": row.data_contestacao.isoformat() if row.data_contestacao else None,
        "excel_row": row.excel_row,
        "dedupe_key": dedupe_key_hash(
            build_reinspecao_dedupe_key(
                contexto=fila_contexto_atual(),
                protocolo=row.protocolo,
                matricula_inspetor=row.matricula_inspetor,
                descricao_irregularidades=row.descricao_irregularidades,
                data_contestacao=row.data_contestacao,
            )
        ),
    }


def compliance_row_registro(
    row: Any,
    *,
    contexto: str = FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
) -> dict[str, Any]:
    data = row.data_conferencia
    return {
        "protocolo": row.protocolo,
        "matricula_inspetor": row.matricula_inspetor,
        "descricao": row.tipo_status or "Conferência",
        "data": data.isoformat() if data else None,
        "excel_row": row.excel_row,
        "dedupe_key": dedupe_key_hash(
            build_compliance_dedupe_key(
                contexto=contexto,
                protocolo=row.protocolo,
                matricula_inspetor=row.matricula_inspetor,
                tipo_status=row.tipo_status,
                data_conferencia=row.data_conferencia,
            )
        ),
    }
