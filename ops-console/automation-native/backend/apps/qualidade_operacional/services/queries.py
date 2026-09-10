# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime

from django.db.models import Q, QuerySet

from apps.qualidade_operacional.services.criticidade import (
    CLIENTE_CLARO_FORMALIZACAO_ID,
)
from apps.qualidade_operacional.services.normalize import (
    TIPO_FALHA_CANONICOS,
    normalize_tipo_conclusao,
    tipo_conclusao_match_values,
)
from apps.qualidade_operacional.services.claro_confer_scope import (
    apply_claro_confer_team_scope,
)
from apps.qualidade_operacional.services.workforce_scope import (
    equipe_occurrence_query,
    facilitator_window_query,
    localidade_hc_occurrence_query,
    scope_quality_queryset,
    turno_occurrence_query,
    workforce_scope_enabled,
)

# Variantes Automático + Processual — Manual = tudo que não for estes
_AUTO_VARIANTS = (
    "Automático",
    "Automatico",
    "AUTOMATICO",
    "AUTOMÁTICO",
    "Mapeamento",
    "MAPEAMENTO",
    "Sistema",
    "SISTEMA",
)
_PROCESSUAL_VARIANTS = ("Processual", "PROCESSUAL")
_AUTO_PROCESSUAL_VARIANTS = _AUTO_VARIANTS + _PROCESSUAL_VARIANTS
NAO_INFORMADO_KEY = "__nao_informado__"


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


def _model_has_field(qs: QuerySet, name: str) -> bool:
    return any(f.name == name for f in qs.model._meta.fields)


