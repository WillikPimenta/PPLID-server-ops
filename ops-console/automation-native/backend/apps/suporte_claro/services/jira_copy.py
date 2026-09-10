# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, timedelta

from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.services.chamados_externos import queryset_sem_chamado_externo
from apps.suporte_claro.services.protocolos import display_titulo, serialize_protocolos
from apps.suporte_claro.services.emails import serialize_emails


def resolve_jira_profile_key(user) -> str:
    """Planejamento → PPLID; Processos (HC) → Processos e Riscos."""
    try:
        from apps.escala_flex.services.permissions import build_operational_profile

        profile = build_operational_profile(user)
        if profile and profile.team:
            team = profile.team.strip().lower()
            if "processo" in team:
                return "processos"
    except Exception:
        pass
    return "planejamento"


def profile_label(key: str) -> str:
    labels = {
        "planejamento": "Planejamento (PPLID)",
        "processos": "Processos e Riscos",
    }
    return labels.get(key, key)


def _week_bounds(reference: date | None = None) -> tuple[date, date]:
    ref = reference or timezone.localdate()
    start = ref - timedelta(days=ref.weekday())
    return start, ref


def _fmt_dt(value) -> str:
    if not value:
        return "—"
    local = timezone.localtime(value) if timezone.is_aware(value) else value
    return local.strftime("%d/%m/%Y %H:%M")


def resolve_retorno_at(registro: SuporteClaroRegistro):
    """Momento do retorno/conclusão: campo editável, senão histórico de avaliação."""
    if not (registro.avaliacao or "").strip():
        return None
    stored = getattr(registro, "retorno_at", None)
    if stored is not None:
        return stored
    entry = (
        registro.historico.filter(field_name="avaliacao")
        .exclude(new_value="")
        .order_by("created_at")
        .first()
    )
    if entry:
        return entry.created_at
    return registro.updated_at


def _format_sla_labels(minutes: int) -> tuple[str, str | None]:
    """Retorna rótulo compacto (ex.: 1d 18min) e total em horas (ex.: 42h 18min)."""
    total_hours, mins = divmod(minutes, 60)
    days, rem_minutes = divmod(minutes, 24 * 60)

    if days >= 1:
        if rem_minutes >= 60:
            rem_h, rem_m = divmod(rem_minutes, 60)
            compact = f"{days}d {rem_h}h {rem_m:02d}min"
        elif rem_minutes:
            compact = f"{days}d {rem_minutes}min"
        else:
            compact = f"{days}d"
        detail = f"{total_hours}h {mins:02d}min" if total_hours else f"{mins} min"
        return compact, detail

    if total_hours >= 1:
        compact = f"{total_hours}h {mins:02d}min"
        return compact, None

    compact = f"{mins} min" if mins != 1 else "1 min"
    return compact, None


