"""Catalogo leve das opcoes usadas nos filtros de Qualidade."""
from __future__ import annotations

from collections import defaultdict

from django.db import transaction

from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeFiltroOpcao,
)
from apps.qualidade_operacional.services.criticidade import (
    CLIENTE_CLARO_FORMALIZACAO_ID,
    CLIENTE_CLARO_FORMALIZACAO_NOME,
    WORKFLOW_CLARO_CONFER_ID,
    WORKFLOW_CLARO_CONFER_NOME,
)
from apps.qualidade_operacional.services.normalize import (
    NIVEL_DIFICULDADE_NAO_INFORMADO,
    nivel_dificuldade_efetivo,
    normalize_tipo_conclusao,
)
from apps.qualidade_operacional.services.performance_cache import (
    bump_quality_cache_version,
    get_or_build,
)
from apps.workforce.models import Agent, AgentHistory
from apps.workforce.services.journey_shift import resolve_journey_shift

CATALOG_DIMS = ("tipo_analise", "tipo_falha", "localidade", "etapa")
INTRANET_FILTER_DIMS = (
    "tipo_registro",
    "origem_tratado",
    "tipo_falha_original",
    "procedencia",
)
NAO_INFORMADO = "__nao_informado__"

CATEGORIA_FALHA_OPTIONS = ("Crítica", "Procedimento", "Não Crítica", "Não informada")

DEFAULT_INTRANET_OPTIONS: dict[str, list[str]] = {
    "tipo_registro": ["auditoria", "contestacao", "reinspecao"],
    "origem_tratado": ["auditoria", "contestacao", "reinspecao", "intranet"],
    "tipo_falha_original": [
        "reinspecao",
        "auditoria",
        "Automático",
        "Colaborador",
        "Sem Falha",
    ],
    "procedencia": ["Procedente", "Improcedente"],
}


def _intranet_dimension_values(field: str) -> set[str]:
    aud = (
        QualidadeAuditado.objects.exclude(**{field: ""})
        .values_list(field, flat=True)
        .distinct()
    )
    fal = (
        QualidadeFalha.objects.exclude(**{field: ""})
        .values_list(field, flat=True)
        .distinct()
    )
    return {str(value).strip() for value in (*aud, *fal) if str(value or "").strip()}


def _nivel_dificuldade_values() -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for confer, original in QualidadeFalha.objects.values_list(
        "nivel_dificuldade_confer",
        "nivel_dificuldade",
    ).distinct():
        label = nivel_dificuldade_efetivo(confer, original)
        if not label or label == NIVEL_DIFICULDADE_NAO_INFORMADO:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(label)
    return sorted(values, key=lambda value: value.casefold())


def _fact_values(dimensao: str) -> set[str]:
    if dimensao == "tipo_analise":
        values = (
            QualidadeAuditado.objects.exclude(tipo_analise="")
            .values_list("tipo_analise", flat=True)
            .distinct()
        )
    elif dimensao == "tipo_falha":
        values = (
            QualidadeFalha.objects.exclude(tipo_falha="")
            .values_list("tipo_falha", flat=True)
            .distinct()
        )
    elif dimensao == "localidade":
        aud = (
            QualidadeAuditado.objects.exclude(localidade_documento="")
            .values_list("localidade_documento", flat=True)
            .distinct()
        )
        fal = (
            QualidadeFalha.objects.exclude(localidade_documento="")
            .values_list("localidade_documento", flat=True)
            .distinct()
        )
        return {
            str(value).strip()
            for value in (*aud, *fal)
            if str(value or "").strip()
        }
    else:
        aud = (
            QualidadeAuditado.objects.exclude(etapa="")
            .values_list("etapa", flat=True)
            .distinct()
        )
        fal = (
            QualidadeFalha.objects.exclude(etapa="")
            .values_list("etapa", flat=True)
            .distinct()
        )
        return {
            str(value).strip()
            for value in (*aud, *fal)
            if str(value or "").strip()
        }
    return {str(value).strip() for value in values if str(value or "").strip()}


