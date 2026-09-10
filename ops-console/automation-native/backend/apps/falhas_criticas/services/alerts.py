# -*- coding: utf-8 -*-
"""Alertas simples para o painel (sync, variação, treinamentos, suporte)."""
from datetime import datetime, timezone

from apps.falhas_criticas.services.sync_status import last_sync_info
from apps.falhas_criticas.services.analytics import build_executive_payload
from apps.falhas_criticas.services.dataframes import load_period_frames
from apps.falhas_criticas.services.training_analytics import _kpis_from_qs, _training_qs_filtered


def build_dashboard_alerts(user, query_params):
    alerts = []
    sync_info = last_sync_info()

    last_sync = sync_info.get('last_sync')
    last_failed = sync_info.get('last_sync_failed')
    fail_trigger = sync_info.get('last_sync_failed_trigger')
    if last_failed and last_sync and last_failed > last_sync:
        msg = sync_info.get('sync_error_message') or 'Verifique se o Excel está fechado.'
        if fail_trigger == 'system':
            title = 'Última sincronização automática falhou.'
        else:
            title = 'Última sincronização manual falhou.'
        alerts.append({
            'level': 'error',
            'message': f'{title} {msg}',
            'module': 'sync',
        })
    elif last_sync:
        diff_h = (datetime.now(timezone.utc) - last_sync).total_seconds() / 3600
        if diff_h > 24:
            alerts.append({
                'level': 'warning',
                'message': f'Dados podem estar desatualizados — última sync há {int(diff_h)}h.',
                'module': 'sync',
            })
    elif not last_sync:
        alerts.append({
            'level': 'warning',
            'message': 'Nenhuma sincronização registrada ainda.',
            'module': 'sync',
        })

    try:
        df_all, df_cur, df_prev, params, _ = load_period_frames(user, query_params)
        if not df_cur.empty:
            exec_p = build_executive_payload(df_all, df_cur, df_prev, params)
            le = exec_p.get('leitura_executiva', {})
            delta = le.get('variacao_delta', 0) or 0
            try:
                delta = int(delta)
            except (TypeError, ValueError):
                delta = 0
            total = exec_p.get('resumo_30s', {}).get('falhas', {}).get('valor') or 0
            if total and delta > 0:
                pct_str = le.get('variacao_perc', '')
                try:
                    pct_num = float(str(pct_str).replace('%', '').replace('+', '').strip())
                except ValueError:
                    pct_num = (delta / max(total - delta, 1)) * 100
                if pct_num >= 20:
                    alerts.append({
                        'level': 'warning',
                        'message': f'Volume de falhas subiu {pct_str or pct_num:.0f}% vs período anterior.',
                        'module': 'falhas',
                    })
    except Exception:
        pass

    try:
        df_all, df_cur, df_prev, params, _ = load_period_frames(user, query_params)
        train_k = _kpis_from_qs(_training_qs_filtered(params))
        vencidos = train_k.get('vencidos', 0) or 0
        if vencidos > 0:
            alerts.append({
                'level': 'warning',
                'message': f'{vencidos} treinamento(s) vencido(s) no escopo.',
                'module': 'treinamentos',
            })
    except Exception:
        pass

    try:
        from apps.falhas_criticas.services.dataframes import apply_support_filters, supports_qs_to_legacy_df
        from apps.falhas_criticas.models import Support
        from apps.falhas_criticas.scoping import scoped_query_params

        params, _ = scoped_query_params(user, query_params)
        qs = apply_support_filters(Support.objects.select_related('agent'), params)
        df_sup = supports_qs_to_legacy_df(qs)
        if not df_sup.empty and 'Crítico' in df_sup.columns:
            r3 = int((df_sup['Crítico'].astype(str).str.upper() == 'SIM').sum())
            if r3 >= 3:
                alerts.append({
                    'level': 'info',
                    'message': f'{r3} casos críticos (Regra 3) no período de suporte.',
                    'module': 'suporte',
                })
    except Exception:
        pass

    return alerts
