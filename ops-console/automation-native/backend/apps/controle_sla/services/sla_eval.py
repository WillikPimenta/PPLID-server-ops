"""Avaliação de breaches SLA e gaps de etapa a partir do JSON BrFlow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.controle_sla.models import EtapaGap, SlaBreach, SlaBreachHistorico
from apps.controle_sla.services.brflow_client import parse_brflow_datetime
from apps.dimensoes_processos.models import DimCliente, DimEtapa, DimWorkflow, ProjecaoSla

TZ_BR = ZoneInfo("America/Sao_Paulo")

# Defaults (podem ser sobrescritos pela config do robô em Automações).
DEFAULT_ALERTA_PCT = 80.0
DEFAULT_MEDIO_PCT = 90.0
DEFAULT_ALTO_PCT = 95.0
DEFAULT_CRITICO_PCT = 100.0
DEFAULT_PROTOCOLOS_DIAS = 5


@dataclass(frozen=True)
class FarolThresholds:
    alerta: float = DEFAULT_ALERTA_PCT
    medio: float = DEFAULT_MEDIO_PCT
    alto: float = DEFAULT_ALTO_PCT
    critico: float = DEFAULT_CRITICO_PCT

    def normalized(self) -> FarolThresholds:
        alerta = float(self.alerta)
        medio = float(self.medio)
        alto = float(self.alto)
        critico = float(self.critico)
        if medio <= alerta:
            medio = alerta + 1
        if alto <= medio:
            alto = medio + 1
        if critico <= alto:
            critico = alto + 1
        return FarolThresholds(alerta=alerta, medio=medio, alto=alto, critico=critico)

    def as_dict(self) -> dict[str, float]:
        n = self.normalized()
        return {
            "sla_alerta_pct": n.alerta,
            "sla_medio_pct": n.medio,
            "sla_alto_pct": n.alto,
            "sla_critico_pct": n.critico,
        }


def expand_dias_token(raw: str | None) -> set[int]:
    """Expande tokens tipo {0..4} / {5} (0=Segunda … 6=Domingo)."""
    text = str(raw or "").strip()
    if not text:
        return set()
    out: set[int] = set()
    tokens = re.findall(r"\{[^}]+\}", text) or [text]
    for token in tokens:
        inner = token.strip().strip("{}")
        range_m = re.match(r"^(\d+)\.\.(\d+)$", inner)
        if range_m:
            a, b = int(range_m.group(1)), int(range_m.group(2))
            for i in range(min(a, b), max(a, b) + 1):
                out.add(i)
            continue
        if inner.isdigit():
            out.add(int(inner))
    return out


def classify_criticidade(
    pct_sla: float,
    thresholds: FarolThresholds | None = None,
) -> str:
    """
    Farol de criticidade pelo percentual do SLA consumido.
    Limiares padrão: Baixo ≥alerta, Médio ≥medio, Alto ≥alto, Crítico ≥critico.
    """
    t = (thresholds or FarolThresholds()).normalized()
    if pct_sla >= t.critico:
        return SlaBreach.CRIT_CRITICO
    if pct_sla >= t.alto:
        return SlaBreach.CRIT_ALTO
    if pct_sla >= t.medio:
        return SlaBreach.CRIT_MEDIO
    return SlaBreach.CRIT_BAIXO


def normalize_protocolos_dias(value: Any, default: int = DEFAULT_PROTOCOLOS_DIAS) -> int:
    """Aceita apenas inteiros ≥ 0 (config do robô)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return max(0, int(default))
    return max(0, n)


def protocolo_corte_date(*, now: datetime | None = None, dias: int) -> date:
    """Data limite inclusiva para protocolos atuais (hoje − N)."""
    ref = (now or timezone.now()).astimezone(TZ_BR).date()
    return ref - timedelta(days=normalize_protocolos_dias(dias))


def is_protocolo_atual(
    dat_registro_antigo: datetime | None,
    *,
    dias: int,
    now: datetime | None = None,
) -> bool:
    """
    Atuais: data mais antiga entre hoje e D−N (inclusive).
    Antigos: anteriores a D−N.
    """
    if dat_registro_antigo is None:
        return False
    d = dat_registro_antigo.astimezone(TZ_BR).date()
    return d >= protocolo_corte_date(now=now, dias=dias)


def _norm_name(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ").replace("\u200b", "").strip()
    # Unifica travessões comuns entre BrFlow e Megazord
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text)


