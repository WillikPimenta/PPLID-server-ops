"""Real Waitress health probes stay available while all business workers wait."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import socket
import threading
import time

import requests
from waitress import create_server

from deploy.bluegreen.slot_worker import HealthTaskDispatcher, WebApplication


def test_reserved_health_avoids_business_queue_and_reclassifies_pipelined_requests(tmp_path):
    release = threading.Event()
    entered = threading.Event()
    lock = threading.Lock()
    running = 0
    executed = []
    clients = []
    def app(environ, start_response):
        nonlocal running
        path = environ['PATH_INFO']
        with lock:
            executed.append(path)
            if path == '/hold':
                running += 1
                if running == 16:
                    entered.set()
        if path == '/hold':
            assert release.wait(10), 'test did not release controlled business requests'
        body = b'business'
        start_response('200 OK', [('Content-Type', 'text/plain'), ('Content-Length', str(len(body)))])
        return [body]
    traffic = tmp_path / 'traffic.json'
    traffic.write_text(json.dumps({'allowed': ['synthetic']}))
    ready, draining = threading.Event(), threading.Event()
    ready.set()
    spec = {'instanceId': 'synthetic', 'sha': 'synthetic', 'slot': 'A', 'trafficFile': str(traffic)}
    wrapped = WebApplication(app, spec, ready, draining, lambda: None)
    server = create_server(wrapped, host='127.0.0.1', port=0, threads=16, connection_limit=256, map={})
    dispatcher = HealthTaskDispatcher(server.task_dispatcher)
    server.task_dispatcher = dispatcher
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    pool = ThreadPoolExecutor(max_workers=17)
    pipelined = None
    base = f'http://127.0.0.1:{server.effective_port}'
    def business(path):
        client = requests.Session()
        client.trust_env = False
        with lock:
            clients.append(client)
        response = client.get(base + path, timeout=8)
        assert response.status_code == 200 and response.content == b'business'
    def queued_at_least(count):
        deadline = time.monotonic() + 3
        while True:
            with dispatcher.business.lock:
                if len(dispatcher.business.queue) >= count:
                    return
            assert time.monotonic() < deadline, 'business request was not held in its own queue'
            time.sleep(.01)
    try:
        futures = [pool.submit(business, '/hold') for _ in range(16)]
        assert entered.wait(5)
        futures.append(pool.submit(business, '/queued'))
        queued_at_least(1)
        probe = requests.Session()
        probe.trust_env = False
        clients.append(probe)
        for path in ('/__pplid/traffic', '/__pplid/ready'):
            response = probe.get(base + path, timeout=1)
            assert response.status_code == 200
            response.content
        # Both requests arrive together. Waitress must redispatch the second
        # request to the saturated business queue after serving the health GET.
        pipelined = socket.create_connection(('127.0.0.1', server.effective_port), timeout=1)
        pipelined.sendall(b'GET /__pplid/traffic HTTP/1.1\r\nHost: localhost\r\n\r\n'
                         b'GET /pipelined-business HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        first = http.client.HTTPResponse(pipelined)
        first.begin()
        assert first.status == 200 and first.read() == b'ready'
        queued_at_least(2)
        assert '/pipelined-business' not in executed
        assert probe.head(base + '/__pplid/ready', timeout=1).status_code == 200
        assert len(dispatcher.business.threads) == 16 and len(dispatcher.health.threads) == 2
        release.set()
        for future in futures:
            future.result(timeout=5)
        pipelined.settimeout(3)
        second = http.client.HTTPResponse(pipelined)
        second.begin()
        assert second.status == 200 and second.read() == b'business'
        assert '/queued' in executed and '/pipelined-business' in executed
    finally:
        release.set()
        pool.shutdown(wait=True, cancel_futures=True)
        if pipelined:
            pipelined.close()
        for client in clients:
            client.close()
        dispatcher.shutdown()
        server.close()
        thread.join(5)
