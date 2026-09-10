# -*- coding: utf-8 -*-
import pandas as pd
from django.db.models import Q
from report_falhas.io.data_loader import norm_matricula
from report_falhas.reincidence import reincidencia_table_full
from rest_framework import status
from rest_framework.response import Response

from apps.falhas_criticas.models import FalhasAgent, Contestation, Failure, Support, Training
from apps.falhas_criticas.permissions import HasDataScope, IsAuthenticatedPortal, IsBaseAuditoriaRole, IsGlobalOnly
from apps.falhas_criticas.scoping import filter_localidades_for_user, scoped_query_params
from apps.falhas_criticas.services.localidade import filter_qs_by_localidade
from apps.falhas_criticas.services.alerts import build_dashboard_alerts
from apps.falhas_criticas.services.analytics import (
    _enrich_reinc_rows,
    build_charts_payload,
    build_clientes_workflows,
    build_consolidado_payload,
    build_contestacoes_summary,
    build_executive_payload,
    build_rank_delta_payload,
    build_reincidence_by_turno,
    build_ult3m_payload,
)
from apps.falhas_criticas.services.compare_analytics import build_comparativo_bsb_sc
from apps.falhas_criticas.services.dataframes import (
    apply_failure_filters,
    apply_support_filters,
    failures_qs_to_legacy_df,
    load_period_frames,
    supports_qs_to_legacy_df,
    _apply_matricula_team,
)
from apps.falhas_criticas.services.formatters import fmt_protocolo
from apps.falhas_criticas.services.narrative import pre_diagnostico_block
from apps.falhas_criticas.services.support_analytics import build_support_full_payload
from apps.falhas_criticas.services.support_falhas_bridge import build_support_falhas_bridge
from apps.falhas_criticas.services.training_analytics import (
    _ref_month_from_params,
    _training_qs_filtered,
    build_agent_trainings,
    build_resumo_mes_payload,
    build_training_full_payload,
)
from apps.falhas_criticas.utils_metrics import pct as _pct
from apps.falhas_criticas.views.base import ScopedAPIView

class FilterOptionsView(ScopedAPIView):
    def get(self, request):
        params, scope = self.scoped_params(request)
        qs = Failure.objects.all()
        qs = apply_failure_filters(qs, {k: v for k, v in params.items() if k not in ('start_date', 'end_date')})

        localidades = list(qs.values_list('localidade', flat=True).distinct())
        localidades = filter_localidades_for_user(request.user, [l for l in localidades if l])

        turnos = sorted([t for t in FalhasAgent.objects.values_list('turno_atual', flat=True).distinct() if t])
        from apps.falhas_criticas.services.data_bounds import get_portal_data_bounds
        from apps.falhas_criticas.services.team_hierarchy import get_team_context

        bounds = get_portal_data_bounds()
        team_ctx = get_team_context(request.user)

        # Colaboradores no escopo (com falhas registradas), para o filtro "ver somente meu time"
        agentes = []
        seen = set()
        for r in qs.values('agent__matricula_norm', 'agent__name').order_by('agent__name'):
            mat = r.get('agent__matricula_norm')
            if not mat or mat in seen:
                continue
            seen.add(mat)
            agentes.append({'matricula': mat, 'nome': r.get('agent__name') or mat})
        agentes.sort(key=lambda a: (a['nome'] or '').lower())

        forced = scope.get('localidade_forcada')
        default_loc = forced if forced and forced != '__BLOCKED__' else 'Geral'

        return Response({
            'localidades': localidades,
            'turnos': turnos,
            'agentes': agentes,
            'min_date': bounds['min_date'],
            'max_date': bounds['max_date'],
            'date_picker_max': bounds['date_picker_max'],
            'default_end_date': bounds['default_end_date'],
            'default_start_date': bounds['default_start_date'],
            'team': team_ctx,
            'scope': scope,
            'default_localidade': default_loc,
        })


