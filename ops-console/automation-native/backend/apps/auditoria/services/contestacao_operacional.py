"""Contestação de falha pela liderança operacional."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.db.models.functions import Lower
from django.utils import timezone

from apps.access import registry as R
from apps.access.resolve import user_has_any_permission, user_has_permission
from apps.auditoria.services.contestacao_operacional_scoping import (
    apply_usuario_scope,
    team_agent_identifiers_for_user,
)
from apps.auditoria.models import (
    AuditoriaCatalogItem,
    AuditoriaFalhaCadastro,
    AuditoriaMotivoFalha,
    ContestacaoOperacional,
    ContestacaoOperacionalHistorico,
    ReinspecaoAuditorPresence,
)
from apps.auditoria.services.agent_links import system_agent_lan_id
from apps.auditoria.services.suporte_operacional_link import resolve_and_link, suporte_operacional_payload_for
from apps.workforce.models import Agent, AgentHistory

CATEGORIA_TO_DOMINIO = {
    ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD: ContestacaoOperacional.DOMINIO_FRAUD,
    ContestacaoOperacional.CATEGORIA_CONTESTACAO_FRAUD: ContestacaoOperacional.DOMINIO_FRAUD,
    ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE: ContestacaoOperacional.DOMINIO_COMPLIANCE,
    ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE: ContestacaoOperacional.DOMINIO_COMPLIANCE,
}

ORIGEM_TO_CATEGORIA_DEFAULT = {
    AuditoriaFalhaCadastro.ORIGEM_AUDITORIA: ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
    AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO: ContestacaoOperacional.CATEGORIA_CONTESTACAO_FRAUD,
    AuditoriaFalhaCadastro.ORIGEM_REINSPECAO: ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE,
}


def _resolve_reclassification_catalog_value(
    *,
    value: str,
    original: str,
    queryset: QuerySet,
    value_field: str,
    label: str,
    max_length: int,
) -> tuple[str, str | None]:
    if len(value) > max_length:
        return "", f"{label} deve ter no máximo {max_length} caracteres."

    original = (original or "").strip()
    if original and value.casefold() == original.casefold():
        return original, None

    stored_value = (
        queryset.filter(active=True, **{f"{value_field}__iexact": value})
        .values_list(value_field, flat=True)
        .first()
    )
    if stored_value:
        return str(stored_value), None
    return "", f"{label} deve ser uma opção ativa do catálogo."


def _fila_contexto_values(falha: AuditoriaFalhaCadastro) -> set[str]:
    """Contextos estruturados persistidos nas duas fontes suportadas."""
    parsed = falha.brflow_parsed if isinstance(falha.brflow_parsed, dict) else {}
    analise_origem = getattr(falha, "analise_origem", None)
    contexto_origem = (
        analise_origem.contexto
        if analise_origem is not None and isinstance(analise_origem.contexto, dict)
        else {}
    )
    return {
        str(value).strip().lower()
        for value in (
            parsed.get("fila_contexto"),
            contexto_origem.get("fila_contexto"),
        )
        if value and str(value).strip()
    }


def _auditoria_compliance_q() -> Q:
    return Q(
        brflow_parsed__fila_contexto=(
            ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE
        )
    ) | Q(
        analise_origem__contexto__fila_contexto=(
            ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE
        )
    )


def _reinspecao_contexto_q() -> Q:
    return Q(
        brflow_parsed__fila_contexto=ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO
    ) | Q(
        analise_origem__contexto__fila_contexto=(
            ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO
        )
    )


def _reinspecao_compliance_q() -> Q:
    return (
        _reinspecao_contexto_q()
        | Q(origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO)
        | Q(tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO)
    )


def _contexto_conflitante_ids():
    return (
        AuditoriaFalhaCadastro.objects.filter(
            _auditoria_compliance_q(),
            _reinspecao_contexto_q(),
        )
        .values("pk")
    )


def _categoria_falha_q(categoria: str) -> Q:
    """Predicado SQL equivalente à classificação canônica, sem negação JSON nula."""
    all_falhas = AuditoriaFalhaCadastro.objects.all()
    compliance_ids = all_falhas.filter(_auditoria_compliance_q()).exclude(
        pk__in=all_falhas.filter(_reinspecao_contexto_q()).values("pk")
    ).values("pk")
    reinspecao_ids = all_falhas.filter(_reinspecao_compliance_q()).exclude(
        pk__in=all_falhas.filter(_auditoria_compliance_q()).values("pk")
    ).values("pk")
    contestacao_ids = all_falhas.filter(
        Q(origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO)
        | Q(tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO)
    ).exclude(pk__in=compliance_ids).exclude(pk__in=reinspecao_ids).exclude(
        pk__in=_contexto_conflitante_ids()
    ).values("pk")

    if categoria == ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE:
        return Q(pk__in=compliance_ids)
    if categoria == ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE:
        return Q(pk__in=reinspecao_ids)
    if categoria == ContestacaoOperacional.CATEGORIA_CONTESTACAO_FRAUD:
        return Q(pk__in=contestacao_ids)
    if categoria == ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD:
        return Q(
            pk__in=all_falhas.filter(
                Q(origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA)
                | Q(origem="", tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA)
            )
            .exclude(pk__in=compliance_ids)
            .exclude(pk__in=reinspecao_ids)
            .exclude(pk__in=contestacao_ids)
            .exclude(pk__in=_contexto_conflitante_ids())
            .values("pk")
        )
    return Q(pk__in=[])


def resolve_categoria(
    origem_tecnica: str,
    *,
    categoria: str | None = None,
) -> str:
    if categoria:
        if categoria not in dict(ContestacaoOperacional.CATEGORIA_CHOICES):
            raise ValidationError({"categoria": "Categoria inválida."})
        return categoria
    mapped = ORIGEM_TO_CATEGORIA_DEFAULT.get((origem_tecnica or "").strip().lower())
    if not mapped:
        raise ValidationError({"origem": "Origem técnica não suportada para contestação operacional."})
    return mapped


def resolve_categoria_falha(
    falha: AuditoriaFalhaCadastro,
    *,
    categoria: str | None = None,
) -> str:
    """Resolve os quatro fluxos sem confundir Auditoria Fraud e Compliance."""
    contextos = _fila_contexto_values(falha)
    contextos_suportados = {
        ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE,
        ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO,
    }
    contextos_reconhecidos = contextos & contextos_suportados
    if len(contextos_reconhecidos) > 1:
        raise ValidationError(
            {"falha_id": "Falha com contextos estruturados divergentes."}
        )
    origem = (falha.origem or "").strip().lower()
    tipo_registro = (falha.tipo_registro or "").strip().lower()

    if ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE in contextos_reconhecidos:
        canonical = ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE
    elif (
        ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO in contextos_reconhecidos
        or origem == AuditoriaFalhaCadastro.ORIGEM_REINSPECAO
        or tipo_registro == AuditoriaFalhaCadastro.REGISTRO_REINSPECAO
    ):
        canonical = ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE
    elif (
        origem == AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO
        or tipo_registro == AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO
    ):
        canonical = ContestacaoOperacional.CATEGORIA_CONTESTACAO_FRAUD
    else:
        canonical = resolve_categoria(
            origem or tipo_registro or AuditoriaFalhaCadastro.ORIGEM_AUDITORIA
        )

    if categoria:
        informed = resolve_categoria("", categoria=categoria)
        if informed != canonical:
            raise ValidationError(
                {"categoria": "Categoria divergente da origem canônica da falha."}
            )
    return canonical


def data_auditoria_for_falha(falha: AuditoriaFalhaCadastro) -> datetime | None:
    """Data da auditoria da falha, por origem — sem fallback silencioso genérico.

    - auditoria Compliance: `data_analise`
    - auditoria Fraud: `analise_concluida_em`
    - contestação Fraud: `data_contestacao`
    - reinspeção Compliance: `data_analise_intranet` / `analise_concluida_em`
    """
    categoria = resolve_categoria_falha(falha)
    if categoria == ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE:
        return falha.data_analise
    if categoria == ContestacaoOperacional.CATEGORIA_CONTESTACAO_FRAUD:
        return falha.data_contestacao
    if categoria == ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE:
        return falha.data_analise_intranet or falha.analise_concluida_em
    if categoria == ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD:
        return falha.analise_concluida_em
    return None


def atribuicao_em_for_falha(falha: AuditoriaFalhaCadastro) -> datetime | None:
    """Compatibilidade do fluxo persistido de contestação com a data de origem da falha."""
    return data_auditoria_for_falha(falha)


COLUMN_FILTER_KEYS = frozenset({
    "protocolo",
    "agente_nome",
    "lider_nome",
    "motivo_falha",
    "categoria",
    "status_falha",
    "data_auditoria",
})

ORDERING_FIELDS = {
    "protocolo": "protocolo",
    "agente_nome": "usuario",
    "motivo_falha": "motivo_falha",
    "status_falha": "status_falha",
    "data_auditoria": "analise_concluida_em",
    "id": "id",
}


def _falhas_manuais_atribuidas(
    qs: QuerySet[AuditoriaFalhaCadastro],
) -> QuerySet[AuditoriaFalhaCadastro]:
    """Aceita falhas manuais ou estados finais comprovados por contestação."""
    finalizada = Q(
        status_falha__in=(
            AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA,
            AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
        ),
        contestacoes_operacionais__status__in=(
            ContestacaoOperacional.STATUS_PROCEDENTE,
            ContestacaoOperacional.STATUS_IMPROCEDENTE,
        ),
    )
    return qs.filter(Q(agente_ref__isnull=False) | finalizada).distinct().exclude(
        Q(agente_ref__user_lan_id__iexact=system_agent_lan_id())
        | Q(usuario__iexact=system_agent_lan_id())
    )


def _falhas_indicador_qualidade(
    qs: QuerySet[AuditoriaFalhaCadastro],
) -> QuerySet[AuditoriaFalhaCadastro]:
    """Paridade com falhas efetivas do indicador EO (projeção Intranet ou regra equivalente)."""
    from apps.qualidade_operacional.models import QualidadeIntranetProjection

    projected = Q(
        qualidade_projection__sync_status=QualidadeIntranetProjection.STATUS_OK,
        qualidade_projection__falha_id__isnull=False,
    )
    equivalent = Q(
        resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
        agente_ref__isnull=False,
    ) & ~Q(tipo_falha__icontains="sem falha")
    return qs.filter(projected | equivalent).distinct()


def _excluir_origens_nao_contestaveis(
    qs: QuerySet[AuditoriaFalhaCadastro],
) -> QuerySet[AuditoriaFalhaCadastro]:
    improcedente = (
        Q(origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO)
        | Q(tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO)
    ) & Q(status__icontains="improcedente")
    # O tipo_registro legado pode ser ``reinspecao`` mesmo quando a origem
    # estruturada identifica Auditoria Compliance. Aplique a regra de
    # procedencia somente ao fluxo classificado canonicamente como reinspecao.
    reinspecao_procedente = _categoria_falha_q(
        ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE
    ) & (
        Q(status__iexact="procedente")
        | Q(brflow_parsed__situacao__iexact="procedente")
    )
    # Negar diretamente uma comparação em chave JSON ausente gera UNKNOWN no
    # PostgreSQL e também remove linhas improcedentes válidas. Materialize a
    # condição positiva como subconsulta de IDs antes de excluí-la.
    reinspecao_procedente_ids = AuditoriaFalhaCadastro.objects.filter(
        reinspecao_procedente
    ).values("pk")
    return qs.exclude(improcedente).exclude(pk__in=reinspecao_procedente_ids)


def falhas_queryset_for_user(user) -> QuerySet[AuditoriaFalhaCadastro]:
    base = _falhas_manuais_atribuidas(AuditoriaFalhaCadastro.objects.all())
    qs = (
        _falhas_indicador_qualidade(base)
        .exclude(pk__in=_contexto_conflitante_ids())
    )
    qs = _excluir_origens_nao_contestaveis(qs)
    return apply_usuario_scope(qs, user)


def filter_falhas(
    qs: QuerySet[AuditoriaFalhaCadastro],
    *,
    protocolo: str = "",
    agente: str = "",
    categoria: str = "",
    data_inicio: datetime | None = None,
    data_fim: datetime | None = None,
) -> QuerySet[AuditoriaFalhaCadastro]:
    protocolo = (protocolo or "").strip()
    agente = (agente or "").strip()
    categoria = (categoria or "").strip().lower()

    if protocolo:
        qs = qs.filter(protocolo__icontains=protocolo)
    if agente:
        agent_identifiers: set[str] = set()
        matching_agents = Agent.objects.filter(
            Q(full_name__icontains=agente)
            | Q(user_lan_id__icontains=agente)
            | Q(time_tracking_id__icontains=agente)
            | Q(oracle_id__icontains=agente)
        ).only("user_lan_id", "time_tracking_id", "oracle_id")
        for item in matching_agents:
            agent_identifiers.update(
                value.strip().lower()
                for value in (item.user_lan_id, item.time_tracking_id, item.oracle_id)
                if value and value.strip()
            )
        qs = qs.alias(usuario_normalized=Lower("usuario")).filter(
            Q(usuario__icontains=agente) | Q(usuario_normalized__in=agent_identifiers)
        )

    if categoria in CATEGORIA_TO_DOMINIO:
        qs = qs.filter(_categoria_falha_q(categoria))

    # Filtro de período pela data de atribuição correta por origem (sem created_at silencioso).
    if data_inicio or data_fim:
        contestacao_q = _categoria_falha_q(
            ContestacaoOperacional.CATEGORIA_CONTESTACAO_FRAUD
        )
        reinspecao_q = _categoria_falha_q(
            ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE
        )
        auditoria_compliance_q = _categoria_falha_q(
            ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE
        )
        outros_q = ~(contestacao_q | reinspecao_q | auditoria_compliance_q)

        def _reinspecao_period_q(*, start: datetime | None, end: datetime | None) -> Q:
            intranet = Q()
            fallback = Q(data_analise_intranet__isnull=True)
            if start and end:
                intranet = Q(
                    data_analise_intranet__gte=start,
                    data_analise_intranet__lte=end,
                )
                fallback &= Q(
                    analise_concluida_em__gte=start,
                    analise_concluida_em__lte=end,
                )
            elif start:
                intranet = Q(data_analise_intranet__gte=start)
                fallback &= Q(analise_concluida_em__gte=start)
            else:
                intranet = Q(data_analise_intranet__lte=end)
                fallback &= Q(analise_concluida_em__lte=end)
            return intranet | fallback

        parts = Q()
        if data_inicio and data_fim:
            parts |= contestacao_q & Q(
                data_contestacao__gte=data_inicio,
                data_contestacao__lte=data_fim,
            )
            parts |= reinspecao_q & _reinspecao_period_q(start=data_inicio, end=data_fim)
            parts |= auditoria_compliance_q & Q(
                data_analise__gte=data_inicio,
                data_analise__lte=data_fim,
            )
            parts |= outros_q & Q(
                analise_concluida_em__gte=data_inicio,
                analise_concluida_em__lte=data_fim,
            )
        elif data_inicio:
            parts |= contestacao_q & Q(data_contestacao__gte=data_inicio)
            parts |= reinspecao_q & _reinspecao_period_q(start=data_inicio, end=None)
            parts |= auditoria_compliance_q & Q(data_analise__gte=data_inicio)
            parts |= outros_q & Q(analise_concluida_em__gte=data_inicio)
        else:
            parts |= contestacao_q & Q(data_contestacao__lte=data_fim)
            parts |= reinspecao_q & _reinspecao_period_q(start=None, end=data_fim)
            parts |= auditoria_compliance_q & Q(data_analise__lte=data_fim)
            parts |= outros_q & Q(analise_concluida_em__lte=data_fim)
        qs = qs.filter(parts).exclude(
            # Exclui registros sem data de atribuição no campo correto
            (contestacao_q & Q(data_contestacao__isnull=True))
            | (
                reinspecao_q
                & Q(data_analise_intranet__isnull=True, analise_concluida_em__isnull=True)
            )
            | (auditoria_compliance_q & Q(data_analise__isnull=True))
            | (outros_q & Q(analise_concluida_em__isnull=True))
        )

    return qs.select_related("created_by", "atividade", "analise_origem")


def _categoria_label(categoria: str) -> str:
    return dict(ContestacaoOperacional.CATEGORIA_CHOICES).get(categoria, categoria)


def _falha_protocol_group_key(falha: AuditoriaFalhaCadastro) -> tuple[str, str, str]:
    categoria = resolve_categoria_falha(falha)
    return (
        (falha.protocolo or "").strip(),
        (falha.usuario or "").strip().lower(),
        categoria,
    )


def _contestacao_solicitante_nome(item: ContestacaoOperacional) -> str:
    if not item.created_by_id:
        return ""
    nome = (item.created_by.get_full_name() or "").strip()
    if nome:
        return nome
    return (
        Agent.objects.filter(user_lan_id__iexact=item.created_by.username)
        .values_list("full_name", flat=True)
        .first()
        or item.created_by.username
        or ""
    ).strip()


def _serialize_contestacao_resumo(
    item: ContestacaoOperacional | None,
) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "id": item.pk,
        "status": item.status,
        "justificativa_lider": item.justificativa_lider,
        "parecer_interno": item.parecer_interno,
        "destino_falha": item.destino_falha,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "decidida_em": item.decidida_em.isoformat() if item.decidida_em else None,
        "created_by_username": getattr(item.created_by, "username", None)
        if item.created_by_id
        else None,
        "created_by_nome": _contestacao_solicitante_nome(item),
        "analista_username": getattr(item.analista, "username", None)
        if item.analista_id
        else None,
    }


def _protocol_contestacao_map_for_falhas(
    falhas: list[AuditoriaFalhaCadastro],
) -> dict[tuple[str, str, str], ContestacaoOperacional]:
    if not falhas:
        return {}
    protocol_usuarios: set[tuple[str, str]] = set()
    for falha in falhas:
        protocol = (falha.protocolo or "").strip()
        usuario = (falha.usuario or "").strip().lower()
        if protocol:
            protocol_usuarios.add((protocol, usuario))
    if not protocol_usuarios:
        return {}
    sibling_q = Q()
    for protocol, usuario in protocol_usuarios:
        sibling_q |= Q(protocolo=protocol, usuario__iexact=usuario)
    sibling_ids = list(
        AuditoriaFalhaCadastro.objects.filter(sibling_q).values_list("pk", flat=True)
    )
    if not sibling_ids:
        return {}
    contestacoes = (
        ContestacaoOperacional.objects.filter(falha_id__in=sibling_ids)
        .select_related("created_by", "analista", "falha")
        .order_by("-created_at", "-id")
    )
    result: dict[tuple[str, str, str], ContestacaoOperacional] = {}
    for item in contestacoes:
        if not item.falha_id:
            continue
        key = _falha_protocol_group_key(item.falha)
        result.setdefault(key, item)
    return result


def _protocol_contestacao_for_falha(
    falha: AuditoriaFalhaCadastro,
) -> ContestacaoOperacional | None:
    siblings = siblings_for_falha(falha)
    return (
        ContestacaoOperacional.objects.filter(falha__in=[item.pk for item in siblings])
        .select_related("created_by", "analista", "falha")
        .order_by("-created_at", "-id")
        .first()
    )


def _irregularidade_text_for_falha(falha: AuditoriaFalhaCadastro) -> str:
    try:
        categoria = resolve_categoria_falha(falha)
        dominio = CATEGORIA_TO_DOMINIO.get(categoria, "")
    except ValidationError:
        categoria = ""
        dominio = ""

    if categoria == ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE:
        return (falha.motivo_falha or "").strip()
    if dominio == ContestacaoOperacional.DOMINIO_COMPLIANCE or categoria in (
        ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE,
    ):
        return (falha.descricao_irregularidades or falha.motivo_falha or "").strip()
    legacy = (falha.descricao_irregularidades or falha.motivo_falha or "").strip()
    if legacy:
        return legacy
    parsed = falha.brflow_parsed if isinstance(falha.brflow_parsed, dict) else {}
    return str(parsed.get("cenario") or "").strip()


def _collect_protocol_irregularidades(falhas: list[AuditoriaFalhaCadastro]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for falha in falhas:
        text = _irregularidade_text_for_falha(falha)
        if text and text not in seen:
            seen.add(text)
            values.append(text)
    return values


def siblings_for_falha(
    falha: AuditoriaFalhaCadastro,
    *,
    scope_qs: QuerySet[AuditoriaFalhaCadastro] | None = None,
) -> list[AuditoriaFalhaCadastro]:
    key = _falha_protocol_group_key(falha)
    if not key[0]:
        return [falha]
    qs = scope_qs if scope_qs is not None else AuditoriaFalhaCadastro.objects.all()
    candidates = list(
        qs.filter(protocolo=key[0], usuario__iexact=falha.usuario or "")
        .select_related("created_by", "atividade", "analise_origem")
        .order_by("id")
    )
    siblings = [item for item in candidates if _falha_protocol_group_key(item) == key]
    return siblings or [falha]


def paginate_falha_protocol_groups(
    qs: QuerySet[AuditoriaFalhaCadastro],
    *,
    page: int,
    page_size: int,
) -> tuple[list[list[AuditoriaFalhaCadastro]], int]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 25), 100))
    groups_map: dict[tuple[str, str, str], list[AuditoriaFalhaCadastro]] = {}
    group_order: list[tuple[str, str, str]] = []
    for falha in qs.iterator(chunk_size=500):
        key = _falha_protocol_group_key(falha)
        if key not in groups_map:
            groups_map[key] = []
            group_order.append(key)
        groups_map[key].append(falha)
    total = len(group_order)
    offset = (page - 1) * page_size
    page_groups = [groups_map[key] for key in group_order[offset : offset + page_size]]
    return page_groups, total


def _protocol_irregularidades_itens(
    falhas: list[AuditoriaFalhaCadastro],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for falha in sorted(falhas, key=lambda row: row.pk):
        text = _irregularidade_text_for_falha(falha)
        if not text:
            continue
        item: dict[str, Any] = {
            "falha_id": falha.pk,
            "texto": text,
            "status_falha": falha.status_falha or AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        }
        try:
            categoria = resolve_categoria_falha(falha)
        except ValidationError:
            categoria = ""
        if categoria == ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE:
            status_irregularidade = (falha.etapa_falha or "").strip()
            if status_irregularidade:
                item["status_irregularidade"] = status_irregularidade
        items.append(item)
    return items


def _falha_motivo_display(falha: AuditoriaFalhaCadastro) -> str:
    try:
        categoria = resolve_categoria_falha(falha)
        dominio = CATEGORIA_TO_DOMINIO.get(categoria, "")
    except ValidationError:
        categoria = ""
        dominio = ""

    if categoria == ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE:
        value = (falha.motivo_falha or "").strip()
    elif dominio == ContestacaoOperacional.DOMINIO_COMPLIANCE or categoria in (
        ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE,
    ):
        value = (falha.descricao_irregularidades or falha.motivo_falha or "").strip()
    else:
        parsed = falha.brflow_parsed if isinstance(falha.brflow_parsed, dict) else {}
        cenario = str(parsed.get("cenario") or "").strip()
        value = cenario or (falha.motivo_falha or "").strip()
    return value or "—"


def _column_value_for_falha(
    falha: AuditoriaFalhaCadastro,
    key: str,
    *,
    agent: Agent | None = None,
    leader_name: str = "",
) -> str:
    if key == "protocolo":
        return falha.protocolo or "—"
    if key == "agente_nome":
        if agent and agent.full_name:
            return agent.full_name.strip()
        return falha.usuario or "—"
    if key == "lider_nome":
        return leader_name or "—"
    if key == "motivo_falha":
        return _falha_motivo_display(falha)
    if key == "categoria":
        try:
            cat = resolve_categoria_falha(falha)
        except ValidationError:
            return "—"
        return _categoria_label(cat) or "—"
    if key == "status_falha":
        return falha.status_falha or "—"
    if key == "data_auditoria":
        dt = data_auditoria_for_falha(falha)
        return dt.isoformat() if dt else "—"
    return ""


def apply_column_filters(
    qs: QuerySet[AuditoriaFalhaCadastro],
    column_filters: dict[str, list[str]] | None,
) -> QuerySet[AuditoriaFalhaCadastro]:
    if not column_filters:
        return qs

    normalized: dict[str, set[str]] = {}
    for key, values in column_filters.items():
        if key not in COLUMN_FILTER_KEYS:
            continue
        cleaned = {str(value).strip() for value in values if str(value).strip()}
        if cleaned:
            normalized[key] = cleaned
    if not normalized:
        return qs

    simple_keys = {"protocolo", "status_falha", "motivo_falha"}
    if set(normalized).issubset(simple_keys):
        if "protocolo" in normalized:
            qs = qs.filter(protocolo__in=sorted(normalized["protocolo"]))
        if "status_falha" in normalized:
            qs = qs.filter(status_falha__in=sorted(normalized["status_falha"]))
        if "motivo_falha" in normalized:
            motivo_q = Q()
            for value in normalized["motivo_falha"]:
                motivo_q |= Q(motivo_falha__iexact=value) | Q(descricao_irregularidades__iexact=value)
            qs = qs.filter(motivo_q)
        return qs

    preview_identifiers = {
        str(value).strip().lower()
        for value in qs.exclude(usuario="").values_list("usuario", flat=True).distinct()
        if value and str(value).strip()
    }
    agent_index = _agent_index_for_identifiers(preview_identifiers)
    histories = (
        AgentHistory.objects.filter(
            agent_id__in={agent.pk for agent in agent_index.values()},
            active=True,
            final_date__isnull=True,
        )
        .select_related("leader")
        .only("agent_id", "leader__full_name")
    )
    leaders_by_agent_id = {
        history.agent_id: (history.leader.full_name or "").strip()
        for history in histories
        if history.leader_id
    }

    matching_ids: list[int] = []
    for falha in qs.iterator(chunk_size=500):
        agent = agent_index.get((falha.usuario or "").strip().lower())
        leader_name = leaders_by_agent_id.get(agent.pk, "") if agent else ""
        if all(
            _column_value_for_falha(
                falha,
                key,
                agent=agent,
                leader_name=leader_name,
            )
            in selected
            for key, selected in normalized.items()
        ):
            matching_ids.append(falha.pk)
    if not matching_ids:
        return qs.none()
    return qs.filter(pk__in=matching_ids)


def apply_falhas_ordering(
    qs: QuerySet[AuditoriaFalhaCadastro],
    ordering: str = "",
) -> QuerySet[AuditoriaFalhaCadastro]:
    ordering = (ordering or "").strip()
    if not ordering:
        return qs.order_by("-id")
    descending = ordering.startswith("-")
    key = ordering[1:] if descending else ordering
    field = ORDERING_FIELDS.get(key)
    if not field:
        return qs.order_by("-id")
    order_expr = f"-{field}" if descending else field
    return qs.order_by(order_expr, "-id")


def column_facets_for_falhas(
    qs: QuerySet[AuditoriaFalhaCadastro],
    *,
    limit_per_column: int = 300,
) -> dict[str, list[str]]:
    falhas = list(
        qs.select_related("created_by", "atividade", "analise_origem").order_by("-id")[
            : limit_per_column * 3
        ]
    )
    if not falhas:
        return {key: [] for key in COLUMN_FILTER_KEYS}

    agent_index = _normalized_agent_index(falhas)
    histories = (
        AgentHistory.objects.filter(
            agent_id__in={agent.pk for agent in agent_index.values()},
            active=True,
            final_date__isnull=True,
        )
        .select_related("leader")
        .only("agent_id", "leader__full_name")
    )
    leaders_by_agent_id = {
        history.agent_id: (history.leader.full_name or "").strip()
        for history in histories
        if history.leader_id
    }

    buckets: dict[str, set[str]] = {key: set() for key in COLUMN_FILTER_KEYS}
    for falha in falhas:
        agent = agent_index.get((falha.usuario or "").strip().lower())
        leader_name = leaders_by_agent_id.get(agent.pk, "") if agent else ""
        for key in COLUMN_FILTER_KEYS:
            if len(buckets[key]) >= limit_per_column:
                continue
            buckets[key].add(
                _column_value_for_falha(
                    falha,
                    key,
                    agent=agent,
                    leader_name=leader_name,
                )
            )

    return {
        key: sorted(values, key=lambda value: value.casefold())
        for key, values in buckets.items()
    }


def _agent_index_for_identifiers(identifiers: set[str]) -> dict[str, Agent]:
    if not identifiers:
        return {}

    agents = (
        Agent.objects.annotate(
            lan_normalized=Lower("user_lan_id"),
            time_tracking_normalized=Lower("time_tracking_id"),
            oracle_normalized=Lower("oracle_id"),
        )
        .filter(
            Q(lan_normalized__in=identifiers)
            | Q(time_tracking_normalized__in=identifiers)
            | Q(oracle_normalized__in=identifiers)
        )
        .only("id", "full_name", "user_lan_id", "time_tracking_id", "oracle_id")
    )
    result: dict[str, Agent] = {}
    for agent in agents:
        for value in (agent.user_lan_id, agent.time_tracking_id, agent.oracle_id):
            normalized = (value or "").strip().lower()
            if normalized:
                result.setdefault(normalized, agent)
    return result


def _normalized_agent_index(falhas: list[AuditoriaFalhaCadastro]) -> dict[str, Agent]:
    identifiers = {
        falha.usuario.strip().lower()
        for falha in falhas
        if falha.usuario and falha.usuario.strip()
    }
    return _agent_index_for_identifiers(identifiers)


def agent_options_for_falhas(qs: QuerySet[AuditoriaFalhaCadastro]) -> list[dict[str, str]]:
    raw_identifiers = {
        str(value).strip()
        for value in qs.exclude(usuario="").values_list("usuario", flat=True).distinct()
        if value and str(value).strip()
    }
    agent_index = _agent_index_for_identifiers(
        {identifier.lower() for identifier in raw_identifiers}
    )

    options_by_key: dict[str, dict[str, str]] = {}
    for identifier in raw_identifiers:
        agent = agent_index.get(identifier.lower())
        if agent:
            value = (agent.user_lan_id or identifier).strip()
            label = (agent.full_name or value).strip()
            key = str(agent.pk)
        else:
            value = identifier
            label = identifier
            key = f"identifier:{identifier.lower()}"
        options_by_key.setdefault(key, {"value": value, "label": label})

    return sorted(
        options_by_key.values(),
        key=lambda option: (option["label"].casefold(), option["value"].casefold()),
    )


def serialize_falhas(falhas: list[AuditoriaFalhaCadastro]) -> list[dict[str, Any]]:
    """Serializa uma página com pessoas e contestação existente resolvidas em lote."""
    if not falhas:
        return []

    agent_index = _normalized_agent_index(falhas)
    agent_ids = {agent.pk for agent in agent_index.values()}
    histories = (
        AgentHistory.objects.filter(
            agent_id__in=agent_ids,
            active=True,
            final_date__isnull=True,
        )
        .select_related("leader")
        .only("agent_id", "leader__full_name")
    )
    leaders_by_agent_id = {
        history.agent_id: (history.leader.full_name or "").strip()
        for history in histories
        if history.leader_id
    }
    contestacao_by_falha_id: dict[int, ContestacaoOperacional] = {}
    for item in (
        ContestacaoOperacional.objects.filter(
            falha_id__in=[falha.pk for falha in falhas],
        )
        .only("id", "falha_id", "status", "created_at")
        .order_by("falha_id", "-created_at", "-id")
    ):
        contestacao_by_falha_id.setdefault(item.falha_id, item)
    protocol_contestacao_by_key = _protocol_contestacao_map_for_falhas(falhas)

    result: list[dict[str, Any]] = []
    for falha in falhas:
        agent = agent_index.get((falha.usuario or "").strip().lower())
        result.append(
            serialize_falha(
                falha,
                agent=agent,
                leader_name=leaders_by_agent_id.get(agent.pk, "") if agent else "",
                active_contestacao=contestacao_by_falha_id.get(falha.pk),
                active_contestacao_resolved=True,
                protocol_contestacao=protocol_contestacao_by_key.get(
                    _falha_protocol_group_key(falha)
                ),
                protocol_contestacao_resolved=True,
            )
        )
    return result


def serialize_protocol_groups(
    groups: list[list[AuditoriaFalhaCadastro]],
) -> list[dict[str, Any]]:
    if not groups:
        return []
    flat = [falha for group in groups for falha in group]
    serialized_by_id = {item["id"]: item for item in serialize_falhas(flat)}
    merged: list[dict[str, Any]] = []
    for group in groups:
        items = [serialized_by_id[falha.pk] for falha in group]
        primary = min(items, key=lambda item: item["id"])
        primary_falha = min(group, key=lambda falha: falha.pk)
        protocol_siblings = siblings_for_falha(primary_falha)
        irregularidades = _collect_protocol_irregularidades(protocol_siblings)
        falha_ids = [falha.pk for falha in protocol_siblings]
        contestacao_id = next(
            (item["contestacao_id"] for item in items if item.get("contestacao_id")),
            None,
        )
        contestacao_status = next(
            (item["contestacao_status"] for item in items if item.get("contestacao_status")),
            None,
        )
        protocolo_contestado = any(item.get("protocolo_contestado") for item in items)
        contestacao_resumo = next(
            (item["contestacao_resumo"] for item in items if item.get("contestacao_resumo")),
            None,
        )
        merged.append(
            {
                **primary,
                "falha_ids": falha_ids,
                "irregularidades": irregularidades,
                "irregularidades_itens": _protocol_irregularidades_itens(protocol_siblings),
                "irregularidade": irregularidades[0] if irregularidades else primary.get("irregularidade", ""),
                "contestacao_id": contestacao_id,
                "contestacao_status": contestacao_status,
                "protocolo_contestado": protocolo_contestado,
                "contestacao_resumo": contestacao_resumo,
            }
        )
    return merged


def serialize_falha(
    falha: AuditoriaFalhaCadastro,
    *,
    agent: Agent | None = None,
    leader_name: str = "",
    active_contestacao: ContestacaoOperacional | None = None,
    active_contestacao_resolved: bool = False,
    protocol_contestacao: ContestacaoOperacional | None = None,
    protocol_contestacao_resolved: bool = False,
) -> dict[str, Any]:
    origem = (falha.origem or falha.tipo_registro or "").strip().lower()
    parsed = falha.brflow_parsed if isinstance(falha.brflow_parsed, dict) else {}

    def parsed_text(key: str) -> str:
        value = parsed.get(key)
        if value is None or isinstance(value, (dict, list, tuple)):
            return ""
        return str(value).strip()

    atividade = falha.atividade if falha.atividade_id else None
    workflow = parsed_text("workflow") or (getattr(atividade, "workflow", "") or "").strip()
    nivel_hierarquico = parsed_text("nivel_hierarquico") or (
        getattr(atividade, "nivel_hierarquico", "") or ""
    ).strip()
    motivo_falha = (falha.motivo_falha or "").strip()
    protocol_siblings = siblings_for_falha(falha)
    irregularidades = _collect_protocol_irregularidades(protocol_siblings)
    irregularidade = irregularidades[0] if irregularidades else (
        (falha.descricao_irregularidades or "").strip() or motivo_falha
    )
    # No fluxo de contestação, o cenário é definido pelo motivo da falha.
    cenario = motivo_falha or parsed_text("cenario")
    data_auditoria = data_auditoria_for_falha(falha)
    atribuida = data_auditoria
    categoria = resolve_categoria_falha(falha)
    if not active_contestacao_resolved:
        active_contestacao = ContestacaoOperacional.objects.filter(
            falha=falha,
        ).only("id", "status", "created_at").order_by("-created_at", "-id").first()
    if not protocol_contestacao_resolved:
        protocol_contestacao = _protocol_contestacao_for_falha(falha)

    contestacao = protocol_contestacao or active_contestacao
    contestacao_is_active = bool(
        contestacao and contestacao.status in ContestacaoOperacional.STATUS_ATIVOS
    )

    observations: list[str] = []
    for value in (
        falha.observacao,
        getattr(falha.atividade, "observacao", "") if falha.atividade_id else "",
    ):
        text = (value or "").strip()
        if text and text not in observations:
            observations.append(text)

    return {
        "id": falha.pk,
        "protocolo": falha.protocolo,
        "usuario": falha.usuario,
        "agente_nome": (agent.full_name or "").strip() if agent else falha.usuario,
        "lider_nome": leader_name,
        "tipo_falha": falha.tipo_falha,
        "origem_tecnica": origem,
        "categoria": categoria,
        "dominio": CATEGORIA_TO_DOMINIO.get(categoria),
        "status_falha": falha.status_falha or AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        "motivo_falha": motivo_falha,
        "etapa_falha": falha.etapa_falha,
        "irregularidade": irregularidade,
        "irregularidades": irregularidades,
        "irregularidades_itens": _protocol_irregularidades_itens(protocol_siblings),
        "falha_ids": [item.pk for item in protocol_siblings],
        "workflow": workflow,
        "nivel_hierarquico": nivel_hierarquico,
        "cenario": cenario,
        "modulo": falha.modulo,
        "cliente": falha.cliente,
        "resultado_cliente": falha.resultado_cliente,
        "novo_resultado": falha.novo_resultado,
        "sinalizacao": falha.sinalizacao,
        "nivel_dificuldade": falha.nivel_dificuldade,
        "tipo_documento": falha.tipo_documento,
        "uf_documento": falha.uf_documento,
        "qualidade_imagem": falha.qualidade_imagem,
        "descricao_irregularidades": falha.descricao_irregularidades,
        "status_registrado": falha.status,
        "observacoes_auditores": observations,
        "data_auditoria": data_auditoria.isoformat() if data_auditoria else None,
        "atribuida_em": atribuida.isoformat() if atribuida else None,
        "contestacao_id": contestacao.pk if contestacao else None,
        "contestacao_status": contestacao.status if contestacao else None,
        "contestacao_ativa_id": contestacao.pk if contestacao_is_active else None,
        "contestacao_ativa_status": contestacao.status if contestacao_is_active else None,
        "protocolo_contestado": contestacao is not None,
        "contestacao_resumo": _serialize_contestacao_resumo(contestacao),
        "created_at": falha.created_at.isoformat() if falha.created_at else None,
    }


def serialize_contestacao(
    item: ContestacaoOperacional,
    *,
    include_falha: bool = False,
    agent: Agent | None = None,
    agent_resolved: bool = False,
    auditor_agent: Agent | None = None,
    auditor_agent_resolved: bool = False,
    user=None,
) -> dict[str, Any]:
    if not agent_resolved:
        identifier = (item.agente_usuario or "").strip().lower()
        agent = _agent_index_for_identifiers({identifier}).get(identifier) if identifier else None
    historico_qs = item.historico.all()[:50]
    cenario = ""
    irregularidade = ""
    irregularidades: list[str] = []
    auditor_nome = ""
    if item.falha_id:
        cenario = (item.falha.motivo_falha or "").strip()
        if not cenario and isinstance(item.falha.brflow_parsed, dict):
            parsed_cenario = item.falha.brflow_parsed.get("cenario")
            if parsed_cenario is not None and not isinstance(parsed_cenario, (dict, list, tuple)):
                cenario = str(parsed_cenario).strip()
        sibling_irregularidades = _collect_protocol_irregularidades(siblings_for_falha(item.falha))
        irregularidades = sibling_irregularidades
        irregularidade = sibling_irregularidades[0] if sibling_irregularidades else (
            (item.falha.descricao_irregularidades or "").strip() or cenario
        )
        auditor_identifier = (item.falha.auditor or "").strip()
        auditor_ref = getattr(item.falha, "auditor_ref", None)
        if auditor_ref and (auditor_ref.full_name or "").strip():
            auditor_nome = auditor_ref.full_name.strip()
        else:
            if not auditor_agent_resolved and auditor_identifier:
                auditor_agent = _agent_index_for_identifiers(
                    {auditor_identifier.lower()}
                ).get(auditor_identifier.lower())
            auditor_nome = (
                (auditor_agent.full_name or "").strip()
                if auditor_agent
                else auditor_identifier
            )

    lider_nome = ""
    if item.created_by_id:
        lider_nome = (item.created_by.get_full_name() or "").strip()
        if not lider_nome:
            lider_nome = (
                Agent.objects.filter(user_lan_id__iexact=item.created_by.username)
                .values_list("full_name", flat=True)
                .first()
                or item.created_by.username
            ).strip()
    data = {
        "id": item.pk,
        "falha_id": item.falha_id,
        "protocolo": item.protocolo,
        "agente_usuario": item.agente_usuario,
        "agente_nome": (agent.full_name or "").strip() if agent else "",
        "dominio": item.dominio,
        "origem_tecnica": item.origem_tecnica,
        "categoria": item.categoria,
        "status": item.status,
        "justificativa_lider": item.justificativa_lider,
        "parecer_interno": item.parecer_interno,
        "destino_falha": item.destino_falha,
        "cenario_original": item.cenario_original,
        "nivel_original": item.nivel_original,
        "etapa_original": item.etapa_original,
        "cenario_decisao": item.cenario_decisao,
        "nivel_decisao": item.nivel_decisao,
        "etapa_decisao": item.etapa_decisao,
        "cenario": cenario,
        "irregularidade": irregularidade,
        "irregularidades": irregularidades,
        "auditor_nome": auditor_nome,
        "lider_nome": lider_nome,
        "data_auditoria": item.atribuida_em.isoformat() if item.atribuida_em else None,
        "data_contestacao": item.created_at.isoformat() if item.created_at else None,
        "atribuida_em": item.atribuida_em.isoformat() if item.atribuida_em else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "iniciada_em": item.iniciada_em.isoformat() if item.iniciada_em else None,
        "decidida_em": item.decidida_em.isoformat() if item.decidida_em else None,
        "created_by": item.created_by_id,
        "created_by_username": getattr(item.created_by, "username", None),
        "analista": item.analista_id,
        "analista_username": getattr(item.analista, "username", None),
        "analista_decisao": item.analista_decisao_id,
        "analista_decisao_username": getattr(item.analista_decisao, "username", None),
        "falhas_manter_ids": list(item.falhas_manter_ids or []),
        "parecer_revisao": item.parecer_revisao,
        "can_revisar": can_user_revise_contestacao(user, item) if user is not None else False,
        "falha_status": item.falha.status_falha if item.falha_id else None,
        "historico": [
            {
                "id": h.pk,
                "evento": h.evento,
                "status_anterior": h.status_anterior,
                "status_novo": h.status_novo,
                "falha_status_anterior": h.falha_status_anterior,
                "falha_status_novo": h.falha_status_novo,
                "justificativa": h.justificativa,
                "parecer": h.parecer,
                **_historico_actor_payload(h, item),
                "metadata": h.metadata or {},
                "created_at": h.created_at.isoformat() if h.created_at else None,
            }
            for h in historico_qs
        ],
        "suporte_operacional": suporte_operacional_payload_for(item),
    }
    if include_falha and item.falha_id:
        data["falha"] = serialize_falha(item.falha)
    return data


def serialize_contestacoes(items: list[ContestacaoOperacional]) -> list[dict[str, Any]]:
    agent_identifiers = {
        item.agente_usuario.strip().lower()
        for item in items
        if item.agente_usuario and item.agente_usuario.strip()
    }
    auditor_identifiers = {
        item.falha.auditor.strip().lower()
        for item in items
        if item.falha_id and item.falha.auditor and item.falha.auditor.strip()
    }
    agent_index = _agent_index_for_identifiers(agent_identifiers | auditor_identifiers)
    return [
        serialize_contestacao(
            item,
            agent=agent_index.get((item.agente_usuario or "").strip().lower()),
            agent_resolved=True,
            auditor_agent=agent_index.get(
                (item.falha.auditor or "").strip().lower()
            ) if item.falha_id else None,
            auditor_agent_resolved=True,
        )
        for item in items
    ]


def _append_historico(
    contestacao: ContestacaoOperacional,
    *,
    evento: str,
    actor,
    status_anterior: str = "",
    status_novo: str = "",
    falha_status_anterior: str = "",
    falha_status_novo: str = "",
    justificativa: str = "",
    parecer: str = "",
    metadata: dict | None = None,
) -> ContestacaoOperacionalHistorico:
    return ContestacaoOperacionalHistorico.objects.create(
        contestacao=contestacao,
        evento=evento,
        status_anterior=status_anterior,
        status_novo=status_novo,
        falha_status_anterior=falha_status_anterior,
        falha_status_novo=falha_status_novo,
        justificativa=justificativa,
        parecer=parecer,
        actor=actor,
        metadata=metadata or {},
    )


@transaction.atomic
def criar_contestacao(
    *,
    user,
    falha_id: int,
    justificativa: str,
    categoria: str | None = None,
) -> ContestacaoOperacional:
    justificativa = (justificativa or "").strip()
    if not justificativa:
        raise ValidationError({"justificativa": "Justificativa obrigatória."})

    falha = (
        AuditoriaFalhaCadastro.objects.select_for_update()
        .filter(pk=falha_id)
        .first()
    )
    if not falha:
        raise ValidationError({"falha_id": "Falha não encontrada."})

    team = team_agent_identifiers_for_user(user)
    if team is not None and falha.usuario.strip().lower() not in team:
        raise ValidationError({"falha_id": "Falha fora do escopo da sua equipe."})

    origem = (falha.origem or falha.tipo_registro or "").strip().lower()
    cat = resolve_categoria_falha(falha, categoria=categoria)

    protocol_siblings = siblings_for_falha(falha)
    if ContestacaoOperacional.objects.filter(falha__in=protocol_siblings).exists():
        raise ValidationError({"falha_id": "Este protocolo já foi contestado."})

    if ContestacaoOperacional.objects.filter(falha=falha).exists():
        raise ValidationError({"falha_id": "Esta falha já foi contestada."})

    # O POST deve respeitar exatamente a mesma elegibilidade da listagem. A
    # trava acima estabiliza a linha; a consulta comum evita que um ID oculto
    # (por exemplo, sem_falha) contorne as regras por chamada direta da API.
    if not falhas_queryset_for_user(user).filter(pk=falha.pk).exists():
        raise ValidationError({"falha_id": "Falha nao elegivel para contestacao."})

    dominio = CATEGORIA_TO_DOMINIO[cat]
    atribuida = atribuicao_em_for_falha(falha)
    if atribuida is None:
        raise ValidationError(
            {
                "falha_id": (
                    "Falha sem data de atribuição no campo exigido pela origem "
                    f"({origem or 'desconhecida'})."
                )
            }
        )

    item = ContestacaoOperacional.objects.create(
        falha=falha,
        dominio=dominio,
        origem_tecnica=origem,
        categoria=cat,
        status=ContestacaoOperacional.STATUS_PENDENTE,
        justificativa_lider=justificativa,
        protocolo=falha.protocolo,
        agente_usuario=falha.usuario,
        atribuida_em=atribuida,
        created_by=user,
    )
    _append_historico(
        item,
        evento=ContestacaoOperacionalHistorico.EVENTO_CRIADA,
        actor=user,
        status_anterior="",
        status_novo=ContestacaoOperacional.STATUS_PENDENTE,
        falha_status_anterior=falha.status_falha or AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        falha_status_novo=falha.status_falha or AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        justificativa=justificativa,
    )
    return item


def build_erros_procedentes_por_auditor() -> list[dict]:
    """Base do indicador: procedências agrupadas pelo ID do auditor original."""
    grouped = list(
        ContestacaoOperacional.objects.filter(
            status=ContestacaoOperacional.STATUS_PROCEDENTE,
        )
        .values("falha__auditor_responsavel_id")
        .annotate(total=Count("id"))
        .order_by("falha__auditor_responsavel_id")
    )
    user_ids = {
        row["falha__auditor_responsavel_id"]
        for row in grouped
        if row["falha__auditor_responsavel_id"]
    }
    User = get_user_model()
    users = {
        user.pk: user
        for user in User.objects.filter(pk__in=user_ids).select_related("profile__agent")
    }

    result = []
    for row in grouped:
        user_id = row["falha__auditor_responsavel_id"]
        user = users.get(user_id)
        if user is None:
            label = "Auditor não identificado"
        else:
            profile = getattr(user, "profile", None)
            agent = getattr(profile, "agent", None) if profile else None
            label = (
                (getattr(agent, "full_name", "") or "").strip()
                or (user.get_full_name() or "").strip()
                or user.username
            )
        result.append(
            {
                "auditor_responsavel_id": str(user_id) if user_id else None,
                "auditor": label,
                "erros_contestacao_procedente": row["total"],
            }
        )
    return result


def build_erros_por_analista_contestacao_revisada() -> list[dict]:
    """Conta revisões de resultado por analista que respondeu originalmente."""
    grouped = list(
        ContestacaoOperacionalHistorico.objects.filter(
            evento=ContestacaoOperacionalHistorico.EVENTO_RESULTADO_ALTERADO,
        )
        .values("contestacao__analista_decisao_id")
        .annotate(total=Count("id"))
        .order_by("contestacao__analista_decisao_id")
    )
    user_ids = {
        row["contestacao__analista_decisao_id"]
        for row in grouped
        if row["contestacao__analista_decisao_id"]
    }
    User = get_user_model()
    users = {
        user.pk: user
        for user in User.objects.filter(pk__in=user_ids).select_related("profile__agent")
    }

    result = []
    for row in grouped:
        user_id = row["contestacao__analista_decisao_id"]
        user = users.get(user_id)
        if user is None:
            label = "Analista não identificado"
        else:
            profile = getattr(user, "profile", None)
            agent = getattr(profile, "agent", None) if profile else None
            label = (
                (getattr(agent, "full_name", "") or "").strip()
                or (user.get_full_name() or "").strip()
                or user.username
            )
        result.append(
            {
                "analista_decisao_id": str(user_id) if user_id else None,
                "analista": label,
                "erros_contestacao_revisada": row["total"],
            }
        )
    return result


FILA_COLUMN_FILTER_KEYS = frozenset({
    "protocolo",
    "agente",
    "motivo",
    "lider",
    "auditor",
    "status",
    "data_auditoria",
    "data_contestacao",
    "decidida_em",
})

FILA_ORDERING_FIELDS = {
    "protocolo": "protocolo",
    "agente": "agente_usuario",
    "status": "status",
    "data_auditoria": "atribuida_em",
    "data_contestacao": "created_at",
    "decidida_em": "decidida_em",
    "id": "id",
}

REVISAO_COLUMN_FILTER_KEYS = frozenset({
    "protocolo",
    "agente",
    "motivo",
    "lider",
    "auditor",
    "analista",
    "revisor",
    "resultado_de",
    "resultado_para",
    "destino",
    "irregularidades_manter",
    "revisado_em",
})

REVISAO_ORDERING_FIELDS = {
    "protocolo": "contestacao__protocolo",
    "agente": "contestacao__agente_usuario",
    "revisado_em": "created_at",
    "id": "id",
}


def _user_display_name(user) -> str:
    if user is None:
        return ""
    name = (user.get_full_name() or "").strip()
    if name:
        return name
    agent = Agent.objects.filter(user_lan_id__iexact=user.username).values_list(
        "full_name", flat=True
    ).first()
    return (agent or user.username or "").strip()


def _falhas_manter_display_label(falha_ids: list | tuple | None) -> str:
    if not falha_ids:
        return "Nenhuma"
    parsed_ids: list[int] = []
    for raw_id in falha_ids:
        try:
            parsed_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    if not parsed_ids:
        return "Nenhuma"
    labels: list[str] = []
    for falha in AuditoriaFalhaCadastro.objects.filter(pk__in=parsed_ids).order_by("id"):
        text = (falha.descricao_irregularidades or falha.motivo_falha or "").strip()
        if not text:
            text = f"Falha #{falha.pk}"
        labels.append(text[:80])
    return "; ".join(labels) if labels else "Nenhuma"


def _contestacao_lider_nome(item: ContestacaoOperacional) -> str:
    if not item.created_by_id:
        return ""
    lider_nome = (item.created_by.get_full_name() or "").strip()
    if lider_nome:
        return lider_nome
    return (
        Agent.objects.filter(user_lan_id__iexact=item.created_by.username)
        .values_list("full_name", flat=True)
        .first()
        or item.created_by.username
        or ""
    ).strip()


def _contestacao_auditor_nome(item: ContestacaoOperacional) -> str:
    if not item.falha_id:
        return ""
    auditor_ref = getattr(item.falha, "auditor_ref", None)
    if auditor_ref and (auditor_ref.full_name or "").strip():
        return auditor_ref.full_name.strip()
    auditor_identifier = (item.falha.auditor or "").strip()
    if not auditor_identifier:
        return ""
    agent = _agent_index_for_identifiers({auditor_identifier.lower()}).get(
        auditor_identifier.lower()
    )
    return (agent.full_name or "").strip() if agent else auditor_identifier


def _contestacao_motivo_display(item: ContestacaoOperacional) -> str:
    if not item.falha_id:
        return ""
    if item.dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        return (item.falha.motivo_falha or "").strip()
    irregularidades = _collect_protocol_irregularidades(siblings_for_falha(item.falha))
    if irregularidades:
        return irregularidades[0]
    return (item.falha.descricao_irregularidades or "").strip()


def _contestacao_agente_nome(item: ContestacaoOperacional, agent_index: dict | None = None) -> str:
    identifier = (item.agente_usuario or "").strip().lower()
    if not identifier:
        return ""
    if agent_index is None:
        agent_index = _agent_index_for_identifiers({identifier})
    agent = agent_index.get(identifier)
    return (agent.full_name or "").strip() if agent else item.agente_usuario


def _format_contestacao_date(value) -> str:
    if not value:
        return "—"
    if hasattr(value, "isoformat"):
        return value.date().isoformat()
    return str(value)


def _column_value_for_contestacao_fila(
    item: ContestacaoOperacional,
    key: str,
    *,
    agent_index: dict | None = None,
) -> str:
    if key == "protocolo":
        return item.protocolo or "—"
    if key == "agente":
        label = _contestacao_agente_nome(item, agent_index)
        return label or item.agente_usuario or "—"
    if key == "motivo":
        return _contestacao_motivo_display(item) or "—"
    if key == "lider":
        return _contestacao_lider_nome(item) or "—"
    if key == "auditor":
        return _contestacao_auditor_nome(item) or "—"
    if key == "status":
        return item.status or "—"
    if key == "data_auditoria":
        return _format_contestacao_date(item.atribuida_em)
    if key == "data_contestacao":
        return _format_contestacao_date(item.created_at)
    if key == "decidida_em":
        return _format_contestacao_date(item.decidida_em)
    return ""


def apply_contestacao_fila_column_filters(
    qs: QuerySet[ContestacaoOperacional],
    column_filters: dict[str, list[str]] | None,
) -> QuerySet[ContestacaoOperacional]:
    if not column_filters:
        return qs
    normalized: dict[str, set[str]] = {}
    for key, values in column_filters.items():
        if key not in FILA_COLUMN_FILTER_KEYS:
            continue
        cleaned = {str(value).strip() for value in values if str(value).strip()}
        if cleaned:
            normalized[key] = cleaned
    if not normalized:
        return qs

    simple_map = {
        "protocolo": "protocolo__in",
        "agente": "agente_usuario__in",
        "status": "status__in",
    }
    for key, lookup in simple_map.items():
        if key in normalized:
            qs = qs.filter(**{lookup: sorted(normalized[key])})

    complex_keys = set(normalized) - set(simple_map)
    if not complex_keys:
        return qs

    identifiers = {
        str(value).strip().lower()
        for value in qs.exclude(agente_usuario="").values_list("agente_usuario", flat=True)
        if value and str(value).strip()
    }
    agent_index = _agent_index_for_identifiers(identifiers)
    matching_ids: list[int] = []
    for item in qs.iterator(chunk_size=200):
        if all(
            _column_value_for_contestacao_fila(item, key, agent_index=agent_index) in selected
            for key, selected in normalized.items()
        ):
            matching_ids.append(item.pk)
    if not matching_ids:
        return qs.none()
    return qs.filter(pk__in=matching_ids)


def apply_contestacao_fila_ordering(
    qs: QuerySet[ContestacaoOperacional],
    sort_key: str = "",
    sort_dir: str = "asc",
) -> QuerySet[ContestacaoOperacional]:
    sort_key = (sort_key or "").strip()
    sort_dir = (sort_dir or "asc").strip().lower()
    if sort_key in {"motivo", "lider", "auditor"}:
        return qs.order_by("-created_at", "-id")
    field = FILA_ORDERING_FIELDS.get(sort_key)
    if not field:
        return qs.order_by("-created_at", "-id")
    prefix = "-" if sort_dir == "desc" else ""
    return qs.order_by(f"{prefix}{field}", "-id")


def fila_column_filter_options(
    qs: QuerySet[ContestacaoOperacional],
    filter_column: str,
    column_filters: dict[str, list[str]] | None,
) -> list[str]:
    filter_column = (filter_column or "").strip()
    if filter_column not in FILA_COLUMN_FILTER_KEYS:
        return []
    scoped = apply_contestacao_fila_column_filters(qs, column_filters)
    identifiers = {
        str(value).strip().lower()
        for value in scoped.exclude(agente_usuario="").values_list("agente_usuario", flat=True)
        if value and str(value).strip()
    }
    agent_index = _agent_index_for_identifiers(identifiers)
    values: set[str] = set()
    for item in scoped.iterator(chunk_size=200):
        values.add(_column_value_for_contestacao_fila(item, filter_column, agent_index=agent_index))
    return sorted(value for value in values if value and value != "—")


def revisoes_queryset(user, *, dominio: str) -> QuerySet[ContestacaoOperacionalHistorico]:
    dominio = (dominio or "").strip().lower()
    if dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        if not user_has_any_permission(
            user,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE,
        ):
            return ContestacaoOperacionalHistorico.objects.none()
    elif dominio == ContestacaoOperacional.DOMINIO_COMPLIANCE:
        if not user_has_any_permission(
            user,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE,
        ):
            return ContestacaoOperacionalHistorico.objects.none()
    else:
        return ContestacaoOperacionalHistorico.objects.none()

    return (
        ContestacaoOperacionalHistorico.objects.filter(
            evento=ContestacaoOperacionalHistorico.EVENTO_RESULTADO_ALTERADO,
            contestacao__dominio=dominio,
        )
        .exclude(contestacao__falha_id__in=_contexto_conflitante_ids())
        .select_related(
            "contestacao",
            "contestacao__falha",
            "contestacao__falha__auditor_ref",
            "contestacao__created_by",
            "contestacao__analista_decisao",
            "actor",
        )
        .order_by("-created_at", "-id")
    )


def _status_label(status: str) -> str:
    labels = dict(ContestacaoOperacional.STATUS_CHOICES)
    return labels.get(status, status or "—")


def _destino_label(destino: str) -> str:
    labels = dict(ContestacaoOperacional.DESTINO_FALHA_CHOICES)
    return labels.get(destino, destino or "—")


def serialize_contestacao_revisao(
    historico: ContestacaoOperacionalHistorico,
    *,
    agent_index: dict | None = None,
) -> dict[str, Any]:
    item = historico.contestacao
    meta = historico.metadata or {}
    if agent_index is None:
        identifiers = set()
        if item.agente_usuario:
            identifiers.add(item.agente_usuario.strip().lower())
        if item.falha_id and item.falha.auditor:
            identifiers.add(item.falha.auditor.strip().lower())
        agent_index = _agent_index_for_identifiers(identifiers)

    resultado_de = meta.get("resultado_de") or historico.status_anterior
    resultado_para = meta.get("resultado_para") or historico.status_novo
    destino_de = meta.get("destino_falha_de") or ""
    destino_para = meta.get("destino_falha_para") or ""
    falhas_manter_de = meta.get("falhas_manter_de") or []
    falhas_manter_para = meta.get("falhas_manter_para") or []

    analista_decisao = item.analista_decisao
    return {
        "id": historico.pk,
        "contestacao_id": item.pk,
        "protocolo": item.protocolo,
        "dominio": item.dominio,
        "agente_usuario": item.agente_usuario,
        "agente_nome": _contestacao_agente_nome(item, agent_index),
        "motivo": _contestacao_motivo_display(item),
        "lider_nome": _contestacao_lider_nome(item),
        "auditor_nome": _contestacao_auditor_nome(item),
        "analista_nome": _user_display_name(analista_decisao),
        "analista_username": getattr(analista_decisao, "username", None),
        "revisor_nome": _user_display_name(historico.actor),
        "revisor_username": getattr(historico.actor, "username", None),
        "resultado_de": resultado_de,
        "resultado_para": resultado_para,
        "resultado_de_label": _status_label(resultado_de),
        "resultado_para_label": _status_label(resultado_para),
        "destino_falha_de": destino_de,
        "destino_falha_para": destino_para,
        "destino_falha_de_label": _destino_label(destino_de),
        "destino_falha_para_label": _destino_label(destino_para),
        "falhas_manter_de": list(falhas_manter_de or []),
        "falhas_manter_para": list(falhas_manter_para or []),
        "falhas_manter_de_label": _falhas_manter_display_label(falhas_manter_de),
        "falhas_manter_para_label": _falhas_manter_display_label(falhas_manter_para),
        "parecer_revisao": historico.parecer,
        "revisado_em": historico.created_at.isoformat() if historico.created_at else None,
        "metadata": meta,
    }


def serialize_contestacao_revisoes(
    historicos: list[ContestacaoOperacionalHistorico],
) -> list[dict[str, Any]]:
    identifiers: set[str] = set()
    for historico in historicos:
        item = historico.contestacao
        if item.agente_usuario:
            identifiers.add(item.agente_usuario.strip().lower())
        if item.falha_id and item.falha.auditor:
            identifiers.add(item.falha.auditor.strip().lower())
    agent_index = _agent_index_for_identifiers(identifiers)
    return [
        serialize_contestacao_revisao(historico, agent_index=agent_index)
        for historico in historicos
    ]


def _column_value_for_revisao(row: dict[str, Any], key: str) -> str:
    if key == "protocolo":
        return row.get("protocolo") or "—"
    if key == "agente":
        return row.get("agente_nome") or row.get("agente_usuario") or "—"
    if key == "motivo":
        return row.get("motivo") or "—"
    if key == "lider":
        return row.get("lider_nome") or "—"
    if key == "auditor":
        return row.get("auditor_nome") or "—"
    if key == "analista":
        return row.get("analista_nome") or row.get("analista_username") or "—"
    if key == "revisor":
        return row.get("revisor_nome") or row.get("revisor_username") or "—"
    if key == "resultado_de":
        return row.get("resultado_de_label") or "—"
    if key == "resultado_para":
        return row.get("resultado_para_label") or "—"
    if key == "destino":
        de = row.get("destino_falha_de_label") or "—"
        para = row.get("destino_falha_para_label") or "—"
        return f"{de} → {para}"
    if key == "irregularidades_manter":
        de = row.get("falhas_manter_de_label") or "Nenhuma"
        para = row.get("falhas_manter_para_label") or "Nenhuma"
        return f"{de} → {para}"
    if key == "revisado_em":
        raw = row.get("revisado_em")
        if not raw:
            return "—"
        return raw.split("T", 1)[0]
    return ""


def apply_revisao_column_filters(
    rows: list[dict[str, Any]],
    column_filters: dict[str, list[str]] | None,
) -> list[dict[str, Any]]:
    if not column_filters:
        return rows
    normalized: dict[str, set[str]] = {}
    for key, values in column_filters.items():
        if key not in REVISAO_COLUMN_FILTER_KEYS:
            continue
        cleaned = {str(value).strip() for value in values if str(value).strip()}
        if cleaned:
            normalized[key] = cleaned
    if not normalized:
        return rows
    return [
        row
        for row in rows
        if all(_column_value_for_revisao(row, key) in selected for key, selected in normalized.items())
    ]


def revisao_column_filter_options(
    rows: list[dict[str, Any]],
    filter_column: str,
    column_filters: dict[str, list[str]] | None,
) -> list[str]:
    filter_column = (filter_column or "").strip()
    if filter_column not in REVISAO_COLUMN_FILTER_KEYS:
        return []
    scoped = apply_revisao_column_filters(rows, column_filters)
    values = {_column_value_for_revisao(row, filter_column) for row in scoped}
    return sorted(value for value in values if value and value != "—")


def _historico_actor_payload(
    historico: ContestacaoOperacionalHistorico,
    contestacao: ContestacaoOperacional | None = None,
) -> dict[str, str | None]:
    actor = historico.actor
    contestacao = contestacao or historico.contestacao
    if historico.evento == ContestacaoOperacionalHistorico.EVENTO_CRIADA:
        return {
            "actor": getattr(actor, "username", None),
            "actor_nome": _contestacao_lider_nome(contestacao) or _user_display_name(actor),
        }
    return {
        "actor": getattr(actor, "username", None),
        "actor_nome": _user_display_name(actor) or getattr(actor, "username", None),
    }


def contestacoes_fila_queryset(user, *, dominio: str) -> QuerySet[ContestacaoOperacional]:
    dominio = (dominio or "").strip().lower()
    if dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        if not user_has_any_permission(
            user,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE,
        ):
            return ContestacaoOperacional.objects.none()
    elif dominio == ContestacaoOperacional.DOMINIO_COMPLIANCE:
        if not user_has_any_permission(
            user,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE,
        ):
            return ContestacaoOperacional.objects.none()
    else:
        return ContestacaoOperacional.objects.none()

    categorias = {
        ContestacaoOperacional.DOMINIO_FRAUD: (
            ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
            ContestacaoOperacional.CATEGORIA_CONTESTACAO_FRAUD,
        ),
        ContestacaoOperacional.DOMINIO_COMPLIANCE: (
            ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
            ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE,
        ),
    }
    return (
        ContestacaoOperacional.objects.filter(
            dominio=dominio,
            categoria__in=categorias[dominio],
        )
        .exclude(falha_id__in=_contexto_conflitante_ids())
        .select_related(
            "falha",
            "falha__auditor_ref",
            "created_by",
            "analista",
            "analista_decisao",
        )
        .prefetch_related("historico")
        .order_by("-created_at", "-id")
    )


def minhas_contestacoes_queryset(user) -> QuerySet[ContestacaoOperacional]:
    return (
        ContestacaoOperacional.objects.filter(created_by=user)
        .exclude(falha_id__in=_contexto_conflitante_ids())
        .select_related("falha", "falha__auditor_ref", "created_by", "analista")
        .prefetch_related("historico")
        .order_by("-created_at")
    )


@transaction.atomic
def iniciar_analise(*, user, contestacao_id: int) -> ContestacaoOperacional:
    item = (
        ContestacaoOperacional.objects.select_for_update()
        .select_related("falha")
        .filter(pk=contestacao_id)
        .first()
    )
    if not item:
        raise ValidationError({"id": "Contestação não encontrada."})
    _assert_can_analyze(user, item.dominio)
    if item.status != ContestacaoOperacional.STATUS_PENDENTE:
        raise ValidationError({"status": "Somente contestações pendentes podem ser iniciadas."})

    anterior = item.status
    item.status = ContestacaoOperacional.STATUS_EM_ANALISE
    item.analista = user
    item.iniciada_em = timezone.now()
    item.save(update_fields=["status", "analista", "iniciada_em", "updated_at"])
    _append_historico(
        item,
        evento=ContestacaoOperacionalHistorico.EVENTO_ANALISE_INICIADA,
        actor=user,
        status_anterior=anterior,
        status_novo=item.status,
        falha_status_anterior=item.falha.status_falha,
        falha_status_novo=item.falha.status_falha,
    )
    resolve_and_link(item)
    return item


@transaction.atomic
def devolver_analise_fila(*, user, contestacao_id: int) -> ContestacaoOperacional:
    item = (
        ContestacaoOperacional.objects.select_for_update()
        .select_related("falha")
        .filter(pk=contestacao_id)
        .first()
    )
    if not item:
        raise ValidationError({"id": "Contestação não encontrada."})
    _assert_can_analyze(user, item.dominio)
    if item.status != ContestacaoOperacional.STATUS_EM_ANALISE:
        raise ValidationError({"status": "Somente contestações em análise podem voltar à fila."})
    if item.analista_id != user.id:
        raise ValidationError({"analista": "Somente o auditor que iniciou a análise pode devolvê-la."})

    status_anterior = item.status
    item.status = ContestacaoOperacional.STATUS_PENDENTE
    item.analista = None
    item.iniciada_em = None
    item.save(update_fields=["status", "analista", "iniciada_em", "updated_at"])
    _append_historico(
        item,
        evento=ContestacaoOperacionalHistorico.EVENTO_DEVOLVIDA_FILA,
        actor=user,
        status_anterior=status_anterior,
        status_novo=item.status,
        falha_status_anterior=item.falha.status_falha,
        falha_status_novo=item.falha.status_falha,
    )
    return item


@transaction.atomic
def decidir_contestacao(
    *,
    user,
    contestacao_id: int,
    resultado: str,
    parecer: str,
    destino_falha: str = "",
    cenario: str = "",
    nivel: str = "",
    etapa: str = "",
    confirmar_outro_analista: bool = False,
    falhas_manter: list[int] | None = None,
) -> ContestacaoOperacional:
    resultado = (resultado or "").strip().lower()
    parecer = (parecer or "").strip()
    destino_falha = (destino_falha or "").strip().lower()
    cenario = (cenario or "").strip()
    nivel = (nivel or "").strip()
    etapa = (etapa or "").strip()
    if resultado not in (
        ContestacaoOperacional.STATUS_PROCEDENTE,
        ContestacaoOperacional.STATUS_IMPROCEDENTE,
    ):
        raise ValidationError({"resultado": "Resultado deve ser procedente ou improcedente."})
    if not parecer:
        raise ValidationError({"parecer": "Parecer interno obrigatório."})

    item = (
        ContestacaoOperacional.objects.select_for_update()
        .select_related("falha")
        .filter(pk=contestacao_id)
        .first()
    )
    if not item:
        raise ValidationError({"id": "Contestação não encontrada."})
    _assert_can_analyze(user, item.dominio)
    if item.status == ContestacaoOperacional.STATUS_PENDENTE:
        raise ValidationError(
            {"status": "Inicie a análise antes de registrar a decisão."}
        )
    if item.status != ContestacaoOperacional.STATUS_EM_ANALISE:
        raise ValidationError({"status": "Contestação já decidida."})
    if (
        item.analista_id is not None
        and item.analista_id != user.id
        and confirmar_outro_analista is not True
    ):
        raise ValidationError(
            {
                "confirmar_outro_analista": (
                    "Esta contestação está atribuída a outro analista. "
                    "Confirme para continuar com a resposta."
                )
            }
        )

    falha = AuditoriaFalhaCadastro.objects.select_for_update().get(pk=item.falha_id)

    if item.dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        if destino_falha not in (
            ContestacaoOperacional.FALHA_STATUS_MANTIDA,
            ContestacaoOperacional.FALHA_STATUS_RETIRADA,
        ):
            raise ValidationError(
                {"destino_falha": "Informe se a falha deve ser mantida ou retirada."}
            )
        reclassificar = (
            resultado == ContestacaoOperacional.STATUS_PROCEDENTE
            and destino_falha == ContestacaoOperacional.FALHA_STATUS_MANTIDA
        )
        if reclassificar:
            errors = {}
            if not cenario:
                errors["cenario"] = "Cenário obrigatório para manter uma falha procedente."
            if not nivel:
                errors["nivel"] = "Nível obrigatório para manter uma falha procedente."
            if not etapa:
                errors["etapa"] = "Etapa obrigatória para manter uma falha procedente."
            if not errors:
                cenario, error = _resolve_reclassification_catalog_value(
                    value=cenario,
                    original=falha.motivo_falha,
                    queryset=AuditoriaMotivoFalha.objects.all(),
                    value_field="motivo",
                    label="Cenário",
                    max_length=AuditoriaFalhaCadastro._meta.get_field("motivo_falha").max_length,
                )
                if error:
                    errors["cenario"] = error
                nivel, error = _resolve_reclassification_catalog_value(
                    value=nivel,
                    original=falha.nivel_dificuldade,
                    queryset=AuditoriaCatalogItem.objects.filter(
                        catalog=AuditoriaCatalogItem.CATALOG_NIVEL_DIFICULDADE
                    ),
                    value_field="value",
                    label="Nível",
                    max_length=AuditoriaFalhaCadastro._meta.get_field(
                        "nivel_dificuldade"
                    ).max_length,
                )
                if error:
                    errors["nivel"] = error
                etapa, error = _resolve_reclassification_catalog_value(
                    value=etapa,
                    original=falha.etapa_falha,
                    queryset=AuditoriaCatalogItem.objects.filter(
                        catalog=AuditoriaCatalogItem.CATALOG_ETAPA_FALHA
                    ),
                    value_field="value",
                    label="Etapa",
                    max_length=AuditoriaFalhaCadastro._meta.get_field("etapa_falha").max_length,
                )
                if error:
                    errors["etapa"] = error
            if errors:
                raise ValidationError(errors)
        else:
            cenario = nivel = etapa = ""
        falha_status_novo = destino_falha
        falhas_manter_ids_saved: list[int] = []
    else:
        if destino_falha or cenario or nivel or etapa:
            raise ValidationError(
                {"destino_falha": "O destino manual da falha está disponível somente em Fraud."}
            )
        reclassificar = False
        siblings = siblings_for_falha(falha)
        sibling_ids = {item.pk for item in siblings}
        siblings_locked = list(
            AuditoriaFalhaCadastro.objects.select_for_update().filter(pk__in=sibling_ids)
        )
        if resultado == ContestacaoOperacional.STATUS_PROCEDENTE:
            keep_ids: set[int] = set()
        elif falhas_manter is None:
            keep_ids = sibling_ids
        else:
            keep_ids = set()
            invalid_ids: list[int] = []
            for raw_id in falhas_manter:
                try:
                    parsed_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if parsed_id in sibling_ids:
                    keep_ids.add(parsed_id)
                else:
                    invalid_ids.append(parsed_id)
            if invalid_ids:
                raise ValidationError(
                    {
                        "falhas_manter": (
                            "Uma ou mais irregularidades informadas não pertencem ao protocolo."
                        )
                    }
                )

        for sibling in siblings_locked:
            sibling.status_falha = (
                AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA
                if sibling.pk in keep_ids
                else AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA
            )
            sibling.save(update_fields=["status_falha", "updated_at"])

        falha.refresh_from_db(fields=["status_falha"])
        falha_status_novo = falha.status_falha or AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA
        falhas_manter_ids_saved = sorted(keep_ids)

    falha_status_anterior = falha.status_falha or AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA

    if reclassificar:
        cenario_original = (falha.motivo_falha or "").strip()
        nivel_original = (falha.nivel_dificuldade or "").strip()
        etapa_original = (falha.etapa_falha or "").strip()
    else:
        cenario_original = nivel_original = etapa_original = ""

    status_anterior = item.status
    item.status = resultado
    item.parecer_interno = parecer
    item.destino_falha = (
        destino_falha
        if item.dominio == ContestacaoOperacional.DOMINIO_FRAUD
        else ""
    )
    item.cenario_original = cenario_original
    item.nivel_original = nivel_original
    item.etapa_original = etapa_original
    item.cenario_decisao = cenario
    item.nivel_decisao = nivel
    item.etapa_decisao = etapa
    item.analista = user
    if item.analista_decisao_id is None:
        item.analista_decisao = user
    item.falhas_manter_ids = falhas_manter_ids_saved
    item.decidida_em = timezone.now()
    item.save(
        update_fields=[
            "status",
            "parecer_interno",
            "destino_falha",
            "cenario_original",
            "nivel_original",
            "etapa_original",
            "cenario_decisao",
            "nivel_decisao",
            "etapa_decisao",
            "analista",
            "analista_decisao",
            "falhas_manter_ids",
            "decidida_em",
            "updated_at",
        ]
    )

    falha.status_falha = falha_status_novo
    falha_update_fields = ["status_falha", "updated_at"]
    if reclassificar:
        falha.motivo_falha = cenario
        falha.nivel_dificuldade = nivel
        falha.etapa_falha = etapa
        falha_update_fields.extend(["motivo_falha", "nivel_dificuldade", "etapa_falha"])
    if item.dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        falha.save(update_fields=falha_update_fields)

    _append_historico(
        item,
        evento=ContestacaoOperacionalHistorico.EVENTO_DECIDIDA,
        actor=user,
        status_anterior=status_anterior,
        status_novo=item.status,
        falha_status_anterior=falha_status_anterior,
        falha_status_novo=falha_status_novo,
        parecer=parecer,
    )
    return item


def can_user_revise_contestacao(user, item: ContestacaoOperacional) -> bool:
    if item.status not in (
        ContestacaoOperacional.STATUS_PROCEDENTE,
        ContestacaoOperacional.STATUS_IMPROCEDENTE,
    ):
        return False
    if item.dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        return user_has_permission(user, R.QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE)
    if item.dominio == ContestacaoOperacional.DOMINIO_COMPLIANCE:
        return user_has_permission(user, R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE)
    return False


def _assert_can_revise(user, dominio: str) -> None:
    if dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        if not user_has_permission(user, R.QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE):
            raise ValidationError({"detail": "Sem permissão para revisar contestações Fraud."})
    elif dominio == ContestacaoOperacional.DOMINIO_COMPLIANCE:
        if not user_has_permission(user, R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE):
            raise ValidationError(
                {"detail": "Sem permissão para revisar contestações Compliance."}
            )
    else:
        raise ValidationError({"dominio": "Domínio inválido."})


@transaction.atomic
def revisar_resultado_contestacao(
    *,
    user,
    contestacao_id: int,
    resultado: str,
    parecer: str,
    destino_falha: str = "",
    cenario: str = "",
    nivel: str = "",
    etapa: str = "",
    falhas_manter: list[int] | None = None,
) -> ContestacaoOperacional:
    resultado = (resultado or "").strip().lower()
    parecer = (parecer or "").strip()
    destino_falha = (destino_falha or "").strip().lower()
    cenario = (cenario or "").strip()
    nivel = (nivel or "").strip()
    etapa = (etapa or "").strip()
    if resultado not in (
        ContestacaoOperacional.STATUS_PROCEDENTE,
        ContestacaoOperacional.STATUS_IMPROCEDENTE,
    ):
        raise ValidationError({"resultado": "Resultado deve ser procedente ou improcedente."})
    if not parecer:
        raise ValidationError({"parecer": "Parecer da revisão obrigatório."})

    item = (
        ContestacaoOperacional.objects.select_for_update()
        .filter(pk=contestacao_id)
        .first()
    )
    if not item:
        raise ValidationError({"id": "Contestação não encontrada."})
    _assert_can_revise(user, item.dominio)
    if item.status not in (
        ContestacaoOperacional.STATUS_PROCEDENTE,
        ContestacaoOperacional.STATUS_IMPROCEDENTE,
    ):
        raise ValidationError({"status": "Somente contestações decididas podem ser revisadas."})

    resultado_anterior = item.status
    destino_anterior = item.destino_falha
    falhas_manter_anterior = list(item.falhas_manter_ids or [])
    cenario_decisao_anterior = item.cenario_decisao
    nivel_decisao_anterior = item.nivel_decisao
    etapa_decisao_anterior = item.etapa_decisao

    falha = AuditoriaFalhaCadastro.objects.select_for_update().get(pk=item.falha_id)
    falha_status_anterior = falha.status_falha or AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA

    if item.dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        if destino_falha not in (
            ContestacaoOperacional.FALHA_STATUS_MANTIDA,
            ContestacaoOperacional.FALHA_STATUS_RETIRADA,
        ):
            raise ValidationError(
                {"destino_falha": "Informe se a falha deve ser mantida ou retirada."}
            )
        reclassificar = (
            resultado == ContestacaoOperacional.STATUS_PROCEDENTE
            and destino_falha == ContestacaoOperacional.FALHA_STATUS_MANTIDA
        )
        if reclassificar:
            errors = {}
            if not cenario:
                errors["cenario"] = "Cenário obrigatório para manter uma falha procedente."
            if not nivel:
                errors["nivel"] = "Nível obrigatório para manter uma falha procedente."
            if not etapa:
                errors["etapa"] = "Etapa obrigatória para manter uma falha procedente."
            if not errors:
                cenario, error = _resolve_reclassification_catalog_value(
                    value=cenario,
                    original=falha.motivo_falha,
                    queryset=AuditoriaMotivoFalha.objects.all(),
                    value_field="motivo",
                    label="Cenário",
                    max_length=AuditoriaFalhaCadastro._meta.get_field("motivo_falha").max_length,
                )
                if error:
                    errors["cenario"] = error
                nivel, error = _resolve_reclassification_catalog_value(
                    value=nivel,
                    original=falha.nivel_dificuldade,
                    queryset=AuditoriaCatalogItem.objects.filter(
                        catalog=AuditoriaCatalogItem.CATALOG_NIVEL_DIFICULDADE
                    ),
                    value_field="value",
                    label="Nível",
                    max_length=AuditoriaFalhaCadastro._meta.get_field(
                        "nivel_dificuldade"
                    ).max_length,
                )
                if error:
                    errors["nivel"] = error
                etapa, error = _resolve_reclassification_catalog_value(
                    value=etapa,
                    original=falha.etapa_falha,
                    queryset=AuditoriaCatalogItem.objects.filter(
                        catalog=AuditoriaCatalogItem.CATALOG_ETAPA_FALHA
                    ),
                    value_field="value",
                    label="Etapa",
                    max_length=AuditoriaFalhaCadastro._meta.get_field("etapa_falha").max_length,
                )
                if error:
                    errors["etapa"] = error
            if errors:
                raise ValidationError(errors)
        else:
            cenario = nivel = etapa = ""
        falha_status_novo = destino_falha
        falhas_manter_ids_saved: list[int] = []
    else:
        if destino_falha or cenario or nivel or etapa:
            raise ValidationError(
                {"destino_falha": "O destino manual da falha está disponível somente em Fraud."}
            )
        reclassificar = False
        siblings = siblings_for_falha(falha)
        sibling_ids = {sibling.pk for sibling in siblings}
        siblings_locked = list(
            AuditoriaFalhaCadastro.objects.select_for_update().filter(pk__in=sibling_ids)
        )
        if resultado == ContestacaoOperacional.STATUS_PROCEDENTE:
            keep_ids: set[int] = set()
        elif falhas_manter is None:
            keep_ids = sibling_ids
        else:
            keep_ids = set()
            invalid_ids: list[int] = []
            for raw_id in falhas_manter:
                try:
                    parsed_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if parsed_id in sibling_ids:
                    keep_ids.add(parsed_id)
                else:
                    invalid_ids.append(parsed_id)
            if invalid_ids:
                raise ValidationError(
                    {
                        "falhas_manter": (
                            "Uma ou mais irregularidades informadas não pertencem ao protocolo."
                        )
                    }
                )

        for sibling in siblings_locked:
            sibling.status_falha = (
                AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA
                if sibling.pk in keep_ids
                else AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA
            )
            sibling.save(update_fields=["status_falha", "updated_at"])

        falha.refresh_from_db(fields=["status_falha"])
        falha_status_novo = falha.status_falha or AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA
        falhas_manter_ids_saved = sorted(keep_ids)

    if reclassificar:
        cenario_original = (falha.motivo_falha or "").strip()
        nivel_original = (falha.nivel_dificuldade or "").strip()
        etapa_original = (falha.etapa_falha or "").strip()
    else:
        cenario_original = nivel_original = etapa_original = ""

    unchanged = (
        resultado == resultado_anterior
        and (item.dominio != ContestacaoOperacional.DOMINIO_FRAUD or destino_falha == destino_anterior)
        and falhas_manter_ids_saved == falhas_manter_anterior
        and cenario == cenario_decisao_anterior
        and nivel == nivel_decisao_anterior
        and etapa == etapa_decisao_anterior
    )
    if unchanged:
        raise ValidationError({"resultado": "Nenhuma alteração foi informada para revisão."})

    item.status = resultado
    item.destino_falha = (
        destino_falha if item.dominio == ContestacaoOperacional.DOMINIO_FRAUD else ""
    )
    item.cenario_original = cenario_original
    item.nivel_original = nivel_original
    item.etapa_original = etapa_original
    item.cenario_decisao = cenario
    item.nivel_decisao = nivel
    item.etapa_decisao = etapa
    item.falhas_manter_ids = falhas_manter_ids_saved
    item.parecer_revisao = parecer
    if item.analista_decisao_id is None and item.analista_id:
        item.analista_decisao_id = item.analista_id
    item.save(
        update_fields=[
            "status",
            "destino_falha",
            "cenario_original",
            "nivel_original",
            "etapa_original",
            "cenario_decisao",
            "nivel_decisao",
            "etapa_decisao",
            "falhas_manter_ids",
            "parecer_revisao",
            "analista_decisao",
            "updated_at",
        ]
    )

    falha.status_falha = falha_status_novo
    falha_update_fields = ["status_falha", "updated_at"]
    if reclassificar:
        falha.motivo_falha = cenario
        falha.nivel_dificuldade = nivel
        falha.etapa_falha = etapa
        falha_update_fields.extend(["motivo_falha", "nivel_dificuldade", "etapa_falha"])
    elif item.dominio == ContestacaoOperacional.DOMINIO_FRAUD and not reclassificar:
        if cenario_decisao_anterior or nivel_decisao_anterior or etapa_decisao_anterior:
            falha.motivo_falha = item.cenario_original or falha.motivo_falha
            falha.nivel_dificuldade = item.nivel_original or falha.nivel_dificuldade
            falha.etapa_falha = item.etapa_original or falha.etapa_falha
            falha_update_fields.extend(["motivo_falha", "nivel_dificuldade", "etapa_falha"])
    if item.dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        falha.save(update_fields=falha_update_fields)

    metadata = {
        "resultado_de": resultado_anterior,
        "resultado_para": resultado,
        "destino_falha_de": destino_anterior,
        "destino_falha_para": item.destino_falha,
        "falhas_manter_de": falhas_manter_anterior,
        "falhas_manter_para": falhas_manter_ids_saved,
        "cenario_decisao_de": cenario_decisao_anterior,
        "cenario_decisao_para": cenario,
        "nivel_decisao_de": nivel_decisao_anterior,
        "nivel_decisao_para": nivel,
        "etapa_decisao_de": etapa_decisao_anterior,
        "etapa_decisao_para": etapa,
        "analista_decisao_id": (
            str(item.analista_decisao_id) if item.analista_decisao_id else None
        ),
    }
    _append_historico(
        item,
        evento=ContestacaoOperacionalHistorico.EVENTO_RESULTADO_ALTERADO,
        actor=user,
        status_anterior=resultado_anterior,
        status_novo=resultado,
        falha_status_anterior=falha_status_anterior,
        falha_status_novo=falha_status_novo,
        parecer=parecer,
        metadata=metadata,
    )
    return item


def _assert_can_analyze(user, dominio: str) -> None:
    if dominio == ContestacaoOperacional.DOMINIO_FRAUD:
        if not user_has_permission(user, R.QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE):
            raise ValidationError({"detail": "Sem permissão para analisar contestações Fraud."})
    elif dominio == ContestacaoOperacional.DOMINIO_COMPLIANCE:
        if not user_has_permission(user, R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_ANALYZE):
            raise ValidationError({"detail": "Sem permissão para analisar contestações Compliance."})
    else:
        raise ValidationError({"dominio": "Domínio inválido."})