def _in_time_window(now_t: time, inicio: time | None, fim: time | None) -> bool:
    if inicio is None and fim is None:
        return True
    if inicio is not None and fim is not None:
        if inicio <= fim:
            return inicio <= now_t <= fim
        # atravessa meia-noite
        return now_t >= inicio or now_t <= fim
    if inicio is not None:
        return now_t >= inicio
    return now_t <= fim  # type: ignore[operator]


@dataclass(frozen=True)
class SlaWindow:
    """Janela de contagem de SLA (dias da semana + horário) da Projeção SLA."""

    days: frozenset[int]  # 0=Seg … 6=Dom
    hora_inicio: time | None
    hora_fim: time | None


def projecao_windows(rows: list[ProjecaoSla]) -> list[SlaWindow]:
    """Extrai janelas úteis (ignora códigos de feriado/exceção sem calendário)."""
    out: list[SlaWindow] = []
    for row in rows:
        days = frozenset(d for d in expand_dias_token(row.dias_semana) if 0 <= d <= 6)
        if not days:
            continue
        out.append(
            SlaWindow(
                days=days,
                hora_inicio=row.hora_inicio,
                hora_fim=row.hora_fim,
            )
        )
    return out


def _shortest_sla_limit(rows: list[ProjecaoSla]) -> int | None:
    limits = [sec for sec in (_sla_limit_seconds(r) for r in rows) if sec is not None]
    return min(limits) if limits else None


def _intervals_on_date(d: date, window: SlaWindow) -> list[tuple[datetime, datetime]]:
    """Intervalos [inicio, fim) no dia ``d`` (America/Sao_Paulo) para a janela."""
    if d.weekday() not in window.days:
        return []
    inicio = window.hora_inicio
    fim = window.hora_fim
    if inicio is None and fim is None:
        start = datetime.combine(d, time(0, 0, 0), tzinfo=TZ_BR)
        end = datetime.combine(d + timedelta(days=1), time(0, 0, 0), tzinfo=TZ_BR)
        return [(start, end)]
    if inicio is None:
        inicio = time(0, 0, 0)
    if fim is None:
        # Sem hora fim: conta até o fim do dia.
        start = datetime.combine(d, inicio, tzinfo=TZ_BR)
        end = datetime.combine(d + timedelta(days=1), time(0, 0, 0), tzinfo=TZ_BR)
        return [(start, end)] if start < end else []
    start = datetime.combine(d, inicio, tzinfo=TZ_BR)
    if inicio <= fim:
        end = datetime.combine(d, fim, tzinfo=TZ_BR)
        if fim == time(23, 59, 59):
            end = datetime.combine(d + timedelta(days=1), time(0, 0, 0), tzinfo=TZ_BR)
        return [(start, end)] if start < end else []
    # Atravessa meia-noite: [inicio → 24h] ∪ continua no dia seguinte via fim.
    end = datetime.combine(d + timedelta(days=1), fim, tzinfo=TZ_BR)
    return [(start, end)] if start < end else []


def business_elapsed_seconds(
    start: datetime,
    end: datetime,
    windows: list[SlaWindow],
) -> int:
    """
    Segundos decorridos entre ``start`` e ``end`` somente nas janelas
    (dias da semana + hora início/fim) cadastradas na Projeção SLA.
    """
    if not windows:
        return 0
    start_l = start.astimezone(TZ_BR)
    end_l = end.astimezone(TZ_BR)
    if end_l <= start_l:
        return 0
    total = 0
    d = start_l.date()
    last = end_l.date()
    while d <= last:
        for window in windows:
            for a, b in _intervals_on_date(d, window):
                lo = max(a, start_l)
                hi = min(b, end_l)
                if hi > lo:
                    total += int((hi - lo).total_seconds())
        d += timedelta(days=1)
    return max(0, total)


