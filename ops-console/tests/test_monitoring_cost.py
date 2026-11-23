"""Monitoring should observe load without multiplying reads or commits."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest
import server_monitoring as sm

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
import ops_store


@pytest.fixture(autouse=True)
def clear_metrics_reads():
    sm._METRICS_READ_CACHE.clear()
    sm._PG_REQUESTER_MAPS.clear()
    yield
    sm._METRICS_READ_CACHE.clear()
    sm._PG_REQUESTER_MAPS.clear()


def test_http_metrics_results_are_shared_but_caller_mutations_are_isolated():
    url = 'http://127.0.0.1:1234/api/v1/ops-metrics/recent-samples/'
    with patch.object(sm.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'{"samples":[{"value":1}]}')) as fetch:
        first = sm._fetch_backend_monitoring_json(url, timeout=1)
        first['samples'][0]['value'] = 99
        assert sm._fetch_backend_monitoring_json(url, timeout=1)['samples'][0]['value'] == 1
    assert fetch.call_count == 1


def test_timeout_cooldown_avoids_reissuing_still_running_backend_work():
    with patch.object(sm.time, 'monotonic', return_value=100) as clock, \
         patch.object(sm.urllib.request, 'urlopen', side_effect=TimeoutError('timed out')) as fetch:
        assert sm._fetch_backend_monitoring_json('http://localhost:1234/recent', timeout=1)['error']
        clock.return_value = 129
        assert sm._fetch_backend_monitoring_json('http://localhost:1234/recent', timeout=1)['retryAfterSec'] == 30
        assert fetch.call_count == 1
        clock.return_value = 131
        sm._fetch_backend_monitoring_json('http://localhost:1234/recent', timeout=1)
        assert fetch.call_count == 2


def test_concurrent_readers_do_not_send_duplicate_backend_requests():
    entered, release = threading.Event(), threading.Event()
    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return io.BytesIO(b'{"count":2}')
    with patch.object(sm.urllib.request, 'urlopen', side_effect=fetch) as http, ThreadPoolExecutor(2) as pool:
        first = pool.submit(sm._fetch_backend_monitoring_json, 'http://localhost:1234/live', timeout=1)
        assert entered.wait(2)
        second = pool.submit(sm._fetch_backend_monitoring_json, 'http://localhost:1234/live', timeout=1)
        release.set()
        assert first.result() == second.result() == {'count': 2}
    assert http.call_count == 1


def test_cache_memory_and_keys_are_bounded():
    with patch.object(sm.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'{"count":1}')):
        for index in range(100):
            sm._fetch_backend_monitoring_json(f'http://localhost:1234/recent?limit={index}', timeout=1)
    assert len(sm._METRICS_READ_CACHE) <= 64


def test_file_slice_commits_once_and_retries_without_losing_lines(tmp_path):
    db, source = tmp_path / 'ops.db', tmp_path / 'backend.log'
    ops_store.init_store(db)
    source.write_bytes(b'first\nsecond\npart')
    key = str(source.resolve())
    sm._SERVICE_LOG_FILE_OFFSETS.pop(key, None)
    try:
        with patch.object(sm, '_service_log_sources', return_value=[('backend', 'stderr', source)]):
            with patch.object(ops_store, 'append_service_logs_batch', side_effect=RuntimeError('locked')):
                assert sm._ingest_service_log_files({}, 'MAIN', ops_store, db) == []
            assert key not in sm._SERVICE_LOG_FILE_OFFSETS
            with patch.object(ops_store, 'append_service_logs_batch', wraps=ops_store.append_service_logs_batch) as batch:
                assert len(sm._ingest_service_log_files({}, 'MAIN', ops_store, db)) == 2
                assert batch.call_count == 1
            with source.open('ab') as file:
                file.write(b'ial\nthird\n')
            assert [row['line'] for row in sm._ingest_service_log_files({}, 'MAIN', ops_store, db)] == ['partial', 'third']
        with ops_store.get_connection(db) as conn:
            assert conn.execute('SELECT COUNT(*) FROM service_log_lines').fetchone()[0] == 4
    finally:
        sm._SERVICE_LOG_FILE_OFFSETS.pop(key, None)


def test_api_and_postgres_collectors_use_one_transaction_each(tmp_path):
    db = tmp_path / 'ops.db'
    ops_store.init_store(db)
    with patch.object(sm, 'fetch_backend_api_metrics', return_value={'totals': {'avgMs': 20},
         'traffic': {'activeUsers': {'count': 3}}}), \
         patch.object(ops_store, 'insert_monitor_samples_batch', wraps=ops_store.insert_monitor_samples_batch) as batch:
        sm._collect_api_metrics({}, 'MAIN', ops_store, db)
        assert batch.call_count == 1
        assert len(batch.call_args.args[0]) == 3
    with patch.object(sm.server_db, 'collect_pg_metrics', return_value={'connections': {'total': 5, 'byState': {'idle': 4}},
         'sizeBytes': 5000, 'blockingLocks': []}), \
         patch.object(ops_store, 'insert_monitor_samples_batch', wraps=ops_store.insert_monitor_samples_batch) as batch:
        sm._collect_postgres({}, 'MAIN', ops_store, db)
        assert batch.call_count == 1
        assert len(batch.call_args.args[0]) == 4


def test_oversized_log_line_does_not_turn_its_tail_into_false_evidence(tmp_path):
    db, source = tmp_path / 'ops.db', tmp_path / 'large.log'
    ops_store.init_store(db)
    source.write_bytes(b'ERROR ' + b'x' * 300_000 + b'\nvalid line\n')
    key = str(source.resolve())
    sm._SERVICE_LOG_FILE_OFFSETS[key] = 0
    try:
        with patch.object(sm, '_service_log_sources', return_value=[('backend', 'stderr', source)]):
            rows = []
            for _ in range(3):
                rows += sm._ingest_service_log_files({}, 'MAIN', ops_store, db)
        assert [row['line'] for row in rows] == ['valid line']
    finally:
        sm._SERVICE_LOG_FILE_OFFSETS.pop(key, None)
        sm._SERVICE_LOG_SKIPPING.discard(key)


def test_enriching_peak_is_read_only(tmp_path):
    db = tmp_path / 'ops.db'
    ops_store.init_store(db)
    data = {'traffic': {'activeUsers': {'count': 8}}}
    with patch.object(sm, '_import_ops_store', return_value=ops_store), \
         patch.object(sm, 'resolve_ops_store_path', return_value=db), \
         patch.object(ops_store, 'insert_monitor_sample', side_effect=AssertionError('read must not write')), \
         patch.object(ops_store, 'init_store', side_effect=AssertionError('read must not initialize')):
        sm._enrich_active_users_peak({}, 'MAIN', data)
    assert data['traffic']['activeUsers']['peakCount'] == 8


def test_schema_initialization_is_shared_between_collectors(tmp_path):
    db = tmp_path / 'ops.db'
    with patch.object(ops_store, 'init_store', wraps=ops_store.init_store) as initialize:
        sm._ensure_monitor_store(ops_store, db)
        sm._ensure_monitor_store(ops_store, db)
        assert initialize.call_count == 1


def test_retention_batches_preserve_recent_samples_and_use_time_index(tmp_path):
    db = tmp_path / 'ops.db'
    ops_store.init_store(db)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    ops_store.insert_monitor_samples_batch([{'environment': 'MAIN', 'metric_key': 'api_avg_ms',
         'value': 1, 'recorded_at': old} for _ in range(2100)], db_path=db)
    ops_store.insert_monitor_sample('MAIN', 'api_avg_ms', 8, db_path=db)
    assert ops_store.purge_monitor_data(7, db_path=db)['monitor_samples'] == 2100
    with ops_store.get_connection(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM monitor_samples').fetchone()[0] == 1
        plan = conn.execute('EXPLAIN QUERY PLAN SELECT id FROM monitor_samples WHERE recorded_at < ? LIMIT 1000', (old,)).fetchall()
        assert any('idx_monitor_samples_retention' in row['detail'] for row in plan)


def test_legacy_fallback_bounds_queries_reuses_directory_and_avoids_schema_error_retry():
    conn = MagicMock()
    conn.info = SimpleNamespace(host='localhost', port=15543, dbname='test_metrics', user='test')
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (1,)
    current = ['']
    def execute(sql, params=None):
        current[0] = sql
    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: [('1', 'agent', '', '')] if 'FROM auth_user' in current[0] else [(datetime.now(timezone.utc), 'GET', '/test/', 200, 12, 1)]
    with patch.object(sm.server_db, 'get_pg_connection', return_value=conn):
        for _ in range(2):
            result = sm.fetch_recent_samples_from_postgres({}, 'MAIN')
            assert result['samples'][0]['requester'] == 'agent'
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert sum('FROM auth_user' in sql for sql in statements) == 1
    assert sum('SET LOCAL statement_timeout' in sql for sql in statements) == 2
    assert not any('error_reason' in sql for sql in statements)
    conn.rollback.assert_not_called()
