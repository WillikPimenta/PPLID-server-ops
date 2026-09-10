# -*- coding: utf-8 -*-
"""Analytics MoM de incidentes — agrupa por motivo (tipo + título) e vínculos."""
from __future__ import annotations

import calendar
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Iterable

from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone

from apps.suporte_claro.models import (
    SuporteClaroChamadoExterno,
    SuporteClaroRegistro,
    SuporteClaroVinculoIncidente,
)

TIPO_LABELS = dict(SuporteClaroRegistro.TIPO_INCIDENTE_CHOICES)
STATUS_LABELS = dict(SuporteClaroRegistro.STATUS_CHOICES)

_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_SPACES = re.compile(r"\s+")
_TRAVAMENTO_WORDS = re.compile(r"\b(?:travamento|travamentos|travando|travou|trava)\b")


def canonical_incident_type(value: str | None) -> str:
    """Consolida categorias equivalentes sem alterar o registro persistido."""
    tipo = (value or "").strip()
    if tipo == SuporteClaroRegistro.TIPO_INCIDENTE_TRAVAMENTO:
        return SuporteClaroRegistro.TIPO_INCIDENTE_LENTIDAO
    if not tipo:
        return SuporteClaroRegistro.TIPO_INCIDENTE_OUTRO
    return tipo


def normalize_motivo_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", (value or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _NON_ALNUM.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def canonical_motivo_text(value: str) -> str:
    """Normaliza sinônimos usados pela operação para o mesmo motivo."""
    text = normalize_motivo_text(value)
    return _SPACES.sub(" ", _TRAVAMENTO_WORDS.sub("lentidao", text)).strip()


def motivo_key_for(registro: SuporteClaroRegistro) -> str:
    tipo = canonical_incident_type(registro.tipo_incidente)
    titulo = canonical_motivo_text(registro.titulo or "")
    if not titulo:
        titulo = canonical_motivo_text((registro.irregularidade or "")[:120])
    if not titulo:
        titulo = f"id-{registro.id}"
    return f"{tipo}::{titulo}"


def month_bounds(ref: date) -> tuple[date, date, date, date]:
    """Retorna (cur_from, cur_to, prev_from, prev_to) inclusive."""
    cur_from = ref.replace(day=1)
    last_day = calendar.monthrange(ref.year, ref.month)[1]
    cur_to = ref.replace(day=last_day)

    if cur_from.month == 1:
        prev_anchor = cur_from.replace(year=cur_from.year - 1, month=12, day=1)
    else:
        prev_anchor = cur_from.replace(month=cur_from.month - 1, day=1)
    prev_last = calendar.monthrange(prev_anchor.year, prev_anchor.month)[1]
    prev_from = prev_anchor
    prev_to = prev_anchor.replace(day=prev_last)
    return cur_from, cur_to, prev_from, prev_to


def shift_month(ref: date, offset: int) -> date:
    """Move para o primeiro dia de outro mês."""
    absolute = ref.year * 12 + (ref.month - 1) + offset
    year, month_zero = divmod(absolute, 12)
    return date(year, month_zero + 1, 1)


def month_sequence(ref: date, months: int) -> list[date]:
    count = max(2, min(int(months or 6), 12))
    return [shift_month(ref.replace(day=1), offset) for offset in range(-(count - 1), 1)]


def parse_month_param(raw: str | None, fallback: date | None = None) -> date:
    """Aceita YYYY-MM; retorna o 1º dia do mês. Default = mês atual (local)."""
    text = (raw or "").strip()
    if text:
        try:
            year_s, month_s = text.split("-", 1)
            year, month = int(year_s), int(month_s)
            if 1 <= month <= 12:
                return date(year, month, 1)
        except (TypeError, ValueError):
            pass
    base = fallback or timezone.localdate()
    return base.replace(day=1)


def _aware_range(day_from: date, day_to: date) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day_from, time.min), tz)
    end = timezone.make_aware(datetime.combine(day_to, time.max), tz)
    return start, end


@dataclass
class MotivoIncidenteItem:
    registro_id: int
    titulo: str
    status: str
    status_label: str
    received_at: datetime | None
    chamado_codigo: str = ""
    tratado_por: str = ""
    chamados_codigos: list[str] = field(default_factory=list)
    service_now_codigos: list[str] = field(default_factory=list)
    tipo: str = ""
    tipo_label: str = ""


