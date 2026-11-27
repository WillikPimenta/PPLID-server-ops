"""The capacity harness must detect auth loss and avoid retrying failed HTTP."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest

from deploy.bluegreen.application_load import stage


def test_queued_users_never_exceed_configured_in_flight_limit(tmp_path):
    lock = threading.Lock()
    active = peak = 0
    observed = set()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                observed.add(self.headers.get('Cookie'))
            try:
                time.sleep(.03)
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            finally:
                with lock:
                    active -= 1
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        result = stage(backend=url, frontend=url, cookies=[str(i) for i in range(15)],
                       cookie_name='synthetic', journeys=[{'name': 'controlled', 'path': '/', 'jsonEquals': {'ok': True}}],
                       users=15, duration=1, output=tmp_path, name='queued-users', think_seconds=.01, max_in_flight=3)
        assert result['ok'] and peak == result['peakInFlight'] == 3
        assert result['peakQueued'] > 0 and result['servedUsers'] == len(observed) == 15
        assert result['queueWaitP95Ms'] > 0
        rows = [json.loads(line) for line in (tmp_path / 'queued-users-requests.jsonl').read_text().splitlines()]
        assert all(row['requestAt'] < 1 and row['ms'] == row['responseMs'] for row in rows)
        assert all(row['totalMs'] + .02 >= row['queueMs'] + row['responseMs'] for row in rows)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_load_distinguishes_independent_sessions_and_detects_lost_authentication(tmp_path):
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(self.headers.get('Cookie'))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'authenticated': self.path != '/expired'}).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        result = stage(backend=url, frontend=url, cookies=['one', 'two'], cookie_name='synthetic',
                       journeys=[{'name': 'session', 'path': '/expired', 'jsonEquals': {'authenticated': True}}],
                       users=2, duration=.6, output=tmp_path, name='auth-loss', think_seconds=.05)
        assert not result['ok']
        assert result['errors']['session:Unexpected JSON value'] == result['requests']
        assert set(observed) == {'synthetic=one', 'synthetic=two'}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_load_rejects_external_targets_and_shared_sessions(tmp_path):
    args = dict(backend='http://127.0.0.1:12345', frontend='http://127.0.0.1:12346',
                cookies=['same', 'same'], cookie_name='synthetic', journeys=[], users=2,
                duration=1, output=tmp_path, name='invalid')
    with pytest.raises(ValueError, match='independent'):
        stage(**args)
    args['backend'] = 'http://example.com:12345'
    with pytest.raises(ValueError, match='loopback'):
        stage(**args)


@pytest.mark.parametrize('resource', ['commit', 'disk'])
def test_load_stops_when_host_commit_or_disk_runs_low(tmp_path, monkeypatch, resource):
    from types import SimpleNamespace
    from deploy.bluegreen import application_load

    monkeypatch.setattr(application_load, 'available_commit_bytes',
                        lambda: (128 if resource == 'commit' else 4096) * 2**20)
    monkeypatch.setattr(application_load.shutil, 'disk_usage',
                        lambda _: SimpleNamespace(free=(128 if resource == 'disk' else 4096) * 2**20))
    def unavailable(*args, **kwargs):
        raise application_load.requests.ConnectionError('Controlled synthetic failure')
    monkeypatch.setattr(application_load.requests.Session, 'get', unavailable)
    # A refusal to connect is also recorded; resource saturation must still be
    # identified separately instead of being reported as an application limit.
    result = stage(backend='http://127.0.0.1:1025', frontend='http://127.0.0.1:1026',
                   cookies=['synthetic'], cookie_name='synthetic',
                   journeys=[{'name': 'local', 'path': '/'}], users=1, duration=6,
                   output=tmp_path, name=resource + '-pressure', think_seconds=.5)
    assert not result['ok']
    assert any(resource in reason.lower() for reason in result['saturation'])
    assert result['actualSeconds'] < 6
