# -*- coding: utf-8 -*-
"""Exportações CSV somente leitura, com os mesmos filtros das listas de detalhe."""

from __future__ import annotations

import csv
from datetime import date, datetime
from types import SimpleNamespace
from typing import Iterable, Iterator

from django.http import StreamingHttpResponse

from apps.qualidade_operacional.services.analytics import (
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.enrichment import (
    build_all_agents_lookup,
    build_dim_lookups,
    build_observacao_auditor_lookup,
    build_responsavel_lookup,
    dias_entre_auditoria_e_origem,
    responsavel_tipo_label,
)
from apps.qualidade_operacional.services.irregularidade_display import (
    irregularidade_apontada_falha,
)
from apps.qualidade_operacional.services.normalize import is_processual_tipificacao
from apps.qualidade_operacional.services.queries import (
    apply_detail_column_filters,
    apply_detail_sort,
    date_field_auditados,
    date_field_falhas,
)

VIRTUAL_EXPORT_FIELDS = frozenset(
    {
        "dias_prazo",
        "agente_nome",
        "cliente_nome",
        "workflow_nome",
        "irregularidade_apontada",
        "observacao_auditor",
        "responsavel_nome",
        "responsavel_tipo",
        "responsavel_matricula",
        "regra_responsabilidade",
    }
)
LOOKUP_EXPORT_FIELDS = ("id_cliente", "id_workflow")

WORKFORCE_EXPORT_COLUMNS = (
    ("Responsável", "responsavel_nome"),
    ("Tipo responsável", "responsavel_tipo"),
    ("Matrícula responsável", "responsavel_matricula"),
    ("Jornada", "regra_responsabilidade"),
)

AUDITADOS_COLUMNS = (
    ("Data de auditoria", "data"),
    ("Data de análise", "data_analise"),
    ("Diff (dias)", "dias_prazo"),
    ("Protocolo", "protocolo"),
    ("Fonte", "source_file"),
    ("Cliente", "cliente_nome"),
    ("Workflow", "workflow_nome"),
    ("Agente", "agente_nome"),
    ("Matrícula", "matricula"),
    ("Matrícula auditor", "matricula_auditor"),
    ("Tipo de análise", "tipo_analise"),
    ("Tipo de conclusão", "tipo_conclusao"),
    ("Etapa", "etapa"),
    ("Resultado origem", "resultado_origem"),
    ("Resultado destino", "resultado_destino"),
    ("Tipo de registro", "tipo_registro"),
    ("Origem tratado", "origem_tratado"),
    ("Procedência", "procedencia"),
)

FALHAS_COLUMNS = (
    ("Data de auditoria", "data"),
    ("Data de análise", "data_analise"),
    ("Diff (dias)", "dias_prazo"),
    ("Protocolo", "protocolo"),
    ("Fonte", "source_file"),
    ("Cliente", "cliente_nome"),
    ("Workflow", "workflow_nome"),
    ("Agente", "agente_nome"),
    ("Matrícula", "matricula"),
    ("Usuário auditor", "usuario_auditor"),
    ("Tipo de análise", "tipo_analise"),
    ("Tipo de falha", "tipo_falha"),
    ("Tipo de falha oficial", "tipo_falha_oficial"),
    ("Categoria da falha", "categoria_falha"),
    ("Nível de dificuldade", "nivel_dificuldade"),
    ("Nível conferido", "nivel_dificuldade_confer"),
    ("Módulo", "modulo"),
    ("Localidade", "localidade"),
    ("Líder", "lider"),
    ("Etapa", "etapa"),
    ("Tipo de documento", "tipo_documento"),
    ("Irregularidade apontada", "irregularidade_apontada"),
    ("Observação do auditor", "observacao_auditor"),
    ("Tipo de registro", "tipo_registro"),
    ("Origem tratado", "origem_tratado"),
    ("Procedência", "procedencia"),
)


class _Echo:
    def write(self, value: str) -> str:
        return value


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _stream_rows(headers: Iterable[str], rows: Iterable[tuple]) -> Iterator[str]:
    writer = csv.writer(_Echo(), delimiter=";", lineterminator="\r\n")
    yield "\ufeff" + writer.writerow(headers)
    for row in rows:
        yield writer.writerow([_csv_value(value) for value in row])


def _workforce_export_enabled(params) -> bool:
    if not hasattr(params, "get"):
        return False
    raw = str(params.get("workforce_only") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    scope = str(params.get("responsibility_scope") or "").strip().lower()
    return scope in {"lider", "facilitador"}


def _columns_with_workforce(base_columns: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for header, field in base_columns:
        out.append((header, field))
        if field == "matricula":
            out.extend(WORKFORCE_EXPORT_COLUMNS)
    return tuple(out)


def _export_filename(kind: str, params) -> str:
    scope = ""
    if hasattr(params, "get"):
        scope = str(params.get("responsibility_scope") or "").strip().lower()
    suffix = "_facilitador" if scope == "facilitador" else ""
    return f"qualidade_operacional_{kind}{suffix}.csv"


def _is_processual_row(kind: str, row_dict: dict) -> bool:
    return kind == "falhas" and is_processual_tipificacao(row_dict.get("tipo_falha"))


def _resolve_export_field(
    field: str,
    row_dict: dict,
    *,
    kind: str,
    agents: dict[str, str],
    clientes: dict[int, str],
    workflows: dict[int, str],
) -> object:
    if field == "dias_prazo":
        return dias_entre_auditoria_e_origem(
            row_dict.get("data"), row_dict.get("data_analise")
        )
    if _is_processual_row(kind, row_dict) and field in {"agente_nome", "matricula"}:
        return ""
    if field == "agente_nome":
        mat = (row_dict.get("matricula") or "").strip().lower()
        return agents.get(mat) if mat else ""
    if field == "cliente_nome":
        cid = row_dict.get("id_cliente")
        return clientes.get(cid, "") if cid else ""
    if field == "workflow_nome":
        wid = row_dict.get("id_workflow")
        return workflows.get(wid, "") if wid else ""
    if field == "irregularidade_apontada":
        return irregularidade_apontada_falha(
            des_problemas=str(row_dict.get("des_problemas") or ""),
            cenario=str(row_dict.get("cenario") or ""),
        )
    if field == "observacao_auditor":
        return row_dict.get("observacao_auditor") or ""
    if field == "responsavel_tipo":
        return row_dict.get("responsavel_tipo") or ""
    return row_dict.get(field)


def _apply_observacao_auditor_enrichment(chunk_dicts: list[dict]) -> None:
    if not chunk_dicts:
        return
    lookup_rows = [
        SimpleNamespace(pk=row["pk"], source_file=row.get("source_file"))
        for row in chunk_dicts
    ]
    lookup = build_observacao_auditor_lookup(lookup_rows)
    for row_dict in chunk_dicts:
        row_dict["observacao_auditor"] = lookup.get(row_dict["pk"], "")


def _row_to_export_tuple(
    row_dict: dict,
    *,
    fields: list[str],
    kind: str,
    agents: dict[str, str],
    clientes: dict[int, str],
    workflows: dict[int, str],
) -> tuple:
    return tuple(
        _resolve_export_field(
            field,
            row_dict,
            kind=kind,
            agents=agents,
            clientes=clientes,
            workflows=workflows,
        )
        for field in fields
    )


def build_csv_export(params, *, kind: str) -> StreamingHttpResponse:
    workforce = _workforce_export_enabled(params)
    if kind == "falhas":
        base_columns = FALHAS_COLUMNS
        date_field = date_field_falhas(params)
        qs = apply_detail_column_filters(
            filtered_falhas(params), params, kind="falhas"
        )
    else:
        base_columns = AUDITADOS_COLUMNS
        date_field = date_field_auditados(params)
        qs = apply_detail_column_filters(
            filtered_auditados(params), params, kind="auditados"
        )

    columns = _columns_with_workforce(base_columns) if workforce else base_columns
    fields = [field for _, field in columns]
    model_fields = list(
        dict.fromkeys(
            [field for field in fields if field not in VIRTUAL_EXPORT_FIELDS]
            + list(LOOKUP_EXPORT_FIELDS)
            + (["des_problemas", "cenario"] if kind == "falhas" else [])
        )
    )
    pairs = qs.values_list("id_cliente", "id_workflow").distinct()
    cliente_ids = {cid for cid, _ in pairs if cid}
    workflow_ids = {wid for _, wid in pairs if wid}
    clientes, workflows, _ = build_dim_lookups(cliente_ids, workflow_ids, set())
    agents = build_all_agents_lookup()
    include_observacao_auditor = kind == "falhas" and "observacao_auditor" in fields

    def _iter_rows():
        ordered = apply_detail_sort(qs, params, kind=kind, default_date_field=date_field)
        chunk_dicts: list[dict] = []
        value_fields = list(model_fields)
        if (workforce or include_observacao_auditor) and "pk" not in value_fields:
            value_fields = ["pk", *value_fields]

        def _flush_chunk(*, with_workforce: bool) -> Iterator[tuple]:
            if not chunk_dicts:
                return
            if include_observacao_auditor:
                _apply_observacao_auditor_enrichment(chunk_dicts)
            if with_workforce:
                lookup_rows = [
                    SimpleNamespace(
                        pk=row["pk"],
                        matricula=row.get("matricula"),
                        **{date_field: row.get(date_field)},
                    )
                    for row in chunk_dicts
                ]
                lookup = build_responsavel_lookup(lookup_rows, date_field=date_field)
                for row_dict in chunk_dicts:
                    meta = lookup.get(row_dict["pk"]) or {}
                    enriched = {
                        **row_dict,
                        "responsavel_nome": meta.get("responsavel_nome") or "",
                        "responsavel_tipo": responsavel_tipo_label(meta.get("responsabilidade")),
                        "responsavel_matricula": meta.get("responsavel_matricula") or "",
                        "regra_responsabilidade": meta.get("regra_responsabilidade") or "",
                    }
                    yield _row_to_export_tuple(
                        enriched,
                        fields=fields,
                        kind=kind,
                        agents=agents,
                        clientes=clientes,
                        workflows=workflows,
                    )
            else:
                for row_dict in chunk_dicts:
                    yield _row_to_export_tuple(
                        row_dict,
                        fields=fields,
                        kind=kind,
                        agents=agents,
                        clientes=clientes,
                        workflows=workflows,
                    )
            chunk_dicts.clear()

        chunk_size = 500 if workforce or include_observacao_auditor else 2000
        for raw in ordered.values_list(*value_fields).iterator(chunk_size=chunk_size):
            row_dict = dict(zip(value_fields, raw))
            chunk_dicts.append(row_dict)
            if len(chunk_dicts) >= chunk_size:
                yield from _flush_chunk(with_workforce=workforce)
        yield from _flush_chunk(with_workforce=workforce)

    response = StreamingHttpResponse(
        _stream_rows((header for header, _ in columns), _iter_rows()),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{_export_filename(kind, params)}"'
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