def catalog_values() -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for dimensao, valor in QualidadeFiltroOpcao.objects.values_list("dimensao", "valor"):
        if dimensao in CATALOG_DIMS and valor:
            grouped[dimensao].add(valor)

    # Mantem testes e bases recem-criadas funcionais antes do primeiro refresh.
    for dimensao in CATALOG_DIMS:
        if not grouped[dimensao]:
            grouped[dimensao] = _fact_values(dimensao)

    return {
        dimensao: sorted(grouped[dimensao], key=lambda value: value.casefold())
        for dimensao in CATALOG_DIMS
    }


def refresh_filter_catalog() -> int:
    rows = {
        (dimensao, valor)
        for dimensao in CATALOG_DIMS
        for valor in _fact_values(dimensao)
    }
    with transaction.atomic():
        QualidadeFiltroOpcao.objects.all().delete()
        QualidadeFiltroOpcao.objects.bulk_create(
            [
                QualidadeFiltroOpcao(dimensao=dimensao, valor=valor)
                for dimensao, valor in sorted(rows)
            ],
            batch_size=1000,
        )
    bump_quality_cache_version()
    try:
        from apps.qualidade_operacional.services.contestacao_metrics import (
            clear_contestacao_tipo_cache,
        )

        clear_contestacao_tipo_cache()
    except Exception:  # noqa: BLE001
        pass
    return len(rows)


