# -*- coding: utf-8 -*-
"""Analytics Treinamentos / Capacitação."""
from datetime import date, datetime

from django.db.models import Count, Q

from report_falhas.io.data_loader import normalize_text, safe_str
from report_falhas.training_utils import training_situacao_calculada

from apps.falhas_criticas.models import Training
from apps.falhas_criticas.services.localidade import filter_qs_by_localidade
from apps.falhas_criticas.services.narrative import build_training_narrative
from apps.falhas_criticas.utils_metrics import pct


def _training_qs_filtered(params, user_scope_localidade=None):
    qs = Training.objects.all()
    loc = params.get('localidade')
    if loc == '__BLOCKED__':
        return qs.none()
    qs = filter_qs_by_localidade(qs, 'localidade', loc or 'Geral')
    start_date = params.get('start_date')
    end_date = params.get('end_date')
    if start_date:
        qs = qs.filter(data_limite__gte=start_date)
    if end_date:
        qs = qs.filter(data_limite__lte=end_date)
    mes = params.get('mes')
    if mes:
        try:
            y, m = mes.split('-')
            qs = qs.filter(data_limite__year=int(y), data_limite__month=int(m))
        except Exception:
            pass
    status = params.get('status')
    if status:
        qs = qs.filter(status__iexact=status)
    team_mats = params.get('team_mats') or []
    matricula = params.get('matricula')
    if matricula == '__BLOCKED__':
        qs = qs.none()
    elif team_mats:
        qs = qs.filter(matricula__in=team_mats)
    elif matricula:
        qs = qs.filter(matricula__iexact=matricula)
    search = params.get('search', '').strip()
    if search:
        qs = qs.filter(
            Q(matricula__icontains=search)
            | Q(nome_agente__icontains=search)
            | Q(titulo__icontains=search)
        )
    return qs