class ExecutiveView(ScopedAPIView):
    def get(self, request):
        df_all, df_cur, df_prev, params, scope = self.load_frames(request)
        payload = build_executive_payload(df_all, df_cur, df_prev, params)
        payload['scope'] = scope
        insights = payload.get('insights', [])
        payload['pre_diagnostico'] = pre_diagnostico_block(
            veredito=insights[0] if insights else 'Sem dados no período.',
            bullets=insights[1:5],
        )
        return Response(payload)


class ConsolidadoView(ScopedAPIView):
    def get(self, request):
        df_all, df_cur, df_prev, params, scope = self.load_frames(request)
        payload = build_consolidado_payload(df_all, df_cur, df_prev, params)
        payload['scope'] = scope
        if scope.get('scope') == 'global':
            por_loc = {}
            qp = request.query_params.copy()
            for loc in ('Brasília', 'São Carlos', 'Geral'):
                qp_loc = qp.copy()
                qp_loc['localidade'] = loc
                da, dc, dp, p_loc, _ = load_period_frames(request.user, qp_loc)
                por_loc[loc] = build_consolidado_payload(da, dc, dp, p_loc)['tiles']
            payload['por_localidade'] = por_loc
        return Response(payload)


class RankDeltaView(ScopedAPIView):
    def get(self, request):
        df_all, df_cur, df_prev, params, _ = self.load_frames(request)
        col = request.query_params.get('col', 'Cenário Unificado')
        payload = {
            'cenario': build_rank_delta_payload(df_cur, df_prev, 'Cenário Unificado'),
            'novo_cenario': build_rank_delta_payload(df_cur, df_prev, 'Novo cenário'),
            'formatacao_fonte': build_rank_delta_payload(
                df_cur[df_cur['Cenário Unificado'].astype(str).str.contains('format|fonte|desalinh', case=False, na=False)]
                if not df_cur.empty and 'Cenário Unificado' in df_cur.columns else df_cur,
                df_prev,
                'UF do documento' if 'UF do documento' in df_cur.columns else col,
            ),
        }
        return Response(payload)


class ReincidenceByTurnoView(ScopedAPIView):
    def get(self, request):
        df_all, df_cur, df_prev, params, _ = self.load_frames(request)
        df_reinc = reincidencia_table_full(df_prev, df_cur)
        payload = build_reincidence_by_turno(df_reinc, df_cur)

        page = max(int(request.query_params.get('page', 1)), 1)
        page_size = min(max(int(request.query_params.get('page_size', 50)), 1), 200)
        turno_filter = request.query_params.get('turno')
        all_rows = []
        for turno, rows in payload['tabelas'].items():
            if turno_filter and turno.lower() != turno_filter.lower():
                continue
            for row in rows:
                row['turno'] = turno
                all_rows.append(row)
        total = len(all_rows)
        start = (page - 1) * page_size
        end = start + page_size

        summary = payload['turnos']
        total_reincidentes = sum(t.get('reincidentes', 0) for t in summary)
        total_ocorrencias = sum(t.get('ocorrencias', 0) for t in summary)
        total_agentes = sum(t.get('agentes', 0) for t in summary)
        turno_dom = max(summary, key=lambda t: t.get('reincidentes', 0), default=None)

        from apps.falhas_criticas.utils_metrics import pct as _pct
        bullets = []
        if turno_dom and turno_dom.get('reincidentes'):
            bullets.append(
                f"Turno com mais reincidentes: {turno_dom['turno']} "
                f"({turno_dom['reincidentes']} agentes)."
            )
        if total_agentes:
            bullets.append(
                f"{total_reincidentes} reincidentes em {total_agentes} agentes monitorados "
                f"({_pct(total_reincidentes, total_agentes) or 0}%)."
            )

        return Response({
            'summary': summary,
            'results': all_rows[start:end],
            'count': total,
            'page': page,
            'page_size': page_size,
            'kpis': {
                'total_reincidentes': total_reincidentes,
                'total_ocorrencias': total_ocorrencias,
                'total_agentes': total_agentes,
                'turno_dominante': turno_dom['turno'] if turno_dom else '—',
                'reinc_pct': _pct(total_reincidentes, total_agentes) or 0,
            },
            'pre_diagnostico': pre_diagnostico_block(
                veredito=f"{total_reincidentes} agentes reincidentes no recorte, em {len(summary)} turnos.",
                bullets=bullets,
                titulo='Leitura — Reincidência',
            ),
        })