def _as_str_list(value) -> list[str]:
    """Normaliza filtro único ou múltiplo (lista, getlist ou CSV)."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_as_str_list(item))
        return out
    text = str(value).strip()
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def param_list(params, key: str) -> list[str]:
    """Lê filtro multi de QueryDict (getlist) ou dict/lista/CSV."""
    if hasattr(params, "getlist"):
        raw_list = [v for v in params.getlist(key) if v not in (None, "")]
        if raw_list:
            return _as_str_list(raw_list)
    return _as_str_list(params.get(key) if hasattr(params, "get") else None)


def clone_params(params) -> dict:
    """Copia params preservando valores multi (QueryDict.getlist).

    ``dict(QueryDict.items())`` descarta todos os valores exceto o último —
    incompatível com filtros repetidos (``id_cliente=1&id_cliente=2``).
    """
    if hasattr(params, "lists"):
        out: dict = {}
        for key, values in params.lists():
            cleaned = [v for v in values if v not in (None, "")]
            if not cleaned:
                continue
            out[key] = cleaned if len(cleaned) > 1 else cleaned[0]
        return out
    if hasattr(params, "items"):
        return dict(params.items())
    return {}


def params_with(params, **updates):
    """Clona params e aplica sobrescritas (datas do período anterior, etc.)."""
    copied = clone_params(params)
    copied.update(updates)
    return copied


def _exclude_auto_processual(qs: QuerySet, field: str) -> QuerySet:
    """Manual tipificação = residual + Manual (exclui Automático/Processual e aliases)."""
    q = Q()
    for label in _AUTO_PROCESSUAL_VARIANTS:
        q |= Q(**{f"{field}__iexact": label})
    return qs.exclude(q)


def _auto_q(field: str) -> Q:
    q = Q()
    for label in _AUTO_VARIANTS:
        q |= Q(**{f"{field}__iexact": label})
    return q


def _processual_q(field: str) -> Q:
    q = Q()
    for label in _PROCESSUAL_VARIANTS:
        q |= Q(**{f"{field}__iexact": label})
    return q


def exclude_processual_agent_links(qs: QuerySet) -> QuerySet:
    """Remove falhas processuais de populações atribuídas a agentes."""
    return qs.exclude(_processual_q("tipo_falha"))


def _filter_tipo_field(qs: QuerySet, field: str, raw: str) -> QuerySet:
    canon = normalize_tipo_conclusao(raw)
    if canon == "Manual":
        return _exclude_auto_processual(qs, field)
    values = tipo_conclusao_match_values(canon) or (canon,)
    q = Q()
    for v in values:
        q |= Q(**{f"{field}__iexact": v})
    return qs.filter(q)


def _filter_tipo_field_multi(qs: QuerySet, field: str, raw_values: list[str]) -> QuerySet:
    """OR entre canônicos Manual / Automático / Processual (com aliases)."""
    canons = {normalize_tipo_conclusao(v) for v in raw_values if str(v).strip()}
    canons.discard("")
    if not canons or canons >= TIPO_FALHA_CANONICOS:
        return qs
    q = Q()
    auto_q = _auto_q(field)
    proc_q = _processual_q(field)
    if "Automático" in canons:
        q |= auto_q
    if "Processual" in canons:
        q |= proc_q
    if "Manual" in canons:
        q |= ~auto_q & ~proc_q
    return qs.filter(q)


def _filter_int_in(qs: QuerySet, field: str, raw_values: list[str]) -> QuerySet:
    ids: list[int] = []
    for raw in raw_values:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        return qs
    return qs.filter(**{f"{field}__in": ids})


def _filter_str_in(qs: QuerySet, field: str, raw_values: list[str], *, iexact: bool = False) -> QuerySet:
    values = [v for v in raw_values if v]
    if not values:
        return qs
    if iexact:
        q = Q()
        for v in values:
            q |= Q(**{f"{field}__iexact": v})
        return qs.filter(q)
    return qs.filter(**{f"{field}__in": values})


def _filter_matricula_multi(qs: QuerySet, raw_values: list[str]) -> QuerySet:
    mats = [m.strip().lower() for m in raw_values if m and str(m).strip()]
    if not mats:
        return qs
    q = Q()
    for m in mats:
        q |= Q(matricula__icontains=m)
    return qs.filter(q)


NAO_INFORMADO = "__nao_informado__"


def _filter_dimension_multi(qs: QuerySet, field: str, raw_values: list[str]) -> QuerySet:
    values = [v for v in raw_values if v]
    if not values:
        return qs
    q = Q()
    concrete = [v for v in values if v != NAO_INFORMADO]
    for v in concrete:
        q |= Q(**{f"{field}__iexact": v})
    if NAO_INFORMADO in values:
        q |= Q(**{field: ""}) | Q(**{f"{field}__isnull": True})
    return qs.filter(q)


# Eixo de data da análise EO
DATE_AXIS_ANALISE = "analise"
DATE_AXIS_AUDITORIA = "auditoria"
VALID_DATE_AXES = frozenset({DATE_AXIS_ANALISE, DATE_AXIS_AUDITORIA})


def resolve_date_axis(params) -> str:
    """auditoria (default) | analise — define o campo de data da consulta."""
    raw = ""
    if hasattr(params, "get"):
        raw = (params.get("date_axis") or params.get("eixo_data") or "").strip().lower()
    if raw in {"analise", "análise", "data_analise"}:
        return DATE_AXIS_ANALISE
    if raw in {"auditoria", "data", "audit"}:
        return DATE_AXIS_AUDITORIA
    return DATE_AXIS_AUDITORIA


def date_field_auditados(params) -> str:
    """Auditados: ``data_analise`` (análise) ou ``data`` (auditoria)."""
    return "data" if resolve_date_axis(params) == DATE_AXIS_AUDITORIA else "data_analise"


def date_field_falhas(params) -> str:
    """Falhas: ``data_analise`` (análise) ou ``data`` (auditoria)."""
    return "data" if resolve_date_axis(params) == DATE_AXIS_AUDITORIA else "data_analise"


def apply_common_filters(
    qs: QuerySet,
    params,
    *,
    date_field: str = "data",
    skip_date_range: bool = False,
) -> QuerySet:
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    segment_start = _parse_date(
        params.get("segment_start") if hasattr(params, "get") else None
    )
    segment_end = _parse_date(
        params.get("segment_end") if hasattr(params, "get") else None
    )
    if not skip_date_range:
        effective_start = segment_start or start
        effective_end = segment_end or end
        if effective_start:
            qs = qs.filter(**{f"{date_field}__gte": effective_start})
        if effective_end:
            qs = qs.filter(**{f"{date_field}__lte": effective_end})

    qs = _filter_int_in(qs, "id_cliente", param_list(params, "id_cliente"))
    qs = _filter_int_in(qs, "id_workflow", param_list(params, "id_workflow"))
    qs = _filter_matricula_multi(qs, param_list(params, "matricula"))
    qs = scope_quality_queryset(qs, params, date_field=date_field)
    equipe_q = equipe_occurrence_query(params, date_field=date_field)
    if equipe_q is not None:
        qs = qs.filter(equipe_q)
    responsibility_scope = (params.get("responsibility_scope") or "").strip().lower()
    if responsibility_scope in {"lider", "facilitador"}:
        facilitator_q = facilitator_window_query(
            params,
            date_field=date_field,
            exact_matricula=workforce_scope_enabled(params),
        )
        qs = qs.filter(facilitator_q) if responsibility_scope == "facilitador" else qs.exclude(facilitator_q)

    localidades = param_list(params, "localidade")
    if localidades and _model_has_field(qs, "localidade_documento"):
        qs = _filter_str_in(qs, "localidade_documento", localidades, iexact=True)

    localidades_hc = param_list(params, "localidade_hc")
    if localidades_hc:
        loc_q = localidade_hc_occurrence_query(params, date_field=date_field)
        if loc_q is not None:
            if _model_has_field(qs, "localidade"):
                direct_q = Q()
                for value in localidades_hc:
                    direct_q |= Q(**{"localidade__iexact": value})
                qs = qs.filter(loc_q | direct_q)
            else:
                qs = qs.filter(loc_q)

    turno_q = turno_occurrence_query(params, date_field=date_field)
    if turno_q is not None:
        qs = qs.filter(turno_q)

    protocolo = (params.get("protocolo") or "").strip() if hasattr(params, "get") else ""
    if protocolo:
        qs = qs.filter(protocolo__icontains=protocolo)

    tipos_analise = param_list(params, "tipo_analise")
    if tipos_analise:
        if len(tipos_analise) == 1:
            qs = qs.filter(tipo_analise__icontains=tipos_analise[0])
        else:
            qs = _filter_str_in(qs, "tipo_analise", tipos_analise, iexact=True)

    etapas = param_list(params, "etapa")
    if etapas:
        if len(etapas) == 1:
            qs = qs.filter(etapa__icontains=etapas[0])
        else:
            qs = _filter_str_in(qs, "etapa", etapas, iexact=True)

    # tipo_bucket=Outros legado → trata como Manual (não existe mais card Outros)
    bucket = (params.get("tipo_bucket") or "").strip().lower() if hasattr(params, "get") else ""
    tipos_conclusao = param_list(params, "tipo_conclusao")
    if bucket == "outros" and _model_has_field(qs, "tipo_conclusao"):
        qs = _exclude_auto_processual(qs, "tipo_conclusao")
    elif tipos_conclusao and _model_has_field(qs, "tipo_conclusao"):
        qs = _filter_tipo_field_multi(qs, "tipo_conclusao", tipos_conclusao)

    for field, param in (
        ("tipo_registro", "tipo_registro"),
        ("origem_tratado", "origem_tratado"),
        ("tipo_falha_original", "tipo_falha_original"),
        ("procedencia", "procedencia"),
    ):
        vals = param_list(params, param)
        if vals and _model_has_field(qs, field):
            qs = _filter_dimension_multi(qs, field, vals)

    qs = apply_claro_confer_team_scope(qs, date_field=date_field)
    return qs


def _categoria_falha_q(categoria: str) -> Q:
    categoria_key = str(categoria or "").strip().casefold()
    if categoria_key in {"__unclassified__", "não informada", "nao informada"}:
        return (
            ~Q(id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID)
            & ~Q(tipo_registro="reinspecao")
            & ~Q(categoria_falha__icontains="procedimento")
            & ~Q(categoria_falha__icontains="crítica")
            & ~Q(categoria_falha__icontains="critica")
            & ~Q(categoria_falha__icontains="crítico")
            & ~Q(categoria_falha__icontains="critico")
        )
    if categoria_key in {"crítica", "critica"}:
        return (
            ~Q(id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID)
            & ~Q(tipo_registro="reinspecao")
            & (
                Q(categoria_falha__icontains="crítica")
                | Q(categoria_falha__icontains="critica")
            )
            & ~Q(categoria_falha__icontains="não crítica")
            & ~Q(categoria_falha__icontains="nao critica")
        )
    if categoria_key in {"não crítica", "nao critica"}:
        return (
            ~Q(id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID)
            & (
                Q(categoria_falha__icontains="não crítica")
                | Q(categoria_falha__icontains="nao critica")
            )
        )
    if categoria_key == "procedimento":
        return (
            Q(id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID)
            | Q(tipo_registro="reinspecao")
            | Q(categoria_falha__icontains="procedimento")
        )
    return Q(categoria_falha__icontains=categoria)


def _nivel_dificuldade_q(label: str) -> Q:
    token = str(label or "").strip()
    if token in {NAO_INFORMADO_KEY, "Não informado", "__unclassified__"}:
        return Q(nivel_dificuldade_confer="") & (
            Q(nivel_dificuldade="") | Q(nivel_dificuldade__isnull=True)
        )
    return Q(nivel_dificuldade_confer=token) | (
        Q(nivel_dificuldade_confer="") & Q(nivel_dificuldade=token)
    )


def apply_falha_filters(
    qs: QuerySet,
    params,
    *,
    skip_date_range: bool = False,
) -> QuerySet:
    qs = apply_common_filters(
        qs,
        params,
        date_field=date_field_falhas(params),
        skip_date_range=skip_date_range,
    )
    bucket = (params.get("tipo_bucket") or "").strip().lower() if hasattr(params, "get") else ""
    tipos_falha = param_list(params, "tipo_falha")
    tipos_conclusao = param_list(params, "tipo_conclusao")

    linked_only = str(params.get("agent_linked_only") or "").strip().lower() in {
        "1",
        "true",
        "sim",
        "yes",
    }
    if linked_only or param_list(params, "matricula"):
        qs = exclude_processual_agent_links(qs)

    if bucket == "outros":
        # Legado: residual ≡ Manual
        qs = _exclude_auto_processual(qs, "tipo_falha")
    elif tipos_falha:
        qs = _filter_tipo_field_multi(qs, "tipo_falha", tipos_falha)
    elif tipos_conclusao:
        # Coerência EO: tipificação nos auditados ↔ tipo_falha nas falhas
        # (common_filters já filtrou auditados por tipo_conclusao; falhas não têm o campo)
        # Reaplica no tipo_falha — mas common_filters não tocou tipo_falha.
        # Porém apply_common_filters já pode ter filtrado por tipo_conclusao se o model tivesse o campo
        # (falhas não tem). Então filtramos aqui.
        qs = _filter_tipo_field_multi(qs, "tipo_falha", tipos_conclusao)

    # Cuidado: apply_common_filters com tipo_conclusao em QualidadeFalha não aplica
    # (sem campo). Mas se ambos tipo_falha e tipo_conclusao forem enviados, tipo_falha ganha.

    categorias = param_list(params, "categoria_falha")
    if categorias:
        categoria_q = Q()
        for categoria in categorias:
            categoria_q |= _categoria_falha_q(categoria)
        qs = qs.filter(categoria_q)

    niveis = param_list(params, "nivel_dificuldade")
    if niveis:
        nivel_q = Q()
        for nivel in niveis:
            nivel_q |= _nivel_dificuldade_q(nivel)
        qs = qs.filter(nivel_q)

    tipos_oficiais = param_list(params, "tipo_falha_oficial")
    if tipos_oficiais:
        q_oficial = Q()
        for value in tipos_oficiais:
            q_oficial |= Q(tipo_falha_oficial__icontains=value) | Q(
                des_problemas__icontains=value
            )
        qs = qs.filter(q_oficial)

    qs = _filter_falha_dimension_exact(qs, "tipo_documento", param_list(params, "tipo_documento"))
    qs = _filter_falha_dimension_exact(qs, "cenario", param_list(params, "cenario"))

    return qs


def _filter_falha_dimension_exact(qs: QuerySet, field: str, raw_values: list[str]) -> QuerySet:
    """Filtro exato por dimensão de falha (drill dos painéis Resumo).

    ``__nao_informado__`` seleciona nulo/vazio/somente espaços.
    Demais valores usam igualdade exata (sem icontains).
    """
    values = [str(v) for v in raw_values if v is not None and str(v) != ""]
    if not values:
        return qs
    q = Q()
    for value in values:
        if value == NAO_INFORMADO_KEY:
            q |= Q(**{f"{field}__isnull": True}) | Q(**{field: ""}) | Q(
                **{f"{field}__regex": r"^\s+$"}
            )
        else:
            q |= Q(**{field: value})
    return qs.filter(q)


def _detail_column_value(params, field: str) -> str:
    if not hasattr(params, "get"):
        return ""
    return str(params.get(f"column_{field}") or "").strip()


def apply_detail_column_filters(qs: QuerySet, params, *, kind: str) -> QuerySet:
    from apps.qualidade_operacional.services.detail_column_filters import (
        apply_detail_column_filters as _apply_detail_column_filters,
    )

    return _apply_detail_column_filters(qs, params, kind=kind)


def apply_detail_sort(qs: QuerySet, params, *, kind: str, default_date_field: str | None = None) -> QuerySet:
    from apps.qualidade_operacional.services.detail_column_filters import (
        apply_detail_sort as _apply_detail_sort,
    )

    return _apply_detail_sort(qs, params, kind=kind, default_date_field=default_date_field)


def detail_list_order_by(params, *, kind: str, default_date_field: str) -> tuple[str, ...]:
    sort_key = str(getattr(params, "get", lambda *_: "")("detail_sort") or "").strip()
    if sort_key:
        sort_dir = str(getattr(params, "get", lambda *_: "")("detail_dir") or "asc").strip().lower()
        descending = sort_dir == "desc"
        prefix = "-" if descending else ""
        from apps.qualidade_operacional.services.detail_column_filters import (
            _column_specs,
        )

        spec = _column_specs(kind).get(sort_key)
        if spec and spec.sort_field:
            if spec.key == "nivel_dificuldade":
                return (f"{prefix}_detail_nivel_sort", f"{prefix}id")
            return (f"{prefix}{spec.sort_field}", f"{prefix}id")
    return (f"-{default_date_field}", "-id")


def parse_page(params) -> tuple[int, int]:
    try:
        page = max(1, int(params.get("page") or 1))
        page_size = min(200, max(1, int(params.get("page_size") or 50)))
    except (TypeError, ValueError):
        page, page_size = 1, 50
    return page, page_size