@dataclass
class MotivoBucket:
    key: str
    label: str
    tipo: str
    tipo_label: str
    current_count: int = 0
    previous_count: int = 0
    current_items: list[MotivoIncidenteItem] = field(default_factory=list)
    previous_items: list[MotivoIncidenteItem] = field(default_factory=list)
    monthly_counts: list[int] = field(default_factory=list)

    @property
    def delta(self) -> int:
        return self.current_count - self.previous_count

    @property
    def delta_pct(self) -> float | None:
        if self.previous_count <= 0:
            return None if self.current_count == 0 else None
        return ((self.current_count - self.previous_count) / self.previous_count) * 100.0

    @property
    def is_recurring(self) -> bool:
        return self.current_count > 0 and self.previous_count > 0

    @property
    def is_new(self) -> bool:
        return self.current_count > 0 and self.previous_count == 0

    @property
    def is_resolved_only_prev(self) -> bool:
        return self.current_count == 0 and self.previous_count > 0

    @property
    def history_total(self) -> int:
        return sum(self.monthly_counts)


@dataclass
class MonthlyIncidentPoint:
    month: date
    total: int = 0

    @property
    def label(self) -> str:
        return self.month.strftime("%m/%y")


@dataclass
class IncidentesMomReport:
    ref_month: date
    current_from: date
    current_to: date
    previous_from: date
    previous_to: date
    current_total: int = 0
    previous_total: int = 0
    recurring_motivos: int = 0
    new_motivos: int = 0
    closed_motivos: int = 0
    buckets: list[MotivoBucket] = field(default_factory=list)
    filters_applied: list[str] = field(default_factory=list)
    history_months: int = 2
    history_points: list[MonthlyIncidentPoint] = field(default_factory=list)
    history_items: list[MotivoIncidenteItem] = field(default_factory=list)

    @property
    def delta_total(self) -> int:
        return self.current_total - self.previous_total

    @property
    def current_label(self) -> str:
        return self.current_from.strftime("%m/%Y")

    @property
    def previous_label(self) -> str:
        return self.previous_from.strftime("%m/%Y")

    @property
    def all_items(self) -> list[MotivoIncidenteItem]:
        items = list(self.history_items)
        if not items:
            items = [item for bucket in self.buckets for item in bucket.current_items]
            items += [item for bucket in self.buckets for item in bucket.previous_items]
        return sorted(
            items,
            key=lambda item: (item.received_at or timezone.now(), item.registro_id),
            reverse=True,
        )

    @property
    def service_now_count(self) -> int:
        return sum(
            1
            for item in self.all_items
            if item.service_now_codigos
        )

    @property
    def without_service_now_count(self) -> int:
        return len(self.all_items) - self.service_now_count

    @property
    def history_labels(self) -> list[str]:
        return [point.label for point in self.history_points]

    @property
    def history_totals(self) -> list[int]:
        return [point.total for point in self.history_points]

    @property
    def top_history_buckets(self) -> list[MotivoBucket]:
        return sorted(
            (bucket for bucket in self.buckets if bucket.history_total > 0),
            key=lambda bucket: (-bucket.history_total, bucket.label.lower()),
        )


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def add(self, x: int) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: int) -> int:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _item_from_registro(reg: SuporteClaroRegistro) -> MotivoIncidenteItem:
    chamado_codigo = ""
    tratado_por = ""
    chamados = list(getattr(reg, "_prefetched_objects_cache", {}).get("chamados_externos") or [])
    if not chamados:
        chamados = list(reg.chamados_externos.all())
    chamados_codigos = list(
        dict.fromkeys((chamado.codigo or "").strip() for chamado in chamados if chamado.codigo)
    )
    service_now_codigos = list(
        dict.fromkeys(
            (chamado.codigo or "").strip()
            for chamado in chamados
            if chamado.codigo and chamado.sistema == SuporteClaroRegistro.CHAMADO_SERVICE
        )
    )
    if chamados:
        # Preferência ServiceNow; senão o primeiro.
        preferred = next(
            (c for c in chamados if c.sistema == SuporteClaroRegistro.CHAMADO_SERVICE),
            chamados[0],
        )
        chamado_codigo = (preferred.codigo or "").strip()
        tratado_por = (preferred.tratado_por or "").strip()
    if not chamado_codigo:
        chamado_codigo = (reg.chamado_codigo or "").strip()
    if chamado_codigo and chamado_codigo not in chamados_codigos:
        chamados_codigos.append(chamado_codigo)
    if (
        chamado_codigo
        and reg.chamado_sistema == SuporteClaroRegistro.CHAMADO_SERVICE
        and chamado_codigo not in service_now_codigos
    ):
        service_now_codigos.append(chamado_codigo)
    tipo = canonical_incident_type(reg.tipo_incidente)
    return MotivoIncidenteItem(
        registro_id=reg.id,
        titulo=(reg.titulo or "").strip() or f"#{reg.id}",
        status=reg.status,
        status_label=STATUS_LABELS.get(reg.status, reg.status),
        received_at=reg.received_at,
        chamado_codigo=chamado_codigo,
        tratado_por=tratado_por,
        chamados_codigos=chamados_codigos,
        service_now_codigos=service_now_codigos,
        tipo=tipo,
        tipo_label=TIPO_LABELS.get(tipo, "Erro"),
    )


