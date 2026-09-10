# -*- coding: utf-8 -*-
"""Filtros e ordenação por coluna do módulo Detalhe (Qualidade Operacional)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Literal

from django.db.models import Case, CharField, F, Q, QuerySet, Value, When

from apps.qualidade_operacional.services.criticidade import criticidade_label
from apps.qualidade_operacional.services.irregularidade_display import (
    irregularidade_apontada_falha,
)
from apps.qualidade_operacional.services.normalize import (
    is_processual_tipificacao,
    nivel_dificuldade_efetivo,
)
from apps.qualidade_operacional.services.queries import (
    exclude_processual_agent_links,
    param_list,
)
from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE

EMPTY_LABEL = "—"
ColumnKind = Literal["text", "date", "number", "dimension", "agent", "source", "responsavel"]

AUDITADOS_COLUMN_KEYS = (
    "data",
    "data_analise",
    "protocolo",
    "source",
    "id_cliente",
    "id_workflow",
    "matricula",
    "tipo_analise",
    "tipo_conclusao",
    "etapa",
    "resultado_origem",
    "responsavel_nome",
)

FALHAS_COLUMN_KEYS = AUDITADOS_COLUMN_KEYS + (
    "tipo_falha_display",
    "categoria_falha",
    "nivel_dificuldade",
    "modulo",
    "localidade",
    "lider",
    "tipo_documento",
    "cenario",
    "localidade_documento",
)


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    kind: ColumnKind
    db_fields: tuple[str, ...] = ()
    sort_field: str | None = None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def format_detail_date(value) -> str:
    if value is None:
        return EMPTY_LABEL
    if hasattr(value, "strftime"):
        return value.strftime("%d-%m-%Y")
    parsed = _parse_date(str(value))
    return parsed.strftime("%d-%m-%Y") if parsed else EMPTY_LABEL


def source_display(source_file: str | None) -> str:
    if (source_file or "").strip() == INTRANET_SOURCE_FILE:
        return "Portal"
    return "Arquivo TSV"


def _text_display(value) -> str:
    text = str(value or "").strip()
    return text or EMPTY_LABEL


def _tipo_falha_display(row) -> str:
    official = str(getattr(row, "tipo_falha_oficial", "") or "").strip()
    raw = str(getattr(row, "tipo_falha", "") or "").strip()
    return official or raw or EMPTY_LABEL


def _nivel_dificuldade_display(row) -> str:
    value = nivel_dificuldade_efetivo(
        getattr(row, "nivel_dificuldade_confer", ""),
        getattr(row, "nivel_dificuldade", ""),
    )
    return str(value or "").strip() or "Não informado"


def _categoria_display(row) -> str:
    return criticidade_label(
        getattr(row, "categoria_falha", ""),
        id_cliente=getattr(row, "id_cliente", None),
        tipo_registro=getattr(row, "tipo_registro", None),
    ) or EMPTY_LABEL


def _build_agent_lookup(qs: QuerySet) -> dict[str, str]:
    from apps.workforce.models import Agent

    mats = {
        (m or "").strip().lower()
        for m in qs.values_list("matricula", flat=True).distinct()
        if (m or "").strip()
    }
    if not mats:
        return {}
    return {
        (lan or "").strip().lower(): (name or "").strip()
        for lan, name in Agent.objects.filter(user_lan_id__in=mats).values_list(
            "user_lan_id", "full_name"
        )
        if (lan or "").strip()
    }


def _build_dim_lookups(qs: QuerySet) -> tuple[dict[int, str], dict[int, str]]:
    from apps.dimensoes_processos.models import DimCliente, DimWorkflow

    cliente_ids = {
        cid for cid in qs.values_list("id_cliente", flat=True).distinct() if cid
    }
    workflow_ids = {
        wid for wid in qs.values_list("id_workflow", flat=True).distinct() if wid
    }
    clientes = {
        cid: (nome or "").strip() or str(cid)
        for cid, nome in DimCliente.objects.filter(id_cliente__in=cliente_ids).values_list(
            "id_cliente", "nome"
        )
    }
    workflows = {
        wid: (nome or "").strip() or str(wid)
        for wid, nome in DimWorkflow.objects.filter(id_workflow__in=workflow_ids).values_list(
            "id_workflow", "nome"
        )
    }
    return clientes, workflows


def _agent_display(matricula: str | None, agents: dict[str, str]) -> str:
    mat = (matricula or "").strip().lower()
    if not mat:
        return EMPTY_LABEL
    return agents.get(mat) or mat


def _cliente_display(cliente_id, clientes: dict[int, str]) -> str:
    if not cliente_id:
        return EMPTY_LABEL
    return clientes.get(cliente_id) or str(cliente_id)


def _workflow_display(workflow_id, workflows: dict[int, str]) -> str:
    if not workflow_id:
        return EMPTY_LABEL
    return workflows.get(workflow_id) or str(workflow_id)


def _column_specs(kind: str) -> dict[str, ColumnSpec]:
    specs = {
        "data": ColumnSpec("data", "date", ("data",), "data"),
        "data_analise": ColumnSpec("data_analise", "date", ("data_analise",), "data_analise"),
        "protocolo": ColumnSpec("protocolo", "text", ("protocolo",), "protocolo"),
        "source": ColumnSpec("source", "source", ("source_file",), "source_file"),
        "id_cliente": ColumnSpec("id_cliente", "dimension", ("id_cliente",), "id_cliente"),
        "id_workflow": ColumnSpec(
            "id_workflow", "dimension", ("id_workflow",), "id_workflow"
        ),
        "matricula": ColumnSpec("matricula", "agent", ("matricula",), "matricula"),
        "tipo_analise": ColumnSpec("tipo_analise", "text", ("tipo_analise",), "tipo_analise"),
        "tipo_conclusao": ColumnSpec(
            "tipo_conclusao", "text", ("tipo_conclusao",), "tipo_conclusao"
        ),
        "etapa": ColumnSpec("etapa", "text", ("etapa",), "etapa"),
        "resultado_origem": ColumnSpec(
            "resultado_origem", "text", ("resultado_origem",), "resultado_origem"
        ),
        "responsavel_nome": ColumnSpec("responsavel_nome", "responsavel", (), None),
    }
    if kind == "falhas":
        specs.update(
            {
                "tipo_falha_display": ColumnSpec(
                    "tipo_falha_display",
                    "text",
                    ("tipo_falha_oficial", "tipo_falha"),
                    "tipo_falha_oficial",
                ),
                "categoria_falha": ColumnSpec(
                    "categoria_falha", "text", ("categoria_falha",), "categoria_falha"
                ),
                "nivel_dificuldade": ColumnSpec(
                    "nivel_dificuldade",
                    "text",
                    ("nivel_dificuldade_confer", "nivel_dificuldade"),
                    "nivel_dificuldade_confer",
                ),
                "modulo": ColumnSpec("modulo", "text", ("modulo",), "modulo"),
                "localidade": ColumnSpec("localidade", "text", ("localidade",), "localidade"),
                "lider": ColumnSpec("lider", "text", ("lider",), "lider"),
                "tipo_documento": ColumnSpec(
                    "tipo_documento", "text", ("tipo_documento",), "tipo_documento"
                ),
                "cenario": ColumnSpec(
                    "cenario",
                    "text",
                    ("des_problemas", "cenario"),
                    "des_problemas",
                ),
                "localidade_documento": ColumnSpec(
                    "localidade_documento",
                    "text",
                    ("localidade_documento",),
                    "localidade_documento",
                ),
            }
        )
    return specs


def _display_for_row(
    row,
    spec: ColumnSpec,
    *,
    agents: dict[str, str],
    clientes: dict[int, str],
    workflows: dict[int, str],
) -> str:
    if spec.kind == "date":
        return format_detail_date(getattr(row, spec.db_fields[0], None))
    if spec.kind == "source":
        return source_display(getattr(row, "source_file", None))
    if spec.kind == "dimension":
        field = spec.db_fields[0]
        value = getattr(row, field, None)
        if field == "id_cliente":
            return _cliente_display(value, clientes)
        return _workflow_display(value, workflows)
    if spec.kind == "agent":
        if is_processual_tipificacao(getattr(row, "tipo_falha", "")):
            return EMPTY_LABEL
        return _agent_display(getattr(row, "matricula", None), agents)
    if spec.key == "tipo_falha_display":
        return _tipo_falha_display(row)
    if spec.key == "nivel_dificuldade":
        return _nivel_dificuldade_display(row)
    if spec.key == "categoria_falha":
        return _categoria_display(row)
    if spec.key == "cenario":
        return _text_display(
            irregularidade_apontada_falha(
                des_problemas=getattr(row, "des_problemas", ""),
                cenario=getattr(row, "cenario", ""),
            )
        )
    if spec.db_fields:
        return _text_display(getattr(row, spec.db_fields[0], None))
    return EMPTY_LABEL


def _responsavel_date_field(params: Any, *, kind: str) -> str:
    from apps.qualidade_operacional.services.queries import (
        date_field_auditados,
        date_field_falhas,
    )

    if kind == "falhas":
        return date_field_falhas(params)
    return date_field_auditados(params)


def _apply_responsavel_filter(
    qs: QuerySet,
    values: list[str],
    *,
    kind: str,
    params: Any,
    icontains: bool = False,
) -> QuerySet:
    if not values:
        return qs
    date_field = _responsavel_date_field(params, kind=kind)
    wanted = {label for label in values}
    include_empty = EMPTY_LABEL in wanted
    matching_pks: list[int] = []
    chunk_rows: list[SimpleNamespace] = []

    def _flush() -> None:
        nonlocal chunk_rows
        if not chunk_rows:
            return
        from apps.qualidade_operacional.services.enrichment import build_responsavel_lookup

        lookup = build_responsavel_lookup(chunk_rows, date_field=date_field)
        for row in chunk_rows:
            meta = lookup.get(row.pk) or {}
            label = (meta.get("responsavel_nome") or "").strip() or EMPTY_LABEL
            matched = False
            if icontains and len(values) == 1:
                needle = values[0].casefold()
                if not needle or needle == EMPTY_LABEL.casefold():
                    matched = label == EMPTY_LABEL
                else:
                    matched = needle in label.casefold()
            elif label in wanted or (include_empty and label == EMPTY_LABEL):
                matched = True
            if matched:
                matching_pks.append(row.pk)
        chunk_rows = []

    for pk, matricula, effective_date in qs.values_list(
        "pk", "matricula", date_field
    ).iterator(chunk_size=500):
        chunk_rows.append(
            SimpleNamespace(pk=pk, matricula=matricula, **{date_field: effective_date})
        )
        if len(chunk_rows) >= 500:
            _flush()
    _flush()
    if not matching_pks:
        return qs.none()
    return qs.filter(pk__in=matching_pks)


def _build_responsavel_column_values(
    qs: QuerySet,
    *,
    kind: str,
    params: Any,
    q: str = "",
    limit: int = 200,
) -> dict:
    date_field = _responsavel_date_field(params, kind=kind)
    counter: Counter[str] = Counter()
    search = (q or "").strip().casefold()
    chunk_rows: list[SimpleNamespace] = []

    def _flush() -> None:
        nonlocal chunk_rows
        if not chunk_rows:
            return
        from apps.qualidade_operacional.services.enrichment import build_responsavel_lookup

        lookup = build_responsavel_lookup(chunk_rows, date_field=date_field)
        for row in chunk_rows:
            meta = lookup.get(row.pk) or {}
            label = (meta.get("responsavel_nome") or "").strip() or EMPTY_LABEL
            if search and search not in label.casefold():
                continue
            counter[label] += 1
        chunk_rows = []

    for pk, matricula, effective_date in qs.values_list(
        "pk", "matricula", date_field
    ).iterator(chunk_size=500):
        chunk_rows.append(
            SimpleNamespace(pk=pk, matricula=matricula, **{date_field: effective_date})
        )
        if len(chunk_rows) >= 500:
            _flush()
    _flush()

    ordered = sorted(counter.keys(), key=lambda item: item.casefold())
    truncated = len(ordered) > limit
    values = [
        {"value": label, "label": label, "count": counter[label]}
        for label in ordered[:limit]
    ]
    return {
        "ok": True,
        "column": "responsavel_nome",
        "values": values,
        "truncated": truncated,
    }


def build_column_values(
    qs: QuerySet,
    column: str,
    *,
    kind: str,
    q: str = "",
    limit: int = 200,
    params: Any = None,
) -> dict:
    specs = _column_specs(kind)
    spec = specs.get(column)
    if not spec:
        return {"ok": False, "column": column, "values": [], "truncated": False}

    if spec.kind == "responsavel":
        return _build_responsavel_column_values(
            qs, kind=kind, params=params or {}, q=q, limit=limit
        )

    agents = _build_agent_lookup(qs) if spec.kind == "agent" else {}
    clientes, workflows = _build_dim_lookups(qs) if spec.kind == "dimension" else ({}, {})

    counter: Counter[str] = Counter()
    search = (q or "").strip().casefold()
    for row in qs.iterator(chunk_size=500):
        label = _display_for_row(
            row,
            spec,
            agents=agents,
            clientes=clientes,
            workflows=workflows,
        )
        if search and search not in label.casefold():
            continue
        counter[label] += 1

    ordered = sorted(counter.keys(), key=lambda item: item.casefold())
    truncated = len(ordered) > limit
    values = [
        {"value": label, "label": label, "count": counter[label]}
        for label in ordered[:limit]
    ]
    return {
        "ok": True,
        "column": column,
        "values": values,
        "truncated": truncated,
    }


def _legacy_text_filter(qs: QuerySet, field: str, value: str) -> QuerySet:
    return qs.filter(**{f"{field}__icontains": value})


def _legacy_detail_agent(qs: QuerySet, value: str) -> QuerySet:
    from apps.workforce.models import Agent

    matriculas = list(
        Agent.objects.filter(
            Q(full_name__icontains=value) | Q(user_lan_id__icontains=value)
        ).values_list("user_lan_id", flat=True)
    )
    query = Q(matricula__icontains=value)
    if matriculas:
        query |= Q(matricula__in=matriculas)
    return qs.filter(query)


def _legacy_detail_dimension(qs: QuerySet, field: str, value: str) -> QuerySet:
    from apps.dimensoes_processos.models import DimCliente, DimWorkflow

    model = DimCliente if field == "id_cliente" else DimWorkflow
    ids = list(
        model.objects.filter(nome__icontains=value).values_list(field, flat=True)
    )
    try:
        ids.append(int(value))
    except (TypeError, ValueError):
        pass
    if not ids:
        return qs.none()
    return qs.filter(**{f"{field}__in": ids})


def _legacy_detail_source(qs: QuerySet, value: str) -> QuerySet:
    folded = value.casefold()
    if folded and ("portal".startswith(folded) or "intranet".startswith(folded)):
        return qs.filter(source_file=INTRANET_SOURCE_FILE)
    if folded and ("arquivo tsv".startswith(folded) or "tsv".startswith(folded)):
        return qs.exclude(source_file=INTRANET_SOURCE_FILE)
    return qs.filter(source_file__icontains=value)


def _apply_legacy_column_filter(
    qs: QuerySet, spec: ColumnSpec, value: str, *, kind: str, params: Any = None
) -> QuerySet:
    if spec.kind == "responsavel":
        return _apply_responsavel_filter(
            qs, [value], kind=kind, params=params or {}, icontains=True
        )
    if spec.kind == "date":
        parsed = _parse_date(value)
        return qs.filter(**{spec.db_fields[0]: parsed}) if parsed else qs.none()
    if spec.kind == "source":
        return _legacy_detail_source(qs, value)
    if spec.kind == "dimension":
        return _legacy_detail_dimension(qs, spec.db_fields[0], value)
    if spec.kind == "agent":
        qs = _legacy_detail_agent(qs, value)
        if kind == "falhas":
            qs = exclude_processual_agent_links(qs)
        return qs
    if spec.key == "tipo_falha_display":
        return qs.filter(
            Q(tipo_falha_oficial__icontains=value) | Q(tipo_falha__icontains=value)
        )
    if spec.key == "nivel_dificuldade":
        return qs.filter(
            Q(nivel_dificuldade_confer__icontains=value)
            | (Q(nivel_dificuldade_confer="") & Q(nivel_dificuldade__icontains=value))
        )
    if spec.key == "categoria_falha":
        return qs.filter(categoria_falha__icontains=value)
    if spec.key == "cenario":
        return qs.filter(
            Q(des_problemas__icontains=value)
            | (
                (Q(des_problemas="") | Q(des_problemas__isnull=True))
                & Q(cenario__icontains=value)
            )
        )
    if spec.db_fields:
        return _legacy_text_filter(qs, spec.db_fields[0], value)
    return qs


def _resolve_agent_matriculas(labels: list[str]) -> set[str]:
    from apps.workforce.models import Agent

    wanted = {label.casefold() for label in labels}
    mats: set[str] = set()
    for lan, name in Agent.objects.values_list("user_lan_id", "full_name"):
        lan_text = (lan or "").strip().lower()
        name_text = (name or "").strip()
        if name_text.casefold() in wanted:
            mats.add(lan_text)
        if lan_text.casefold() in wanted:
            mats.add(lan_text)
    for label in labels:
        if label == EMPTY_LABEL:
            continue
        text = label.strip().lower()
        if text:
            mats.add(text)
    return mats


def _resolve_dimension_ids(field: str, labels: list[str]) -> set[int]:
    from apps.dimensoes_processos.models import DimCliente, DimWorkflow

    model = DimCliente if field == "id_cliente" else DimWorkflow
    wanted = {label.casefold() for label in labels if label != EMPTY_LABEL}
    ids: set[int] = set()
    for pk, nome in model.objects.values_list(field, "nome"):
        label = (nome or "").strip() or str(pk)
        if label.casefold() in wanted:
            ids.add(pk)
    for label in labels:
        if label == EMPTY_LABEL:
            continue
        try:
            ids.add(int(label))
        except (TypeError, ValueError):
            continue
    return ids


def _apply_multi_column_filter(
    qs: QuerySet, spec: ColumnSpec, values: list[str], *, kind: str, params: Any = None
) -> QuerySet:
    if not values:
        return qs

    if spec.kind == "responsavel":
        return _apply_responsavel_filter(qs, values, kind=kind, params=params or {})

    if spec.kind == "date":
        dates = []
        include_empty = EMPTY_LABEL in values
        for label in values:
            if label == EMPTY_LABEL:
                continue
            parsed = _parse_date(label)
            if parsed is not None:
                dates.append(parsed)
        q = Q()
        if dates:
            q |= Q(**{f"{spec.db_fields[0]}__in": dates})
        if include_empty:
            q |= Q(**{f"{spec.db_fields[0]}__isnull": True})
        return qs.filter(q) if q else qs.none()

    if spec.kind == "source":
        q = Q()
        if "Portal" in values:
            q |= Q(source_file=INTRANET_SOURCE_FILE)
        if "Arquivo TSV" in values:
            q |= ~Q(source_file=INTRANET_SOURCE_FILE)
        return qs.filter(q) if q else qs.none()

    if spec.kind == "dimension":
        ids = _resolve_dimension_ids(spec.db_fields[0], values)
        q = Q()
        if ids:
            q |= Q(**{f"{spec.db_fields[0]}__in": list(ids)})
        if EMPTY_LABEL in values:
            q |= Q(**{f"{spec.db_fields[0]}__isnull": True})
        return qs.filter(q) if q else qs.none()

    if spec.kind == "agent":
        mats = _resolve_agent_matriculas(values)
        q = Q()
        if mats:
            q |= Q(matricula__in=list(mats))
        if EMPTY_LABEL in values:
            q |= Q(matricula="") | Q(matricula__isnull=True)
        qs = qs.filter(q) if q else qs.none()
        if kind == "falhas":
            qs = exclude_processual_agent_links(qs)
        return qs

    if spec.key == "tipo_falha_display":
        q = Q()
        for label in values:
            if label == EMPTY_LABEL:
                q |= (Q(tipo_falha_oficial="") & Q(tipo_falha="")) | Q(
                    tipo_falha__isnull=True
                )
            else:
                q |= Q(tipo_falha_oficial=label) | Q(tipo_falha=label)
        return qs.filter(q)

    if spec.key == "nivel_dificuldade":
        q = Q()
        for label in values:
            if label in {EMPTY_LABEL, "Não informado"}:
                q |= (
                    Q(nivel_dificuldade_confer="")
                    & (Q(nivel_dificuldade="") | Q(nivel_dificuldade__isnull=True))
                )
            else:
                q |= Q(nivel_dificuldade_confer=label) | (
                    Q(nivel_dificuldade_confer="") & Q(nivel_dificuldade=label)
                )
        return qs.filter(q)

    if spec.key == "categoria_falha":
        q = Q()
        for label in values:
            if label == EMPTY_LABEL:
                q |= Q(categoria_falha="") | Q(categoria_falha__isnull=True)
            else:
                q |= Q(categoria_falha__iexact=label)
        return qs.filter(q)

    if spec.key == "cenario":
        q = Q()
        for label in values:
            if label == EMPTY_LABEL:
                q |= (
                    (Q(des_problemas="") | Q(des_problemas__isnull=True))
                    & (Q(cenario="") | Q(cenario__isnull=True))
                )
            else:
                q |= Q(des_problemas=label) | (
                    (Q(des_problemas="") | Q(des_problemas__isnull=True))
                    & Q(cenario=label)
                )
        return qs.filter(q)

    field = spec.db_fields[0]
    q = Q()
    for label in values:
        if label == EMPTY_LABEL:
            q |= Q(**{field: ""}) | Q(**{f"{field}__isnull": True})
        else:
            q |= Q(**{field: label})
    return qs.filter(q)


def apply_detail_column_filters(qs: QuerySet, params, *, kind: str) -> QuerySet:
    specs = _column_specs(kind)
    keys = AUDITADOS_COLUMN_KEYS if kind == "auditados" else FALHAS_COLUMN_KEYS
    for key in keys:
        values = param_list(params, f"column_{key}")
        if not values:
            continue
        spec = specs[key]
        if len(values) == 1:
            qs = _apply_legacy_column_filter(
                qs, spec, values[0], kind=kind, params=params
            )
        else:
            qs = _apply_multi_column_filter(qs, spec, values, kind=kind, params=params)
    return qs


def apply_detail_sort(
    qs: QuerySet, params, *, kind: str, default_date_field: str | None = None
) -> QuerySet:
    sort_key = str(getattr(params, "get", lambda *_: "")("detail_sort") or "").strip()
    if not sort_key:
        if default_date_field:
            return qs.order_by(f"-{default_date_field}", "-id")
        return qs
    sort_dir = str(getattr(params, "get", lambda *_: "")("detail_dir") or "asc").strip().lower()
    descending = sort_dir == "desc"
    specs = _column_specs(kind)
    spec = specs.get(sort_key)
    if not spec or not spec.sort_field:
        if default_date_field:
            return qs.order_by(f"-{default_date_field}", "-id")
        return qs

    prefix = "-" if descending else ""
    if spec.key == "nivel_dificuldade":
        qs = qs.annotate(
            _detail_nivel_sort=Case(
                When(nivel_dificuldade_confer="", then=F("nivel_dificuldade")),
                default=F("nivel_dificuldade_confer"),
                output_field=CharField(),
            )
        )
        return qs.order_by(f"{prefix}_detail_nivel_sort", f"{prefix}id")

    if spec.key == "cenario":
        qs = qs.annotate(
            _detail_cenario_sort=Case(
                When(des_problemas="", then=F("cenario")),
                When(des_problemas__isnull=True, then=F("cenario")),
                default=F("des_problemas"),
                output_field=CharField(),
            )
        )
        return qs.order_by(f"{prefix}_detail_cenario_sort", f"{prefix}id")

    return qs.order_by(f"{prefix}{spec.sort_field}", f"{prefix}id")
