from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ops-console'))
sys.path.insert(0, str(ROOT / 'lib'))
import ops_store  # noqa: E402
import server_congestion as cg  # noqa: E402


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    db = tmp_path / 'ops.db'
    ops_store.init_store(db)
    config = {'logDir': str(tmp_path), 'MAIN': {'repoName': 'PPLID_MAIN', 'enabled': True}, 'HOM': {'enabled': False}}
    monkeypatch.setattr(cg, 'dependencies', lambda config: (ops_store, db))
    monkeypatch.setattr(cg, 'settings', lambda config: {'enabled': True, 'budget': 256 * 1024 * 1024, 'days': 7})
    return config, db, tmp_path / 'PPLID_MAIN.waitress.queue.log'


def event(state, at, pid=26368, **kwargs):
    data = {'event_at': cg.iso(at), 'environment': 'MAIN', 'pid': pid, 'state': state,
            'threads': 16, 'pending_requests': 1, 'running_requests': 16,
            'dispatcher_queued_channels': 1, 'waitress_queue_depth': 1,
            'pending_routes': [{'method': 'GET', 'route': '/api/v1/notifications/broadcasts/active/', 'count': 1, 'max_ms': 0}],
            'running_routes': [], 'slow_executions': [], 'running_routes_partial': True}
    data.update(kwargs)
    return f'{cg.iso(at)} WARNING waitress.queue {json.dumps(data)}\n'


def stamp():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=706000)


def test_real_snapshot_transition_and_offline_export(evidence):
    config, db, file = evidence
    at = stamp()
    file.write_text(event('congestion_start', at) + event('recovered', at + timedelta(milliseconds=71),
        running_requests=13, pending_requests=0, running_routes_partial=False, pending_routes=[]), encoding='utf-8')
    import server_monitoring as sm
    with patch.object(sm, 'fetch_backend_api_inflight', side_effect=AssertionError('must stay local')):
        cg.collect_once(config)
        overview = cg.overview(config, 'MAIN')
        episode = overview['episodes'][0]
        assert episode['duration_ms'] == 71
        assert episode['max_running'] == 16 and episode['max_pending'] == 1
        detail = cg.detail(config, 'MAIN', episode_id=episode['id'])
        assert [row['pending_requests'] for row in detail['snapshots']] == [1, 0]
        assert [row['running'] for row in detail['series']] == [16, 13]
        assert overview['latest']['running_requests'] == 13
        with patch.object(sm, '_import_ops_store', return_value=ops_store), patch.object(sm, 'resolve_ops_store_path', return_value=db):
            body, _, _ = sm.build_monitoring_availability_event_log(config, 'MAIN',
                since=cg.iso(at - timedelta(minutes=5)), until=cg.iso(at + timedelta(minutes=5)), event_at=cg.iso(at))
        assert b'congestion_start' in body and b'"running_requests": 13' in body