class ClientesWorkflowsView(ScopedAPIView):
    def get(self, request):
        _, df_cur, _, params, _ = self.load_frames(request)
        payload = build_clientes_workflows(df_cur)

        # Origem dos apontamentos: Auditoria (falhas) x Contestação
        falhas_total = payload.get('total', 0)
        cont_qs = Contestation.objects.all()
        loc = params.get('localidade')
        if loc == '__BLOCKED__':
            cont_qs = cont_qs.none()
        else:
            cont_qs = filter_qs_by_localidade(cont_qs, 'localidade', loc or 'Geral')
        start_date = params.get('start_date')
        end_date = params.get('end_date')
        if start_date:
            cont_qs = cont_qs.filter(data__gte=start_date)
        if end_date:
            cont_qs = cont_qs.filter(data__lte=end_date)

        cont_total = cont_qs.count()
        cont_interna = cont_qs.filter(fonte__iexact='Interna').count()
        cont_externa = cont_qs.filter(fonte__iexact='Externa').count()
        origem_total = falhas_total + cont_total

        from apps.falhas_criticas.utils_metrics import pct as _pct
        payload['origem'] = {
            'auditoria': falhas_total,
            'auditoria_pct': _pct(falhas_total, origem_total) or 0,
            'contestacao': cont_total,
            'contestacao_pct': _pct(cont_total, origem_total) or 0,
            'contestacao_interna': cont_interna,
            'contestacao_externa': cont_externa,
            'total': origem_total,
        }

        top_cli = payload.get('top_clientes') or []
        top_wf = payload.get('top_workflows') or []
        bullets = []
        if top_cli:
            bullets.append(
                f"Cliente com mais falhas: {top_cli[0]['nome']} "
                f"({top_cli[0]['qtd']} — {top_cli[0]['percentual']}% do total)."
            )
        if top_wf:
            bullets.append(
                f"Workflow mais recorrente: {top_wf[0]['nome']} "
                f"({top_wf[0]['qtd']} — {top_wf[0]['percentual']}%)."
            )
        if cont_total:
            bullets.append(
                f"{cont_total} contestações no período ({payload['origem']['contestacao_pct']}% dos apontamentos) — "
                f"{cont_interna} internas, {cont_externa} externas."
            )
        payload['pre_diagnostico'] = pre_diagnostico_block(
            veredito=(
                f"{falhas_total} falhas de auditoria e {cont_total} contestações compõem os apontamentos do período."
            ),
            bullets=bullets,
            titulo='Leitura — Clientes e Workflows',
        )
        return Response(payload)


class Ult3mView(ScopedAPIView):
    def get(self, request):
        df_all, df_cur, _, params, _ = self.load_frames(request)
        scope_only = dict(params)
        scope_only.pop('start_date', None)
        scope_only.pop('end_date', None)
        from apps.falhas_criticas.models import Failure
        df_scope = failures_qs_to_legacy_df(
            apply_failure_filters(Failure.objects.select_related('agent'), scope_only)
        )
        return Response(build_ult3m_payload(df_scope, params) | {
            'pre_diagnostico': pre_diagnostico_block(
                veredito='Radar ULT3M — agentes com maior concentração de falhas nos últimos 3 meses.',
                titulo='Leitura ULT3M',
            ),
        })


class TreinamentosView(ScopedAPIView):
    def get(self, request):
        params, scope = self.scoped_params(request)
        params = dict(params)
        params['search'] = request.query_params.get('search', '')
        params['page'] = request.query_params.get('page', 1)
        params['page_size'] = request.query_params.get('page_size', 50)
        qs = _training_qs_filtered(params)
        try:
            from apps.falhas_criticas.services.training_reincidence_bridge import (
                build_training_reincidence_bridge,
            )
            bridge = build_training_reincidence_bridge(
                qs, params, user=request.user, query_params=request.query_params,
            )
        except Exception:
            from apps.falhas_criticas.services.training_reincidence_bridge import _empty_bridge
            bridge = _empty_bridge()
        bridge_only = request.query_params.get('bridge_only', '').strip().lower() in ('true', '1', 'yes')
        if bridge_only:
            return Response({'bridge_reincidencia': bridge})
        payload = build_training_full_payload(qs, params)
        payload['bridge_reincidencia'] = bridge
        payload['scope'] = scope
        return Response(payload)


