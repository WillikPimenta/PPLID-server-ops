from __future__ import annotations

import random
import unicodedata
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, F, Max, Min, Q, QuerySet
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaCompliance,
    AuditoriaComplianceAuditorPresence,
    AuditoriaComplianceFilaHistorico,
)
from apps.auditoria.services.fila_presence_mutex import (
    FILA_CONTEXTO_AUDITORIA_COMPLIANCE,
    assert_can_go_online,
    lock_presence_user,
)
from apps.auditoria.services.qualidade_promocao import promover_pendente_auditoria_compliance
from apps.auditoria.services.suporte_operacional_link import resolve_and_link, suporte_operacional_payload_for

User = get_user_model()

FILA_CONTEXTO_REINSPECAO = "reinspecao"
FILA_CONTEXTO_AUDITORIA_COMPLIANCE = "auditoria_compliance"

ANALISE_ATIVA = (
    QualidadePendenteAuditoriaCompliance.ANALISE_AGUARDANDO,
    QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE,
)

# Capacidade da fila pessoal: posição 1 (análise) + posição 2 (direcionado).
MAX_ACTIVE_PER_AUDITOR = 2
# Auto-claim / distribuição automática só preenche a 1ª posição quando a fila está vazia.
MAX_AUTO_ACTIVE_PER_AUDITOR = 1
# Tempo máximo do protocolo na fila pessoal antes de voltar à fila geral.
FILA_TIMEOUT_SECONDS = 5 * 60

MODULO_CONTESTACAO = "Contestação"

ANALISE_LABELS = {
    **dict(QualidadePendenteAuditoriaCompliance.ANALISE_STATUS_CHOICES),
    QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO: "Concluído",
    QualidadePendenteAuditoriaCompliance.ANALISE_NAO_ATRIBUIDO: "Não atribuído",
}
PRESENCE_LABELS = dict(AuditoriaComplianceAuditorPresence.STATUS_CHOICES)


def fila_contexto_atual() -> str:
    return FILA_CONTEXTO_AUDITORIA_COMPLIANCE


def _data_origem_field() -> str:
    if fila_contexto_atual() == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        return "data_analise"
    return "data_contestacao"


def _data_origem(falha):
    return getattr(falha, _data_origem_field(), None)


def _modulo_conclusao() -> str:
    if fila_contexto_atual() == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        return "Auditoria"
    return MODULO_CONTESTACAO


def _tipo_falha_padrao() -> str:
    if fila_contexto_atual() == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        return "auditoria"
    return "reinspecao"


def _origem_tratado() -> str:
    if fila_contexto_atual() == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        return AuditoriaFalhaCadastro.ORIGEM_AUDITORIA
    return AuditoriaFalhaCadastro.ORIGEM_REINSPECAO


def user_display_name(user) -> str:
    if not user:
        return ""
    full = (user.get_full_name() or "").strip()
    return full or user.username


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _write_historico(
    *,
    tipo: str,
    actor,
    falha: AuditoriaFalhaCadastro | None = None,
    pendente: QualidadePendenteAuditoriaCompliance | None = None,
    auditor=None,
    justificativa: str = "",
    detalhe: dict | None = None,
) -> None:
    AuditoriaComplianceFilaHistorico.objects.create(
        tipo=tipo,
        actor=actor,
        falha=falha,
        pendente=pendente,
        auditor=auditor,
        justificativa=(justificativa or "").strip(),
        detalhe=_json_safe(detalhe or {}),
    )


def get_or_create_presence(user) -> AuditoriaComplianceAuditorPresence:
    presence, _ = AuditoriaComplianceAuditorPresence.objects.get_or_create(
        user=user,
    )
    return presence


def _presence_qs() -> QuerySet[AuditoriaComplianceAuditorPresence]:
    return AuditoriaComplianceAuditorPresence.objects.all()


def reinspecao_falhas_qs() -> QuerySet[QualidadePendenteAuditoriaCompliance]:
    """Fila/consulta em andamento: protocolos pendentes (P…)."""
    return QualidadePendenteAuditoriaCompliance.objects.all()


def reinspecao_tratados_qs() -> QuerySet[AuditoriaFalhaCadastro]:
    """Consulta finalizados: tabela única de tratados com origem reinspeção."""
    from apps.auditoria.services.qualidade_promocao import tratados_qs

    contexto = fila_contexto_atual()
    qs = tratados_qs(origem=_origem_tratado())
    if contexto == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        return qs.filter(
            Q(brflow_parsed__fila_contexto=contexto)
            | Q(analise_origem__contexto__fila_contexto=contexto)
        )
    return qs


def build_fila_kpis() -> dict[str, int]:
    """Distribui protocolos pendentes distintos pela data de inserção na fila."""
    today = timezone.localdate()
    grouped = (
        reinspecao_falhas_qs()
        .values("protocolo")
        .annotate(inserido_em=Min("created_at"))
        .values_list("inserido_em", flat=True)
    )
    counts = {"d": 0, "d1": 0, "d2": 0, "dmais": 0}
    for inserted_at in grouped.iterator(chunk_size=500):
        if not inserted_at:
            continue
        inserted_date = timezone.localtime(inserted_at).date()
        age = (today - inserted_date).days
        if age == 0:
            counts["d"] += 1
        elif age == 1:
            counts["d1"] += 1
        elif age == 2:
            counts["d2"] += 1
        elif age >= 3:
            counts["dmais"] += 1

    return {"total": sum(counts.values()), **counts}


def serialize_pendente_reinspecao(pendente: QualidadePendenteAuditoriaCompliance, *, agente_nome: str | None = None) -> dict:
    origem = (pendente.fila_origem or "").strip() or QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_AUTO
    analise_status = pendente.analise_status or QualidadePendenteAuditoriaCompliance.ANALISE_NAO_ATRIBUIDO
    return {
        "id": pendente.id,
        "contexto": pendente.contexto,
        "protocolo": pendente.protocolo,
        "data_contestacao": pendente.data_contestacao.isoformat() if pendente.data_contestacao else None,
        "data_analise": pendente.data_analise.isoformat() if pendente.data_analise else None,
        "descricao_irregularidades": pendente.descricao_irregularidades,
        "analise_status": analise_status,
        "analise_status_label": ANALISE_LABELS.get(analise_status, "Não atribuído"),
        "fila_origem": origem,
        "fila_origem_label": (
            "Direcionado"
            if origem == QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_DIRECIONADO
            else "Automático"
        ),
        "usuario": pendente.usuario,
        "agente": agente_nome or pendente.usuario,
        "cliente": pendente.cliente,
        "status": pendente.status,
        "observacao": pendente.observacao,
        "auditor": pendente.auditor,
        "responsavel_id": str(pendente.responsavel_id) if pendente.responsavel_id else None,
        "atribuido_em": pendente.atribuido_em.isoformat() if pendente.atribuido_em else None,
        "fase": "pendente",
        "suporte_operacional": suporte_operacional_payload_for(pendente),
    }


def ativos_por_auditor() -> dict:
    """Carga ativa por auditor: quantidade de protocolos distintos (não linhas de falha)."""
    rows = (
        reinspecao_falhas_qs()
        .filter(analise_status__in=ANALISE_ATIVA, responsavel_id__isnull=False)
        .values("responsavel_id")
        .annotate(total=Count("protocolo", distinct=True))
    )
    return {row["responsavel_id"]: int(row["total"]) for row in rows}


def _count_protocolos_nao_atribuido() -> int:
    return (
        reinspecao_falhas_qs()
        .filter(
            responsavel__isnull=True,
            analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_NAO_ATRIBUIDO,
        )
        .values("protocolo")
        .distinct()
        .count()
    )


def _diversify_seeds_by_agent(
    bucket: list[AuditoriaFalhaCadastro],
) -> list[AuditoriaFalhaCadastro]:
    """Embaralha protocolos do mesmo balde de data, intercalando agentes distintos."""
    if len(bucket) <= 1:
        return list(bucket)

    by_agent: dict[str, list[AuditoriaFalhaCadastro]] = {}
    for falha in bucket:
        key = (falha.usuario or "").strip().casefold() or "__vazio__"
        by_agent.setdefault(key, []).append(falha)
    for items in by_agent.values():
        random.shuffle(items)

    ordered: list[AuditoriaFalhaCadastro] = []
    agent_keys = list(by_agent.keys())
    random.shuffle(agent_keys)
    while agent_keys:
        random.shuffle(agent_keys)
        remaining: list[str] = []
        for key in agent_keys:
            items = by_agent[key]
            if not items:
                continue
            ordered.append(items.pop())
            if items:
                remaining.append(key)
        agent_keys = remaining
    return ordered


def _unassigned_protocolo_seeds(
    *,
    limit: int,
    exclude_protocolos: set[str] | None = None,
) -> list[AuditoriaFalhaCadastro]:
    """
    Uma falha-semente por protocolo ainda não atribuído.

    Preferência pela data de contestação/análise mais antiga; dentro da mesma
    data, sorteia e intercala agentes (evita o mesmo agente cair sempre em sequência).
    Datas nulas ficam por último.
    """
    if limit <= 0:
        return []
    exclude = {p.strip() for p in (exclude_protocolos or set()) if (p or "").strip()}
    qs = (
        reinspecao_falhas_qs()
        .filter(
            responsavel__isnull=True,
            analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_NAO_ATRIBUIDO,
        )
        .order_by(F(_data_origem_field()).asc(nulls_last=True), "id")
    )

    seeds: list[AuditoriaFalhaCadastro] = []
    seen: set[str] = set()
    current_date = object()
    bucket: list[AuditoriaFalhaCadastro] = []

    def flush_bucket() -> bool:
        """Diversifica o balde da data atual e acrescenta às sementes. True se já encheu."""
        nonlocal bucket
        if not bucket:
            return False
        for falha in _diversify_seeds_by_agent(bucket):
            seeds.append(falha)
            if len(seeds) >= limit:
                bucket = []
                return True
        bucket = []
        return False

    for falha in qs.iterator(chunk_size=200):
        protocolo = (falha.protocolo or "").strip()
        if not protocolo or protocolo in seen or protocolo in exclude:
            continue
        seen.add(protocolo)
        date_key = _data_origem(falha)
        if date_key != current_date:
            if flush_bucket():
                return seeds
            current_date = date_key
            bucket = [falha]
        else:
            bucket.append(falha)

    flush_bucket()
    return seeds


def _timeout_excluded_protocolos_for_auditor(user) -> set[str]:
    """
    Protocolos liberados por timeout para este auditor recentemente.
    Evita reatribuir o mesmo protocolo na sequência imediata do expire.
    """
    if not user or not getattr(user, "id", None):
        return set()
    cutoff = timezone.now() - timedelta(seconds=FILA_TIMEOUT_SECONDS)
    rows = (
        AuditoriaComplianceFilaHistorico.objects.filter(
            tipo=AuditoriaComplianceFilaHistorico.TIPO_LIBERACAO_TIMEOUT,
            auditor_id=user.id,
            created_at__gte=cutoff,
        )
        .order_by("-created_at")
        .values_list("detalhe", flat=True)[:100]
    )
    excluded: set[str] = set()
    for detalhe in rows:
        if not isinstance(detalhe, dict):
            continue
        protocolo = str(detalhe.get("protocolo") or "").strip()
        if protocolo:
            excluded.add(protocolo)
    return excluded