def _canonical_label(regs: Iterable[SuporteClaroRegistro]) -> str:
    items = list(regs)
    if not items:
        return "Motivo sem título"
    counts: dict[str, list[int]] = defaultdict(list)
    for r in items:
        t = (r.titulo or "").strip() or f"#{r.id}"
        counts[t].append(r.id)
    return max(counts.items(), key=lambda kv: (len(kv[1]), -min(kv[1])))[0]


def build_incidentes_mom_report(
    *,
    ref_month: date | None = None,
    created_by=None,
    created_by_username: str | None = None,
    status_filter: str | None = None,
    tipo_incidente: str | None = None,
    search: str | None = None,
    ticket_scope: str | None = None,
    filters_applied: list[str] | None = None,
    history_months: int = 6,
) -> IncidentesMomReport:
    """
    Cruza incidentes do mês de referência vs mês anterior.

    Chave de motivo:
      1) tipo_incidente + título normalizado
      2) union-find por vínculos manuais (mesmo cluster = mesmo motivo)
    """
    ref = (ref_month or timezone.localdate()).replace(day=1)
    cur_from, cur_to, prev_from, prev_to = month_bounds(ref)
    months = month_sequence(ref, history_months)
    history_start, history_end = _aware_range(months[0], cur_to)

    base: QuerySet = SuporteClaroRegistro.objects.filter(
        categoria=SuporteClaroRegistro.CATEGORIA_INCIDENTE,
    ).prefetch_related(
        Prefetch(
            "chamados_externos",
            queryset=SuporteClaroChamadoExterno.objects.order_by("ordem", "id"),
        )
    )
    if created_by is not None:
        base = base.filter(created_by=created_by)
    elif created_by_username:
        base = base.filter(created_by__username=created_by_username)
    if status_filter == "pendentes":
        base = base.filter(
            status__in=(
                SuporteClaroRegistro.STATUS_ABERTO,
                SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
            )
        )
    elif status_filter:
        base = base.filter(status=status_filter)
    if tipo_incidente == SuporteClaroRegistro.TIPO_INCIDENTE_LENTIDAO:
        base = base.filter(
            tipo_incidente__in=(
                SuporteClaroRegistro.TIPO_INCIDENTE_LENTIDAO,
                SuporteClaroRegistro.TIPO_INCIDENTE_TRAVAMENTO,
            )
        )
    elif tipo_incidente == SuporteClaroRegistro.TIPO_INCIDENTE_ERRO:
        base = base.filter(tipo_incidente=SuporteClaroRegistro.TIPO_INCIDENTE_ERRO)
    elif tipo_incidente == SuporteClaroRegistro.TIPO_INCIDENTE_OUTRO:
        base = base.filter(
            Q(tipo_incidente=SuporteClaroRegistro.TIPO_INCIDENTE_OUTRO)
            | Q(tipo_incidente="")
        )
    elif tipo_incidente:
        base = base.filter(tipo_incidente=tipo_incidente)
    if search and len(search.strip()) >= 2:
        term = search.strip()
        base = base.filter(
            Q(titulo__icontains=term)
            | Q(irregularidade__icontains=term)
            | Q(chamados_externos__codigo__icontains=term)
            | Q(chamado_codigo__icontains=term)
        ).distinct()
    if ticket_scope == "com_servicenow":
        base = base.filter(
            Q(chamados_externos__sistema=SuporteClaroRegistro.CHAMADO_SERVICE)
            | Q(chamado_sistema=SuporteClaroRegistro.CHAMADO_SERVICE)
        ).distinct()
    elif ticket_scope == "sem_servicenow":
        base = base.exclude(
            Q(chamados_externos__sistema=SuporteClaroRegistro.CHAMADO_SERVICE)
            | Q(chamado_sistema=SuporteClaroRegistro.CHAMADO_SERVICE)
        ).distinct()

    history_qs = list(
        base.filter(received_at__gte=history_start, received_at__lte=history_end).order_by(
            "received_at", "id"
        )
    )
    current_qs = [
        reg
        for reg in history_qs
        if reg.received_at and cur_from <= timezone.localtime(reg.received_at).date() <= cur_to
    ]
    previous_qs = [
        reg
        for reg in history_qs
        if reg.received_at and prev_from <= timezone.localtime(reg.received_at).date() <= prev_to
    ]
    month_index = {(month.year, month.month): idx for idx, month in enumerate(months)}
    monthly_totals = [0] * len(months)
    for reg in history_qs:
        local_dt = timezone.localtime(reg.received_at) if reg.received_at else None
        if local_dt:
            idx = month_index.get((local_dt.year, local_dt.month))
            if idx is not None:
                monthly_totals[idx] += 1
    history_points = [
        MonthlyIncidentPoint(month=month, total=monthly_totals[idx])
        for idx, month in enumerate(months)
    ]
    item_by_id = {reg.id: _item_from_registro(reg) for reg in history_qs}
    all_regs = {reg.id: reg for reg in history_qs}
    if not all_regs:
        return IncidentesMomReport(
            ref_month=ref,
            current_from=cur_from,
            current_to=cur_to,
            previous_from=prev_from,
            previous_to=prev_to,
            filters_applied=list(filters_applied or []),
            history_months=len(months),
            history_points=history_points,
        )

    uf = _UnionFind()
    for rid in all_regs:
        uf.add(rid)

    # 1) une por mesma chave textual
    by_text: dict[str, list[int]] = defaultdict(list)
    for rid, reg in all_regs.items():
        by_text[motivo_key_for(reg)].append(rid)
    for ids in by_text.values():
        root = ids[0]
        for other in ids[1:]:
            uf.union(root, other)

    # 2) une por vínculos manuais (qualquer ponta no conjunto)
    vinculos = SuporteClaroVinculoIncidente.objects.filter(
        from_registro_id__in=all_regs.keys(),
        to_registro_id__in=all_regs.keys(),
    ).values_list("from_registro_id", "to_registro_id")
    for a, b in vinculos:
        uf.union(a, b)

    clusters: dict[int, list[SuporteClaroRegistro]] = defaultdict(list)
    for rid, reg in all_regs.items():
        clusters[uf.find(rid)].append(reg)

    current_ids = {r.id for r in current_qs}
    previous_ids = {r.id for r in previous_qs}

    buckets: list[MotivoBucket] = []
    for members in clusters.values():
        tipos = [canonical_incident_type(m.tipo_incidente) for m in members]
        tipo = max(set(tipos), key=tipos.count)
        label = _canonical_label(members)
        key = f"cluster:{min(m.id for m in members)}"
        cur_members = [m for m in members if m.id in current_ids]
        prev_members = [m for m in members if m.id in previous_ids]
        monthly_counts = [0] * len(months)
        for member in members:
            local_dt = timezone.localtime(member.received_at) if member.received_at else None
            if local_dt:
                idx = month_index.get((local_dt.year, local_dt.month))
                if idx is not None:
                    monthly_counts[idx] += 1
        bucket = MotivoBucket(
            key=key,
            label=label,
            tipo=tipo,
            tipo_label=TIPO_LABELS.get(tipo, tipo or "Outro"),
            current_count=len(cur_members),
            previous_count=len(prev_members),
            current_items=[item_by_id[m.id] for m in sorted(cur_members, key=lambda r: r.id)],
            previous_items=[item_by_id[m.id] for m in sorted(prev_members, key=lambda r: r.id)],
            monthly_counts=monthly_counts,
        )
        buckets.append(bucket)

    buckets.sort(
        key=lambda b: (
            0 if b.is_recurring else 1 if b.is_new else 2,
            -(b.current_count + b.previous_count),
            -abs(b.delta),
            b.label.lower(),
        )
    )

    return IncidentesMomReport(
        ref_month=ref,
        current_from=cur_from,
        current_to=cur_to,
        previous_from=prev_from,
        previous_to=prev_to,
        current_total=len(current_qs),
        previous_total=len(previous_qs),
        recurring_motivos=sum(1 for b in buckets if b.is_recurring),
        new_motivos=sum(1 for b in buckets if b.is_new),
        closed_motivos=sum(1 for b in buckets if b.is_resolved_only_prev),
        buckets=buckets,
        filters_applied=list(filters_applied or []),
        history_months=len(months),
        history_points=history_points,
        history_items=list(item_by_id.values()),
    )