class TreinamentosResumoMesView(ScopedAPIView):
    def get(self, request):
        params, scope = self.scoped_params(request)
        qs = _training_qs_filtered(params)
        ref = _ref_month_from_params(params)
        payload = build_resumo_mes_payload(qs, ref=ref)
        payload['scope'] = scope
        return Response(payload)


class TreinamentosAgenteView(ScopedAPIView):
    def get(self, request, matricula):
        params, scope = self.scoped_params(request)
        allowed = scope.get('localidade_forcada')
        qs = _training_qs_filtered(params)
        payload = build_agent_trainings(qs, matricula, allowed_localidade=allowed)
        if payload['total'] == 0 and allowed and allowed != '__BLOCKED__':
            return Response({'error': 'Agente sem treinamentos no escopo permitido.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(payload)


class ComparativoBsbScView(ScopedAPIView):
    permission_classes = [IsAuthenticatedPortal, HasDataScope, IsGlobalOnly]

    def get(self, request):
        params, scope = self.scoped_params(request)
        payload = build_comparativo_bsb_sc(request.user, params)
        payload['scope'] = scope
        return Response(payload)


class ContestacoesView(ScopedAPIView):
    permission_classes = [IsAuthenticatedPortal, HasDataScope, IsBaseAuditoriaRole]

    def get(self, request):
        params, scope = self.scoped_params(request)
        qs = Contestation.objects.all()
        loc = params.get('localidade')
        if loc == '__BLOCKED__':
            qs = qs.none()
        else:
            qs = filter_qs_by_localidade(qs, 'localidade', loc or 'Geral')
        from apps.falhas_criticas.services.dataframes import _apply_matricula_team
        qs = _apply_matricula_team(qs, params)
        start_date = params.get('start_date')
        end_date = params.get('end_date')
        if start_date:
            qs = qs.filter(data__gte=start_date)
        if end_date:
            qs = qs.filter(data__lte=end_date)
        return Response(build_contestacoes_summary(qs))


class DashboardKPIsView(ScopedAPIView):
    def get(self, request):
        df_all, df_cur, df_prev, params, _ = self.load_frames(request)
        payload = build_executive_payload(df_all, df_cur, df_prev, params)
        le = payload['leitura_executiva']
        res = payload['resumo_30s']
        suporte_resumo = {'total': 0, 'nc_oficial_pct': 0}
        try:
            qs_sup = apply_support_filters(Support.objects.all(), params)
            df_sup = supports_qs_to_legacy_df(qs_sup)
            sup_payload = build_support_full_payload(df_sup, params)
            suporte_resumo = {
                'total': sup_payload.get('total', 0),
                'nc_oficial_pct': sup_payload.get('nc_oficial_pct', 0),
            }
        except Exception:
            pass
        return Response({
            'total_falhas': res['falhas']['valor'],
            'total_anterior': res['falhas']['valor'] - (res['falhas'].get('delta_abs') or 0),
            'variacao_delta': le['variacao_delta'],
            'variacao_perc': le['variacao_perc'],
            'reinc_total': le['reinc_total'],
            'reinc_pct': le.get('reinc_pct'),
            'novos_mes_count': le['novos_mes_count'],
            'novos_pct': le.get('novos_pct'),
            'top1_cenario_nome': le['top1_cenario_nome'],
            'top1_cenario_qtd': le['top1_cenario_qtd'],
            'top1_cenario_pct': le.get('top1_cenario_pct'),
            'resumo_30s': res,
            'suporte_resumo': suporte_resumo,
            'pre_diagnostico': pre_diagnostico_block(
                veredito=payload.get('insights', [''])[0],
                bullets=payload.get('insights', [])[1:4],
            ),
        })


class ChartsView(ScopedAPIView):
    def get(self, request):
        df_all, df_cur, _, params, _ = self.load_frames(request)
        charts = build_charts_payload(df_cur, df_all, params)
        turno_qualidade = charts.get('qualidade_turno_pct', {})
        legacy_qualidade = {}
        for turno, data in turno_qualidade.items():
            if turno.startswith('_'):
                continue
            legacy_qualidade[turno] = {
                'Fácil': data.get('Fácil', {}).get('qtd', 0),
                'Médio': data.get('Médio', {}).get('qtd', 0),
                'Difícil': data.get('Difícil', {}).get('qtd', 0),
            }
        charts['qualidade_turno'] = legacy_qualidade
        return Response(charts)


class ReincidenceListView(ScopedAPIView):
    def get(self, request):
        df_all, df_cur, df_prev, params, _ = self.load_frames(request)
        df_reinc = reincidencia_table_full(df_prev, df_cur)
        from apps.falhas_criticas.services.analytics import _enrich_reinc_rows
        rows = _enrich_reinc_rows(df_reinc, df_cur)

        search = request.query_params.get('search', '').strip().lower()
        if search:
            rows = [
                r for r in rows
                if search in r['matricula'].lower() or search in r.get('nome', '').lower()
            ]

        def _is_reinc_sim(row):
            return (row.get('reincidente') or '').lower() == 'sim'

        filtro = request.query_params.get('filtro', 'todos').strip().lower()
        if filtro == 'reincidentes':
            rows = [r for r in rows if _is_reinc_sim(r)]
        elif filtro == 'alta_frequencia':
            rows = [r for r in rows if r['ocorrencias'] > 1 and not _is_reinc_sim(r)]
        else:
            rows = [r for r in rows if r['ocorrencias'] > 1 or _is_reinc_sim(r)]
        rows.sort(key=lambda x: x['ocorrencias'], reverse=True)

        page = max(int(request.query_params.get('page', 1)), 1)
        page_size = min(max(int(request.query_params.get('page_size', 50)), 1), 200)
        total = len(rows)
        start = (page - 1) * page_size

        legacy = [{
            'matricula': r['matricula'],
            'name': r.get('nome') or 'N/A',
            'ocorrencias': r['ocorrencias'],
            'reincidente': r.get('reincidente', ''),
            'percentual': r.get('percentual'),
            'top_cenario': r.get('cenario', ''),
            'tempo_casa': r.get('tempo_casa', '—'),
            'tempo_etapa': r.get('tempo_etapa', '—'),
            'atividade': r.get('atividade_hc', ''),
            'turno': r.get('turno', ''),
        } for r in rows[start:start + page_size]]

        return Response({
            'results': legacy,
            'count': total,
            'page': page,
            'page_size': page_size,
        })


class AgentDetailView(ScopedAPIView):
    def get(self, request, matricula):
        params, scope = self.scoped_params(request)
        matricula_norm = norm_matricula(matricula)
        mat_forced = scope.get('matricula_forcada')
        if mat_forced == '__BLOCKED__':
            return Response({'error': 'Sem escopo de agente.'}, status=status.HTTP_403_FORBIDDEN)
        if mat_forced and matricula_norm != mat_forced:
            return Response(
                {'error': 'Acesso restrito ao próprio perfil.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            agent = FalhasAgent.objects.get(matricula_norm=matricula_norm)
        except FalhasAgent.DoesNotExist:
            return Response({'error': 'Agente não encontrado'}, status=status.HTTP_404_NOT_FOUND)

        failures = apply_failure_filters(
            Failure.objects.filter(agent=agent).select_related('agent'),
            params,
        ).order_by('-data_analise')
        failures_data = [{
            'protocolo': fmt_protocolo(f.protocolo),
            'data_analise': f.data_analise,
            'data_auditoria': f.data_auditoria,
            'etapa': f.etapa,
            'categoria': f.categoria,
            'cenario': f.cenario,
            'cliente': f.cliente,
            'localidade': f.localidade,
            'tipo_falha': f.tipo_falha,
            'modulo': f.modulo,
        } for f in failures]

        supports = apply_support_filters(
            Support.objects.filter(agent=agent).select_related('agent'),
            params,
        ).order_by('-data')
        supports_data = [{
            'protocolo': fmt_protocolo(s.protocolo),
            'data': s.data,
            'cliente': s.cliente,
            'workflow': s.workflow,
            'tipo_solicitacao': s.tipo_solicitacao,
            'conformidade': s.conformidade_regra or s.conformidade,
            'dificuldade': s.dificuldade,
            'critico': s.critico,
        } for s in supports]

        train_params = dict(params)
        train_params['matricula'] = matricula_norm
        trainings = _training_qs_filtered(train_params).order_by('-data_limite')
        trainings_data = [{
            'titulo': t.titulo,
            'tipo': t.tipo_acao_prefixo or t.tipo_acao,
            'status': t.status,
            'situacao': t.situacao_calculada or t.situacao,
            'data_limite': t.data_limite,
            'data_assinatura': t.data_assinatura,
            'localidade': t.localidade,
        } for t in trainings]
        trainings_pendentes = sum(
            1 for t in trainings_data
            if (t['situacao'] or '').lower() in ('vencido', 'a vencer', 'previsto', 'dentro do prazo', 'pendente de assinatura do agente')
        )

        oficial = params.get('oficial')
        if oficial is True or oficial == 'true':
            oficial_label = True
        elif oficial is False or oficial == 'false':
            oficial_label = False
        else:
            oficial_label = bool(oficial)

        return Response({
            'matricula': agent.matricula_norm,
            'name': agent.name,
            'admissao_date': agent.admissao_date,
            'tempo_casa': agent.tempo_casa,
            'tempo_etapa': agent.tempo_etapa,
            'atividade_atual': agent.atividade_atual,
            'turno_atual': agent.turno_atual,
            'localidade': agent.localidade,
            'failures': failures_data,
            'failures_count': len(failures_data),
            'supports': supports_data,
            'supports_count': len(supports_data),
            'trainings': trainings_data,
            'trainings_count': len(trainings_data),
            'trainings_pendentes': trainings_pendentes,
            'filter_context': {
                'start_date': params.get('start_date'),
                'end_date': params.get('end_date'),
                'localidade': params.get('localidade') or 'Geral',
                'oficial': oficial_label,
                'scope': scope.get('scope'),
            },
        })


class MatrixHeatmapView(ScopedAPIView):
    def get(self, request):
        _, df_cur, _, _, _ = self.load_frames(request)
        empty = {
            'rows': [], 'cols': [], 'matrix': [],
            'cenario_rows': [], 'cenario_cols': [], 'matrix_cenario_uf': [],
            'cenario_doc_rows': [], 'cenario_doc_cols': [], 'matrix_cenario_doc': [],
        }
        if df_cur.empty:
            return Response(empty)

        df_cur = df_cur.copy()
        df_cur['tipo_doc'] = df_cur.get('Tipo de documento', pd.Series(dtype=str)).fillna('Desconhecido').replace('', 'Desconhecido')
        df_cur['uf'] = df_cur.get('UF do documento', pd.Series(dtype=str)).fillna('Desconhecido').replace('', 'Desconhecido')
        df_cur['cenario'] = df_cur.get('Cenário Unificado', pd.Series(dtype=str)).fillna('Desconhecido').replace('', 'Desconhecido')

        def _build_matrix(row_col, col_col, top_rows=8, top_cols=12):
            top_rows_list = df_cur[row_col].value_counts().head(top_rows).index.tolist()
            top_cols_list = df_cur[col_col].value_counts().head(top_cols).index.tolist()
            df_f = df_cur[df_cur[row_col].isin(top_rows_list) & df_cur[col_col].isin(top_cols_list)]
            cross = df_f.groupby([row_col, col_col]).size().unstack(fill_value=0)
            matrix = []
            for r in top_rows_list:
                vals = {}
                for c in top_cols_list:
                    vals[c] = int(cross.loc[r, c]) if r in cross.index and c in cross.columns else 0
                matrix.append({row_col: r, 'valores': vals})
            return top_rows_list, top_cols_list, matrix

        top_types, top_ufs, heatmap_data = _build_matrix('tipo_doc', 'uf', 8, 12)
        top_cenarios, top_ufs2, cenario_uf = _build_matrix('cenario', 'uf', 8, 12)
        top_cenarios2, top_docs, cenario_doc = _build_matrix('cenario', 'tipo_doc', 8, 8)

        return Response({
            'rows': top_types,
            'cols': top_ufs,
            'matrix': [{'tipo_doc': r['tipo_doc'], 'valores': r['valores']} for r in heatmap_data],
            'cenario_rows': top_cenarios,
            'cenario_cols': top_ufs2,
            'matrix_cenario_uf': [{'cenario': r['cenario'], 'valores': r['valores']} for r in cenario_uf],
            'cenario_doc_rows': top_cenarios2,
            'cenario_doc_cols': top_docs,
            'matrix_cenario_doc': [{'cenario': r['cenario'], 'valores': r['valores']} for r in cenario_doc],
        })


class SupportStatsView(ScopedAPIView):
    def get(self, request):
        params, _ = self.scoped_params(request)
        qs = Support.objects.select_related('agent')
        qs_cur = apply_support_filters(qs, params)
        search = request.query_params.get('search', '').strip()
        if search:
            qs_cur = qs_cur.filter(
                Q(agent__matricula_norm__icontains=search)
                | Q(agent__name__icontains=search)
                | Q(agente_nome_planilha__icontains=search)
            )
        p_all = dict(params)
        p_all.pop('start_date', None)
        p_all.pop('end_date', None)
        qs_all = apply_support_filters(qs, p_all)
        df_cur = supports_qs_to_legacy_df(qs_cur)
        df_all = supports_qs_to_legacy_df(qs_all)
        try:
            bridge = build_support_falhas_bridge(
                df_cur, params, user=request.user, query_params=request.query_params,
            )
        except Exception:
            from apps.falhas_criticas.services.support_falhas_bridge import _empty_bridge
            bridge = _empty_bridge()
        bridge_only = request.query_params.get('bridge_only', '').strip().lower() in ('true', '1', 'yes')
        if bridge_only:
            return Response({'bridge_falhas': bridge})
        payload = build_support_full_payload(df_cur, params, df_all)
        payload['bridge_falhas'] = bridge
        return Response(payload)


class AlertsView(ScopedAPIView):
    def get(self, request):
        alerts = build_dashboard_alerts(request.user, request.query_params)
        return Response({'alerts': alerts})


class DashboardSummaryView(ScopedAPIView):
    """Agregador Fase 4 — Início (kpis, executive, charts, reincidentes, alerts, comparativo)."""

    def get(self, request):
        from apps.falhas_criticas.services.aggregators import build_dashboard_summary
        return Response(build_dashboard_summary(request.user, request.query_params))


class DiagnosticoSummaryView(ScopedAPIView):
    """Agregador Fase 4 — Diagnóstico (rank-delta, matrix, clientes, consolidado, bridges)."""

    def get(self, request):
        from apps.falhas_criticas.services.aggregators import build_diagnostico_summary
        return Response(build_diagnostico_summary(request.user, request.query_params))


class FailureCasesView(ScopedAPIView):
    """Drill-down: casos (falhas) filtrados por cenário / UF / documento."""

    def get(self, request):
        from apps.falhas_criticas.services.failure_cases import build_failure_cases

        limit = request.query_params.get('limit', 50)
        return Response(build_failure_cases(request.user, request.query_params, limit=limit))


class ReincidenceSummaryView(ScopedAPIView):
    """Agregador Fase 5 — Reincidência (by_turno, list, ult3m, charts)."""

    def get(self, request):
        from apps.falhas_criticas.services.aggregators import build_reincidence_summary
        return Response(build_reincidence_summary(request.user, request.query_params))