def compute_breach_moment(
    start: datetime,
    sla_seconds: int,
    windows: list[SlaWindow],
    *,
    max_days: int = 800,
) -> datetime | None:
    """
    Momento em que o SLA estoura contando apenas janelas da Projeção SLA.
    Ex.: item às 00:00 com janela 08:00–18:00 e SLA 4h → estouro às 12:00.
    """
    if sla_seconds <= 0:
        return None
    if not windows:
        # Sem janela cadastrada: fallback wall-clock.
        return start.astimezone(TZ_BR) + timedelta(seconds=int(sla_seconds))

    start_l = start.astimezone(TZ_BR)
    remaining = float(sla_seconds)
    cursor = start_l
    limit_date = start_l.date() + timedelta(days=max_days)
    d = start_l.date()

    while remaining > 0 and d <= limit_date:
        intervals: list[tuple[datetime, datetime]] = []
        for window in windows:
            intervals.extend(_intervals_on_date(d, window))
        intervals.sort(key=lambda pair: pair[0])
        for a, b in intervals:
            if b <= cursor:
                continue
            lo = max(a, cursor)
            if lo >= b:
                continue
            available = (b - lo).total_seconds()
            if available >= remaining:
                return lo + timedelta(seconds=remaining)
            remaining -= available
            cursor = b
        d += timedelta(days=1)
        day_start = datetime.combine(d, time(0, 0, 0), tzinfo=TZ_BR)
        if cursor < day_start:
            cursor = day_start
    return None


def _sla_limit_seconds(row: ProjecaoSla) -> int | None:
    """Usa a coluna SLA (``sla_segundos``) da Projeção SLA — não o SLA ajuste."""
    if row.sla_segundos is not None and row.sla_segundos > 0:
        return int(row.sla_segundos)
    return None


def _parse_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_catalogs() -> tuple[dict[str, int], dict[str, bool], dict[str, int], set[int], set[tuple[int, int]]]:
    """Catálogos por nome: cliente, etapa (manual?), workflow e pares com Projeção SLA vigente."""
    cliente_by_name: dict[str, int] = {}
    for cid, nome in DimCliente.objects.values_list("id_cliente", "nome"):
        key = _norm_name(nome).casefold()
        if key and key not in cliente_by_name:
            cliente_by_name[key] = cid
    etapa_manual_by_name = {
        _norm_name(nome).casefold(): bool(manual)
        for nome, manual in DimEtapa.objects.values_list("nome", "manual")
    }
    workflow_by_name = {
        _norm_name(nome).casefold(): wid
        for wid, nome in DimWorkflow.objects.values_list("id_workflow", "nome")
    }
    manual_workflow_ids = set(
        DimWorkflow.objects.filter(tipo_atendimento__iexact="Manual").values_list(
            "id_workflow", flat=True
        )
    )
    projecao_pairs = {
        (cid, wid)
        for cid, wid in ProjecaoSla.objects.filter(data_fim__isnull=True).values_list(
            "cliente_id", "workflow_id"
        )
    }
    return cliente_by_name, etapa_manual_by_name, workflow_by_name, manual_workflow_ids, projecao_pairs


def _resolve_cliente_id(nom_cliente: str, cliente_by_name: dict[str, int]) -> int | None:
    """Casa BrFlow ``nomCliente`` com ``DimCliente.nome`` (Megazord / Projeção SLA)."""
    key = _norm_name(nom_cliente).casefold()
    if not key:
        return None
    return cliente_by_name.get(key)


def _resolve_workflow_id(nom_workflow: str, workflow_by_name: dict[str, int]) -> int | None:
    """Casa BrFlow ``nomWorkflow`` com ``DimWorkflow.nome`` (Megazord / Projeção SLA)."""
    if not nom_workflow:
        return None
    return workflow_by_name.get(nom_workflow.casefold())


def _vigente_sla_by_workflow(
    reference_date: date | None = None,
) -> dict[tuple[int, int], list[ProjecaoSla]]:
    """Indexa projeções vigentes por (cliente, workflow) — acompanhamento por workflow."""
    from django.db.models import Q

    ref = reference_date or timezone.localdate()
    qs = ProjecaoSla.objects.filter(
        data_inicio__lte=ref,
    ).filter(
        Q(data_fim__isnull=True) | Q(data_fim__gte=ref)
    ).select_related(
        "cliente", "workflow", "nivel_hierarquico"
    )
    out: dict[tuple[int, int], list[ProjecaoSla]] = {}
    for row in qs:
        out.setdefault((row.cliente_id, row.workflow_id), []).append(row)
    return out


