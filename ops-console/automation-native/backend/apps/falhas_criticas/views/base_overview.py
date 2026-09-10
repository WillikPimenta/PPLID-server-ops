# -*- coding: utf-8 -*-
from pathlib import Path

from rest_framework.response import Response

from apps.falhas_criticas.models import (
    Contestation,
    Failure,
    FalhasAgent,
    Support,
    SyncAuditLog,
    Training,
)
from apps.falhas_criticas.permissions import IsAuthenticatedPortal, HasDataScope, IsBaseAuditoriaRole
from apps.falhas_criticas.scoping import get_user_scope, scoped_query_params
from apps.falhas_criticas.services.data_bounds import get_portal_data_bounds
from apps.falhas_criticas.services.localidade import filter_qs_by_localidade
from apps.falhas_criticas.views.base import ScopedAPIView

IMPORT_NOTE = (
    'A base exibida no portal já considera contestações e falhas desconsideradas '
    'aplicadas durante a importação da planilha.'
)


def _serialize_last_sync(log: SyncAuditLog | None) -> dict | None:
    if not log:
        return None
    path_raw = (log.path or '').strip()
    filename = Path(path_raw).name if path_raw else ''
    username = log.user.username if log.user_id and log.user else ''
    stats = log.stats if isinstance(log.stats, dict) and log.stats else None
    return {
        'started_at': log.started_at,
        'finished_at': log.finished_at,
        'success': log.success,
        'filename': filename,
        'username': username,
        'duration_seconds': log.duration_seconds,
        'message': log.message or '',
        'stats': stats,
    }


def _db_totals_global() -> dict:
    return {
        'failures': Failure.objects.count(),
        'support': Support.objects.count(),
        'training': Training.objects.count(),
        'contestations': Contestation.objects.count(),
        'agents': FalhasAgent.objects.count(),
    }


def _scoped_totals_for_localidade(localidade: str) -> dict | None:
    """Totais filtrados por localidade canônica. None quando recorte = base completa."""
    if not localidade or localidade in ('Geral', '__BLOCKED__'):
        return None
    return {
        'failures': filter_qs_by_localidade(Failure.objects.all(), 'localidade', localidade).count(),
        'support': filter_qs_by_localidade(Support.objects.all(), 'localidade', localidade).count(),
        'training': filter_qs_by_localidade(Training.objects.all(), 'localidade', localidade).count(),
        'contestations': filter_qs_by_localidade(
            Contestation.objects.all(), 'localidade', localidade
        ).count(),
        'agents': filter_qs_by_localidade(FalhasAgent.objects.all(), 'localidade', localidade).count(),
    }


class BaseOverviewView(ScopedAPIView):
    permission_classes = [IsAuthenticatedPortal, HasDataScope, IsBaseAuditoriaRole]

    def get(self, request):
        params, _scope_meta = scoped_query_params(request.user, request.query_params)
        user_scope = get_user_scope(request.user)
        is_global_scope = user_scope.get('scope') == 'global'

        localidade = (params.get('localidade') or 'Geral').strip()
        if localidade == '__BLOCKED__':
            scope_label = user_scope.get('localidade_forcada') or '—'
            scoped_totals = {
                'failures': 0,
                'support': 0,
                'training': 0,
                'contestations': 0,
                'agents': 0,
            }
        else:
            scope_label = localidade or 'Geral'
            scoped_totals = _scoped_totals_for_localidade(localidade)

        bounds = get_portal_data_bounds()
        last_success = (
            SyncAuditLog.objects.filter(success=True)
            .select_related('user')
            .order_by('-finished_at')
            .first()
        )
        last_sync = last_success
        if not last_sync:
            last_sync = SyncAuditLog.objects.select_related('user').order_by('-started_at').first()

        return Response({
            'db_totals': _db_totals_global(),
            'scoped_totals': scoped_totals,
            'scope_label': scope_label,
            'is_global_scope': is_global_scope,
            'last_sync': _serialize_last_sync(last_sync),
            'data_bounds': {
                'min_date': bounds.get('min_date'),
                'max_date': bounds.get('max_date'),
            },
            'import_note': IMPORT_NOTE,
        })