def _pick_auditor(online: list[AuditoriaComplianceAuditorPresence], loads: dict):
    def sort_key(presence: AuditoriaComplianceAuditorPresence):
        load = loads.get(presence.user_id, 0)
        last = presence.last_assigned_at or datetime.min.replace(tzinfo=dt_timezone.utc)
        return (load, last, presence.user_id)

    return min(online, key=sort_key)


def _assign_falha(
    falha: AuditoriaFalhaCadastro,
    *,
    auditor,
    actor,
    tipo_historico: str,
    justificativa: str = "",
    detalhe: dict | None = None,
    touch_presence: bool = True,
    fila_origem: str = "",
    start_sla: bool = True,
    now=None,
) -> AuditoriaFalhaCadastro:
    now = now or timezone.now()
    origem = (fila_origem or "").strip()
    if not origem:
        if tipo_historico in {
            AuditoriaComplianceFilaHistorico.TIPO_DIRECIONAMENTO,
            AuditoriaComplianceFilaHistorico.TIPO_REATRIBUICAO,
        }:
            origem = QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_DIRECIONADO
        else:
            origem = QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_AUTO
    falha.responsavel = auditor
    falha.analise_status = QualidadePendenteAuditoriaCompliance.ANALISE_AGUARDANDO
    # A segunda posição já pertence ao auditor, mas seu SLA permanece pausado
    # até que ela seja promovida para a primeira posição.
    falha.atribuido_em = now if start_sla else None
    falha.analise_iniciada_em = None
    falha.analise_concluida_em = None
    falha.fila_origem = origem
    falha.auditor = user_display_name(auditor)
    falha.save(
        update_fields=[
            "responsavel",
            "analise_status",
            "atribuido_em",
            "analise_iniciada_em",
            "analise_concluida_em",
            "fila_origem",
            "auditor",
            "updated_at",
        ]
    )
    if touch_presence:
        presence = get_or_create_presence(auditor)
        presence.last_assigned_at = now
        presence.save(update_fields=["last_assigned_at", "updated_at"])
    _write_historico(
        tipo=tipo_historico,
        actor=actor,
        pendente=falha,
        auditor=auditor,
        justificativa=justificativa,
        detalhe={
            "protocolo": falha.protocolo,
            "auditor_id": auditor.id,
            "fila_origem": origem,
            "sla_ativo": start_sla,
            **(detalhe or {}),
        },
    )
    return falha


def _assign_protocolo_group(
    seed: AuditoriaFalhaCadastro,
    *,
    auditor,
    actor,
    tipo_historico: str,
    justificativa: str = "",
    detalhe: dict | None = None,
    fila_origem: str = "",
    start_sla: bool = True,
) -> AuditoriaFalhaCadastro:
    """Atribui todas as irregularidades ativas do mesmo protocolo ao auditor."""
    now = timezone.now()
    protocolo = (seed.protocolo or "").strip()
    siblings = list(
        reinspecao_falhas_qs()
        .filter(protocolo=protocolo)
        .exclude(analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO)
        .select_for_update(skip_locked=True)
        .order_by("id")
    )
    if not siblings:
        # Fallback: ao menos a semente (pode ter sido skip_locked em corrida).
        locked_seed = (
            reinspecao_falhas_qs()
            .filter(pk=seed.pk)
            .exclude(analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO)
            .select_for_update()
            .first()
        )
        siblings = [locked_seed] if locked_seed else []
    if not siblings:
        raise ValueError("Protocolo não disponível para atribuição.")

    primary = None
    for index, falha in enumerate(siblings):
        assigned = _assign_falha(
            falha,
            auditor=auditor,
            actor=actor,
            tipo_historico=tipo_historico,
            justificativa=justificativa if index == 0 else "",
            detalhe={**(detalhe or {}), "grupo_protocolo": True, "irregularidades": len(siblings)},
            touch_presence=index == 0,
            fila_origem=fila_origem,
            start_sla=start_sla,
            now=now,
        )
        if primary is None:
            primary = assigned
    return primary or seed


def _release_falha(
    falha: AuditoriaFalhaCadastro,
    *,
    actor,
    tipo_historico: str,
    motivo: str,
) -> None:
    previous = falha.responsavel
    falha.responsavel = None
    falha.analise_status = QualidadePendenteAuditoriaCompliance.ANALISE_NAO_ATRIBUIDO
    falha.atribuido_em = None
    falha.analise_iniciada_em = None
    falha.fila_origem = ""
    falha.auditor = ""
    falha.save(
        update_fields=[
            "responsavel",
            "analise_status",
            "atribuido_em",
            "analise_iniciada_em",
            "fila_origem",
            "auditor",
            "updated_at",
        ]
    )
    _write_historico(
        tipo=tipo_historico,
        actor=actor,
        pendente=falha,
        auditor=previous,
        detalhe={"protocolo": falha.protocolo, "motivo": motivo},
    )


def release_auditor_active_queue(*, auditor, actor, motivo: str) -> int:
    """Devolve protocolos ativos do auditor para a fila geral."""
    released = 0
    falhas = list(
        reinspecao_falhas_qs()
        .filter(responsavel=auditor, analise_status__in=ANALISE_ATIVA)
        .select_for_update()
        .order_by("id")
    )
    seen: set[str] = set()
    for falha in falhas:
        protocolo = (falha.protocolo or "").strip() or f"id:{falha.id}"
        if protocolo not in seen:
            seen.add(protocolo)
            released += 1
        _release_falha(
            falha,
            actor=actor,
            tipo_historico=AuditoriaComplianceFilaHistorico.TIPO_LIBERACAO_STATUS,
            motivo=motivo,
        )
    return released


@transaction.atomic
def remover_protocolo_direcionado(*, falha_id: int, user) -> dict[str, Any]:
    """Devolve à fila geral o protocolo direcionado que ocupa a posição 2."""
    falha = (
        reinspecao_falhas_qs()
        .select_for_update()
        .filter(pk=falha_id, responsavel=user, analise_status__in=ANALISE_ATIVA)
        .first()
    )
    if not falha:
        raise ValueError("Protocolo direcionado não encontrado na sua fila.")

    ativos = list(
        reinspecao_falhas_qs()
        .filter(responsavel=user, analise_status__in=ANALISE_ATIVA)
        .select_for_update()
        .order_by(F("atribuido_em").asc(nulls_last=True), "id")
    )
    slot_groups = _group_protocolo_slots(ativos)
    if len(slot_groups) < 2:
        raise ValueError("Não há protocolo direcionado na posição 2.")

    waiting_group = slot_groups[1]
    waiting_ids = {item.id for item in waiting_group}
    origem = (waiting_group[0].fila_origem or "").strip()
    if falha.id not in waiting_ids or origem != QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_DIRECIONADO:
        raise ValueError("Somente o protocolo direcionado da posição 2 pode ser removido.")

    protocolo = (waiting_group[0].protocolo or "").strip()
    for item in waiting_group:
        _release_falha(
            item,
            actor=user,
            tipo_historico=AuditoriaComplianceFilaHistorico.TIPO_LIBERACAO_STATUS,
            motivo="remocao_manual_direcionado",
        )
    return {"removed": True, "protocolo": protocolo}


@transaction.atomic
def remover_protocolo_fila_controle(
    *,
    auditor_id,
    falha_id: int | None = None,
    protocolo: str = "",
    actor,
) -> dict[str, Any]:
    """Devolve à fila geral um protocolo ativo (posição 1 ou 2) da fila do auditor."""
    auditor = User.objects.filter(pk=auditor_id, is_active=True).first()
    if not auditor:
        raise ValueError("Auditor inválido.")

    ativos = list(
        reinspecao_falhas_qs()
        .filter(responsavel=auditor, analise_status__in=ANALISE_ATIVA)
        .select_for_update()
        .order_by(F("atribuido_em").asc(nulls_last=True), "id")
    )
    if not ativos:
        raise ValueError("A fila do auditor está vazia.")

    slot_groups = _group_protocolo_slots(ativos)
    target_group: list[AuditoriaFalhaCadastro] | None = None
    protocolo_alvo = (protocolo or "").strip()
    if falha_id:
        for group in slot_groups:
            if any(item.id == falha_id for item in group):
                target_group = group
                break
    elif protocolo_alvo:
        for group in slot_groups:
            if (group[0].protocolo or "").strip() == protocolo_alvo:
                target_group = group
                break
    else:
        raise ValueError("Informe o protocolo a remover.")

    if not target_group:
        raise ValueError("Protocolo não encontrado na fila do auditor.")

    protocolo_liberado = (target_group[0].protocolo or "").strip()
    for item in target_group:
        _release_falha(
            item,
            actor=actor,
            tipo_historico=AuditoriaComplianceFilaHistorico.TIPO_LIBERACAO_STATUS,
            motivo="remocao_manual_controle",
        )
    _activate_first_slot_sla(auditor_id=auditor.id)
    return {"removed": True, "protocolo": protocolo_liberado}


@transaction.atomic
def expire_stale_fila_assignments(*, actor=None) -> int:
    """Devolve à fila geral somente a posição 1 quando o SLA expira."""
    cutoff = timezone.now() - timedelta(seconds=FILA_TIMEOUT_SECONDS)
    stale_auditor_ids = list(
        reinspecao_falhas_qs()
        .filter(
            responsavel__isnull=False,
            analise_status__in=ANALISE_ATIVA,
            atribuido_em__lt=cutoff,
        )
        .order_by()
        .values_list("responsavel_id", flat=True)
        .distinct()[:500]
    )
    if not stale_auditor_ids:
        return 0

    ativos = list(
        reinspecao_falhas_qs()
        .filter(
            responsavel_id__in=stale_auditor_ids,
            analise_status__in=ANALISE_ATIVA,
        )
        .order_by("responsavel_id", F("atribuido_em").asc(nulls_last=True), "id")
    )
    by_auditor: dict[Any, list[AuditoriaFalhaCadastro]] = {}
    for falha in ativos:
        by_auditor.setdefault(falha.responsavel_id, []).append(falha)

    release_ids: set[int] = set()
    for auditor_items in by_auditor.values():
        slot_groups = _group_protocolo_slots(auditor_items)
        if not slot_groups:
            continue
        current_group = slot_groups[0]
        started_at = min(
            (item.atribuido_em for item in current_group if item.atribuido_em),
            default=None,
        )
        if started_at and started_at < cutoff:
            release_ids.update(item.id for item in current_group)

    if not release_ids:
        return 0

    falhas = list(
        reinspecao_falhas_qs()
        .filter(id__in=release_ids)
        .select_for_update(skip_locked=True)
        .order_by("id")
    )
    released_protocolos = 0
    seen: set[str] = set()
    affected_auditor_ids: set[Any] = set()
    for falha in falhas:
        if falha.analise_status not in ANALISE_ATIVA:
            continue
        protocolo = (falha.protocolo or "").strip() or f"id:{falha.id}"
        if falha.responsavel_id:
            affected_auditor_ids.add(falha.responsavel_id)
        _release_falha(
            falha,
            actor=actor,
            tipo_historico=AuditoriaComplianceFilaHistorico.TIPO_LIBERACAO_TIMEOUT,
            motivo="timeout_fila",
        )
        if protocolo not in seen:
            seen.add(protocolo)
            released_protocolos += 1
    promoted_at = timezone.now()
    for auditor_id in affected_auditor_ids:
        _activate_first_slot_sla(auditor_id=auditor_id, now=promoted_at)
        auditor = User.objects.filter(pk=auditor_id, is_active=True).first()
        if not auditor:
            continue
        presence = _presence_qs().filter(user_id=auditor_id).first()
        if presence and presence.status == AuditoriaComplianceAuditorPresence.STATUS_ONLINE:
            set_auditor_status(
                user=auditor,
                status=AuditoriaComplianceAuditorPresence.STATUS_OFFLINE,
                actor=actor,
            )
    return released_protocolos