def _pick_sla_for_now(rows: list[ProjecaoSla], now: datetime) -> ProjecaoSla | None:
    """Escolhe a projeção vigente para o dia/hora atuais (SLA mais curto em empate)."""
    day_code = now.weekday()  # Mon=0 … Sun=6 (igual Megazord)
    now_t = now.timetz().replace(tzinfo=None)
    matches: list[ProjecaoSla] = []
    for row in rows:
        if day_code not in expand_dias_token(row.dias_semana):
            continue
        if not _in_time_window(now_t, row.hora_inicio, row.hora_fim):
            continue
        if _sla_limit_seconds(row) is None:
            continue
        matches.append(row)
    if not matches:
        return None
    return min(matches, key=lambda r: _sla_limit_seconds(r) or 10**12)


def find_active_sla(
    *,
    cliente_id: int,
    workflow_id: int,
    now: datetime,
    sla_map_cw: dict[tuple[int, int], list[ProjecaoSla]],
) -> ProjecaoSla | None:
    """Resolve Projeção SLA por cliente + workflow (nome via DimWorkflow)."""
    return _pick_sla_for_now(sla_map_cw.get((cliente_id, workflow_id), []), now)


def evaluate_sla_batch(
    rows: list[dict[str, Any]],
    *,
    reference_at: datetime | None = None,
    thresholds: FarolThresholds | None = None,
) -> list[dict[str, Any]]:
    """Avalia SLA de vários cliente/workflows carregando catálogos uma única vez.

    A função é deliberadamente read-only e serve consumidores que precisam do
    farol completo, inclusive itens saudáveis ou sem configuração. ``rows`` deve
    informar ``cliente``, ``workflow`` e ``oldest_at``; quaisquer outros campos
    são preservados no resultado.
    """
    now = (reference_at or timezone.now()).astimezone(TZ_BR)
    farol = (thresholds or FarolThresholds()).normalized()
    cliente_by_name, _, workflow_by_name, _, _ = _load_catalogs()
    sla_map = _vigente_sla_by_workflow(now.date())
    evaluated: list[dict[str, Any]] = []

    for raw in rows:
        item = dict(raw)
        start = item.get("oldest_at")
        if isinstance(start, str):
            start = parse_brflow_datetime(start)

        cliente_id = _resolve_cliente_id(str(item.get("cliente") or ""), cliente_by_name)
        workflow_id = _resolve_workflow_id(
            _norm_name(item.get("workflow")), workflow_by_name
        )
        status = "ok"
        limit: int | None = None
        elapsed: int | None = None
        remaining: int | None = None
        pct: float | None = None
        due_at: datetime | None = None

        if start is None:
            status = "no_date"
        elif cliente_id is None or workflow_id is None:
            status = "unmapped"
        else:
            rules = sla_map.get((cliente_id, workflow_id), [])
            windows = projecao_windows(rules)
            active = find_active_sla(
                cliente_id=cliente_id,
                workflow_id=workflow_id,
                now=now,
                sla_map_cw=sla_map,
            )
            limit = _sla_limit_seconds(active) if active is not None else _shortest_sla_limit(rules)
            if limit is None or not windows:
                status = "unconfigured"
            else:
                elapsed = business_elapsed_seconds(start, now, windows)
                remaining = limit - elapsed
                pct = round((elapsed / float(limit)) * 100.0, 2)
                due_at = compute_breach_moment(start, limit, windows)
                if pct >= farol.critico:
                    status = "breached"
                elif pct >= farol.alto:
                    status = "high"
                elif pct >= farol.alerta:
                    status = "attention"

        item.update(
            {
                "sla_limit_seconds": limit,
                "sla_elapsed_seconds": elapsed,
                "sla_remaining_seconds": remaining,
                "sla_pct": pct,
                "sla_status": status,
                "sla_due_at": due_at,
            }
        )
        evaluated.append(item)
    return evaluated


def _upsert_breach_historico(payload: dict[str, Any], *, now: datetime) -> None:
    """Persiste histórico sem sobrescrever identidade; atualiza métricas se já existir."""
    defaults = {
        "nom_cliente": payload["nom_cliente"],
        "nom_workflow": payload["nom_workflow"],
        "cod_nivel_hierarquico": payload["cod_nivel_hierarquico"],
        "nom_fluxo": payload["nom_fluxo"],
        "tipo_analise": payload["tipo_analise"],
        "qtd_registro": payload["qtd_registro"],
        "qtd_fila": payload["qtd_fila"],
        "idade_segundos": payload["idade_segundos"],
        "sla_limite_segundos": payload["sla_limite_segundos"],
        "excedente_segundos": payload["excedente_segundos"],
        "pct_sla": payload["pct_sla"],
        "criticidade": payload["criticidade"],
        "last_seen_at": now,
    }
    obj, created = SlaBreachHistorico.objects.get_or_create(
        cod_cliente=payload["cod_cliente"],
        id_workflow=payload["id_workflow"],
        dat_registro_antigo=payload["dat_registro_antigo"],
        defaults={**defaults, "first_detected_at": now},
    )
    if not created:
        for key, value in defaults.items():
            setattr(obj, key, value)
        obj.save(update_fields=[*defaults.keys()])


