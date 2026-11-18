from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import server
import server_ops as ops


def test_redeploy_requires_confirmed_remote_and_fetches_only_once(tmp_path, monkeypatch):
    config = {'logDir': str(tmp_path / 'logs'), 'DEV': {'repoDir': str(tmp_path / 'repo')}}
    script = tmp_path / 'ops' / 'deploy' / 'run_pipeline_locked.ps1'
    script.parent.mkdir(parents=True)
    script.write_text('# test', encoding='utf-8')
    monkeypatch.setattr(ops, 'require_env_enabled', lambda *_: None)
    monkeypatch.setattr(ops, 'is_deploy_busy', lambda *_: False)
    monkeypatch.setattr(ops, 'read_env_git_worktree_status', lambda *_, **__: {'dirty': False})
    monkeypatch.setattr(ops, '_resolve_origin_branch_sha', lambda *_: ('abc1234', 'a' * 40))
    resolve = Mock(return_value=('abc1234', 'a' * 40))
    worker = Mock(return_value={'accepted': True})
    monkeypatch.setattr(ops, '_resolve_sha_in_mirror', resolve)
    monkeypatch.setattr(ops, 'run_powershell_async', worker)
    result = ops.action_redeploy(config, 'DEV', async_mode=True)
    assert result['accepted'] is True
    assert resolve.call_args.kwargs['fetch'] is False
    assert worker.call_count == 1
    duplicate = ops.action_redeploy(config, 'DEV', async_mode=True)
    assert not duplicate['ok'] and 'andamento' in duplicate['error']
    assert worker.call_count == 1
    assert ops.get_redeploy_job('DEV', result['runId'])['state'] == 'running'
    worker.call_args.kwargs['on_complete']({'ok': False, 'exitCode': 2})
    assert ops.get_redeploy_job('DEV', result['runId'])['state'] == 'failed'
    assert 'andamento' in ops.get_redeploy_job('DEV', result['runId'])['error']
    assert ops.get_redeploy_job('MAIN', result['runId'])['state'] == 'unknown'

    monkeypatch.setattr(ops, '_resolve_origin_branch_sha', lambda *_: ('', ''))
    result = ops.action_redeploy(config, 'DEV', async_mode=True)
    assert not result['ok']
    assert 'origin/dev' in result['error']
    assert worker.call_count == 1

    clear = Mock(return_value={'clearedBlockedSha': 'old'})
    monkeypatch.setattr(ops, 'clear_deploy_blocked', clear)
    assert not ops.action_clear_block_and_redeploy(config, 'DEV')['ok']
    clear.assert_not_called()

    resolve.return_value = ('f' * 40, '')
    result = ops.action_redeploy(config, 'DEV', target_sha='f' * 40, async_mode=True)
    assert not result['ok']
    assert 'não encontrado' in result['error'] or 'nao encontrado' in result['error']
    assert worker.call_count == 1


def test_failed_fetch_does_not_treat_a_stale_remote_ref_as_latest(tmp_path, monkeypatch):
    mirror = tmp_path / 'deploy' / 'DEV' / 'mirror'
    mirror.mkdir(parents=True)
    run = Mock(return_value=Mock(returncode=1, stdout='', stderr='network error'))
    monkeypatch.setattr(ops, '_run_hidden', run)
    assert ops._resolve_origin_branch_sha(tmp_path, 'DEV') == ('', '')
    assert run.call_count == 1


def test_unexpected_worker_exception_reports_failure(monkeypatch):
    event = threading.Event()
    results = []
    def fail(*_args, **_kwargs):
        raise RuntimeError('falha de inicialização')
    monkeypatch.setattr(ops, 'run_powershell', fail)
    ops.run_powershell_async(Path('placeholder.ps1'), [], on_complete=lambda result: (results.append(result), event.set()))
    assert event.wait(2)
    assert not results[0]['ok'] and 'inicialização' in results[0]['error']


