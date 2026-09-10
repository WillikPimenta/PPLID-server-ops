"""Painel operacional — espelha a coleção colUsuarios do Power Apps."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.db.models import Max, Q
from django.utils import timezone

from ..models import OperationalOccurrence, ScheduleToday
from .break_times import resolve_agent_break_interval
from .kpis import AVAILABLE_STATUS_ID, LOGGED_OUT_STATUS_ID
from .occurrence_workflow import (
    is_operational_occurrence_in_progress,
    occurrence_end_at,
    occurrence_start_at,
)
from .schedule_utils import is_shift_active_at, is_time_range_schedule, normalize_time_schedule
from .status_event_coverage import load_linked_status_ids

PRIORIDADE_ORDER = {"Alta": 0, "Média": 1, "Baixa": 2, "Info": 3, "—": 4}
INTERVAL_STATUS_ID = 4
PESSOAL_STATUS_ID = 10
SYSTEMIC_ISSUES_STATUS_ID = 11
INTERVAL_LATE_MEDIUM_MINUTES = 2
INTERVAL_LATE_MEDIUM_MINUTES_HE = 5
PESSOAL_BAIXA_MAX_MINUTES = 2
PESSOAL_MEDIA_MAX_MINUTES = 5
OCCURRENCE_EXCESS_BAIXA_MAX_MINUTES = 2
OCCURRENCE_EXCESS_MEDIA_MAX_MINUTES = 5
# Status com regra própria de alerta — não usam excesso de ocorrência.
_OCCURRENCE_ALERT_EXCLUDED_STATUS_IDS = frozenset(
    {
        AVAILABLE_STATUS_ID,
        LOGGED_OUT_STATUS_ID,
        INTERVAL_STATUS_ID,
        PESSOAL_STATUS_ID,
        SYSTEMIC_ISSUES_STATUS_ID,
    }
)


def _params_get(params, key: str, default=None):
    if hasattr(params, "get"):
        return params.get(key, default)
    return default


def _params_getlist(params, key: str) -> list[str]:
    if hasattr(params, "getlist"):
        return params.getlist(key)
    value = _params_get(params, key)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _planned_bounds(work_schedule: str) -> tuple[str, str]:
    normalized = normalize_time_schedule(work_schedule or "")
    if " - " not in normalized:
        return "", ""
    parts = normalized.split(" - ", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0][:5], parts[1][-5:]


def filter_schedule_today(queryset, params):
    qs = queryset.exclude(full_name="")
    if names := _params_getlist(params, "full_name"):
        qs = qs.filter(full_name__in=names)
    if activities := _params_getlist(params, "job_activity"):
        qs = qs.filter(job_activity__in=activities)
    if schedules := _params_getlist(params, "work_schedule"):
        qs = qs.filter(work_schedule__in=schedules)
    if locations := _params_getlist(params, "location"):
        qs = qs.filter(location__in=locations)
    if sectors := _params_getlist(params, "sector"):
        qs = qs.filter(sector__in=sectors)
    if statuses := _params_getlist(params, "status"):
        qs = qs.filter(status_id__in=[int(s) for s in statuses])
    if leader_lan := _params_get(params, "leader_lan_id"):
        qs = qs.filter(leader_lan_id__iexact=leader_lan)
    if leaders := _params_getlist(params, "leader"):
        qs = qs.filter(leader__full_name__in=leaders)
    if search := _params_get(params, "search"):
        qs = qs.filter(
            Q(full_name__icontains=search) | Q(agent__user_lan_id__icontains=search)
        )
    return qs.order_by("full_name")


def _reference_minutes(reference_time, target_date) -> int:
    local = timezone.localtime(reference_time)
    if local.date() != target_date:
        return local.hour * 60 + local.minute
    return local.hour * 60 + local.minute


def _minutes_to_hhmm(total_minutes: int) -> str:
    hour, minute = divmod(total_minutes % (24 * 60), 60)
    return f"{hour:02d}:{minute:02d}"


def _break_interval_bounds(row: ScheduleToday, reference_time) -> tuple[int, int] | None:
    return resolve_agent_break_interval(
        {
            "overtime": row.overtime,
            "week": row.week_break,
            "weekend": row.weekend_break,
        },
        target_date=row.date,
    )


def _interval_not_started_alert(row: ScheduleToday, reference_time) -> bool:
    """Disponível após o início do intervalo escalado (página Intervalo), sem entrar em pausa."""
    if row.status_id != AVAILABLE_STATUS_ID:
        return False
    interval = _break_interval_bounds(row, reference_time)
    if not interval:
        return False
    start_min, _end_min = interval
    ref_min = _reference_minutes(reference_time, row.date)
    return ref_min >= start_min


def _interval_out_of_schedule_alert(
    row: ScheduleToday, reference_time, intervalo_atraso_minutos: int | None
) -> bool:
    """Intervalo iniciado fora da janela escalada (antes do horário; após o fim usa alerta de atraso)."""
    if row.status_id != INTERVAL_STATUS_ID:
        return False
    interval = _break_interval_bounds(row, reference_time)
    if not interval:
        return False
    start_min, end_min = interval
    ref_min = _reference_minutes(reference_time, row.date)
    if ref_min < start_min:
        return True
    if ref_min > end_min:
        return intervalo_atraso_minutos is None or intervalo_atraso_minutos <= 0
    return False


def _interval_delay_minutes(row: ScheduleToday, reference_time) -> int | None:
    if row.status_id != INTERVAL_STATUS_ID:
        return None
    interval = _break_interval_bounds(row, reference_time)
    if not interval:
        return None
    _, end_min = interval
    ref_min = _reference_minutes(reference_time, row.date)
    if ref_min <= end_min:
        return None
    return ref_min - end_min


def _interval_alert_priority(delay_minutes: int | None, hora_extra: bool) -> str | None:
    if delay_minutes is None or delay_minutes <= 0:
        return None
    threshold = (
        INTERVAL_LATE_MEDIUM_MINUTES_HE if hora_extra else INTERVAL_LATE_MEDIUM_MINUTES
    )
    if delay_minutes <= threshold:
        return "Média"
    return "Alta"


def _pessoal_duration_minutes(row: ScheduleToday, reference_time) -> int | None:
    if row.status_id != PESSOAL_STATUS_ID or not row.last_change:
        return None
    ref = timezone.localtime(reference_time)
    changed = timezone.localtime(row.last_change)
    elapsed = int((ref - changed).total_seconds() // 60)
    return max(0, elapsed)


def _pessoal_alert_priority(duration_minutes: int | None) -> str | None:
    if duration_minutes is None:
        return None
    if duration_minutes <= PESSOAL_BAIXA_MAX_MINUTES:
        return "Baixa"
    if duration_minutes <= PESSOAL_MEDIA_MAX_MINUTES:
        return "Média"
    return "Alta"


def _occurrence_excess_priority(excess_minutes: int) -> str:
    """Farol do tempo excedido de ocorrência: <2 Baixa; 2–<5 Média; ≥5 Alta."""
    if excess_minutes < OCCURRENCE_EXCESS_BAIXA_MAX_MINUTES:
        return "Baixa"
    if excess_minutes < OCCURRENCE_EXCESS_MEDIA_MAX_MINUTES:
        return "Média"
    return "Alta"


def _elapsed_minutes_since(start, reference_time) -> int:
    if not start:
        return 0
    ref = timezone.localtime(reference_time)
    started = timezone.localtime(start)
    return max(0, int((ref - started).total_seconds() // 60))


def _load_occurrence_alert_context(
    target_date,
    agent_ids: list[int],
) -> dict[str, Any]:
    """Pré-carrega status vinculados e ocorrências do dia por agente/status."""
    linked_status_ids = load_linked_status_ids()
    by_agent_status: dict[tuple[int, int], list[OperationalOccurrence]] = defaultdict(list)
    all_by_agent_status: dict[tuple[int, int], list[OperationalOccurrence]] = defaultdict(list)
    if not agent_ids or not linked_status_ids:
        return {
            "linked_status_ids": linked_status_ids,
            "by_agent_status": by_agent_status,
            "all_by_agent_status": all_by_agent_status,
        }

    occurrences = (
        OperationalOccurrence.objects.filter(
            date=target_date,
            agent_id__in=agent_ids,
            cancelled=False,
            occurrence_type__active=True,
            occurrence_type__status_types__id__in=linked_status_ids,
        )
        .select_related("occurrence_type")
        .prefetch_related("occurrence_type__status_types")
        .distinct()
    )
    for occ in occurrences:
        linked = {st.id for st in occ.occurrence_type.status_types.all()}
        for status_id in linked & linked_status_ids:
            key = (occ.agent_id, status_id)
            all_by_agent_status[key].append(occ)
            if occ.approved is True:
                by_agent_status[key].append(occ)
    return {
        "linked_status_ids": linked_status_ids,
        "by_agent_status": by_agent_status,
        "all_by_agent_status": all_by_agent_status,
    }


def _live_occurrence_excess_minutes(
    row: ScheduleToday,
    reference_time,
    occurrences: list[OperationalOccurrence],
) -> int | None:
    """
    Minutos além do tempo liberado pela ocorrência para o status atual.

    - Com ocorrência ainda em andamento → sem excesso.
    - Com ocorrência já finalizada e agente ainda no status → ref - fim da ocorrência.
    - Sem ocorrência aprovada (status vinculado) → tempo desde last_change.
    """
    if not occurrences:
        if not row.last_change:
            return 0
        return _elapsed_minutes_since(row.last_change, reference_time)

    ref = timezone.localtime(reference_time)
    if any(is_operational_occurrence_in_progress(occ, ref) for occ in occurrences):
        return None

    ended_ats = []
    for occ in occurrences:
        start_at = occurrence_start_at(occ)
        end_at = occurrence_end_at(occ)
        if not start_at or not end_at:
            continue
        start_local = timezone.localtime(start_at)
        end_local = timezone.localtime(end_at)
        if start_local <= ref and end_local <= ref:
            ended_ats.append(end_local)

    if ended_ats:
        latest_end = max(ended_ats)
        last_change = timezone.localtime(row.last_change) if row.last_change else None
        # Sessão atual começou depois do fim → conta desde a entrada no status.
        if last_change and last_change >= latest_end:
            return _elapsed_minutes_since(row.last_change, reference_time)
        return max(0, int((ref - latest_end).total_seconds() // 60))

    # Ocorrência existe mas ainda não começou: sem excesso até o horário.
    return None


def _logged_in_today(row: ScheduleToday) -> bool:
    if not row.start_of_work:
        return False
    return timezone.localtime(row.start_of_work).date() == row.date


def _ausente_dia(row: ScheduleToday) -> bool:
    if not is_time_range_schedule(row.work_schedule or ""):
        return False
    return not _logged_in_today(row)


def _ausente_agora(row: ScheduleToday, reference_time) -> bool:
    if not is_shift_active_at(
        reference_time,
        row.date,
        row.work_schedule,
        is_previous_night_shift=row.is_previous_night_shift,
    ):
        return False
    sow = row.start_of_work
    if sow is None:
        return True
    return timezone.localtime(sow) > timezone.localtime(reference_time)


def _enrich_row(
    row: ScheduleToday,
    reference_time,
    occurrence_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status_id = row.status_id
    disponivel = status_id == AVAILABLE_STATUS_ID
    deslogado = status_id == LOGGED_OUT_STATUS_ID
    presente = disponivel
    em_pausa = status_id is not None and status_id not in (
        AVAILABLE_STATUS_ID,
        LOGGED_OUT_STATUS_ID,
    )
    ausente_dia = _ausente_dia(row)
    ausente_agora = _ausente_agora(row, reference_time)
    ausente = ausente_dia
    hora_extra = bool(row.overtime)
    journey = (row.journey or "").strip()
    work_schedule = (row.work_schedule or "").strip()
    escala_divergente = bool(journey and work_schedule and journey != work_schedule)
    em_turno = is_shift_active_at(
        reference_time,
        row.date,
        row.work_schedule,
        is_previous_night_shift=row.is_previous_night_shift,
    )
    hora_extra_dia = hora_extra or escala_divergente
    hora_extra_agora = hora_extra_dia and em_turno
    hora_inicio, hora_fim = _planned_bounds(row.work_schedule or "")
    status_name = row.status.name if row.status else ""
    valid_schedule = is_time_range_schedule(work_schedule)
    intervalo_bounds = _break_interval_bounds(row, reference_time)
    intervalo_atraso_minutos = _interval_delay_minutes(row, reference_time)
    alerta_intervalo = _interval_alert_priority(intervalo_atraso_minutos, hora_extra)
    pessoal_duracao_minutos = _pessoal_duration_minutes(row, reference_time)
    alerta_pessoal = _pessoal_alert_priority(pessoal_duracao_minutos)
    iniciou_jornada = _logged_in_today(row)
    # Alertas de status (intervalo/pessoal/sistêmico): com login e ainda ativo.
    agente_ativo = iniciou_jornada and not deslogado
    # Deslogado após ter logado no dia = saída antecipada (Informativos).
    # Sem login no dia = Absenteísmo.
    alerta_saida_antecipada = em_turno and deslogado and iniciou_jornada
    # Logado (não Deslogado) fora da janela da escala → Informativos.
    alerta_login_fora_escala = (
        iniciou_jornada
        and not deslogado
        and is_time_range_schedule(work_schedule)
        and not em_turno
    )
    escala_divergente_informativo = escala_divergente and not hora_extra

    # Flags de alerta pelo status atual (dia); o recorte "horário atual" exige em_turno.
    alerta_sistemico = status_id == SYSTEMIC_ISSUES_STATUS_ID
    alerta_intervalo_nao_iniciado = _interval_not_started_alert(row, reference_time)
    alerta_intervalo_fora_escala = _interval_out_of_schedule_alert(
        row, reference_time, intervalo_atraso_minutos
    )

    linked_status_ids = (occurrence_ctx or {}).get("linked_status_ids") or set()
    occs_by_agent_status = (occurrence_ctx or {}).get("by_agent_status") or {}
    all_occs_by_agent_status = (occurrence_ctx or {}).get("all_by_agent_status") or {}
    ocorrencia_excesso_minutos: int | None = None
    ocorrencia_situacao: str | None = None
    alerta_ocorrencia_excedente = False
    occs_for_status: list[OperationalOccurrence] = []
    all_occs_for_status: list[OperationalOccurrence] = []
    if (
        agente_ativo
        and status_id is not None
        and status_id in linked_status_ids
        and status_id not in _OCCURRENCE_ALERT_EXCLUDED_STATUS_IDS
    ):
        occurrence_key = (row.agent_id, status_id)
        occs_for_status = occs_by_agent_status.get(occurrence_key, [])
        all_occs_for_status = all_occs_by_agent_status.get(occurrence_key, [])
        ocorrencia_excesso_minutos = _live_occurrence_excess_minutes(
            row, reference_time, occs_for_status
        )
        if ocorrencia_excesso_minutos is None:
            alerta_ocorrencia_excedente = False
        elif not occs_for_status:
            alerta_ocorrencia_excedente = True
            if any(occ.approved is None for occ in all_occs_for_status):
                ocorrencia_situacao = "pendente"
            elif any(occ.approved is False for occ in all_occs_for_status):
                ocorrencia_situacao = "recusada"
            else:
                ocorrencia_situacao = "ausente"
        else:
            alerta_ocorrencia_excedente = ocorrencia_excesso_minutos > 0
            if alerta_ocorrencia_excedente:
                ocorrencia_situacao = "excedida"

    alerta_config_escala_invalida = agente_ativo and not valid_schedule
    alerta_config_intervalo_ausente = (
        agente_ativo and valid_schedule and intervalo_bounds is None
    )
    alerta_status_sem_inicio = (
        agente_ativo and status_id == PESSOAL_STATUS_ID and not row.last_change
    )

    precisa_acompanhamento_dia = (
        agente_ativo
        and is_time_range_schedule(work_schedule)
        and (
            bool(alerta_intervalo)
            or alerta_sistemico
            or alerta_intervalo_nao_iniciado
            or alerta_intervalo_fora_escala
            or bool(alerta_pessoal)
            or alerta_ocorrencia_excedente
        )
    )
    precisa_acompanhamento = em_turno and precisa_acompanhamento_dia

    estado_operacional: str | None = None
    prioridade_operacional: str | None = None
    motivo_operacional: str | None = None
    acao_operacional: str | None = None
    if em_turno or precisa_acompanhamento_dia:
        if em_turno and deslogado and iniciou_jornada:
            estado_operacional = "Saída antecipada"
        elif em_turno and deslogado:
            estado_operacional = "Deslogado"
        elif disponivel:
            if alerta_intervalo_nao_iniciado:
                start_label = _minutes_to_hhmm(intervalo_bounds[0]) if intervalo_bounds else "—"
                estado_operacional = f"Intervalo previsto para {start_label} não iniciado"
                motivo_operacional = "Intervalo não iniciado"
                acao_operacional = "Orientar o agente a iniciar o intervalo previsto."
            elif em_turno:
                estado_operacional = "Disponível"
        elif alerta_intervalo:
            end_label = _minutes_to_hhmm(intervalo_bounds[1]) if intervalo_bounds else "—"
            estado_operacional = (
                f"Intervalo excedido em {intervalo_atraso_minutos} min — "
                f"fim previsto {end_label}"
            )
            motivo_operacional = "Intervalo excedido"
            acao_operacional = "Orientar o retorno do agente à operação."
        elif alerta_intervalo_fora_escala:
            start_label = _minutes_to_hhmm(intervalo_bounds[0]) if intervalo_bounds else "—"
            estado_operacional = (
                f"Intervalo iniciado antes do horário previsto ({start_label})"
            )
            motivo_operacional = "Intervalo fora do horário"
            acao_operacional = "Validar com o agente o início antecipado do intervalo."
        elif status_id == SYSTEMIC_ISSUES_STATUS_ID:
            if row.last_change:
                systemic_minutes = _elapsed_minutes_since(row.last_change, reference_time)
                estado_operacional = f"Problema sistêmico em andamento há {systemic_minutes} min"
            else:
                estado_operacional = "Problema sistêmico em andamento"
            motivo_operacional = status_name or "Problemas sistêmicos"
            acao_operacional = "Validar o impacto sistêmico e acompanhar a normalização."
        elif status_id == PESSOAL_STATUS_ID and pessoal_duracao_minutos is not None:
            estado_operacional = f"Status Pessoal há {pessoal_duracao_minutos} min"
            motivo_operacional = "Status Pessoal"
            acao_operacional = "Verificar com o agente a necessidade de permanência no status."
        elif alerta_ocorrencia_excedente and ocorrencia_excesso_minutos is not None:
            label = status_name or f"Status {status_id}"
            if ocorrencia_situacao == "pendente":
                estado_operacional = f"{label} com ocorrência pendente de aprovação"
                motivo_operacional = "Ocorrência pendente de aprovação"
                acao_operacional = "Avaliar a ocorrência pendente de aprovação."
            elif ocorrencia_situacao == "recusada":
                estado_operacional = f"{label} com ocorrência recusada"
                motivo_operacional = "Ocorrência recusada"
                acao_operacional = "Validar a justificativa e regularizar a ocorrência."
            elif ocorrencia_situacao == "ausente":
                estado_operacional = f"{label} sem ocorrência cadastrada"
                motivo_operacional = "Ocorrência não cadastrada"
                acao_operacional = "Cadastrar ou regularizar a ocorrência do agente."
            else:
                estado_operacional = (
                    f"{label} excedeu o tempo da ocorrência em "
                    f"{ocorrencia_excesso_minutos} min"
                )
                motivo_operacional = "Tempo de ocorrência excedido"
                acao_operacional = "Orientar o retorno ou solicitar extensão da ocorrência."
        elif em_turno and ausente_agora:
            estado_operacional = "Ausente"
        elif em_turno:
            estado_operacional = "Sem status"

        if not agente_ativo:
            prioridade_operacional = "—"
        elif alerta_intervalo:
            prioridade_operacional = alerta_intervalo
        elif alerta_sistemico:
            prioridade_operacional = "Alta"
        elif alerta_intervalo_nao_iniciado or alerta_intervalo_fora_escala:
            prioridade_operacional = "Alta"
        elif alerta_pessoal:
            prioridade_operacional = alerta_pessoal
        elif alerta_ocorrencia_excedente and ocorrencia_excesso_minutos is not None:
            prioridade_operacional = _occurrence_excess_priority(
                ocorrencia_excesso_minutos
            )
        elif em_turno:
            prioridade_operacional = "—"

    estado: str | None = estado_operacional
    prioridade: str | None = prioridade_operacional
    motivo: str | None = motivo_operacional
    acao_recomendada: str | None = acao_operacional
    informativo = False
    if alerta_saida_antecipada:
        prioridade = "Info"
        informativo = True
        estado = (
            f"Deslogado antes do fim da escala ({hora_fim})"
            if hora_fim
            else "Saída antecipada"
        )
        motivo = "Saída antecipada"
        acao_recomendada = "Confirmar com o agente o encerramento antecipado da jornada."
    elif (
        is_time_range_schedule(work_schedule)
        and hora_extra_dia
        and escala_divergente_informativo
    ):
        prioridade = "Info"
        informativo = True
        estado = estado_operacional or "Escala divergente do horário base"
        motivo = "Escala divergente"
        acao_recomendada = "Verificar se a diferença corresponde a uma troca de escala planejada."
    elif (
        is_time_range_schedule(work_schedule)
        and hora_extra_dia
        and hora_extra
        and not precisa_acompanhamento_dia
    ):
        prioridade = "Info"
        informativo = True
        estado = estado_operacional or "Hora extra"
        motivo = "Hora extra"
        acao_recomendada = "Acompanhar a jornada adicional do agente."
    elif alerta_login_fora_escala:
        prioridade = "Info"
        informativo = True
        estado = f"Logado fora da escala — horário previsto {normalize_time_schedule(work_schedule)}"
        motivo = "Login fora da escala"
        acao_recomendada = "Validar o acesso fora do horário planejado."
    elif alerta_status_sem_inicio:
        prioridade = "Info"
        informativo = True
        estado = "Horário de início do status indisponível"
        motivo = "Integridade do status"
        acao_recomendada = "Regularizar o horário de início do status Pessoal."
    elif alerta_config_escala_invalida:
        prioridade = "Info"
        informativo = True
        estado = "Escala inválida ou não configurada"
        motivo = "Configuração de escala"
        acao_recomendada = "Cadastrar ou corrigir a escala do agente."
    elif alerta_config_intervalo_ausente and not precisa_acompanhamento_dia:
        prioridade = "Info"
        informativo = True
        estado = "Horário de intervalo não configurado"
        motivo = "Configuração de intervalo"
        acao_recomendada = "Cadastrar o horário de intervalo aplicável ao agente."

    informativo_dia = informativo
    informativo_agora = informativo and (em_turno or alerta_login_fora_escala)

    occurrence_source = None
    occurrence_candidates = occs_for_status or all_occs_for_status
    if occurrence_candidates:
        occurrence_source = max(
            occurrence_candidates,
            key=lambda item: (item.created_at, str(item.id)),
        )

    alert_type = ""
    source_type = "status"
    source_id = str(status_id or "")
    if precisa_acompanhamento_dia:
        if alerta_intervalo_nao_iniciado:
            alert_type = "break_not_started"
        elif alerta_intervalo:
            alert_type = "break_exceeded"
        elif alerta_intervalo_fora_escala:
            alert_type = "break_outside_schedule"
        elif alerta_sistemico:
            alert_type = "systemic_issue"
        elif alerta_pessoal:
            alert_type = "personal_status_exceeded"
        elif alerta_ocorrencia_excedente:
            source_type = "operational_occurrence"
            source_id = str(occurrence_source.id) if occurrence_source else str(status_id or "")
            alert_type = {
                "pendente": "occurrence_pending",
                "recusada": "occurrence_rejected",
                "ausente": "occurrence_missing",
            }.get(ocorrencia_situacao or "", "occurrence_exceeded")
    elif informativo_dia:
        if alerta_saida_antecipada:
            alert_type = "early_logout"
        elif alerta_login_fora_escala:
            alert_type = "login_outside_schedule"
        elif alerta_status_sem_inicio:
            alert_type = "status_start_missing"
        elif alerta_config_escala_invalida:
            alert_type = "invalid_schedule"
        elif alerta_config_intervalo_ausente:
            alert_type = "missing_break_configuration"
        elif hora_extra_dia:
            alert_type = "overtime_information"

    return {
        "id": str(row.id),
        "agent_id": row.agent_id,
        "user_lan_id": row.agent.user_lan_id,
        "full_name": row.full_name or row.agent.full_name,
        "leader_name": row.leader.full_name if row.leader else "",
        "leader_lan_id": row.leader_lan_id,
        "sector": row.sector,
        "location": row.location,
        "job_activity": row.job_activity,
        "work_schedule": row.work_schedule,
        "journey": row.journey,
        "status": status_id,
        "status_name": status_name,
        "status_color": row.status.color if row.status else "",
        "overtime": row.overtime,
        "start_of_work": timezone.localtime(row.start_of_work).isoformat()
        if row.start_of_work
        else None,
        "week_break": row.week_break,
        "weekend_break": row.weekend_break,
        "hora_inicio": hora_inicio or None,
        "hora_fim": hora_fim or None,
        "disponivel": disponivel,
        "deslogado": deslogado,
        "presente": presente,
        "em_pausa": em_pausa,
        "ausente": ausente,
        "ausente_agora": ausente_agora,
        "ausente_dia": ausente_dia,
        "hora_extra": hora_extra,
        "hora_extra_agora": hora_extra_agora,
        "hora_extra_dia": hora_extra_dia,
        "escala_divergente": escala_divergente,
        "em_turno": em_turno,
        "capacidade_operacional": disponivel,
        "estado": estado,
        "prioridade": prioridade,
        "motivo": motivo,
        "acao_recomendada": acao_recomendada,
        "alerta_intervalo": bool(alerta_intervalo),
        "intervalo_atraso_minutos": intervalo_atraso_minutos,
        "alerta_intervalo_nao_iniciado": alerta_intervalo_nao_iniciado,
        "alerta_intervalo_fora_escala": alerta_intervalo_fora_escala,
        "alerta_sistemico": alerta_sistemico,
        "alerta_pessoal": bool(alerta_pessoal),
        "alerta_saida_antecipada": alerta_saida_antecipada,
        "alerta_login_fora_escala": alerta_login_fora_escala,
        "alerta_ocorrencia_excedente": alerta_ocorrencia_excedente,
        "ocorrencia_excesso_minutos": ocorrencia_excesso_minutos,
        "ocorrencia_situacao": ocorrencia_situacao,
        "pessoal_duracao_minutos": pessoal_duracao_minutos,
        "alerta_config_escala_invalida": alerta_config_escala_invalida,
        "alerta_config_intervalo_ausente": alerta_config_intervalo_ausente,
        "alerta_status_sem_inicio": alerta_status_sem_inicio,
        "informativo": informativo,
        "informativo_agora": informativo_agora,
        "informativo_dia": informativo_dia,
        "precisa_acompanhamento": precisa_acompanhamento,
        "precisa_acompanhamento_dia": precisa_acompanhamento_dia,
        "alert_type": alert_type,
        "alert_source_type": source_type if alert_type else "",
        "alert_source_id": source_id if alert_type else "",
    }


def _executive_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "escalados": len(rows),
        "presentes": sum(1 for r in rows if r["presente"]),
        "ausentes": sum(1 for r in rows if r["ausente"]),
        "em_pausa": sum(1 for r in rows if r["em_pausa"]),
        "deslogados": sum(1 for r in rows if r["deslogado"]),
        "hora_extra": sum(1 for r in rows if r["hora_extra"]),
        "disponiveis": sum(1 for r in rows if r["capacidade_operacional"]),
    }


def _capacity_day(rows: list[dict[str, Any]]) -> dict[str, Any]:
    presentes = sum(1 for r in rows if r["presente"])
    ausentes = sum(1 for r in rows if r["ausente"])
    escalados = len(rows)
    center_pct = round((presentes / escalados) * 100) if escalados else 0
    return {
        "scheduled": escalados,
        "present": presentes,
        "absent": ausentes,
        "center_pct": center_pct,
        "center_label": "presentes",
        "slices": [
            {
                "label": "Escalados",
                "count": escalados,
                "color": "#e5e7eb",
                "legend_only": True,
            },
            {
                "label": "Presentes",
                "count": presentes,
                "chart_count": presentes,
                "color": "#1e3a5f",
            },
            {
                "label": "Ausentes",
                "count": ausentes,
                "chart_count": ausentes,
                "color": "#e5e7eb",
                "info_only": True,
            },
        ],
    }


def _capacity_now(rows: list[dict[str, Any]]) -> dict[str, Any]:
    disponiveis = sum(1 for r in rows if r["capacidade_operacional"])
    em_pausa = sum(1 for r in rows if r["em_pausa"])
    active_total = disponiveis + em_pausa
    center_pct = round((disponiveis / active_total) * 100) if active_total else 0
    return {
        "available": disponiveis,
        "on_pause": em_pausa,
        "center_pct": center_pct,
        "center_label": "disponíveis",
        "slices": [
            {"label": "Disponíveis", "count": disponiveis, "color": "#1e3a5f"},
            {"label": "Em pausa", "count": em_pausa, "color": "#e5e7eb"},
        ],
    }


def _aggregate_absent(rows: list[dict[str, Any]], key_field: str) -> list[dict[str, Any]]:
    buckets: dict[str, int] = defaultdict(int)
    for row in rows:
        label = (row.get(key_field) or "—").strip() or "—"
        if row["ausente_dia"]:
            buckets[label] += 1
    return [
        {"label": label, "ausentes": count}
        for label, count in sorted(buckets.items(), key=lambda x: (-x[1], x[0].lower()))
    ]


def _aggregate_activity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "escalados": 0,
            "disponiveis": 0,
            "ausentes": 0,
            "em_pausa": 0,
        }
    )
    for row in rows:
        label = (row.get("job_activity") or "—").strip() or "—"
        bucket = buckets[label]
        bucket["escalados"] += 1
        if row["capacidade_operacional"]:
            bucket["disponiveis"] += 1
        if row["ausente_dia"]:
            bucket["ausentes"] += 1
        if row["em_pausa"]:
            bucket["em_pausa"] += 1

    return [
        {"label": label, **bucket}
        for label, bucket in sorted(buckets.items(), key=lambda x: x[0].lower())
    ]


def _matrix_location_sector(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"escalados": 0, "disponiveis": 0}
    )
    for row in rows:
        location = (row.get("location") or "—").strip() or "—"
        sector = (row.get("sector") or "—").strip() or "—"
        key = (location, sector)
        buckets[key]["escalados"] += 1
        if row["capacidade_operacional"]:
            buckets[key]["disponiveis"] += 1

    result = []
    for (location, sector), bucket in sorted(buckets.items()):
        result.append(
            {
                "location": location,
                "sector": sector,
                "escalados": bucket["escalados"],
                "disponiveis": bucket["disponiveis"],
                "texto_celula": f"{bucket['disponiveis']} / {bucket['escalados']}",
            }
        )
    return result


def _filter_options(base_qs, rows: list[dict[str, Any]]) -> dict[str, Any]:
    leaders_map: dict[str, str] = {}
    for row in rows:
        lan = (row.get("leader_lan_id") or "").strip().lower()
        name = (row.get("leader_name") or "").strip()
        if lan and name:
            leaders_map[lan] = name

    return {
        "full_names": sorted({r["full_name"] for r in rows if r.get("full_name")}),
        "job_activities": sorted({r["job_activity"] for r in rows if r.get("job_activity")}),
        "work_schedules": sorted({r["work_schedule"] for r in rows if r.get("work_schedule")}),
        "locations": sorted({r["location"] for r in rows if r.get("location")}),
        "sectors": sorted({r["sector"] for r in rows if r.get("sector")}),
        "statuses": [
            {"id": row["status_id"], "name": row["status__name"]}
            for row in base_qs.exclude(status_id__isnull=True)
            .values("status_id", "status__name")
            .distinct()
            .order_by("status__name")
            if row["status_id"] is not None
        ],
        "leaders": [
            {"lan_id": lan, "name": name}
            for lan, name in sorted(leaders_map.items(), key=lambda x: x[1].lower())
        ],
    }


def _sort_by_prioridade(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            PRIORIDADE_ORDER.get(r.get("prioridade") or "—", 9),
            (r.get("full_name") or "").lower(),
        ),
    )


def _acompanhamento_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _sort_by_prioridade(
        [r for r in rows if r["precisa_acompanhamento"] or r.get("informativo")]
    )


def evaluate_operational_dashboard_rows(target_date, params, reference_time=None) -> list[dict[str, Any]]:
    """Calcula as linhas uma única vez para o painel e para o coletor histórico."""
    reference_time = reference_time or timezone.now()
    base_qs = ScheduleToday.objects.filter(date=target_date).select_related(
        "agent", "status", "leader"
    )
    filtered_qs = list(filter_schedule_today(base_qs, params))
    occurrence_ctx = _load_occurrence_alert_context(
        target_date,
        [row.agent_id for row in filtered_qs],
    )
    return [_enrich_row(row, reference_time, occurrence_ctx) for row in filtered_qs]


def build_operational_dashboard(target_date, params, reference_time=None) -> dict[str, Any]:
    base_qs = ScheduleToday.objects.filter(date=target_date).select_related(
        "agent", "status", "leader"
    )
    rows = evaluate_operational_dashboard_rows(target_date, params, reference_time)

    executive = _executive_summary(rows)
    lists = {
        "acompanhamento": _acompanhamento_rows(rows),
        "alertas": _sort_by_prioridade([r for r in rows if r["precisa_acompanhamento"]]),
        "alertas_agora": _sort_by_prioridade(
            [r for r in rows if r["precisa_acompanhamento"]]
        ),
        "alertas_dia": _sort_by_prioridade(
            [r for r in rows if r["precisa_acompanhamento_dia"]]
        ),
        "informativos": _sort_by_prioridade([r for r in rows if r.get("informativo")]),
        "informativos_agora": _sort_by_prioridade(
            [r for r in rows if r.get("informativo_agora")]
        ),
        "informativos_dia": _sort_by_prioridade(
            [r for r in rows if r.get("informativo_dia")]
        ),
        "ausentes_agora": sorted(
            [r for r in rows if r["ausente_agora"]],
            key=lambda r: r.get("hora_inicio") or "99:99",
        ),
        "ausentes_dia": sorted(
            [r for r in rows if r["ausente_dia"]],
            key=lambda r: r.get("hora_inicio") or "99:99",
        ),
        "ausentes": sorted(
            [r for r in rows if r["ausente_dia"]],
            key=lambda r: r.get("hora_inicio") or "99:99",
        ),
        "hora_extra_agora": sorted(
            [r for r in rows if r["hora_extra_agora"]],
            key=lambda r: r.get("hora_inicio") or "99:99",
        ),
        "hora_extra_dia": sorted(
            [r for r in rows if r["hora_extra_dia"]],
            key=lambda r: r.get("hora_inicio") or "99:99",
        ),
        "hora_extra": sorted(
            [r for r in rows if r["hora_extra_dia"]],
            key=lambda r: r.get("hora_inicio") or "99:99",
        ),
        "pausas": sorted(
            [r for r in rows if r["em_pausa"]],
            key=lambda r: r.get("start_of_work") or "",
            reverse=True,
        ),
        "deslogados": [r for r in rows if r["deslogado"]],
    }

    last_updated = base_qs.aggregate(ts=Max("updated_at"))["ts"]

    return {
        "date": str(target_date),
        "last_updated": timezone.localtime(last_updated).isoformat() if last_updated else None,
        "executive": executive,
        "capacity_day": _capacity_day(rows),
        "capacity_now": _capacity_now(rows),
        "rows": rows,
        "by_location": _aggregate_absent(rows, "location"),
        "by_sector": _aggregate_absent(rows, "sector"),
        "by_work_schedule": _aggregate_absent(rows, "work_schedule"),
        "by_activity": _aggregate_activity(rows),
        "matrix_location_sector": _matrix_location_sector(rows),
        "lists": lists,
        "filter_options": _filter_options(base_qs, rows),
    }