def _upsert_gap(
    payload: dict[str, Any],
    *,
    now: datetime,
    etapa_conhecida: bool,
    tem_projecao_sla: bool,
) -> str:
    """
    Atualiza gap existente ou cria Pendente.
    Retorna: created | updated | skipped_cadastrada | skipped_ignorada | skipped_exists | auto_cadastrada
    """
    existing = EtapaGap.objects.filter(
        cod_cliente=payload["cod_cliente"],
        nom_fluxo=payload["nom_fluxo"],
    ).first()

    # Já existe no cadastro (DimEtapa) ou já há Projeção SLA vigente para o par:
    # não cria pendência nova; se pendente antiga, promove a Cadastrada.
    if etapa_conhecida or tem_projecao_sla:
        if existing is None:
            return "skipped_exists"
        if existing.situacao == EtapaGap.SIT_PENDENTE:
            existing.situacao = EtapaGap.SIT_CADASTRADA
            existing.ocorrencias = int(existing.ocorrencias or 0) + 1
            existing.last_seen_at = now
            existing.nom_cliente = payload["nom_cliente"]
            existing.nom_workflow = payload["nom_workflow"]
            existing.id_workflow = payload.get("id_workflow")
            existing.id_cliente_megazord = payload.get("id_cliente_megazord")
            existing.cod_nivel_hierarquico = payload.get("cod_nivel_hierarquico")
            existing.qtd_fila = payload.get("qtd_fila")
            existing.dat_registro_antigo = payload.get("dat_registro_antigo")
            if existing.tratado_em is None:
                existing.tratado_em = now
            existing.save()
            return "auto_cadastrada"
        existing.ocorrencias = int(existing.ocorrencias or 0) + 1
        existing.last_seen_at = now
        existing.save(update_fields=["ocorrencias", "last_seen_at"])
        return "skipped_exists"

    snapshot_fields = {
        "nom_cliente": payload["nom_cliente"],
        "nom_workflow": payload["nom_workflow"],
        "id_workflow": payload.get("id_workflow"),
        "id_cliente_megazord": payload.get("id_cliente_megazord"),
        "cod_nivel_hierarquico": payload.get("cod_nivel_hierarquico"),
        "qtd_fila": payload.get("qtd_fila"),
        "dat_registro_antigo": payload.get("dat_registro_antigo"),
        "last_seen_at": now,
    }

    if existing is not None:
        existing.ocorrencias = int(existing.ocorrencias or 0) + 1
        for key, value in snapshot_fields.items():
            setattr(existing, key, value)
        # Não recria / não reabre Ignorada ou Cadastrada
        existing.save()
        if existing.situacao == EtapaGap.SIT_IGNORADA:
            return "skipped_ignorada"
        if existing.situacao == EtapaGap.SIT_CADASTRADA:
            return "skipped_cadastrada"
        return "updated"

    EtapaGap.objects.create(
        cod_cliente=payload["cod_cliente"],
        nom_fluxo=payload["nom_fluxo"],
        situacao=EtapaGap.SIT_PENDENTE,
        ocorrencias=1,
        first_seen_at=now,
        **snapshot_fields,
    )
    return "created"