def test_copy_includes_complete_redacted_file_and_sqlite_logs(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
    import ops_store
    run_dir = tmp_path / 'deploy' / 'DEV' / 'logs' / 'runs' / 'run-1'
    run_dir.mkdir(parents=True)
    (run_dir / 'build.log').write_text('\n'.join([f'file-line-{i}' for i in range(1200)]) + '\npassword=topsecret\nauthorization: Bearer sensitive\npostgresql://user:secret@host/db\n', encoding='utf-8')
    db_path = tmp_path / 'ops' / 'data' / 'ops-store.db'
    ops_store.init_store(db_path)
    with ops_store.get_connection(db_path) as conn:
        conn.executemany('INSERT INTO deploy_log_lines(environment, run_id, log_name, level, message, logged_at) VALUES (?, ?, ?, ?, ?, ?)',
                         [('DEV', 'run-1', 'build', 'INFO', f'sql-line-{i}', '2026-01-01T00:00:00Z') for i in range(501)])
        conn.commit()
    (tmp_path / 'machine.config.json').write_text(json.dumps({'opsStore': {'path': str(db_path)}}), encoding='utf-8')
    result = ops.load_run_logs_for_copy(tmp_path, 'DEV', 'run-1')
    assert result['ok'] and not result['truncated'] and not result['warnings']
    assert 'file-line-0' in result['text'] and 'file-line-1199' in result['text']
    assert 'sql-line-0' in result['text'] and 'sql-line-500' in result['text']
    assert 'topsecret' not in result['text'] and 'sensitive' not in result['text'] and 'user:secret' not in result['text']
    assert ops.load_run_logs_for_copy(tmp_path, 'DEV', '../outside')['ok'] is False
    assert ops.load_run_logs_for_copy(tmp_path, 'DEV', 'run-1', max_chars=100)['truncated'] is True


def test_live_log_offsets_do_not_skip_lines_and_recover_after_rotation(tmp_path):
    run_dir = tmp_path / 'deploy' / 'DEV' / 'logs' / 'runs' / 'run-1'
    run_dir.mkdir(parents=True)
    path = run_dir / 'build.log'
    path.write_text('\n'.join(f'line-{i}' for i in range(450)), encoding='utf-8')
    first = ops.load_run_log_chunk(tmp_path, 'DEV', 'run-1', 'build.log', limit=200)
    second = ops.load_run_log_chunk(tmp_path, 'DEV', 'run-1', 'build.log', file_offset=first['nextFileOffset'], limit=200)
    third = ops.load_run_log_chunk(tmp_path, 'DEV', 'run-1', 'build.log', file_offset=second['nextFileOffset'], limit=200)
    assert (first['nextFileOffset'], second['nextFileOffset'], third['nextFileOffset']) == (200, 400, 450)
    assert [*first['lines'], *second['lines'], *third['lines']] == [f'line-{i}' for i in range(450)]
    path.write_text('new-0\nnew-1\n', encoding='utf-8')
    rotated = ops.load_run_log_chunk(tmp_path, 'DEV', 'run-1', 'build.log', file_offset=450, limit=200)
    assert rotated['lines'] == ['new-0', 'new-1']
    assert rotated['nextFileOffset'] == 2


def test_update_start_is_serialized_and_returns_existing_lock(tmp_path, monkeypatch):
    script = tmp_path / 'update.ps1'
    script.write_text('# test', encoding='utf-8')
    config = {'logDir': str(tmp_path / 'logs')}
    monkeypatch.setattr(ops, '_console_update_script', lambda _: script)
    monkeypatch.setattr(ops, 'check_console_update', lambda _: {'ok': True, 'supported': True, 'updateAvailable': True, 'currentSha': 'abc', 'remoteSha': 'def'})
    monkeypatch.setattr(ops, 'append_console_update_log', lambda *_, **__: None)
    spawn = Mock()
    monkeypatch.setattr(ops, '_spawn_detached_powershell', spawn)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ops.apply_console_update(config), range(2)))
    assert spawn.call_count == 1
    assert sum(bool(row.get('accepted')) for row in results) == 1
    assert any(row.get('inProgress') for row in results)


def test_auth_status_identifies_server_instance():
    handler = Mock()
    handler._current_session.return_value = None
    handler.config = {}
    server.OpsConsoleHandler._handle_auth_status(handler)
    payload = handler._send_json.call_args.args[0]
    assert payload['serverInstanceId'] == server.SERVER_INSTANCE_ID


def test_copy_endpoint_requires_session_and_routes_to_full_log_loader(tmp_path, monkeypatch):
    handler = Mock()
    handler.path = '/api/v1/runs/DEV/copy/run-1'
    handler.config = {'logDir': str(tmp_path / 'logs')}
    load = Mock(return_value={'ok': True, 'text': 'first\nlast', 'truncated': False, 'warnings': []})
    monkeypatch.setattr(ops, 'load_run_logs_for_copy', load)
    handler._require_unlocked_session.return_value = None
    server.OpsConsoleHandler.do_GET(handler)
    load.assert_not_called()
    assert handler._send_json.call_args.kwargs['status'] == 401
    handler._send_json.reset_mock()
    handler._require_unlocked_session.return_value = {'username': 'tester'}
    server.OpsConsoleHandler.do_GET(handler)
    assert load.call_args.args[1:] == ('DEV', 'run-1')
    assert handler._send_json.call_args.args[0]['text'] == 'first\nlast'


def test_deploy_status_endpoint_is_protected_and_scoped(tmp_path, monkeypatch):
    handler = Mock()
    handler.path = '/api/v1/actions/deploy-status/DEV?runId=console-123'
    handler.config = {'logDir': str(tmp_path / 'logs')}
    lookup = Mock(return_value={'ok': True, 'state': 'running', '_updatedAt': 10})
    monkeypatch.setattr(ops, 'get_redeploy_job', lookup)
    handler._require_unlocked_session.return_value = None
    server.OpsConsoleHandler.do_GET(handler)
    lookup.assert_not_called()
    handler._send_json.reset_mock()
    handler._require_unlocked_session.return_value = {'username': 'tester'}
    server.OpsConsoleHandler.do_GET(handler)
    lookup.assert_called_once_with('DEV', 'console-123')
    assert '_updatedAt' not in handler._send_json.call_args.args[0]