def _ref_date_from_params(params) -> date:
    for key in ('end_date', 'start_date'):
        raw = params.get(key) if params else None
        if not raw:
            continue
        if isinstance(raw, date):
            return raw
        try:
            return datetime.strptime(str(raw)[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            continue
    return date.today()


def _training_row_dict(obj) -> dict:
    if isinstance(obj, dict):
        return {
            'Status': obj.get('status') or obj.get('Status') or '',
            'SignatureDate': obj.get('data_assinatura') or obj.get('SignatureDate'),
            'Event:EventDeadline': obj.get('data_limite') or obj.get('Event:EventDeadline'),
        }
    return {
        'Status': getattr(obj, 'status', '') or '',
        'SignatureDate': getattr(obj, 'data_assinatura', None),
        'Event:EventDeadline': getattr(obj, 'data_limite', None),
    }


def effective_training_situacao(obj, ref_date: date | None = None) -> str:
    """Situação recalculada na leitura (prazo ultrapassado = Vencido)."""
    return training_situacao_calculada(_training_row_dict(obj), ref_date or date.today())


def _kpis_from_qs(qs, ref_date: date | None = None):
    ref_date = ref_date or date.today()
    total = qs.count()
    empty = {
        'total': 0,
        'assinados': 0,
        'ministrados': 0,
        'previstos': 0,
        'pendentes': 0,
        'vencidos': 0,
        'a_vencer_7d': 0,
        'assinados_pct': 0,
        'pendentes_pct': 0,
        'vencidos_pct': 0,
        'a_vencer_pct': 0,
    }
    if not total:
        return empty

    assinados = 0
    vencidos = 0
    a_vencer = 0
    ministrados = 0
    previstos = 0
    for t in qs.only('status', 'data_assinatura', 'data_limite').iterator(chunk_size=500):
        sit = effective_training_situacao(t, ref_date)
        st = normalize_text(safe_str(t.status))
        if sit == 'Finalizado':
            assinados += 1
        elif sit == 'Vencido':
            vencidos += 1
        elif sit == 'A vencer':
            a_vencer += 1
        if sit != 'Finalizado' and st == 'ministrado':
            ministrados += 1
        if sit != 'Finalizado' and st == 'previsto':
            previstos += 1

    pendentes = max(0, total - assinados - vencidos)
    return {
        'total': total,
        'assinados': assinados,
        'ministrados': ministrados,
        'previstos': previstos,
        'pendentes': pendentes,
        'vencidos': vencidos,
        'a_vencer_7d': a_vencer,
        'assinados_pct': pct(assinados, total),
        'pendentes_pct': pct(pendentes, total),
        'vencidos_pct': pct(vencidos, total),
        'a_vencer_pct': pct(a_vencer, total),
    }


def build_training_full_payload(qs, params):
    ref_date = _ref_date_from_params(params)
    kpis = _kpis_from_qs(qs, ref_date=ref_date)
    total = kpis['total']
    por_status = list(qs.values('status').annotate(q=Count('id')).order_by('-q'))
    por_tipo = list(qs.values('tipo_acao_prefixo').annotate(q=Count('id')).order_by('-q'))
    por_localidade = list(qs.values('localidade').annotate(q=Count('id')).order_by('-q'))

    page = max(int(params.get('page', 1)), 1)
    page_size = min(max(int(params.get('page_size', 50)), 1), 200)
    start = (page - 1) * page_size
    items_qs = qs.order_by('-data_limite', 'nome_agente')[start:start + page_size]

    items = []
    for t in items_qs:
        sit = effective_training_situacao(t, ref_date)
        items.append({
            'titulo': t.titulo,
            'matricula': t.matricula,
            'nome_agente': t.nome_agente,
            'status': t.status,
            'situacao_calculada': sit,
            'situacao_stored': t.situacao_calculada or t.situacao,
            'tipo_acao_prefixo': t.tipo_acao_prefixo or t.tipo_acao,
            'data_limite': t.data_limite,
            'localidade': t.localidade,
        })

    return {
        **kpis,
        'por_status': [
            {'status': r['status'] or '—', 'qtd': r['q'], 'percentual': pct(r['q'], total)}
            for r in por_status
        ],
        'por_tipo': [
            {'tipo': r['tipo_acao_prefixo'] or '—', 'qtd': r['q'], 'percentual': pct(r['q'], total)}
            for r in por_tipo
        ],
        'por_localidade': [
            {'localidade': r['localidade'] or '—', 'qtd': r['q'], 'percentual': pct(r['q'], total)}
            for r in por_localidade
        ],
        'items': items,
        'count': total,
        'page': page,
        'page_size': page_size,
        'ref_date': ref_date.isoformat(),
        'pre_diagnostico': build_training_narrative(kpis),
    }


def _ref_month_from_params(params) -> date:
    return _ref_date_from_params(params)


def build_resumo_mes_payload(qs, ref: date | None = None):
    ref = ref or date.today()
    mes_qs = qs.filter(
        Q(data_limite__year=ref.year, data_limite__month=ref.month)
        | Q(data_atribuicao__year=ref.year, data_atribuicao__month=ref.month)
    )
    kpis = _kpis_from_qs(mes_qs, ref_date=ref)
    destinatarios = []
    for row in mes_qs.order_by('nome_agente', 'data_limite').values(
        'matricula', 'nome_agente', 'titulo', 'status', 'situacao_calculada',
        'data_limite', 'data_assinatura', 'localidade', 'tipo_acao_prefixo',
    )[:200]:
        row = dict(row)
        row['situacao_calculada'] = effective_training_situacao(row, ref)
        destinatarios.append(row)
    return {
        'mes': ref.strftime('%m/%Y'),
        **kpis,
        'destinatarios': destinatarios,
        'pre_diagnostico': build_training_narrative(kpis),
    }


def build_agent_trainings(qs, matricula, allowed_localidade=None, ref_date: date | None = None):
    qs = qs.filter(matricula__iexact=matricula)
    if allowed_localidade and allowed_localidade not in ('Geral', None, ''):
        qs = filter_qs_by_localidade(qs, 'localidade', allowed_localidade)
    ref_date = ref_date or date.today()
    items = []
    for row in qs.order_by('-data_limite').values(
        'titulo', 'status', 'situacao_calculada', 'data_limite', 'data_assinatura',
        'localidade', 'tipo_acao_prefixo',
    ):
        row = dict(row)
        row['situacao_calculada'] = effective_training_situacao(row, ref_date)
        items.append(row)
    return {'matricula': matricula, 'total': len(items), 'items': items}
