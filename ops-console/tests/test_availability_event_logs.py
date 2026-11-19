import contextlib
import io
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server_monitoring as monitoring


def test_historical_file_excerpt_keeps_traceback_and_excludes_other_times():
    start = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    content = (b'[2026-11-18T11:59:00Z] before\n'
               b'[2026-11-18T12:00:00Z] ERROR backend stopped\n  traceback detail\n'
               + b'[2026-11-18T13:00:00Z] recent\n' * 2000)
    with patch.object(Path, 'open', side_effect=lambda *a, **kw: io.BytesIO(content)):
        rows, status = monitoring._availability_file_log_excerpt(Path('unused'), start, start + timedelta(minutes=5))
        limited, partial = monitoring._availability_file_log_excerpt(Path('unused'), start, start + timedelta(minutes=5), max_bytes=100)
    assert rows == ['[2026-11-18T12:00:00Z] ERROR backend stopped', '  traceback detail']
    assert 'concluída' in status
    assert 'parcial' in partial
    assert len(limited) <= 2


@pytest.fixture
def evidence_store():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE monitor_events(id INTEGER PRIMARY KEY, environment TEXT, category TEXT,
            severity TEXT, title TEXT, detail TEXT, recorded_at TEXT);
        CREATE TABLE monitor_samples(id INTEGER PRIMARY KEY, environment TEXT, metric_key TEXT,
            value REAL, labels_json TEXT, recorded_at TEXT);
    ''')
    @contextlib.contextmanager
    def connection(_path):
        yield conn
    store = SimpleNamespace(get_connection=connection, query_service_log_lines=lambda *a, **kw: [])
    with patch.object(monitoring, '_import_ops_store', return_value=store), \
         patch.object(monitoring, 'resolve_ops_store_path', return_value=Path('unused')), \
         patch.object(monitoring, '_service_log_sources', return_value=[]):
        yield conn, store
    conn.close()


def test_event_log_includes_probe_failures_and_event_detail_from_selected_env(evidence_store):
    conn, store = evidence_store
    at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
    for env, title in [('MAIN', 'Backend offline'), ('DEV', 'Do not include')]:
        conn.execute('INSERT INTO monitor_events(environment,category,severity,title,detail,recorded_at) VALUES (?, ?, ?, ?, ?, ?)',
                     (env, 'availability', 'critical', title, 'Connection refused', at.isoformat()))
        conn.execute('INSERT INTO monitor_samples(environment,metric_key,value,recorded_at) VALUES (?, ?, ?, ?)',
                     (env, 'health_reachable', 0, at.isoformat()))
    store.query_service_log_lines = lambda env, **kw: [{'logged_at': at.isoformat(), 'service': 'backend', 'stream': 'stderr', 'line': 'ERROR worker stopped'}]
    body, mime, filename = monitoring.build_monitoring_availability_event_log({}, 'MAIN',
        since=(at - timedelta(minutes=5)).isoformat(), until=(at + timedelta(minutes=5)).isoformat(), event_at=at.isoformat())
    text = body.decode('utf-8-sig')
    assert 'Backend offline' in text and 'Connection refused' in text
    assert 'health_reachable=0' in text and 'ERROR worker stopped' in text
    assert 'Do not include' not in text
    assert filename.startswith('pplid-main-evento-') and filename.endswith('.log')
    assert mime.startswith('text/plain')


def test_empty_event_log_is_explicit_about_missing_evidence(evidence_store):
    at = datetime.now(timezone.utc) - timedelta(hours=1)
    body, _, _ = monitoring.build_monitoring_availability_event_log({}, 'MAIN',
        since=(at - timedelta(minutes=5)).isoformat(), until=(at + timedelta(minutes=5)).isoformat(), event_at=at.isoformat())
    text = body.decode('utf-8-sig')
    assert 'Nenhuma sondagem registrada' in text
    assert 'Nenhum log de serviço armazenado' in text
    assert 'não comprova ausência de falha' in text


def test_file_evidence_survives_missing_service_log_database(evidence_store):
    _, store = evidence_store
    at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
    content = f'[{at.isoformat()}] ERROR connection refused\n  stack frame\n'.encode()
    store.query_service_log_lines = Mock(side_effect=sqlite3.OperationalError('service logs unavailable'))
    with patch.object(monitoring, '_service_log_sources', return_value=[('backend', 'stderr', Path('backend.err.log'))]), \
         patch.object(Path, 'open', side_effect=lambda *a, **kw: io.BytesIO(content)):
        body, _, _ = monitoring.build_monitoring_availability_event_log({}, 'MAIN',
            since=(at - timedelta(minutes=5)).isoformat(), until=(at + timedelta(minutes=5)).isoformat(), event_at=at.isoformat())
    text = body.decode('utf-8-sig')
    assert 'Fonte indisponível' in text
    assert 'backend.err.log' in text
    assert 'ERROR connection refused\n  stack frame' in text


@pytest.mark.parametrize('since,until,at', [
    ('invalid', 'invalid', 'invalid'),
    ('2020-01-01T00:00:00Z', '2020-02-15T00:00:00Z', '2020-01-01T00:00:00Z'),
    ('2020-01-01T00:00:00Z', '2020-01-01T00:10:00Z', '2020-01-01T00:20:00Z'),
    ('2020-01-01T00:00:00', '2020-01-01T00:10:00', '2020-01-01T00:05:00'),
])
def test_event_log_rejects_invalid_or_unbounded_interval(since, until, at):
    with pytest.raises(ValueError):
        monitoring.build_monitoring_availability_event_log({}, 'MAIN', since=since, until=until, event_at=at)


def test_event_log_route_returns_download_and_validation_errors():
    import server
    handler = object.__new__(server.OpsConsoleHandler)
    handler.config = {}
    handler._send_attachment_bytes = Mock()
    handler._send_json = Mock()
    path = '/api/v1/monitoring/MAIN/availability-event-log'
    parsed = urlparse(path + '?since=2020-01-01T11:55:00Z&until=2020-01-01T12:05:00Z&at=2020-01-01T12:00:00Z')
    payload = (b'event evidence', 'text/plain; charset=utf-8', 'main-event.log')
    with patch.object(monitoring, 'build_monitoring_availability_event_log', return_value=payload) as build:
        handler._handle_monitoring_get(path, parsed)
        build.assert_called_once_with({}, 'MAIN', since='2020-01-01T11:55:00Z',
                                      until='2020-01-01T12:05:00Z', event_at='2020-01-01T12:00:00Z')
        handler._send_attachment_bytes.assert_called_once_with(*payload)
    with patch.object(monitoring, 'build_monitoring_availability_event_log', side_effect=ValueError('Intervalo inválido')):
        handler._handle_monitoring_get(path, parsed)
        handler._send_json.assert_called_once_with({'error': 'Intervalo inválido'}, status=400)


def test_queue_diagnostic_rotations_export_offline_preserves_event_time_and_pid(evidence_store):
    _, store = evidence_store
    at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
    original = at.isoformat()
    ingested = (at + timedelta(seconds=30)).isoformat()
    line = f'{original} WARNING waitress.queue {{"event_at":"{original}","environment":"MAIN","pid":4321,"pending_requests":73,"running_requests":16,"route":"/api/report/{{id}}/"}}'
    store.query_service_log_lines = lambda *a, **kw: [
        {'logged_at': ingested, 'service': 'backend', 'stream': 'queue', 'line': line}]
    opened = []
    def open_log(path, *args, **kwargs):
        opened.append(path.name)
        if path.name.endswith('.1'):
            return io.BytesIO((line + '\n').encode())
        raise FileNotFoundError()
    with patch.object(monitoring, '_service_log_sources', return_value=[('backend', 'queue', Path('PPLID_MAIN.waitress.queue.log'))]), \
         patch.object(Path, 'open', open_log), \
         patch.object(monitoring, 'fetch_backend_api_inflight', side_effect=AssertionError('must work offline')):
        body, _, _ = monitoring.build_monitoring_availability_event_log({}, 'MAIN',
            since=(at - timedelta(minutes=1)).isoformat(), until=(at + timedelta(minutes=1)).isoformat(), event_at=original)
    text = body.decode('utf-8-sig')
    assert f'ingestão={ingested}' in text
    assert line in text and '"pid":4321' in text
    assert 'Arquivo não encontrado' in text
    assert len(opened) == 4
    assert 'não comprovam causa raiz' in text


def test_queue_source_uses_configured_local_path():
    paths = monitoring._service_log_sources({'logDir': 'logs', 'HOM': {'repoName': 'PPLID_HOM'}}, 'HOM')
    assert ('backend', 'queue', Path('logs/PPLID_HOM.waitress.queue.log')) in paths
    custom = monitoring._service_log_sources({'HOM': {'queueDiagnosticsLog': 'custom/queue.log'}}, 'HOM')
    assert ('backend', 'queue', Path('custom/queue.log')) in custom


def test_queue_ingestion_preserves_complete_lines_and_separate_clock():
    event = '2026-11-18T12:00:00Z WARNING waitress.queue {"event_at":"2026-11-18T12:00:00Z","pid":99}\n'
    source = Path('queue-ingestion-contract.log')
    key = str(source.resolve())
    store = SimpleNamespace(append_service_log=Mock())
    monitoring._SERVICE_LOG_FILE_OFFSETS.pop(key, None)
    content = event.encode()
    def ingest():
        with patch.object(monitoring, '_service_log_sources', return_value=[('backend', 'queue', source)]), \
             patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'stat', return_value=SimpleNamespace(st_size=len(content))), \
             patch.object(Path, 'open', side_effect=lambda *a, **kw: io.BytesIO(content)), \
             patch.object(monitoring, '_utc_now_iso', return_value='2026-11-18T13:00:00Z'):
            return monitoring._ingest_service_log_files({}, 'MAIN', store, Path('unused'))
    try:
        assert len(ingest()) == 1
        content += event.encode() + b'partial'
        rows = ingest()
        assert len(rows) == 1
        assert rows[0]['line'] == event.strip()
        assert rows[0]['logged_at'] == '2026-11-18T13:00:00Z'
        content += b' completed\n'
        assert ingest()[0]['line'] == 'partial completed'
    finally:
        monitoring._SERVICE_LOG_FILE_OFFSETS.pop(key, None)
