from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

import pytest

from deploy.bluegreen.caddy import OPENER, request
from deploy.bluegreen.store import digest, read_json


def test_real_proxy_switch_drain_headers_and_invalid_reload(laboratory, make_release):
    config, manager, _ = laboratory
    first = make_release(config, 'a')
    second = make_release(config, 'b')
    manager.prepare(first)
    # Public ports are not bound during preparation.
    with pytest.raises(OSError):
        request(f'http://127.0.0.1:{config.backend_port}/api/echo')
    manager.activate()
    initial = manager.proxy.get()
    with pytest.raises(urllib.error.HTTPError):
        manager.proxy.load({'unknown_module': True})
    assert digest(manager.proxy.get()) == digest(initial)
    errors, samples = [], []
    running = threading.Event()
    running.set()
    def probe():
        while running.is_set():
            try:
                result = request(f'http://127.0.0.1:{config.backend_port}/api/echo')
                samples.append(result['sha'])
            except Exception as error:
                errors.append(str(error))
            time.sleep(.03)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(probe)
        with OPENER.open(f'http://127.0.0.1:{config.backend_port}/slow', timeout=5) as response:
            assert response.read(5) == b'start'
            manager.promote(second)
            assert response.read() == b'finish'
        for _ in range(10):
            manager.rollback()
        running.clear()
        future.result(timeout=5)
    assert not errors
    assert first in samples and second in samples
    echo = request(f'http://127.0.0.1:{config.frontend_port}/api/echo', value={'hello': 'world'})
    assert echo['method'] == 'POST'
    assert json.loads(echo['body']) == {'hello': 'world'}
    assert echo['webOnly'] == '1' and echo['scheduler'] == 'False'
    forged = urllib.request.Request(f'http://127.0.0.1:{config.backend_port}/api/echo',
                                    headers={'X-Forwarded-For': '8.8.8.8', 'Cookie': 'session=kept'})
    with OPENER.open(forged) as response:
        assert 'session=kept' in response.headers['Set-Cookie']
        payload = json.load(response)
    assert payload['cookie'] == 'session=kept'
    # Waitress strips untrusted proxy headers by default; either sanitized peer
    # or stripped header is valid, but the forged address must never survive.
    assert payload['forwarded'] in (None, '127.0.0.1')
    with OPENER.open(f'http://127.0.0.1:{config.frontend_port}/assets/{first}.js') as response:
        assert first.encode() in response.read()
    state = manager.read()
    assert state['phase'] == 'idle' and not state['intent']
    print(json.dumps({'samples': len(samples), 'errors': len(errors), 'alternations': 11}))


def test_candidate_failure_preserves_active(laboratory, make_release):
    config, manager, _ = laboratory
    first = make_release(config, 'c')
    broken = make_release(config, 'd', broken=True)
    manager.prepare(first)
    manager.activate()
    with pytest.raises(RuntimeError):
        manager.promote(broken)
    assert request(f'http://127.0.0.1:{config.backend_port}/api/echo')['sha'] == first
    assert manager.read()['active'] == 'A'