def test_checkpoint_resume_rotation_and_deduplication(evidence):
    config, db, file = evidence
    at = stamp()
    first = event('congestion_start', at)
    file.write_text(first, encoding='utf-8')
    cg.collect_once(config)
    file.rename(Path(f'{file}.1'))
    file.write_text(first + event('recovered', at + timedelta(seconds=1)), encoding='utf-8')
    cg.collect_once(config)
    cg.collect_once(config)
    with ops_store.get_connection(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM congestion_snapshots').fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM service_log_lines WHERE stream='queue'").fetchone()[0] == 2
    assert cg.overview(config, 'MAIN')['episodes'][0]['duration_ms'] == 1000


def test_incomplete_invalid_oversized_and_truncated_lines(evidence):
    config, db, file = evidence
    at = stamp()
    line = event('congestion_start', at)
    file.write_bytes(b'not json\n' + b'x' * (cg.MAX_LINE + 5) + b'\n' + line[:-1].encode())
    cg.collect_once(config)
    assert not cg.overview(config, 'MAIN')['episodes']
    with file.open('ab') as output:
        output.write(b'\n')
    cg.collect_once(config)
    assert len(cg.overview(config, 'MAIN')['episodes']) == 1
    file.write_text(event('recovered', at + timedelta(seconds=1)), encoding='utf-8')
    cg.collect_once(config)
    overview = cg.overview(config, 'MAIN')
    assert overview['collection']['invalid_lines'] == 2
    assert overview['episodes'][0]['duration_ms'] == 1000


def test_late_records_orphans_and_process_sessions(evidence):
    config, db, file = evidence
    at = stamp()
    file.write_text(event('congestion_ongoing', at + timedelta(seconds=1)), encoding='utf-8')
    cg.collect_once(config)
    assert not cg.overview(config, 'MAIN')['episodes'][0]['start_known']
    file.write_text(event('congestion_start', at) + event('congestion_ongoing', at + timedelta(seconds=1)) +
        event('recovered', at + timedelta(seconds=2)) + event('enabled', at + timedelta(seconds=3)) +
        event('congestion_start', at + timedelta(seconds=4)) +
        event('recovered', at + timedelta(seconds=5), pid=99), encoding='utf-8')
    cg.collect_once(config)
    episodes = cg.overview(config, 'MAIN')['episodes']
    assert len(episodes) == 3
    known = [row for row in episodes if row['duration_ms'] == 2000]
    assert len(known) == 1
    assert episodes[0]['pid'] == 99 and episodes[0]['duration_ms'] is None
    assert episodes[1]['end_at'] is None
    assert episodes[1]['session'] != known[0]['session']


def test_enabled_without_event_time_and_environment_validation():
    at = cg.iso(stamp())
    line = f'{at} WARNING waitress.queue '+json.dumps({'state': 'enabled', 'environment': 'MAIN', 'pid': 123, 'interval_seconds': 5})
    assert cg.decode_line(line, 'MAIN')[1]['event_at'] == at
    with pytest.raises(ValueError):
        cg.decode_line(line, 'HOM')
    for values in ({'running_requests': -1}, {'running_requests': True}, {'running_requests': '16'}, {'pending_routes_partial': 'false'}):
        with pytest.raises(ValueError):
            cg.decode_line(event('congestion_start', stamp(), **values), 'MAIN')


def test_queries_strip_and_unknown_fields_are_not_kept():
    line = event('congestion_start', stamp(), token='NEVER_SAVE',
        pending_routes=[{'method': 'GET', 'route': '/api/route/?token=NEVER_SAVE', 'count': 1, 'max_ms': 3}])
    _, data, serialized = cg.decode_line(line, 'MAIN')
    assert data['pending_routes'][0]['route'] == '/api/route/'
    assert 'NEVER_SAVE' not in serialized


def test_retention_and_budget_mark_partial_history(evidence):
    config, db, file = evidence
    at = stamp()
    file.write_text(event('congestion_start', at) + event('recovered', at + timedelta(seconds=2)), encoding='utf-8')
    cg.collect_once(config)
    with ops_store.get_connection(db) as conn:
        size = conn.execute('SELECT payload_bytes FROM congestion_snapshots ORDER BY event_at DESC LIMIT 1').fetchone()[0]
        cg.purge(conn, cg.utcnow(), size + 1)
    overview = cg.overview(config, 'MAIN')
    assert overview['collection']['dropped_records'] == 1
    assert not overview['episodes'][0]['start_known']
    with ops_store.get_connection(db) as conn:
        cg.purge(conn, cg.utcnow() + timedelta(days=8), 256 * 1024 * 1024)
    assert not cg.overview(config, 'MAIN')['episodes']


def test_missing_unreadable_and_disabled(evidence, monkeypatch):
    config, db, file = evidence
    cg.collect_once(config)
    assert all(row['status'] == 'missing' for row in cg.overview(config, 'MAIN')['collection']['sources'])
    with patch.object(Path, 'open', side_effect=PermissionError()):
        cg.collect_once(config)
    assert all(row['status'] == 'unreadable' for row in cg.overview(config, 'MAIN')['collection']['sources'])
    monkeypatch.setattr(cg, 'settings', lambda config: {'enabled': False, 'budget': 256 * 1024 * 1024})
    cg.collect_once(config)
    assert cg.overview(config, 'MAIN')['collection']['enabled'] is False


def test_pagination_scoping_and_validation(evidence):
    config, db, file = evidence
    at = stamp()
    file.write_text(''.join(event('congestion_start', at + timedelta(seconds=i)) for i in range(4)), encoding='utf-8')
    cg.collect_once(config)
    page = cg.overview(config, 'MAIN', limit=2)
    assert page['next_offset'] == 2 and len(page['episodes']) == 2
    assert cg.overview(config, 'MAIN', offset=2)['next_offset'] is None
    assert not cg.overview(config, 'HOM')['episodes']
    episode_id = page['episodes'][0]['id']
    with pytest.raises(ValueError):
        cg.detail(config, 'HOM', episode_id=episode_id)
    for args in ({'window': 'bad'}, {'since': 'bad', 'until': 'bad'}, {'limit': 'bad'},
                 {'since': cg.iso(at - timedelta(days=8)), 'until': cg.iso(at)}):
        with pytest.raises(ValueError):
            cg.overview(config, 'MAIN', **args)


def test_schema_upgrade_is_additive(evidence):
    config, db, file = evidence
    with ops_store.get_connection(db) as conn:
        conn.execute("INSERT INTO service_log_lines(environment,service,stream,line,logged_at) VALUES('MAIN','backend','stderr','preserved','2026-01-01')")
        conn.execute("UPDATE schema_meta SET value='2' WHERE key='version'")
    ops_store.init_store(db)
    with ops_store.get_connection(db) as conn:
        assert conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0] == '3'
        assert conn.execute('SELECT line FROM service_log_lines').fetchone()[0] == 'preserved'