def compute_sla_info(registro: SuporteClaroRegistro) -> dict:
    """Calcula SLA de atendimento: recebimento → retorno do suporte.

    A data inicial (`received_at` / “Recebido em”) é a âncora do SLA e pode ser
    corrigida via PATCH em chamados retroativos/importados; o intervalo é
    recalculado a partir da nova data.
    """
    received = registro.received_at
    retorno_at = resolve_retorno_at(registro)
    if not received:
        return {
            "retorno_at": None,
            "sla_minutes": None,
            "sla_label": None,
            "sla_label_total": None,
            "sla_pending": False,
        }
    if not retorno_at:
        return {
            "retorno_at": None,
            "sla_minutes": None,
            "sla_label": None,
            "sla_label_total": None,
            "sla_pending": True,
        }
    delta = retorno_at - received
    minutes = max(0, int(delta.total_seconds() // 60))
    sla_label, sla_label_total = _format_sla_labels(minutes)
    local_retorno = timezone.localtime(retorno_at) if timezone.is_aware(retorno_at) else retorno_at
    return {
        "retorno_at": local_retorno.isoformat(),
        "sla_minutes": minutes,
        "sla_label": sla_label,
        "sla_label_total": sla_label_total,
        "sla_pending": False,
    }


def build_registro_portal_url(registro_id: int) -> str:
    base = (settings.PPLID_FRONTEND_URL or "").rstrip("/")
    if not base:
        return f"/processos/suporte-claro/kanban?demanda={registro_id}"
    return f"{base}/processos/suporte-claro/kanban?demanda={registro_id}"


def is_registro_eligible_for_jira_formalization(registro: SuporteClaroRegistro) -> bool:
    """Concluída, com retorno registrado e sem chamado externo vinculado."""
    if registro.status != SuporteClaroRegistro.STATUS_CONCLUIDO:
        return False
    if not (registro.avaliacao or "").strip():
        return False
    if (registro.chamado_sistema or "").strip():
        return False
    if registro.chamados_externos.exists():
        return False
    return True


def jira_formalization_eligible_queryset(qs: QuerySet) -> QuerySet:
    qs = queryset_sem_chamado_externo(qs)
    return qs.filter(
        status=SuporteClaroRegistro.STATUS_CONCLUIDO,
    ).exclude(Q(avaliacao="") | Q(avaliacao__isnull=True))


def validate_registros_for_jira_formalization(registros) -> str | None:
    """Retorna mensagem de erro se algum registro não for elegível."""
    for registro in registros:
        if not is_registro_eligible_for_jira_formalization(registro):
            return (
                f"Demanda #{registro.id} ({registro.protocolo}) precisa estar concluída "
                "com retorno registrado e sem chamado externo vinculado."
            )
    return None


def build_weekly_jira_copy(
    user,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Monta resumo/descrição da formalização semanal."""
    if date_from is None or date_to is None:
        date_from, date_to = _week_bounds()

    start_dt = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))

    qs = (
        SuporteClaroRegistro.objects.filter(
            created_by=user,
            received_at__gte=start_dt,
            received_at__lte=end_dt,
        )
        .order_by("received_at", "id")
    )
    items = list(qs)
    period = f"{date_from.strftime('%d/%m/%Y')} a {date_to.strftime('%d/%m/%Y')}"
    summary = f"Suporte Claro — formalização semanal ({period})"

    lines = [
        "Formalização dos casos atendidos no Suporte Claro.",
        "",
        f"Período: {period}",
        f"Cadastrado por: {user.get_full_name() or user.username}",
        f"Total de demandas: {len(items)}",
        "",
    ]
    if not items:
        lines.append("Nenhuma demanda registrada no período.")
    else:
        lines.append("Demandas:")
        for reg in items:
            lines.append(
                f"- #{reg.id} | {display_titulo(reg)} | Protocolo {reg.protocolo} | "
                f"Status: {reg.get_status_display()} | Recebido: {_fmt_dt(reg.received_at)}"
            )
            if reg.irregularidade.strip():
                lines.append(f"  Irregularidade: {reg.irregularidade.strip()[:500]}")
            if reg.avaliacao.strip():
                lines.append(f"  Retorno: {reg.avaliacao.strip()[:500]}")
        lines.append("")
        lines.append("Casos formalizados via portal Suporte Claro.")

    return {
        "summary": summary,
        "description": "\n".join(lines),
        "count": len(items),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }


def build_registro_jira_copy(registro: SuporteClaroRegistro) -> dict:
    """Monta resumo/descrição diária — um chamado Jira por atendimento."""
    received = registro.received_at
    received_local = timezone.localtime(received) if received and timezone.is_aware(received) else received
    received_date = received_local.strftime("%d/%m/%Y") if received_local else "—"
    formalized_at = timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M")

    created_by = ""
    if registro.created_by:
        created_by = registro.created_by.get_full_name() or registro.created_by.username

    titulo = display_titulo(registro)
    summary = f"[Suporte Claro] {titulo} — {received_date}"
    if len(summary) > 255:
        summary = summary[:252] + "..."

    protocolos = serialize_protocolos(registro)
    protocolos_lines = []
    for item in protocolos:
        line = f"- {item['numero']}"
        if item.get("comentario"):
            line += f" — {item['comentario']}"
        protocolos_lines.append(line)

    emails = serialize_emails(registro)
    solicitante = ", ".join(item["endereco"] for item in emails) if emails else (registro.sent_by or "—")

    lines = [
        "Formalização diária — Suporte Claro",
        "",
        "── Atendimento ──",
        f"Titulo: {titulo}",
        f"Protocolo(s): {registro.protocolo}",
        f"ID portal: #{registro.id}",
        f"Recebido em: {_fmt_dt(registro.received_at)}",
        f"Canal: {registro.get_origem_display() if registro.origem else '—'}",
        f"Solicitante: {solicitante}",
        f"Status portal: {registro.get_status_display()}",
        "",
    ]
    if emails and len(emails) > 1:
        lines.extend(["── E-mails ──", *[f"- {item['endereco']}" for item in emails], ""])
    if protocolos_lines:
        lines.extend(["── Protocolos ──", *protocolos_lines, ""])
    sla = compute_sla_info(registro)
    retorno_dt = resolve_retorno_at(registro)
    if sla["sla_pending"]:
        lines.extend(
            [
                "── SLA ──",
                "Retorno do suporte: pendente no portal",
                "",
            ]
        )
    elif retorno_dt:
        lines.extend(
            [
                "── SLA ──",
                f"Retorno do suporte: {_fmt_dt(retorno_dt)}",
                f"Tempo de atendimento: {sla['sla_label']}"
                + (f" ({sla['sla_label_total']})" if sla.get("sla_label_total") else ""),
                "",
            ]
        )
    portal_url = build_registro_portal_url(registro.id)
    lines.extend(
        [
            "── Demanda ──",
            registro.irregularidade.strip() or "—",
            "",
            "── Retorno ──",
            registro.avaliacao.strip() or "Pendente no portal.",
            "",
            "── Cadastro ──",
            f"Registrado por: {created_by or '—'}",
            f"Formalizado em: {formalized_at}",
            f"Link portal: {portal_url}",
            "",
            "---",
            "Origem: Portal Suporte Claro",
        ]
    )
    return {
        "summary": summary,
        "description": "\n".join(lines),
        "registro_id": registro.id,
        "titulo": titulo,
        "protocolo": registro.protocolo,
        "portal_url": portal_url,
        **sla,
    }


def build_batch_jira_copy(registros) -> list[dict]:
    """Monta lista de copies para formalização em lote (1 login Okta)."""
    items: list[dict] = []
    for registro in registros:
        copy = build_registro_jira_copy(registro)
        copy["protocolo"] = registro.protocolo
        items.append(copy)
    return items


def resolve_batch_registros(registro_ids: list[int] | None = None, reference: date | None = None):
    """Registros elegíveis para lote: concluídos, com retorno, sem chamado externo."""
    if registro_ids:
        qs = jira_formalization_eligible_queryset(
            SuporteClaroRegistro.objects.filter(pk__in=registro_ids)
        )
        return list(qs.select_related("created_by").order_by("received_at", "id"))
    return list(jira_pendentes_queryset(reference))


def jira_pendentes_all_queryset():
    """Todas as demandas elegíveis para formalização (qualquer data)."""
    qs = SuporteClaroRegistro.objects.all()
    return jira_formalization_eligible_queryset(qs).select_related("created_by").order_by(
        "-received_at", "-id"
    )


def jira_pendentes_queryset(reference: date | None = None):
    """Demandas do dia concluídas com retorno, sem chamado externo vinculado."""
    ref = reference or timezone.localdate()
    start_dt = timezone.make_aware(datetime.combine(ref, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(ref, datetime.max.time()))
    qs = SuporteClaroRegistro.objects.filter(
        received_at__gte=start_dt,
        received_at__lte=end_dt,
    )
    return jira_formalization_eligible_queryset(qs).select_related("created_by").order_by(
        "received_at", "id"
    )


def jira_pendentes_today_count(reference: date | None = None) -> int:
    """Quantidade de pendentes elegíveis com received_at no dia de referência."""
    return jira_pendentes_queryset(reference).count()