@transaction.atomic
def set_auditor_status(*, user, status: str, actor=None) -> AuditoriaComplianceAuditorPresence:
    status = (status or "").strip().lower()
    valid = {c[0] for c in AuditoriaComplianceAuditorPresence.STATUS_CHOICES}
    if status not in valid:
        raise ValueError("Status inválido. Use online, offline ou ausente.")

    actor = actor or user
    lock_presence_user(user=user)
    presence = (
        _presence_qs().select_for_update()
        .filter(user=user)
        .first()
    )
    if not presence:
        presence = AuditoriaComplianceAuditorPresence(
            user=user,
        )

    previous = presence.status if presence.pk else AuditoriaComplianceAuditorPresence.STATUS_OFFLINE
    if (
        status == AuditoriaComplianceAuditorPresence.STATUS_ONLINE
        and previous != AuditoriaComplianceAuditorPresence.STATUS_ONLINE
    ):
        assert_can_go_online(user=user, target_context=FILA_CONTEXTO_AUDITORIA_COMPLIANCE)
    now = timezone.now()
    presence.status = status
    presence.status_changed_at = now
    presence.save()

    _write_historico(
        tipo=AuditoriaComplianceFilaHistorico.TIPO_STATUS_AUDITOR,
        actor=actor,
        auditor=user,
        detalhe={"from": previous, "to": status},
    )

    if status in {
        AuditoriaComplianceAuditorPresence.STATUS_OFFLINE,
        AuditoriaComplianceAuditorPresence.STATUS_AUSENTE,
    }:
        release_auditor_active_queue(
            auditor=user,
            actor=actor,
            motivo=f"status_{status}",
        )

    return presence


def set_auditor_status_and_maybe_distribute(*, user, status: str, actor=None) -> dict:
    """Altera status; se passar a Online, atribui o próximo protocolo da fila geral."""
    actor = actor or user
    presence = set_auditor_status(user=user, status=status, actor=actor)
    distribution = None
    if presence.status == AuditoriaComplianceAuditorPresence.STATUS_ONLINE:
        claimed = claim_next_for_auditor(user=user)
        distribution = {
            "assigned": 1 if claimed else 0,
            "online_auditors": 1,
            "remaining": _count_protocolos_nao_atribuido(),
            "protocolo": claimed.protocolo if claimed else None,
        }
    return {
        "presence": serialize_presence(presence),
        "distribution": distribution,
    }


@transaction.atomic
def distribuir_protocolos(*, actor=None, limit: int | None = None) -> dict[str, Any]:
    """Distribui protocolos não atribuídos (preenche só a 1ª posição de auditores Online vazios)."""
    expire_stale_fila_assignments(actor=actor)
    online = list(
        _presence_qs().select_for_update()
        .filter(status=AuditoriaComplianceAuditorPresence.STATUS_ONLINE)
        .select_related("user")
        .order_by("id")
    )
    if not online:
        return {"assigned": 0, "online_auditors": 0, "remaining": 0}

    loads = ativos_por_auditor()
    eligible = [p for p in online if loads.get(p.user_id, 0) < MAX_AUTO_ACTIVE_PER_AUDITOR]
    if not eligible:
        return {
            "assigned": 0,
            "online_auditors": len(online),
            "remaining": _count_protocolos_nao_atribuido(),
        }

    max_assign = len(eligible) if limit is None else min(len(eligible), max(0, int(limit)))
    exclude_by_auditor: dict = {
        presence.user_id: _timeout_excluded_protocolos_for_auditor(presence.user)
        for presence in eligible
    }
    # Busca sementes extras para pular protocolos bloqueados por timeout recente.
    seeds = _unassigned_protocolo_seeds(limit=max(max_assign * 3, max_assign + 5))
    assigned = 0

    for seed in seeds:
        eligible = [p for p in online if loads.get(p.user_id, 0) < MAX_AUTO_ACTIVE_PER_AUDITOR]
        if not eligible:
            break
        protocolo = (seed.protocolo or "").strip()
        pool = [
            p
            for p in eligible
            if not protocolo or protocolo not in exclude_by_auditor.get(p.user_id, set())
        ]
        if not pool:
            continue
        presence = _pick_auditor(pool, loads)
        auditor = presence.user
        _assign_protocolo_group(
            seed,
            auditor=auditor,
            actor=actor,
            tipo_historico=AuditoriaComplianceFilaHistorico.TIPO_ATRIBUICAO_AUTO,
            fila_origem=QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_AUTO,
        )
        loads[auditor.id] = loads.get(auditor.id, 0) + 1
        assigned += 1
        if assigned >= max_assign:
            break

    return {
        "assigned": assigned,
        "online_auditors": len(online),
        "remaining": _count_protocolos_nao_atribuido(),
    }


@transaction.atomic
def claim_next_for_auditor(*, user) -> AuditoriaFalhaCadastro | None:
    """Se o auditor está Online e com a 1ª posição livre, atribui um protocolo da fila geral.

    Prefere a data mais antiga e, entre protocolos da mesma data, escolhe aleatoriamente.
    """
    expire_stale_fila_assignments(actor=user)
    presence = (
        _presence_qs().select_for_update()
        .filter(user=user)
        .first()
    )
    if not presence or presence.status != AuditoriaComplianceAuditorPresence.STATUS_ONLINE:
        return None
    if ativos_por_auditor().get(user.id, 0) >= MAX_AUTO_ACTIVE_PER_AUDITOR:
        return None

    seeds = _unassigned_protocolo_seeds(
        limit=1,
        exclude_protocolos=_timeout_excluded_protocolos_for_auditor(user),
    )
    if not seeds:
        return None
    try:
        return _assign_protocolo_group(
            seeds[0],
            auditor=user,
            actor=user,
            tipo_historico=AuditoriaComplianceFilaHistorico.TIPO_ATRIBUICAO_AUTO,
            detalhe={"claim": True},
            fila_origem=QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_AUTO,
        )
    except ValueError:
        return None


@transaction.atomic
def direcionar_protocolo(
    *,
    falha_id: int | None = None,
    protocolo: str = "",
    auditor_id,
    actor,
    justificativa: str,
    reatribuicao: bool = False,
) -> AuditoriaFalhaCadastro:
    expire_stale_fila_assignments(actor=actor)
    justificativa = (justificativa or "").strip()
    if not justificativa:
        raise ValueError("Informe a justificativa do direcionamento.")

    auditor = User.objects.filter(pk=auditor_id, is_active=True).first()
    if not auditor:
        raise ValueError("Auditor de destino inválido.")

    qs = reinspecao_falhas_qs().select_for_update()
    falha = None
    if falha_id:
        falha = qs.filter(pk=falha_id).first()
    else:
        protocolo = (protocolo or "").strip()
        if protocolo:
            falha = (
                qs.filter(protocolo=protocolo)
                .exclude(analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO)
                .order_by(F(_data_origem_field()).asc(nulls_last=True), "id")
                .first()
            )
        else:
            # Sem protocolo informado: atribui o próximo da fila geral.
            seeds = _unassigned_protocolo_seeds(limit=1)
            falha = seeds[0] if seeds else None
            if falha:
                falha = qs.filter(pk=falha.pk).first()

    if not falha:
        raise ValueError(
            "Protocolo não encontrado."
            if (falha_id or (protocolo or "").strip())
            else "Não há protocolos pendentes na fila geral."
        )

    if falha.analise_status == QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO:
        raise ValueError("Protocolo concluído não pode ser reatribuído.")

    previous_id = falha.responsavel_id
    if previous_id and previous_id != auditor.id and not reatribuicao:
        reatribuicao = True

    # Até 2 posições: 1ª em análise / 2ª direcionado.
    start_sla = True
    if falha.responsavel_id != auditor.id:
        outros = (
            reinspecao_falhas_qs()
            .filter(responsavel=auditor, analise_status__in=ANALISE_ATIVA)
            .exclude(protocolo=falha.protocolo)
            .values("protocolo")
            .distinct()
            .count()
        )
        if outros >= MAX_ACTIVE_PER_AUDITOR:
            raise ValueError(
                "Auditor já possui 2 protocolos ativos na fila (análise + direcionado)."
            )
        start_sla = outros == 0

    tipo = (
        AuditoriaComplianceFilaHistorico.TIPO_REATRIBUICAO
        if reatribuicao or previous_id
        else AuditoriaComplianceFilaHistorico.TIPO_DIRECIONAMENTO
    )
    return _assign_protocolo_group(
        falha,
        auditor=auditor,
        actor=actor,
        tipo_historico=tipo,
        justificativa=justificativa,
        detalhe={"previous_auditor_id": previous_id, "proximo_fila": not bool(protocolo or falha_id)},
        fila_origem=QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_DIRECIONADO,
        start_sla=start_sla,
    )


@transaction.atomic
def iniciar_analise(*, falha_id: int, user, allow_any: bool = False) -> AuditoriaFalhaCadastro:
    falha = reinspecao_falhas_qs().select_for_update().filter(pk=falha_id).first()
    if not falha:
        raise ValueError("Protocolo não encontrado.")
    if falha.responsavel_id != user.id and not allow_any:
        raise PermissionError("Protocolo atribuído a outro auditor.")
    if falha.analise_status not in {
        QualidadePendenteAuditoriaCompliance.ANALISE_AGUARDANDO,
        QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE,
    }:
        raise ValueError("Protocolo não está disponível para análise.")

    now = timezone.now()
    restarted = falha.analise_status == QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE
    if falha.analise_status == QualidadePendenteAuditoriaCompliance.ANALISE_AGUARDANDO:
        falha.analise_status = QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE
        falha.analise_iniciada_em = now
    update_fields = ["atribuido_em", "updated_at"]
    if not restarted:
        update_fields.extend(["analise_status", "analise_iniciada_em"])
    falha.atribuido_em = now
    falha.save(update_fields=update_fields)
    resolve_and_link(falha)
    _write_historico(
        tipo=AuditoriaComplianceFilaHistorico.TIPO_INICIO_ANALISE,
        actor=user,
        pendente=falha,
        auditor=falha.responsavel,
        detalhe={
            "protocolo": falha.protocolo,
            "timeout_reiniciado": restarted,
        },
    )
    return falha


STATUS_FORA_DO_PRAZO = "Fora do prazo"


def _normalize_status_conformidade(value: str) -> str:
    text = (value or "").strip()
    folded = unicodedata.normalize("NFKD", text)
    key = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    if key in {"improcedente", "conforme"}:
        return "Improcedente"
    if key in {"procedente", "nao conforme"}:
        return "Procedente"
    if key in {"fora do prazo", "fora_do_prazo"}:
        return STATUS_FORA_DO_PRAZO
    return ""