def test_failed_upgrade_does_not_mark_schema_three_complete(evidence):
    _, db, _ = evidence
    with cg.connection(ops_store, db) as conn:
        conn.execute("UPDATE schema_meta SET value='1' WHERE key='version'")
    with patch.object(ops_store,'SCHEMA_V3_SQL','INVALID SQL'):
        with pytest.raises(sqlite3.OperationalError):
            ops_store.init_store(db)
    with cg.connection(ops_store, db) as conn:
        assert conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0]=='2'
    ops_store.init_store(db)
    with cg.connection(ops_store, db) as conn:
        assert conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0]=='3'


def test_endpoint_calls_local_builder_and_rejects_bad_parameters(evidence):
    import server
    from urllib.parse import urlparse
    config, _, _ = evidence
    handler = object.__new__(server.OpsConsoleHandler)
    handler.config = config
    handler._send_json = Mock()
    path = '/api/v1/monitoring/MAIN/congestion'
    handler._handle_monitoring_get(path, urlparse(path))
    assert handler._send_json.call_args.args[0]['environment'] == 'MAIN'
    handler._handle_monitoring_get(path, urlparse(path+'?window=invalid'))
    assert handler._send_json.call_args.kwargs['status'] == 400


def test_same_millisecond_transitions_and_selected_snapshot(evidence):
    config, _, file = evidence
    at = stamp()
    file.write_text(event('recovered', at, pending_requests=0) + event('congestion_start', at), encoding='utf-8')
    cg.collect_once(config)
    overview = cg.overview(config, 'MAIN')
    assert len(overview['episodes']) == 1
    episode_id = overview['episodes'][0]['id']
    assert overview['episodes'][0]['duration_ms'] == 0
    detail = cg.detail(config, 'MAIN', episode_id=episode_id, limit=1)
    other = cg.detail(config, 'MAIN', episode_id=episode_id, snapshot_id=detail['latest_snapshot']['id'])
    assert other['selected']['id'] == detail['latest_snapshot']['id']