def evaluate_and_persist(
    rows: list[dict[str, Any]],
    *,
    update_gaps: bool = True,
    thresholds: FarolThresholds | None = None,
    protocolos_dias: int = DEFAULT_PROTOCOLOS_DIAS,
) -> dict[str, int]:
    """
    Avalia filas BrFlow e persiste acompanhamento **por workflow**.

    Join de SLA (Megazord Projeção SLA):
    - ``nomCliente`` ↔ ``DimCliente.nome`` (``codCliente`` BrFlow ≠ ``id_cliente``)
    - ``nomWorkflow`` ↔ ``DimWorkflow.nome`` ↔ ``ProjecaoSla.workflow``
    - somente workflows com ``tipo_atendimento`` = Manual

    Etapa (``nomFluxo``) não bloqueia o acompanhamento; alimenta o relatório de gaps.
    Histórico de breach é append-safe (chave cliente+workflow+data mais antiga).
    """
    farol = (thresholds or FarolThresholds()).normalized()
    dias_n = normalize_protocolos_dias(protocolos_dias)
    now = timezone.now().astimezone(TZ_BR)
    (
        cliente_by_name,
        etapa_manual_by_name,
        workflow_by_name,
        manual_workflow_ids,
        projecao_pairs,
    ) = _load_catalogs()
    sla_map_cw = _vigente_sla_by_workflow()

    # (cod_cliente BrFlow, id_workflow Megazord) → melhor candidato (maior pct)
    candidates: dict[tuple[int, int], dict[str, Any]] = {}
    gap_payloads: dict[tuple[int, str], dict[str, Any]] = {}
    skip = {
        "skip_no_cliente": 0,
        "skip_cliente_desconhecido": 0,
        "skip_etapa_desconhecida": 0,
        "skip_sem_data": 0,
        "skip_sem_workflow": 0,
        "skip_nao_manual": 0,
        "skip_sem_projecao": 0,
        "skip_sem_sla_vigente": 0,
        "skip_abaixo_alerta": 0,
        "gap_created": 0,
        "gap_updated": 0,
        "gap_skipped": 0,
        "historico_upserts": 0,
        "protocolos_dias": dias_n,
    }

    for item in rows:
        cod_cliente = _parse_optional_int(item.get("codCliente")) or 0
        if cod_cliente <= 0:
            skip["skip_no_cliente"] += 1
            continue

        nom_cliente = str(item.get("nomCliente") or "")
        nom_fluxo = _norm_name(item.get("nomFluxo"))
        nom_workflow = _norm_name(item.get("nomWorkflow"))
        cod_nh = _parse_optional_int(item.get("codNivelHierarquico"))
        qtd_fila_i = _parse_optional_int(item.get("qtdFila"))
        qtd_registro_i = _parse_optional_int(item.get("qtdRegistro"))

        dat_antigo = parse_brflow_datetime(item.get("datRegistroAntigo"))
        fluxo_key = nom_fluxo.casefold()
        etapa_conhecida = fluxo_key in etapa_manual_by_name if fluxo_key else False
        if etapa_conhecida:
            tipo_analise = "Manual" if etapa_manual_by_name[fluxo_key] else "Automática"
        else:
            tipo_analise = ""

        mz_cliente_id = _resolve_cliente_id(nom_cliente, cliente_by_name)
        wf_id = _resolve_workflow_id(nom_workflow, workflow_by_name)
        is_manual = wf_id is not None and wf_id in manual_workflow_ids
        tem_projecao = (
            mz_cliente_id is not None
            and wf_id is not None
            and (mz_cliente_id, wf_id) in projecao_pairs
        )

        # Gap: cliente no Megazord + workflow Manual; etapa ausente no cadastro Megazord.
        # Se a etapa (ou o par cliente+workflow na Projeção SLA) já existir, só atualiza
        # ocorrências / promove Pendente → Cadastrada — não cria nova pendência.
        if update_gaps and mz_cliente_id is not None and is_manual and fluxo_key:
            gap_payloads[(cod_cliente, nom_fluxo)] = {
                "cod_cliente": cod_cliente,
                "nom_fluxo": nom_fluxo,
                "nom_cliente": nom_cliente,
                "nom_workflow": nom_workflow,
                "id_workflow": wf_id,
                "id_cliente_megazord": mz_cliente_id,
                "cod_nivel_hierarquico": cod_nh,
                "qtd_fila": qtd_fila_i,
                "dat_registro_antigo": dat_antigo,
                "etapa_conhecida": etapa_conhecida,
                # Já cadastrada no Megazord (DimEtapa) — não cria pendência.
                "tem_projecao_sla": etapa_conhecida,
            }

        if not etapa_conhecida:
            # Informativo: não bloqueia acompanhamento por workflow
            skip["skip_etapa_desconhecida"] += 1

        if mz_cliente_id is None:
            skip["skip_cliente_desconhecido"] += 1
            continue
        if wf_id is None:
            skip["skip_sem_workflow"] += 1
            continue
        if not is_manual:
            skip["skip_nao_manual"] += 1
            continue
        if not dat_antigo:
            skip["skip_sem_data"] += 1
            continue

        has_projecao = (mz_cliente_id, wf_id) in sla_map_cw
        rules = sla_map_cw.get((mz_cliente_id, wf_id), [])
        windows = projecao_windows(rules)
        sla = find_active_sla(
            cliente_id=mz_cliente_id,
            workflow_id=wf_id,
            now=now,
            sla_map_cw=sla_map_cw,
        )
        limit = _sla_limit_seconds(sla) if sla is not None else _shortest_sla_limit(rules)
        if limit is None or not windows:
            if has_projecao:
                skip["skip_sem_sla_vigente"] += 1
            else:
                skip["skip_sem_projecao"] += 1
            continue

        idade = business_elapsed_seconds(dat_antigo, now, windows)
        if idade < 0:
            idade = 0

        pct_sla = (idade / float(limit)) * 100.0
        if pct_sla < farol.alerta:
            skip["skip_abaixo_alerta"] += 1
            continue

        key = (cod_cliente, wf_id)
        prev = candidates.get(key)
        if prev is not None and float(prev["pct_sla"]) >= pct_sla:
            continue

        candidates[key] = {
            "cod_cliente": cod_cliente,
            "nom_cliente": nom_cliente,
            "nom_workflow": nom_workflow,
            "id_workflow": wf_id,
            "cod_nivel_hierarquico": cod_nh,
            "nom_fluxo": nom_fluxo,
            "tipo_analise": tipo_analise,
            "qtd_registro": qtd_registro_i,
            "qtd_fila": qtd_fila_i,
            "dat_registro_antigo": dat_antigo,
            "idade_segundos": idade,
            "sla_limite_segundos": limit,
            "excedente_segundos": max(idade - limit, 0),
            "pct_sla": round(pct_sla, 2),
            "criticidade": classify_criticidade(pct_sla, farol),
        }

    breach_keys = set(candidates.keys())
    with transaction.atomic():
        for payload in candidates.values():
            SlaBreach.objects.update_or_create(
                cod_cliente=payload["cod_cliente"],
                id_workflow=payload["id_workflow"],
                defaults={
                    "nom_cliente": payload["nom_cliente"],
                    "nom_workflow": payload["nom_workflow"],
                    "cod_nivel_hierarquico": payload["cod_nivel_hierarquico"],
                    "nom_fluxo": payload["nom_fluxo"],
                    "tipo_analise": payload["tipo_analise"],
                    "qtd_registro": payload["qtd_registro"],
                    "qtd_fila": payload["qtd_fila"],
                    "dat_registro_antigo": payload["dat_registro_antigo"],
                    "idade_segundos": payload["idade_segundos"],
                    "sla_limite_segundos": payload["sla_limite_segundos"],
                    "excedente_segundos": payload["excedente_segundos"],
                    "pct_sla": payload["pct_sla"],
                    "criticidade": payload["criticidade"],
                },
            )
            _upsert_breach_historico(payload, now=now)
            skip["historico_upserts"] += 1

        keep_breach_ids: list[int] = []
        for row in SlaBreach.objects.only("id", "cod_cliente", "id_workflow").iterator():
            if row.id_workflow is not None and (row.cod_cliente, row.id_workflow) in breach_keys:
                keep_breach_ids.append(row.id)
        SlaBreach.objects.exclude(id__in=keep_breach_ids).delete()

        if update_gaps:
            for raw in gap_payloads.values():
                payload = {
                    k: v
                    for k, v in raw.items()
                    if k not in {"etapa_conhecida", "tem_projecao_sla"}
                }
                result = _upsert_gap(
                    payload,
                    now=now,
                    etapa_conhecida=bool(raw.get("etapa_conhecida")),
                    tem_projecao_sla=bool(raw.get("tem_projecao_sla")),
                )
                if result == "created":
                    skip["gap_created"] += 1
                elif result in {"updated", "auto_cadastrada"}:
                    skip["gap_updated"] += 1
                else:
                    skip["gap_skipped"] += 1

    return {
        "breaches": len(candidates),
        "gaps": skip["gap_created"] + skip["gap_updated"] if update_gaps else 0,
        "rows_in": len(rows),
        "alerta_pct": int(farol.alerta),
        **skip,
    }