def _situacao_code_from_status(status: str) -> str:
    key = _normalize_status_conformidade(status)
    if key == "Improcedente":
        return "improcedente"
    if key == "Procedente":
        return "procedente"
    if key == STATUS_FORA_DO_PRAZO:
        return "fora_do_prazo"
    return ""


def _is_fora_do_prazo_status(status: str) -> bool:
    return _normalize_status_conformidade(status) == STATUS_FORA_DO_PRAZO


def _apply_etapa_fields(falha: AuditoriaFalhaCadastro, etapa: dict) -> str:
    """Aplica campos da análise na falha. Retorna status normalizado."""
    situacao_raw = str(etapa.get("situacao") or "")
    status_norm = _normalize_status_conformidade(situacao_raw)
    if not status_norm:
        raise ValueError("Informe a situação: Improcedente ou Procedente.")

    agente = str(etapa.get("agente") or "").strip()
    if not agente:
        raise ValueError("Informe a matrícula do agente em todas as irregularidades.")
    falha.usuario = agente

    tipo_falha = str(etapa.get("tipo_falha") or "").strip()
    if tipo_falha:
        falha.tipo_falha = tipo_falha
    falha.etapa_falha = str(etapa.get("etapa_falha") or "").strip()
    falha.motivo_falha = str(etapa.get("motivo_falha") or "").strip()
    falha.nivel_dificuldade = str(etapa.get("nivel_dificuldade") or "").strip()
    falha.tipo_documento = str(etapa.get("tipo_documento") or "").strip()
    falha.uf_documento = str(etapa.get("uf_documento") or "").strip()
    falha.novo_resultado = str(etapa.get("resultado_correto") or "").strip()
    falha.status = status_norm
    row_obs = str(etapa.get("observacao") or "").strip()
    if row_obs:
        falha.observacao = row_obs

    parsed = dict(falha.brflow_parsed or {}) if isinstance(falha.brflow_parsed, dict) else {}
    parsed["tempo_analise"] = str(etapa.get("tempo_analise") or "").strip()
    data_hora_analise = str(etapa.get("data_hora_analise") or "").strip()
    if data_hora_analise:
        parsed["data_hora_analise"] = data_hora_analise
    parsed["cruzamento_bases"] = str(etapa.get("cruzamento_bases") or "").strip()
    parsed["qualidade_imagem"] = str(etapa.get("qualidade_imagem") or "").strip()
    parsed["situacao"] = _situacao_code_from_status(status_norm)
    parsed["fila_contexto"] = fila_contexto_atual()
    falha.brflow_parsed = parsed
    return status_norm


@transaction.atomic
def concluir_analise(
    *,
    falha_id: int,
    user,
    allow_any: bool = False,
    cliente: str = "",
    status_texto: str = "",
    observacao: str = "",
    matricula_agente: str = "",
    matricula_auditor: str = "",
    etapas: list | None = None,
) -> AuditoriaFalhaCadastro:
    falha = reinspecao_falhas_qs().select_for_update().filter(pk=falha_id).first()
    if not falha:
        raise ValueError("Protocolo não encontrado.")
    if falha.responsavel_id != user.id and not allow_any:
        raise PermissionError("Protocolo atribuído a outro auditor.")
    if falha.analise_status not in ANALISE_ATIVA:
        raise ValueError("Protocolo não está em análise.")

    auditor_mat = (matricula_auditor or "").strip() or (getattr(user, "username", "") or "").strip()
    if not auditor_mat:
        raise ValueError("Matrícula do auditor indisponível.")

    siblings = list(
        reinspecao_falhas_qs()
        .select_for_update()
        .filter(
            protocolo=falha.protocolo,
            responsavel_id=falha.responsavel_id,
            analise_status__in=ANALISE_ATIVA,
        )
        .order_by("id")
    )
    if not siblings:
        siblings = [falha]

    now = timezone.now()
    etapas_list = [e for e in (etapas or []) if isinstance(e, dict)]
    observacao_txt = (observacao or "").strip()

    if etapas_list:
        primary_status = ""
        for index, etapa in enumerate(etapas_list):
            target = siblings[index] if index < len(siblings) else None
            if target is None:
                if fila_contexto_atual() == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
                    irreg = str(
                        etapa.get("descricao") or etapa.get("descricao_irregularidades") or ""
                    ).strip()
                    tipo = str(
                        etapa.get("tipo_conferencia")
                        or falha.descricao_irregularidades
                        or ""
                    ).strip()
                    if irreg and not str(etapa.get("motivo_falha") or "").strip():
                        etapa = {**etapa, "motivo_falha": irreg}
                    target = QualidadePendenteAuditoriaCompliance(
                        protocolo=falha.protocolo,
                        tipo_falha=_tipo_falha_padrao(),
                        usuario="",
                        data_contestacao=falha.data_contestacao,
                        data_analise=falha.data_analise,
                        descricao_irregularidades=tipo or irreg,
                        responsavel=falha.responsavel,
                        atribuido_em=falha.atribuido_em or now,
                        analise_iniciada_em=falha.analise_iniciada_em or now,
                        created_by=user,
                    )
                else:
                    target = QualidadePendenteAuditoriaCompliance(
                        protocolo=falha.protocolo,
                        tipo_falha=_tipo_falha_padrao(),
                        usuario="",
                        data_contestacao=falha.data_contestacao,
                        data_analise=falha.data_analise,
                        descricao_irregularidades=str(
                            etapa.get("descricao") or etapa.get("descricao_irregularidades") or ""
                        ).strip(),
                        responsavel=falha.responsavel,
                        atribuido_em=falha.atribuido_em or now,
                        analise_iniciada_em=falha.analise_iniciada_em or now,
                        created_by=user,
                    )
                siblings.append(target)
            else:
                descricao_manual = str(
                    etapa.get("descricao") or etapa.get("descricao_irregularidades") or ""
                ).strip()
                if fila_contexto_atual() == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
                    # Tipo (importado) permanece em descricao_irregularidades;
                    # irregularidade escolhida pelo auditor vai em motivo_falha.
                    if descricao_manual and not str(etapa.get("motivo_falha") or "").strip():
                        etapa = {**etapa, "motivo_falha": descricao_manual}
                    tipo_conferencia = str(
                        etapa.get("tipo_conferencia")
                        or target.descricao_irregularidades
                        or ""
                    ).strip()
                    if tipo_conferencia and not (target.descricao_irregularidades or "").strip():
                        target.descricao_irregularidades = tipo_conferencia
                elif descricao_manual and not (target.descricao_irregularidades or "").strip():
                    target.descricao_irregularidades = descricao_manual
            status_norm = _apply_etapa_fields(target, etapa)
            if not primary_status:
                primary_status = status_norm
            elif status_norm == "Procedente":
                primary_status = "Procedente"

            if not target.analise_iniciada_em:
                target.analise_iniciada_em = now
            if not target.atribuido_em:
                target.atribuido_em = falha.atribuido_em or now
            target.analise_status = QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO
            target.analise_concluida_em = now
            target.data_resposta = now
            target.cliente = "Claro"
            if not (target.observacao or "").strip():
                target.observacao = observacao_txt
            target.auditor = auditor_mat
            target.modulo = _modulo_conclusao()
            target.save()

        # Irregularidades sem etapa correspondente: conclui com status resumido do protocolo.
        for leftover in siblings[len(etapas_list) :]:
            if leftover.analise_status == QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO:
                continue
            if not leftover.analise_iniciada_em:
                leftover.analise_iniciada_em = now
            leftover.analise_status = QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO
            leftover.analise_concluida_em = now
            leftover.data_resposta = now
            leftover.cliente = "Claro"
            leftover.status = primary_status or "Improcedente"
            leftover.observacao = observacao_txt
            leftover.auditor = auditor_mat
            leftover.modulo = _modulo_conclusao()
            leftover.save(
                update_fields=[
                    "analise_status",
                    "analise_iniciada_em",
                    "analise_concluida_em",
                    "data_resposta",
                    "cliente",
                    "status",
                    "observacao",
                    "auditor",
                    "modulo",
                    "updated_at",
                ]
            )

        status_for_hist = primary_status or "Improcedente"
        agente_hist = str(etapas_list[0].get("agente") or "").strip()
    else:
        status_norm = _normalize_status_conformidade(status_texto)
        if not status_norm:
            raise ValueError("Informe a situação: Improcedente ou Procedente.")
        fora_prazo = _is_fora_do_prazo_status(status_norm)
        if fora_prazo and fila_contexto_atual() != FILA_CONTEXTO_REINSPECAO:
            raise ValueError("Conclusão fora do prazo não disponível neste contexto.")
        agente = (matricula_agente or "").strip()
        if not fora_prazo and not agente:
            raise ValueError("Informe a matrícula do agente.")
        for target in siblings:
            if not target.analise_iniciada_em:
                target.analise_iniciada_em = now
            if not target.atribuido_em:
                target.atribuido_em = falha.atribuido_em or now
            target.analise_status = QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO
            target.analise_concluida_em = now
            target.data_resposta = now
            target.cliente = "Claro"
            target.status = status_norm
            target.observacao = observacao_txt
            if not fora_prazo:
                target.usuario = agente
            target.auditor = auditor_mat
            target.modulo = _modulo_conclusao()
            parsed = dict(target.brflow_parsed or {}) if isinstance(target.brflow_parsed, dict) else {}
            parsed["fila_contexto"] = fila_contexto_atual()
            parsed["situacao"] = _situacao_code_from_status(status_norm)
            target.brflow_parsed = parsed
            update_fields = [
                "analise_status",
                "analise_iniciada_em",
                "analise_concluida_em",
                "atribuido_em",
                "data_resposta",
                "cliente",
                "status",
                "observacao",
                "auditor",
                "modulo",
                "brflow_parsed",
                "updated_at",
            ]
            if not fora_prazo:
                update_fields.insert(7, "usuario")
            target.save(update_fields=update_fields)
        status_for_hist = status_norm
        agente_hist = agente or (siblings[0].usuario or "").strip() if siblings else ""

    falha.refresh_from_db()
    auditor_id = falha.responsavel_id
    atribuido = falha.atribuido_em or now
    sla_segundos = max(0, int((now - atribuido).total_seconds()))
    _write_historico(
        tipo=AuditoriaComplianceFilaHistorico.TIPO_CONCLUSAO,
        actor=user,
        pendente=falha,
        auditor=falha.responsavel,
        detalhe={
            "protocolo": falha.protocolo,
            "sla_atendimento_segundos": sla_segundos,
            "status": status_for_hist,
            "matricula_agente": agente_hist,
            "matricula_auditor": auditor_mat,
            "modulo": _modulo_conclusao(),
            "irregularidades": len(siblings),
            "etapas": len(etapas_list),
        },
    )
    # Pendente (P) → tratado (F) na tabela única; remove da fila de pendentes.
    promovidos: list[AuditoriaFalhaCadastro] = []
    for ordem_etapa, sibling in enumerate(siblings):
        if not sibling.pk:
            continue
        parsed = dict(sibling.brflow_parsed or {}) if isinstance(sibling.brflow_parsed, dict) else {}
        parsed["fila_contexto"] = fila_contexto_atual()
        sibling.brflow_parsed = parsed
        sibling.save(update_fields=["brflow_parsed", "updated_at"])
        promovidos.append(
            promover_pendente_auditoria_compliance(
                sibling,
                finalizador=user,
                origem=_origem_tratado(),
                ordem_etapa=ordem_etapa,
                user=user,
            )
        )
    if auditor_id:
        _activate_first_slot_sla(auditor_id=auditor_id, now=now)
    return promovidos[0] if promovidos else falha


