from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from hashlib import sha256
from typing import Iterable

from apps.dimensoes_processos.models import ProjecaoSla
from apps.monitoramento_sla.services.projecao_lookup import (
    ProjecaoDia,
    _covers,
    _duracao_segundos,
)

ProjectionKey = tuple[int, int, int]


def projection_signature(rows: Iterable[ProjecaoSla]) -> str:
    digest = sha256()
    for row in sorted(rows, key=lambda item: item.pk or 0):
        values = (
            row.pk,
            row.cliente_id,
            row.workflow_id,
            row.nivel_hierarquico_id,
            row.data_inicio,
            row.data_fim,
            row.dias_semana,
            row.hora_inicio,
            row.hora_fim,
            row.duracao_atendimento,
            row.sla_segundos,
            row.sla_ajuste,
            row.flag_ajuste_sla,
            row.volume,
        )
        digest.update(repr(values).encode("utf-8"))
    return digest.hexdigest()


class ProjectionCalendar:
    """Índice de projeções e prefixos de segundos úteis por combinação/data."""

    def __init__(
        self,
        rows: Iterable[ProjecaoSla],
        *,
        date_min: date,
        date_max: date,
    ) -> None:
        self.rows = list(rows)
        self.date_min = date_min
        self.date_max = date_max
        self.signature = projection_signature(self.rows)
        self._by_key: dict[ProjectionKey, list[ProjecaoSla]] = defaultdict(list)
        self._lookup: dict[tuple[ProjectionKey, date], tuple[ProjecaoDia | None, tuple[str, ...]]] = {}
        self._prefix: dict[ProjectionKey, list[int]] = {}
        self._duplicate_prefix: dict[ProjectionKey, list[int]] = {}
        self._d2u_cache: dict[tuple[ProjectionKey, date, int], tuple[date | None, tuple[str, ...]]] = {}
        for row in self.rows:
            self._by_key[self._key(row.cliente_id, row.workflow_id, row.nivel_hierarquico_id)].append(row)
        self._build()

    @staticmethod
    def _key(id_cliente: int, id_workflow: int, id_nh: int) -> ProjectionKey:
        return int(id_cliente), int(id_workflow), int(id_nh)

    def _to_projection(self, key: ProjectionKey, on_date: date, row: ProjecaoSla) -> ProjecaoDia:
        return ProjecaoDia(
            on_date=on_date,
            id_cliente=key[0],
            id_workflow=key[1],
            id_nh=key[2],
            hora_inicio=row.hora_inicio,
            hora_fim=row.hora_fim,
            duracao_segundos=_duracao_segundos(row),
            sla_segundos=int(row.sla_segundos) if row.sla_segundos else None,
            sla_ajuste=int(row.sla_ajuste) if row.sla_ajuste else None,
            flag_ajuste_sla=(
                float(row.flag_ajuste_sla)
                if row.flag_ajuste_sla is not None
                else None
            ),
            volume=float(row.volume) if row.volume is not None else None,
            dia_semana=on_date.weekday(),
        )

    def _build(self) -> None:
        day_count = max(0, (self.date_max - self.date_min).days + 1)
        for key, rows in self._by_key.items():
            prefix = [0]
            duplicate_prefix = [0]
            for offset in range(day_count):
                on_date = self.date_min + timedelta(days=offset)
                candidates = [row for row in rows if _covers(row, on_date)]
                problems: tuple[str, ...] = ()
                projection = None
                if len(candidates) == 1:
                    projection = self._to_projection(key, on_date, candidates[0])
                elif len(candidates) > 1:
                    problems = ("PROJECAO_DUPLICADA",)
                self._lookup[(key, on_date)] = (projection, problems)
                prefix.append(prefix[-1] + (projection.duracao_segundos if projection else 0))
                duplicate_prefix.append(
                    duplicate_prefix[-1] + (1 if problems else 0)
                )
            self._prefix[key] = prefix
            self._duplicate_prefix[key] = duplicate_prefix

    def find(
        self,
        *,
        id_cliente: int,
        id_workflow: int,
        id_nh: int,
        on_date: date,
    ) -> tuple[ProjecaoDia | None, list[str]]:
        key = self._key(id_cliente, id_workflow, id_nh)
        cached = self._lookup.get((key, on_date))
        if cached is not None:
            return cached[0], list(cached[1])
        rows = self._by_key.get(key, ())
        candidates = [row for row in rows if _covers(row, on_date)]
        if len(candidates) > 1:
            return None, ["PROJECAO_DUPLICADA"]
        if not candidates:
            return None, []
        return self._to_projection(key, on_date, candidates[0]), []

    def d2u_deadline(
        self,
        *,
        id_cliente: int,
        id_workflow: int,
        id_nh: int,
        data_cadastro: date,
        horizon_days: int = 60,
    ) -> tuple[date | None, list[str]]:
        key = self._key(id_cliente, id_workflow, id_nh)
        cache_key = (key, data_cadastro, horizon_days)
        cached = self._d2u_cache.get(cache_key)
        if cached is not None:
            return cached[0], list(cached[1])

        problems: list[str] = []
        first_useful = None
        current = data_cadastro
        horizon = data_cadastro + timedelta(days=horizon_days)
        while current <= horizon:
            projection, current_problems = self.find(
                id_cliente=id_cliente,
                id_workflow=id_workflow,
                id_nh=id_nh,
                on_date=current,
            )
            problems.extend(current_problems)
            if (
                projection is not None
                and projection.dia_semana <= 4
                and projection.duracao_segundos > 0
            ):
                if first_useful is None:
                    first_useful = current
                elif current > first_useful:
                    next_current = current + timedelta(days=1)
                    while next_current <= first_useful + timedelta(days=horizon_days):
                        next_projection, next_problems = self.find(
                            id_cliente=id_cliente,
                            id_workflow=id_workflow,
                            id_nh=id_nh,
                            on_date=next_current,
                        )
                        problems.extend(next_problems)
                        if (
                            next_projection is not None
                            and next_projection.dia_semana <= 4
                            and next_projection.duracao_segundos > 0
                        ):
                            result = (next_current, tuple(problems))
                            self._d2u_cache[cache_key] = result
                            return result[0], list(result[1])
                        next_current += timedelta(days=1)
                    break
            current += timedelta(days=1)
        problems.append("VENCIMENTO_D2U_NAO_ENCONTRADO")
        result = (None, tuple(problems))
        self._d2u_cache[cache_key] = result
        return result[0], list(result[1])

    def sum_duration(
        self,
        *,
        id_cliente: int,
        id_workflow: int,
        id_nh: int,
        start: date,
        end: date,
    ) -> tuple[int, list[str]]:
        if end < start:
            return 0, []
        if start < self.date_min or end > self.date_max:
            total = 0
            problems: list[str] = []
            current = start
            while current <= end:
                projection, current_problems = self.find(
                    id_cliente=id_cliente,
                    id_workflow=id_workflow,
                    id_nh=id_nh,
                    on_date=current,
                )
                problems.extend(current_problems)
                if projection is not None:
                    total += projection.duracao_segundos
                current += timedelta(days=1)
            return total, problems

        key = self._key(id_cliente, id_workflow, id_nh)
        prefix = self._prefix.get(key)
        if prefix is None:
            return 0, []
        start_index = (start - self.date_min).days
        end_index = (end - self.date_min).days + 1
        duplicate_prefix = self._duplicate_prefix[key]
        duplicate_count = duplicate_prefix[end_index] - duplicate_prefix[start_index]
        return (
            prefix[end_index] - prefix[start_index],
            ["PROJECAO_DUPLICADA"] * duplicate_count,
        )