def _build_filter_payload() -> dict:
    values = catalog_values()
    tipos_falha = ["Manual", "Autom\u00e1tico", "Processual"]
    for raw in values["tipo_falha"]:
        canon = normalize_tipo_conclusao(raw)
        if raw not in tipos_falha and canon == raw:
            tipos_falha.append(raw)

    lideres = list(
        AgentHistory.objects.exclude(leader__isnull=True)
        .exclude(leader__full_name="")
        .values_list("leader__full_name", flat=True)
        .distinct()
        .order_by("leader__full_name")
    )
    equipes_raw = (
        AgentHistory.objects.exclude(team="")
        .exclude(team__isnull=True)
        .values_list("team", flat=True)
        .distinct()
    )
    equipes_by_key: dict[str, str] = {}
    for raw in equipes_raw:
        team = str(raw or "").strip()
        if not team:
            continue
        key = team.casefold()
        if key not in equipes_by_key:
            equipes_by_key[key] = team
    equipes = sorted(equipes_by_key.values(), key=lambda value: value.casefold())

    agent_rows = (
        AgentHistory.objects.exclude(agent__user_lan_id="")
        .values("agent__user_lan_id", "agent__full_name")
        .distinct()
    )
    agentes_by_mat: dict[str, dict[str, str]] = {}
    for row in agent_rows:
        matricula = str(row.get("agent__user_lan_id") or "").strip().lower()
        if not matricula:
            continue
        nome = str(row.get("agent__full_name") or "").strip() or matricula
        prev = agentes_by_mat.get(matricula)
        if prev is None or (prev["label"] == matricula and nome != matricula):
            agentes_by_mat[matricula] = {
                "value": matricula,
                "matricula": matricula,
                "label": nome,
            }
    # Desambiguar nomes duplicados com a matrícula, mantendo o nome visível.
    label_counts: dict[str, int] = {}
    for item in agentes_by_mat.values():
        key = item["label"].casefold()
        label_counts[key] = label_counts.get(key, 0) + 1
    for item in agentes_by_mat.values():
        if label_counts.get(item["label"].casefold(), 0) > 1:
            item["label"] = f"{item['label']} ({item['matricula']})"
    agentes = sorted(
        agentes_by_mat.values(),
        key=lambda item: (item["label"].casefold(), item["matricula"]),
    )
    clientes = list(
        DimCliente.objects.order_by("id_cliente").values("id_cliente", "nome")
    )
    workflows = list(
        DimWorkflow.objects.order_by("id_workflow").values("id_workflow", "nome")
    )
    if (
        QualidadeAuditado.objects.filter(
            id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID
        ).exists()
        or QualidadeFalha.objects.filter(
            id_cliente=CLIENTE_CLARO_FORMALIZACAO_ID
        ).exists()
    ):
        clientes = [
            row
            for row in clientes
            if row["id_cliente"] != CLIENTE_CLARO_FORMALIZACAO_ID
        ]
        clientes.append(
            {
                "id_cliente": CLIENTE_CLARO_FORMALIZACAO_ID,
                "nome": CLIENTE_CLARO_FORMALIZACAO_NOME,
            }
        )
        clientes.sort(key=lambda row: row["id_cliente"])
    if (
        QualidadeAuditado.objects.filter(
            id_workflow=WORKFLOW_CLARO_CONFER_ID
        ).exists()
        or QualidadeFalha.objects.filter(
            id_workflow=WORKFLOW_CLARO_CONFER_ID
        ).exists()
    ):
        workflows = [
            row
            for row in workflows
            if row["id_workflow"] != WORKFLOW_CLARO_CONFER_ID
        ]
        workflows.append(
            {
                "id_workflow": WORKFLOW_CLARO_CONFER_ID,
                "nome": WORKFLOW_CLARO_CONFER_NOME,
            }
        )
        workflows.sort(key=lambda row: row["id_workflow"])
    localidades_hc = sorted(
        {
            str(value).strip()
            for value in QualidadeFalha.objects.exclude(localidade="")
            .values_list("localidade", flat=True)
            .distinct()
            if str(value or "").strip()
        },
        key=lambda value: value.casefold(),
    )
    hc_from_history = (
        AgentHistory.objects.exclude(location="")
        .values_list("location", flat=True)
        .distinct()
    )
    for raw in hc_from_history:
        loc = str(raw or "").strip()
        if loc and loc not in localidades_hc:
            localidades_hc.append(loc)
    localidades_hc.sort(key=lambda value: value.casefold())

    turnos: set[str] = set()
    for raw_shift in AgentHistory.objects.exclude(journey_shift="").values_list(
        "journey_shift", flat=True
    ).distinct():
        shift = str(raw_shift or "").strip()
        if shift:
            turnos.add(shift)
    for raw_journey in AgentHistory.objects.exclude(journey="").values_list(
        "journey", flat=True
    ).distinct():
        derived = resolve_journey_shift(str(raw_journey or ""))
        if derived:
            turnos.add(derived)
    turnos_list = sorted(turnos, key=lambda value: value.casefold())

    return {
        "ok": True,
        "tipos_analise": values["tipo_analise"][:100],
        "tipos_falha": tipos_falha[:200],
        "tipos_conclusao": ["Manual", "Autom\u00e1tico", "Processual"],
        "localidades": values["localidade"][:200],
        "localidades_hc": localidades_hc[:200],
        "turnos": turnos_list[:50],
        "etapas": values["etapa"][:300],
        "lideres": lideres,
        "equipes": equipes,
        "agentes": agentes,
        "clientes": clientes,
        "workflows": workflows,
        "tipos_registro": sorted(
            _intranet_dimension_values("tipo_registro") | set(DEFAULT_INTRANET_OPTIONS["tipo_registro"]),
            key=lambda v: v.casefold(),
        ),
        "origens_tratado": sorted(
            _intranet_dimension_values("origem_tratado") | set(DEFAULT_INTRANET_OPTIONS["origem_tratado"]),
            key=lambda v: v.casefold(),
        ),
        "tipos_falha_original": sorted(
            _intranet_dimension_values("tipo_falha_original")
            | set(DEFAULT_INTRANET_OPTIONS["tipo_falha_original"]),
            key=lambda v: v.casefold(),
        ),
        "procedencias": sorted(
            _intranet_dimension_values("procedencia") | set(DEFAULT_INTRANET_OPTIONS["procedencia"]),
            key=lambda v: v.casefold(),
        ),
        "categorias": list(CATEGORIA_FALHA_OPTIONS),
        "niveis_dificuldade": _nivel_dificuldade_values()[:200],
        "nao_informado_token": NAO_INFORMADO,
    }


def get_filter_payload() -> dict:
    return get_or_build("filters", {}, _build_filter_payload, timeout=3600)