def serialize_presence(presence: AuditoriaComplianceAuditorPresence) -> dict:
    return {
        "user_id": str(presence.user_id),
        "contexto": presence.contexto,
        "username": presence.user.username,
        "nome": user_display_name(presence.user),
        "status": presence.status,
        "status_label": PRESENCE_LABELS.get(presence.status, presence.status),
        "status_changed_at": presence.status_changed_at.isoformat() if presence.status_changed_at else None,
        "last_assigned_at": presence.last_assigned_at.isoformat() if presence.last_assigned_at else None,
    }


def _agente_display_name(falha, *, resolved: str | None = None) -> str | None:
    """Nome do agente: catálogo/workforce, senão nome do CSV, senão matrícula."""
    matricula = (getattr(falha, "usuario", None) or "").strip()
    parsed = falha.brflow_parsed if isinstance(getattr(falha, "brflow_parsed", None), dict) else {}
    from_csv = str(parsed.get("nome_inspetor") or parsed.get("nome_agente") or "").strip()
    from_resolved = (resolved or "").strip()
    matricula_l = matricula.casefold()
    if from_resolved and from_resolved.casefold() != matricula_l:
        return from_resolved
    if from_csv:
        return from_csv
    return from_resolved or matricula or None


def serialize_fila_item(
    falha: AuditoriaFalhaCadastro,
    *,
    now=None,
    agente_nome: str | None = None,
    sla_ativo: bool = True,
) -> dict:
    from apps.auditoria.services.reinspecao_sla import (
        as_of_for_falha,
        data_final_sla,
        sla_business_days_elapsed,
        sla_farol,
        sla_farol_label,
    )

    now = now or timezone.now()
    effective_sla_ativo = sla_ativo and (falha.analise_status or "") in ANALISE_ATIVA
    wait_seconds = None
    if effective_sla_ativo and falha.atribuido_em and falha.analise_status in ANALISE_ATIVA:
        wait_seconds = max(0, int((now - falha.atribuido_em).total_seconds()))
    elif falha.atribuido_em and falha.analise_concluida_em:
        wait_seconds = max(0, int((falha.analise_concluida_em - falha.atribuido_em).total_seconds()))

    sla_segundos = None
    if falha.analise_iniciada_em and (falha.analise_status or "") == QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE:
        sla_segundos = max(0, int((now - falha.analise_iniciada_em).total_seconds()))
    elif falha.atribuido_em and falha.analise_concluida_em:
        sla_segundos = max(0, int((falha.analise_concluida_em - falha.atribuido_em).total_seconds()))
    elif effective_sla_ativo and falha.atribuido_em and falha.analise_status in ANALISE_ATIVA:
        sla_segundos = max(0, int((now - falha.atribuido_em).total_seconds()))

    parsed = falha.brflow_parsed if isinstance(falha.brflow_parsed, dict) else {}
    tipo_falha = (falha.tipo_falha or "").strip()
    if tipo_falha.lower() == "reinspecao":
        tipo_falha = ""

    responsavel = falha.responsavel
    timeout_restante = None
    if effective_sla_ativo and falha.atribuido_em and falha.analise_status in ANALISE_ATIVA:
        elapsed = max(0, int((now - falha.atribuido_em).total_seconds()))
        timeout_restante = max(0, FILA_TIMEOUT_SECONDS - elapsed)
    origem = (falha.fila_origem or "").strip() or QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_AUTO

    data_origem = _data_origem(falha)
    as_of = as_of_for_falha(falha, now=now)
    data_final = data_final_sla(data_origem)
    farol = sla_farol(data_origem, as_of=as_of)
    sla_dias_uteis = sla_business_days_elapsed(data_origem, as_of=as_of)
    matricula = (falha.usuario or "").strip()
    nome_agente = _agente_display_name(falha, resolved=agente_nome)

    return {
        "id": falha.id,
        "contexto": getattr(falha, "contexto", None) or parsed.get("fila_contexto") or fila_contexto_atual(),
        "protocolo": falha.protocolo,
        "data_contestacao": falha.data_contestacao.isoformat() if falha.data_contestacao else None,
        "data_analise": falha.data_analise.isoformat() if falha.data_analise else None,
        "data_final": data_final.isoformat() if data_final else None,
        "sla_dias_uteis": sla_dias_uteis,
        "sla_farol": farol,
        "sla_farol_label": sla_farol_label(farol),
        "descricao_irregularidades": falha.descricao_irregularidades,
        "analise_status": falha.analise_status or QualidadePendenteAuditoriaCompliance.ANALISE_NAO_ATRIBUIDO,
        "analise_status_label": ANALISE_LABELS.get(
            falha.analise_status or QualidadePendenteAuditoriaCompliance.ANALISE_NAO_ATRIBUIDO,
            "Não atribuído",
        ),
        "fila_origem": origem,
        "fila_origem_label": (
            "Direcionado"
            if origem == QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_DIRECIONADO
            else "Automático"
        ),
        "atribuido_em": falha.atribuido_em.isoformat() if falha.atribuido_em else None,
        "analise_iniciada_em": falha.analise_iniciada_em.isoformat() if falha.analise_iniciada_em else None,
        "analise_concluida_em": falha.analise_concluida_em.isoformat() if falha.analise_concluida_em else None,
        "tempo_espera_segundos": wait_seconds,
        "sla_atendimento_segundos": sla_segundos,
        "sla_ativo": effective_sla_ativo,
        "timeout_restante_segundos": timeout_restante,
        "fila_timeout_segundos": FILA_TIMEOUT_SECONDS,
        "responsavel_id": str(falha.responsavel_id) if falha.responsavel_id else None,
        "responsavel_nome": user_display_name(responsavel) if responsavel else None,
        "cliente": falha.cliente,
        "status": falha.status,
        "observacao": falha.observacao,
        "auditor": falha.auditor,
        "usuario": falha.usuario,
        "agente_nome": nome_agente,
        "modulo": falha.modulo,
        "tipo_falha": tipo_falha,
        "etapa_falha": falha.etapa_falha or "",
        "motivo_falha": falha.motivo_falha or "",
        "nivel_dificuldade": falha.nivel_dificuldade or "",
        "tipo_documento": falha.tipo_documento or "",
        "uf_documento": falha.uf_documento or "",
        "resultado_correto": falha.novo_resultado or "",
        "tempo_analise": str(parsed.get("tempo_analise") or ""),
        "cruzamento_bases": str(parsed.get("cruzamento_bases") or ""),
        "qualidade_imagem": str(parsed.get("qualidade_imagem") or ""),
        "situacao": _situacao_code_from_status(falha.status) or str(parsed.get("situacao") or ""),
        "data_resposta": falha.data_resposta.isoformat() if falha.data_resposta else None,
        "created_at": falha.created_at.isoformat() if falha.created_at else None,
    }


def _group_protocolo_slots(falhas: list[AuditoriaFalhaCadastro]) -> list[list[AuditoriaFalhaCadastro]]:
    """Agrupa por protocolo e ordena: em análise primeiro, depois atribuido_em ASC."""
    groups: dict[str, list[AuditoriaFalhaCadastro]] = {}
    order: list[str] = []
    for falha in falhas:
        key = (falha.protocolo or "").strip() or f"id:{falha.id}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(falha)

    def sort_key(proto: str):
        items = groups[proto]
        has_em_analise = any(
            item.analise_status == QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE for item in items
        )
        atribuido = min(
            (item.atribuido_em for item in items if item.atribuido_em),
            default=datetime.max.replace(tzinfo=dt_timezone.utc),
        )
        # Posição 1: em análise (ou o mais antigo). Direcionado recente fica na 2.
        return (0 if has_em_analise else 1, atribuido, proto)

    ordered_keys = sorted(order, key=sort_key)
    return [groups[key] for key in ordered_keys[:MAX_ACTIVE_PER_AUDITOR]]


def _activate_first_slot_sla(*, auditor_id, now=None) -> None:
    """Inicia o SLA do primeiro protocolo após a saída da posição anterior."""
    now = now or timezone.now()
    ativos = list(
        reinspecao_falhas_qs()
        .filter(responsavel_id=auditor_id, analise_status__in=ANALISE_ATIVA)
        .select_for_update()
        .order_by(F("atribuido_em").asc(nulls_last=True), "id")
    )
    slot_groups = _group_protocolo_slots(ativos)
    if not slot_groups:
        return
    first_group = slot_groups[0]
    # Reinicia também registros legados da posição 2 que receberam timestamp
    # antes da regra de pausa do SLA.
    for item in first_group:
        item.atribuido_em = now
        item.save(update_fields=["atribuido_em", "updated_at"])