def test_database_error_rolls_back_checkpoint_and_retries(evidence):
    config, db, file = evidence
    at = stamp()
    file.write_text(event('congestion_start', at), encoding='utf-8')
    with patch.object(cg, '_update_processes', side_effect=RuntimeError('controlled database failure')):
        with pytest.raises(RuntimeError):
            cg.collect_once(config)
    with ops_store.get_connection(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM congestion_checkpoints').fetchone()[0] == 0
    cg.collect_once(config)
    assert len(cg.overview(config, 'MAIN')['episodes']) == 1


def test_bounded_import_has_progress_and_completes_on_next_cycles(evidence, monkeypatch):
    config, _, file = evidence
    at = stamp()
    lines = [event('congestion_start', at + timedelta(seconds=i)) for i in range(10)]
    file.write_text(''.join(lines), encoding='utf-8')
    monkeypatch.setattr(cg, 'READ_BUDGET', len(lines[0].encode()) * 3)
    cg.collect_once(config)
    assert cg.overview(config, 'MAIN')['collection']['pending_import']
    for _ in range(10):
        cg.collect_once(config)
    assert cg.overview(config, 'MAIN')['coverage']['snapshots'] == 10
    assert not cg.overview(config, 'MAIN')['collection']['pending_import']


def test_aggregated_history_preserves_peak_and_recovery(evidence):
    config, db, file = evidence
    at = stamp()
    file.write_text(event('congestion_start', at) + ''.join(event('congestion_ongoing', at + timedelta(seconds=i),
        pending_requests=80 if i == 250 else 1) for i in range(1, 600)) +
        event('recovered', at + timedelta(seconds=600), pending_requests=0, running_requests=13), encoding='utf-8')
    cg.collect_once(config)
    episode = cg.overview(config, 'MAIN')['episodes'][0]
    detail = cg.detail(config, 'MAIN', episode_id=episode['id'])
    assert detail['series_aggregated']
    assert max(row['pending'] for row in detail['series']) == 80
    assert any(row.get('state') == 'recovered' and row['pending'] == 0 for row in detail['series'])
    assert detail['next_offset'] == 100
    assert len(detail['series']) < 500


def test_event_time_export_survives_delayed_ingestion_and_source_removal(evidence):
    config, db, file = evidence
    at = stamp()
    file.write_text(event('congestion_start', at), encoding='utf-8')
    cg.collect_once(config)
    file.unlink()
    lines = cg.export_evidence(config, 'MAIN', at - timedelta(seconds=1), at + timedelta(seconds=1))
    assert 'congestion_start' in lines[0]
    assert 'ingestão=' in lines[0]
    assert 'parcial' in cg.export_evidence(config, 'MAIN', at - timedelta(seconds=1), at + timedelta(seconds=1), max_bytes=1)[0]


def test_retention_repairs_episode_without_replaying_entire_process(evidence):
    config, db, file = evidence
    at = stamp()
    file.write_text(event('congestion_start', at) + event('recovered', at + timedelta(seconds=2)), encoding='utf-8')
    cg.collect_once(config)
    episode_id = cg.overview(config, 'MAIN')['episodes'][0]['id']
    with cg.connection(ops_store, db) as conn:
        budget = conn.execute('SELECT payload_bytes FROM congestion_snapshots ORDER BY event_at DESC LIMIT 1').fetchone()[0]+1
        with patch.object(cg, '_rebuild_process', side_effect=AssertionError('retention must be incremental')):
            cg.purge(conn, cg.utcnow(), budget)
    episode = cg.overview(config, 'MAIN')['episodes'][0]
    assert episode['id'] == episode_id and not episode['start_known'] and episode['partial']


def test_snapshot_pages_respect_byte_budget_without_losing_records(evidence, monkeypatch):
    config, db, file = evidence
    at = stamp()
    file.write_text(event('congestion_start', at) + ''.join(event('congestion_ongoing', at+timedelta(seconds=i)) for i in range(1,8)),encoding='utf-8')
    cg.collect_once(config)
    episode = cg.overview(config,'MAIN')['episodes'][0]
    monkeypatch.setattr(cg,'PAGE_BUDGET',1300)
    first = cg.detail(config,'MAIN',episode_id=episode['id'])
    assert first['page_limited_by_bytes'] and first['next_offset'] < 8
    seen = [row['id'] for row in first['snapshots']]
    offset = first['next_offset']
    while offset is not None:
        page = cg.detail(config,'MAIN',episode_id=episode['id'],offset=offset)
        seen += [row['id'] for row in page['snapshots']]
        offset=page['next_offset']
    assert len(seen)==len(set(seen))==8