def _agent_daily_metrics(*, user, now=None) -> dict[str, int | None]:
    """Indicadores do dia do auditor, isolados pelo contexto atual da fila."""
    from apps.auditoria.services.qualidade_promocao import tratados_qs

    now = now or timezone.now()
    local_now = timezone.localtime(now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    contexto = fila_contexto_atual()
    tratados = tratados_qs(origem=_origem_tratado())
    if contexto == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        tratados = tratados.filter(
            Q(brflow_parsed__fila_contexto=contexto)
            | Q(analise_origem__contexto__fila_contexto=contexto)
        )

    concluidos = list(
        tratados.filter(
            responsavel_id=user.id,
            analise_concluida_em__gte=day_start,
            analise_concluida_em__lt=day_end,
        ).values("protocolo", "atribuido_em", "analise_concluida_em")
    )

    analyses = {
        (
            str(row["protocolo"] or "").strip(),
            row["atribuido_em"],
            row["analise_concluida_em"],
        )
        for row in concluidos
        if row["atribuido_em"] is not None and row["analise_concluida_em"] is not None
    }
    durations = [
        max(0, int((completed_at - assigned_at).total_seconds()))
        for _, assigned_at, completed_at in analyses
    ]
    return {
        # Compliance: cada irregularidade concluída conta como uma análise.
        "realizados": len(concluidos),
        "tempo_medio_analise_segundos": (
            round(sum(durations) / len(durations)) if durations else None
        ),
    }


def list_minha_fila(user, *, auto_claim: bool = True) -> dict:
    """Lista até 2 protocolos da fila pessoal (posição 1 em análise, 2 direcionado)."""
    expire_stale_fila_assignments(actor=user)
    if auto_claim:
        claim_next_for_auditor(user=user)
    now = timezone.now()
    ativos = list(
        reinspecao_falhas_qs()
        .filter(responsavel=user, analise_status__in=ANALISE_ATIVA)
        .select_related("responsavel")
        .order_by(F("atribuido_em").asc(nulls_last=True), "id")
    )
    slots_groups = _group_protocolo_slots(ativos)
    if not slots_groups:
        return {
            "results": [],
            "irregularidades": [],
            "slots": [],
            "total": 0,
            "fila_timeout_segundos": FILA_TIMEOUT_SECONDS,
            "metricas_hoje": _agent_daily_metrics(user=user, now=now),
        }

    from apps.auditoria.services.reinspecao_sla import resolve_agente_names

    all_items = [item for group in slots_groups for item in group]
    agent_names = resolve_agente_names(item.usuario for item in all_items)

    slots = []
    for index, group in enumerate(slots_groups, start=1):
        primary = group[0]
        sla_ativo = index == 1

        def _ser(item, *, ativo: bool = sla_ativo):
            return serialize_fila_item(
                item,
                now=now,
                sla_ativo=ativo,
                agente_nome=agent_names.get((item.usuario or "").strip().lower()),
            )

        slots.append(
            {
                "posicao": index,
                "protocolo": primary.protocolo,
                "fila_origem": (
                    (primary.fila_origem or "").strip() or QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_AUTO
                ),
                "results": [_ser(primary)],
                "irregularidades": [_ser(item) for item in group],
            }
        )

    first = slots[0]
    return {
        "results": first["results"],
        "irregularidades": first["irregularidades"],
        "slots": slots,
        "total": len(slots),
        "fila_timeout_segundos": FILA_TIMEOUT_SECONDS,
        "metricas_hoje": _agent_daily_metrics(user=user, now=now),
    }


def list_auditor_fila(auditor_id) -> dict:
    expire_stale_fila_assignments()
    now = timezone.now()
    items = list(
        reinspecao_falhas_qs()
        .filter(responsavel_id=auditor_id, analise_status__in=ANALISE_ATIVA)
        .select_related("responsavel")
        .order_by(F("atribuido_em").asc(nulls_last=True), "id")
    )
    slots_groups = _group_protocolo_slots(items)
    from apps.auditoria.services.reinspecao_sla import resolve_agente_names

    all_items = [item for group in slots_groups for item in group]
    agent_names = resolve_agente_names(item.usuario for item in all_items)
    slots = []
    for index, group in enumerate(slots_groups, start=1):
        primary = group[0]
        sla_ativo = index == 1

        def _ser(item, *, ativo: bool = sla_ativo):
            return serialize_fila_item(
                item,
                now=now,
                sla_ativo=ativo,
                agente_nome=agent_names.get((item.usuario or "").strip().lower()),
            )

        slots.append(
            {
                "posicao": index,
                "protocolo": primary.protocolo,
                "fila_origem": (
                    (primary.fila_origem or "").strip() or QualidadePendenteAuditoriaCompliance.FILA_ORIGEM_AUTO
                ),
                "results": [_ser(primary)],
                "irregularidades": [_ser(item) for item in group],
            }
        )
    flat = [
        serialize_fila_item(item, now=now, sla_ativo=index == 0)
        for index, group in enumerate(slots_groups)
        for item in group
    ]
    return {
        "results": flat,
        "irregularidades": flat,
        "slots": slots,
        "total": len(slots),
        "fila_timeout_segundos": FILA_TIMEOUT_SECONDS,
    }


def list_historico(*, falha_id: int | None = None, auditor_id=None, limit: int = 100) -> list[dict]:
    qs = (
        AuditoriaComplianceFilaHistorico.objects.all()
        .select_related("falha", "pendente", "auditor", "actor")
        .order_by("-created_at", "-id")
    )
    if falha_id:
        # A fila ativa usa QualidadePendenteAuditoriaCompliance; registros já promovidos
        # podem usar AuditoriaFalhaCadastro. O parâmetro legado `falha_id`
        # identifica qualquer uma das duas origens para manter compatibilidade.
        qs = qs.filter(Q(falha_id=falha_id) | Q(pendente_id=falha_id))
    if auditor_id:
        qs = qs.filter(Q(auditor_id=auditor_id) | Q(actor_id=auditor_id))
    qs = qs[: max(1, min(limit, 500))]
    results = []
    for item in qs:
        protocolo_origem = item.falha if item.falha_id else item.pendente
        results.append(
            {
                "id": item.id,
                "tipo": item.tipo,
                "tipo_label": dict(AuditoriaComplianceFilaHistorico.TIPO_CHOICES).get(item.tipo, item.tipo),
                "justificativa": item.justificativa,
                "detalhe": item.detalhe or {},
                "falha_id": item.falha_id or item.pendente_id,
                "pendente_id": item.pendente_id,
                "protocolo": protocolo_origem.protocolo if protocolo_origem else None,
                "auditor_id": str(item.auditor_id) if item.auditor_id else None,
                "auditor_nome": user_display_name(item.auditor) if item.auditor_id else None,
                "actor_id": str(item.actor_id) if item.actor_id else None,
                "actor_nome": user_display_name(item.actor) if item.actor_id else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
        )
    return results


def list_auditores_controle() -> list[dict]:
    from apps.access.constants import (
        ROLE_QUAL_AUDITORIA_COMPLIANCE,
        ROLE_QUAL_CONTESTACAO_COMPLIANCE,
    )
    from apps.access.services.agents_with_permission import users_with_role

    role = (
        ROLE_QUAL_AUDITORIA_COMPLIANCE
        if fila_contexto_atual() == FILA_CONTEXTO_AUDITORIA_COMPLIANCE
        else ROLE_QUAL_CONTESTACAO_COMPLIANCE
    )
    users_meta = {u["id"]: u for u in users_with_role(role)}
    active_user_ids = set(
        reinspecao_falhas_qs()
        .exclude(responsavel_id__isnull=True)
        .values_list("responsavel_id", flat=True)
    ) | set(
        reinspecao_tratados_qs()
        .exclude(responsavel_id__isnull=True)
        .values_list("responsavel_id", flat=True)
    )
    missing_ids = active_user_ids - set(users_meta.keys())
    if missing_ids:
        for user in User.objects.filter(pk__in=missing_ids, is_active=True).only(
            "id", "username", "first_name", "last_name"
        ):
            users_meta[user.pk] = {"id": user.pk, "label": user_display_name(user)}
    presence_map = {
        p.user_id: p
        for p in _presence_qs().select_related("user").filter(user_id__in=users_meta.keys())
    }

    counts = {
        row["responsavel_id"]: row
        for row in (
            reinspecao_falhas_qs()
            .filter(responsavel_id__isnull=False)
            .values("responsavel_id")
            .annotate(
                aguardando=Count(
                    "protocolo",
                    distinct=True,
                    filter=Q(analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_AGUARDANDO),
                ),
                em_analise=Count(
                    "protocolo",
                    distinct=True,
                    filter=Q(analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE),
                ),
                concluidos=Count(
                    "protocolo",
                    distinct=True,
                    filter=Q(analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO),
                ),
                ativos=Count(
                    "protocolo",
                    distinct=True,
                    filter=Q(analise_status__in=ANALISE_ATIVA),
                ),
            )
        )
    }

    # Concluídos vivem na tabela central de tratados (não na fila P).
    tratados_counts = {
        row["responsavel_id"]: int(row["total"] or 0)
        for row in (
            reinspecao_tratados_qs()
            .filter(responsavel_id__isnull=False)
            .values("responsavel_id")
            .annotate(total=Count("protocolo", distinct=True))
        )
    }

    durations = list(
        reinspecao_tratados_qs()
        .filter(
            atribuido_em__isnull=False,
            analise_concluida_em__isnull=False,
            responsavel_id__isnull=False,
        )
        .values("responsavel_id", "atribuido_em", "analise_concluida_em")
    )
    avg_map: dict = {}
    for row in durations:
        rid = row["responsavel_id"]
        seconds = (row["analise_concluida_em"] - row["atribuido_em"]).total_seconds()
        avg_map.setdefault(rid, []).append(max(0.0, seconds))

    now = timezone.now()
    online_seconds_map = _online_seconds_today_by_auditor(
        user_ids=users_meta.keys(),
        presence_map=presence_map,
        now=now,
    )
    last_analise_map = {
        row["responsavel_id"]: row["last_at"]
        for row in (
            reinspecao_tratados_qs()
            .filter(
                responsavel_id__isnull=False,
                analise_concluida_em__isnull=False,
            )
            .values("responsavel_id")
            .annotate(last_at=Max("analise_concluida_em"))
        )
    }

    results = []
    for user_id, meta in sorted(users_meta.items(), key=lambda x: str(x[1].get("label") or "")):
        presence = presence_map.get(user_id)
        status = presence.status if presence else AuditoriaComplianceAuditorPresence.STATUS_OFFLINE
        c = counts.get(user_id) or {}
        samples = avg_map.get(user_id) or []
        avg = round(sum(samples) / len(samples)) if samples else None
        last_analise = last_analise_map.get(user_id)
        last_activity = last_analise
        if not last_activity and presence and presence.status_changed_at:
            last_activity = presence.status_changed_at
        if presence and presence.last_assigned_at and (
            not last_activity or presence.last_assigned_at > last_activity
        ):
            last_activity = presence.last_assigned_at
        aguardando = int(c.get("aguardando") or 0)
        em_analise = int(c.get("em_analise") or 0)
        concluidos = int(tratados_counts.get(user_id) or 0)
        ativos = int(c.get("ativos") or 0)
        capacity = MAX_ACTIVE_PER_AUDITOR
        ocupacao = min(100, round((ativos / capacity) * 100)) if capacity else 0
        if ativos == 0:
            carga_nivel = "idle"
        elif ocupacao < 40:
            carga_nivel = "baixa"
        elif ocupacao < 80:
            carga_nivel = "moderada"
        else:
            carga_nivel = "alta"
        tempo_online = int(online_seconds_map.get(user_id) or 0)
        disponibilidade = (
            min(100, round((tempo_online / CARGA_HORARIA_SEGUNDOS) * 100))
            if CARGA_HORARIA_SEGUNDOS
            else 0
        )
        results.append(
            {
                "user_id": str(user_id),
                "nome": meta.get("label") or str(user_id),
                "username": "",
                "status": status,
                "status_label": PRESENCE_LABELS.get(status, status),
                "aguardando": aguardando,
                "em_analise": em_analise,
                "concluidos": concluidos,
                "ativos": ativos,
                "capacidade": capacity,
                "ocupacao_percent": ocupacao,
                "carga_nivel": carga_nivel,
                "tempo_medio_analise_segundos": avg,
                "tempo_medio_sla_segundos": avg,
                "tempo_online_segundos": tempo_online,
                "disponibilidade_percent": disponibilidade,
                "carga_horaria_segundos": CARGA_HORARIA_SEGUNDOS,
                "ultima_atividade": last_activity.isoformat() if last_activity else None,
                "ultima_analise": last_analise.isoformat() if last_analise else None,
            }
        )
    return results


CAPACITY_TARGET = MAX_ACTIVE_PER_AUDITOR
WAIT_SLA_SECONDS = 4 * 3600
# Carga horária diária usada no card de Disponibilidade (05:30:00).
CARGA_HORARIA_SEGUNDOS = 5 * 3600 + 30 * 60


def _online_seconds_today_by_auditor(
    *,
    user_ids,
    presence_map: dict,
    now=None,
) -> dict:
    """Soma o tempo Online de hoje (atalho para o range do dia corrente local)."""
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return _online_seconds_by_auditor(
        user_ids=user_ids,
        presence_map=presence_map,
        range_start=day_start,
        range_end=now,
        now=now,
    )


def _online_seconds_by_auditor(
    *,
    user_ids,
    presence_map: dict,
    range_start,
    range_end,
    now=None,
) -> dict:
    """Soma o tempo Online em ``[range_start, range_end]``.

    Regras (conservadoras para evitar inflar a disponibilidade):
    - Conta apenas intervalos em que o status era Online.
    - Se o auditor está Offline/Ausente agora, fecha o intervalo em
      ``status_changed_at`` (não projeta Online até ``now``).
    - Sessão Online atual conta desde ``max(range_start, status_changed_at)``.
    """
    now = now or timezone.now()
    end_cap = min(now, range_end) if range_end else now
    start = range_start
    ids = list(user_ids)
    if not ids or not start or end_cap <= start:
        return {uid: 0 for uid in ids}

    events_by_user: dict = {uid: [] for uid in ids}
    for row in (
        AuditoriaComplianceFilaHistorico.objects.filter(
            tipo=AuditoriaComplianceFilaHistorico.TIPO_STATUS_AUDITOR,
            auditor_id__in=ids,
            created_at__gte=start,
            created_at__lte=end_cap,
        )
        .order_by("created_at")
        .values("auditor_id", "created_at", "detalhe")
    ):
        detalhe = row.get("detalhe") or {}
        to_status = str(detalhe.get("to") or "").strip().lower()
        if not to_status:
            continue
        events_by_user.setdefault(row["auditor_id"], []).append((row["created_at"], to_status))

    prior_status: dict = {}
    for row in (
        AuditoriaComplianceFilaHistorico.objects.filter(
            tipo=AuditoriaComplianceFilaHistorico.TIPO_STATUS_AUDITOR,
            auditor_id__in=ids,
            created_at__lt=start,
        )
        .order_by("auditor_id", "-created_at")
        .values("auditor_id", "detalhe")
    ):
        uid = row["auditor_id"]
        if uid in prior_status:
            continue
        detalhe = row.get("detalhe") or {}
        prior_status[uid] = str(detalhe.get("to") or "").strip().lower()

    results: dict = {}
    for uid in ids:
        presence = presence_map.get(uid)
        events = events_by_user.get(uid) or []

        if uid in prior_status:
            current = prior_status[uid] or AuditoriaComplianceAuditorPresence.STATUS_OFFLINE
        elif presence and presence.status_changed_at and presence.status_changed_at < start:
            current = presence.status
        else:
            current = AuditoriaComplianceAuditorPresence.STATUS_OFFLINE

        cursor = start
        online_seconds = 0.0
        for ts, to_status in events:
            if ts < cursor:
                current = to_status
                continue
            if current == AuditoriaComplianceAuditorPresence.STATUS_ONLINE:
                online_seconds += max(0.0, (ts - cursor).total_seconds())
            current = to_status
            cursor = ts

        if presence and presence.status == AuditoriaComplianceAuditorPresence.STATUS_ONLINE:
            if not events:
                open_start = max(start, presence.status_changed_at or start)
                online_seconds = max(0.0, (end_cap - open_start).total_seconds())
            elif current == AuditoriaComplianceAuditorPresence.STATUS_ONLINE:
                online_seconds += max(0.0, (end_cap - cursor).total_seconds())
            else:
                open_start = max(cursor, presence.status_changed_at or cursor)
                online_seconds += max(0.0, (end_cap - open_start).total_seconds())
        elif current == AuditoriaComplianceAuditorPresence.STATUS_ONLINE:
            end = cursor
            if presence and presence.status_changed_at and presence.status_changed_at >= cursor:
                end = min(end_cap, presence.status_changed_at)
            else:
                end = min(end_cap, cursor)
            online_seconds += max(0.0, (end - cursor).total_seconds())

        results[uid] = int(max(0, online_seconds))
    return results


def _resolve_controle_period(
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    now=None,
) -> tuple[datetime, datetime]:
    """Define o intervalo do gráfico/KPIs de período (padrão: dia corrente local)."""
    now = now or timezone.now()
    tz = timezone.get_current_timezone()
    local_now = timezone.localtime(now, tz)

    if date_from is None and date_to is None:
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now

    start = date_from or date_to or now
    end = date_to or date_from or now
    if timezone.is_naive(start):
        start = timezone.make_aware(start, tz)
    else:
        start = timezone.localtime(start, tz)
    if timezone.is_naive(end):
        end = timezone.make_aware(end, tz)
    else:
        end = timezone.localtime(end, tz)

    # date-only (meia-noite): cobrir o dia civil inteiro no fuso local
    if date_from is not None and start.hour == 0 and start.minute == 0 and start.second == 0 and start.microsecond == 0:
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if date_to is not None and end.hour == 0 and end.minute == 0 and end.second == 0 and end.microsecond == 0:
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)

    if end < start:
        start, end = end, start
    return start, min(end, now)


def _period_day_count(start: datetime, end: datetime) -> int:
    start_d = timezone.localtime(start).date()
    end_d = timezone.localtime(end).date()
    return max(1, (end_d - start_d).days + 1)


def build_controle_operacoes(
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    """Payload único para o Centro de Operações da reinspeção.

    - ``auditores`` / fila / presença: sempre snapshot ao vivo.
    - KPIs de período (concluídos, tempo médio) e ``chart.modes``: respeitam
      ``date_from``/``date_to`` (padrão = hoje).

    Somente leitura: não chama expire/assign aqui para não travar o GET
    com select_for_update sob o runserver single-thread.
    """
    from django.db.models import Avg, DurationField, ExpressionWrapper

    now = timezone.now()
    period_start, period_end = _resolve_controle_period(date_from=date_from, date_to=date_to, now=now)
    period_days = _period_day_count(period_start, period_end)
    carga_periodo = CARGA_HORARIA_SEGUNDOS * period_days

    auditores = list_auditores_controle()

    presence_counts = {
        "online": sum(1 for a in auditores if a["status"] == AuditoriaComplianceAuditorPresence.STATUS_ONLINE),
        "ausente": sum(1 for a in auditores if a["status"] == AuditoriaComplianceAuditorPresence.STATUS_AUSENTE),
        "offline": sum(1 for a in auditores if a["status"] == AuditoriaComplianceAuditorPresence.STATUS_OFFLINE),
    }

    unassigned_qs = reinspecao_falhas_qs().filter(
        responsavel__isnull=True,
        analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_NAO_ATRIBUIDO,
    )
    unassigned_total = unassigned_qs.values("protocolo").distinct().count()
    oldest = unassigned_qs.order_by(F(_data_origem_field()).asc(nulls_last=True), "id").first()
    oldest_wait = None
    oldest_date = _data_origem(oldest) if oldest else None
    if oldest_date:
        oldest_wait = max(0, int((now - oldest_date).total_seconds()))
    elif oldest and oldest.created_at:
        oldest_wait = max(0, int((now - oldest.created_at).total_seconds()))

    status_counts = {
        row["analise_status"]: row["total"]
        for row in (
            reinspecao_falhas_qs()
            .values("analise_status")
            .annotate(total=Count("protocolo", distinct=True))
        )
    }

    concluidos_periodo_qs = reinspecao_tratados_qs().filter(
        analise_concluida_em__gte=period_start,
        analise_concluida_em__lte=period_end,
    )
    concluidos_periodo = concluidos_periodo_qs.values("protocolo").distinct().count()

    avg_duration = (
        concluidos_periodo_qs.filter(
            atribuido_em__isnull=False,
            analise_concluida_em__isnull=False,
        )
        .annotate(
            dur=ExpressionWrapper(
                F("analise_concluida_em") - F("atribuido_em"),
                output_field=DurationField(),
            )
        )
        .aggregate(avg=Avg("dur"))
        .get("avg")
    )
    tempo_medio = None
    if avg_duration is not None:
        tempo_medio = max(0, int(avg_duration.total_seconds()))

    auditor_ids = [a["user_id"] for a in auditores]
    users_meta = {user.pk: user for user in User.objects.filter(pk__in=auditor_ids)}
    presence_map = {
        p.user_id: p
        for p in _presence_qs().select_related("user").filter(user_id__in=users_meta.keys())
    }
    uid_by_str = {str(uid): uid for uid in users_meta.keys()}

    analises_map = {
        row["responsavel_id"]: int(row["total"] or 0)
        for row in (
            concluidos_periodo_qs.filter(responsavel_id__isnull=False)
            .values("responsavel_id")
            .annotate(total=Count("id"))
        )
    }
    protocolos_map = {
        row["responsavel_id"]: int(row["total"] or 0)
        for row in (
            concluidos_periodo_qs.filter(responsavel_id__isnull=False)
            .values("responsavel_id")
            .annotate(total=Count("protocolo", distinct=True))
        )
    }

    sla_samples: dict = {}
    for row in concluidos_periodo_qs.filter(
        responsavel_id__isnull=False,
        atribuido_em__isnull=False,
        analise_concluida_em__isnull=False,
    ).values("responsavel_id", "atribuido_em", "analise_concluida_em"):
        rid = row["responsavel_id"]
        seconds = max(0.0, (row["analise_concluida_em"] - row["atribuido_em"]).total_seconds())
        sla_samples.setdefault(rid, []).append(seconds)

    online_period_map = _online_seconds_by_auditor(
        user_ids=users_meta.keys(),
        presence_map=presence_map,
        range_start=period_start,
        range_end=period_end,
        now=now,
    )

    chart_labels: list[str] = []
    chart_analises: list[int] = []
    chart_protocolos: list[int] = []
    chart_sla: list[int] = []
    chart_disp: list[int] = []
    chart_tempo_logado: list[int] = []
    chart_ativos = [a["ativos"] for a in auditores]
    chart_concluidos_live = [a["concluidos"] for a in auditores]

    for a in auditores:
        uid = uid_by_str.get(str(a["user_id"]))
        chart_labels.append(a["nome"])
        chart_analises.append(int(analises_map.get(uid) or 0) if uid else 0)
        chart_protocolos.append(int(protocolos_map.get(uid) or 0) if uid else 0)
        samples = sla_samples.get(uid) or [] if uid else []
        chart_sla.append(int(round(sum(samples) / len(samples))) if samples else 0)
        online_s = int(online_period_map.get(uid) or 0) if uid else 0
        chart_tempo_logado.append(online_s)
        chart_disp.append(
            min(100, round((online_s / carga_periodo) * 100)) if carga_periodo else 0
        )

    from apps.auditoria.services.reinspecao_distribuicao import build_distribuicao_matrix
    from apps.auditoria.services.reinspecao_sla import (
        build_sla_kpis_from_falhas,
        sla_panel_reference_dates,
    )

    open_falhas = list(
        reinspecao_falhas_qs()
        .exclude(analise_status=QualidadePendenteAuditoriaCompliance.ANALISE_CONCLUIDO)
        .filter(data_resposta__isnull=True, status="")
        .only(
            "id",
            "protocolo",
            "data_contestacao",
            "data_analise",
            "analise_status",
            "status",
            "data_resposta",
        )
    )
    sla_kpis = build_sla_kpis_from_falhas(
        open_falhas,
        now=now,
        data_origem_field=_data_origem_field(),
    )
    sla_kpis["reference_dates"] = sla_panel_reference_dates(as_of=now)
    fila_kpis = build_fila_kpis()
    distribuicao = build_distribuicao_matrix(falhas=open_falhas)

    return {
        "kpis": {
            "auditores_online": presence_counts["online"],
            "auditores_ausente": presence_counts["ausente"],
            "auditores_offline": presence_counts["offline"],
            "protocolos_sem_responsavel": unassigned_total,
            "protocolos_aguardando": int(status_counts.get(QualidadePendenteAuditoriaCompliance.ANALISE_AGUARDANDO, 0)),
            "protocolos_em_analise": int(status_counts.get(QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE, 0)),
            "protocolos_concluidos_hoje": concluidos_periodo,
            "protocolos_concluidos_periodo": concluidos_periodo,
            "tempo_medio_analise_segundos": tempo_medio,
        },
        "sla_kpis": sla_kpis,
        "fila_kpis": fila_kpis,
        "periodo": {
            "date_from": period_start.isoformat(),
            "date_to": period_end.isoformat(),
            "dias": period_days,
        },
        "fila_geral": {
            "total": unassigned_total,
            "protocolo_mais_antigo": oldest.protocolo if oldest else None,
            "falha_id": oldest.id if oldest else None,
            "data_contestacao": (
                oldest.data_contestacao.isoformat() if oldest and oldest.data_contestacao else None
            ),
            "data_analise": (
                oldest.data_analise.isoformat() if oldest and oldest.data_analise else None
            ),
            "tempo_espera_segundos": oldest_wait,
            "fora_sla": bool(oldest_wait and oldest_wait >= WAIT_SLA_SECONDS),
        },
        "chart": {
            "labels": chart_labels,
            "ativos": chart_ativos,
            "concluidos": chart_concluidos_live,
            "modes": {
                "analises": {
                    "label": "Total de análises",
                    "unit": "count",
                    "values": chart_analises,
                },
                "protocolos": {
                    "label": "Total de protocolos",
                    "unit": "count",
                    "values": chart_protocolos,
                },
                "sla": {
                    "label": "SLA médio",
                    "unit": "seconds",
                    "values": chart_sla,
                },
                "tempo_logado": {
                    "label": "Tempo logado",
                    "unit": "seconds",
                    "values": chart_tempo_logado,
                },
                "disponibilidade": {
                    "label": "Disponibilidade",
                    "unit": "percent",
                    "values": chart_disp,
                },
            },
        },
        "auditores": auditores,
        "distribuicao": distribuicao,
        "generated_at": now.isoformat(),
    }


def redistribuir_fila_auditor(*, auditor_id, actor) -> dict:
    auditor = User.objects.filter(pk=auditor_id, is_active=True).first()
    if not auditor:
        raise ValueError("Auditor inválido.")
    released = release_auditor_active_queue(
        auditor=auditor,
        actor=actor,
        motivo="redistribuir_fila",
    )
    distribution = distribuir_protocolos(actor=actor)
    return {"released": released, "distribution": distribution}


def dashboard_reinspecao(*, date_from: datetime | None = None, date_to: datetime | None = None) -> dict:
    falhas = reinspecao_falhas_qs()
    data_origem_field = _data_origem_field()
    if date_from:
        falhas = falhas.filter(
            Q(created_at__gte=date_from)
            | Q(atribuido_em__gte=date_from)
            | Q(**{f"{data_origem_field}__gte": date_from})
        )
    if date_to:
        falhas = falhas.filter(
            Q(created_at__lte=date_to)
            | Q(atribuido_em__lte=date_to)
            | Q(**{f"{data_origem_field}__lte": date_to})
        )

    presence_counts = {
        row["status"]: row["total"]
        for row in _presence_qs().values("status").annotate(total=Count("id"))
    }

    status_counts = {
        row["analise_status"]: row["total"]
        for row in falhas.values("analise_status").annotate(total=Count("id"))
    }

    tratados = reinspecao_tratados_qs()
    if date_from:
        tratados = tratados.filter(
            Q(created_at__gte=date_from)
            | Q(atribuido_em__gte=date_from)
            | Q(**{f"{data_origem_field}__gte": date_from})
            | Q(analise_concluida_em__gte=date_from)
        )
    if date_to:
        tratados = tratados.filter(
            Q(created_at__lte=date_to)
            | Q(atribuido_em__lte=date_to)
            | Q(**{f"{data_origem_field}__lte": date_to})
            | Q(analise_concluida_em__lte=date_to)
        )
    tratados_total = tratados.count()

    by_auditor = list(
        falhas.filter(responsavel_id__isnull=False)
        .values("responsavel_id", "responsavel__username", "responsavel__first_name", "responsavel__last_name")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    por_auditor = []
    for row in by_auditor:
        nome = f"{row.get('responsavel__first_name') or ''} {row.get('responsavel__last_name') or ''}".strip()
        por_auditor.append(
            {
                "user_id": str(row["responsavel_id"]),
                "nome": nome or row.get("responsavel__username") or str(row["responsavel_id"]),
                "total": row["total"],
            }
        )

    waits = []
    analyses = []
    for item in falhas.filter(atribuido_em__isnull=False).only(
        "atribuido_em", "analise_iniciada_em", "analise_concluida_em", "analise_status"
    ):
        end_wait = item.analise_iniciada_em or (
            timezone.now() if item.analise_status in ANALISE_ATIVA else None
        )
        if end_wait and item.atribuido_em:
            waits.append(max(0.0, (end_wait - item.atribuido_em).total_seconds()))
    for item in tratados.filter(
        atribuido_em__isnull=False,
        analise_concluida_em__isnull=False,
    ).only("atribuido_em", "analise_concluida_em"):
        # SLA de atendimento: entrada na fila (atribuido_em) até finalização.
        analyses.append(max(0.0, (item.analise_concluida_em - item.atribuido_em).total_seconds()))

    loads = ativos_por_auditor()
    atividade = []
    for presence in _presence_qs().select_related("user").order_by("user__username"):
        atividade.append(
            {
                **serialize_presence(presence),
                "ativos": loads.get(presence.user_id, 0),
            }
        )

    return {
        "auditores": {
            "online": int(presence_counts.get(AuditoriaComplianceAuditorPresence.STATUS_ONLINE, 0)),
            "offline": int(presence_counts.get(AuditoriaComplianceAuditorPresence.STATUS_OFFLINE, 0)),
            "ausente": int(presence_counts.get(AuditoriaComplianceAuditorPresence.STATUS_AUSENTE, 0)),
        },
        "protocolos": {
            "nao_atribuido": int(status_counts.get(QualidadePendenteAuditoriaCompliance.ANALISE_NAO_ATRIBUIDO, 0)),
            "aguardando_analise": int(status_counts.get(QualidadePendenteAuditoriaCompliance.ANALISE_AGUARDANDO, 0)),
            "em_analise": int(status_counts.get(QualidadePendenteAuditoriaCompliance.ANALISE_EM_ANALISE, 0)),
            "concluido": int(tratados_total),
        },
        "por_auditor": por_auditor,
        "tempo_medio_espera_segundos": round(sum(waits) / len(waits)) if waits else None,
        "tempo_medio_analise_segundos": round(sum(analyses) / len(analyses)) if analyses else None,
        "atividade": atividade,
    }


def list_reinspecao_falhas_enriched(
    *,
    protocolo: str = "",
    auditor_id=None,
    atribuicao: str = "",
    analise_status: str = "",
    andamento: str = "",
    page: int = 1,
    page_size: int = 50,
    column_filters: dict | None = None,
    sort_key: str = "",
    sort_dir: str = "asc",
    filter_column: str = "",
) -> dict:
    """Lista pendentes (P, em andamento) ou tratados (F, finalizados)."""
    from apps.auditoria.services.reinspecao_fila import (
        _apply_consulta_column_filters,
        _apply_consulta_sort,
        _consulta_filter_options,
    )

    andamento = (andamento or "").strip().lower()
    use_tratados = andamento in {"finalizados", "finalizado", "concluidos"}

    if use_tratados:
        queryset = reinspecao_tratados_qs().order_by("-created_at", "-id")
    else:
        queryset = reinspecao_falhas_qs().order_by("-created_at", "-id")

    protocolo_q = (protocolo or "").strip()
    if protocolo_q:
        queryset = queryset.filter(protocolo__icontains=protocolo_q)
    if auditor_id:
        queryset = queryset.filter(responsavel_id=auditor_id)
    atribuicao = (atribuicao or "").strip().lower()
    if atribuicao in {"atribuido", "assigned"}:
        queryset = queryset.filter(responsavel_id__isnull=False)
    elif atribuicao in {"nao_atribuido", "unassigned"}:
        queryset = queryset.filter(responsavel_id__isnull=True)
    analise_status = (analise_status or "").strip().lower()
    if analise_status and not use_tratados:
        queryset = queryset.filter(analise_status=analise_status)

    queryset = _apply_consulta_column_filters(queryset, column_filters or {}, use_tratados=use_tratados)
    queryset = _apply_consulta_sort(queryset, sort_key=sort_key, sort_dir=sort_dir, use_tratados=use_tratados)

    filter_column = (filter_column or "").strip()
    if filter_column:
        return {
            "column": filter_column,
            "options": _consulta_filter_options(queryset, filter_column, use_tratados=use_tratados),
        }

    pendentes_count = reinspecao_falhas_qs().count()
    realizados_count = reinspecao_tratados_qs().count()
    progress_total = pendentes_count + realizados_count
    progress_percent = (
        round((pendentes_count / progress_total) * 100) if progress_total else 0
    )

    total = queryset.count()
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))
    offset = (page - 1) * page_size

    now = timezone.now()
    from apps.auditoria.services.reinspecao_sla import (
        build_sla_kpis_from_falhas,
        resolve_agente_names,
        sla_panel_reference_dates,
    )

    # KPIs de SLA sobre pendentes (em andamento).
    sla_kpis = build_sla_kpis_from_falhas(
        reinspecao_falhas_qs().iterator(chunk_size=500),
        now=now,
        data_origem_field=_data_origem_field(),
    )
    sla_kpis["reference_dates"] = sla_panel_reference_dates(as_of=now)
    fila_kpis = build_fila_kpis()

    page_rows = list(
        queryset.select_related("created_by", "responsavel")[offset : offset + page_size]
    )
    agent_names = resolve_agente_names(item.usuario for item in page_rows)

    def _serialize(item):
        if use_tratados:
            return {
                **serialize_fila_item(
                    item,
                    now=now,
                    agente_nome=agent_names.get((item.usuario or "").strip().lower()),
                ),
                "fase": "tratado",
                "analise_status": AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
                "analise_status_label": "Concluído",
                "resultado_qualidade": item.resultado_qualidade,
                "resultado_qualidade_label": item.get_resultado_qualidade_display(),
            }
        return {
            **serialize_fila_item(
                item,
                now=now,
                agente_nome=agent_names.get((item.usuario or "").strip().lower()),
            ),
            "fase": "pendente",
        }

    return {
        "results": [_serialize(item) for item in page_rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size) if total else 1,
        "realizadas": realizados_count,
        "pendentes": pendentes_count,
        "progress_total": progress_total,
        "progress_percent": progress_percent,
        "sla_kpis": sla_kpis,
        "fila_kpis": fila_kpis,
    }


