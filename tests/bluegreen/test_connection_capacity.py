"""150 real keep-alive user channels must leave room for health probes."""
import requests

from deploy.bluegreen import processes


def test_slot_keeps_150_independent_connections_and_answers_health_probes(
        laboratory, make_release, record_property):
    config, manager, _ = laboratory
    sha = make_release(config, 'a')
    # The generic streaming fixture omits Content-Length, which makes Waitress
    # close that response's channel. This test needs persistent HTTP/1.1 pools.
    wsgi = config.release(sha) / 'backend/config/wsgi.py'
    wsgi.write_text(wsgi.read_text(encoding='utf-8').replace(
        "('Content-Type','application/json'), ('Set-Cookie'",
        "('Content-Type','application/json'), ('Content-Length',str(len(result))), ('Set-Cookie'"), encoding='utf-8')
    clients = []
    activated = False
    try:
        manager.prepare(sha)
        state = manager.activate()
        activated = True
        slot = state['slots'][state['active']]
        base = f"http://127.0.0.1:{slot['backendPort']}"
        for index in range(150):
            client = requests.Session()
            client.trust_env = False
            client.cookies.set('probe_session', str(index), domain='127.0.0.1', path='/')
            clients.append(client)
            response = client.get(base + '/', timeout=(2, 5))
            assert response.status_code == 200
            assert response.headers.get('Connection', '').lower() != 'close'
            body = response.json()  # Consume the body, returning this channel to its own live pool.
            assert body['sha'] == sha and f'probe_session={index}' in body['cookie']
        owner = processes.owned(slot['process'])
        assert owner is not None
        connections = set()
        for process in [owner, *owner.children(recursive=True)]:
            for connection in process.net_connections(kind='tcp'):
                if connection.status == 'ESTABLISHED' and connection.laddr.port == slot['backendPort']:
                    connections.add((connection.laddr.ip, connection.laddr.port, connection.raddr.ip, connection.raddr.port))
        record_property('backendEstablishedConnections', len(connections))
        assert len(connections) >= 150  # A default-100 server cannot pass by closing idle channels.
        probe = requests.Session()
        probe.trust_env = False
        clients.append(probe)
        for path in ('/__pplid/traffic', '/__pplid/ready'):
            response = probe.get(base + path, timeout=1)
            assert response.status_code == 200
            if path.endswith('ready'):
                assert response.json()['ready'] is True
            else:
                assert response.content == b'ready'
        record_property('independentUserSessions', 150)
        record_property('healthProbeTimeoutSeconds', 1)
    finally:
        # Keep every user pool open until both health probes have completed.
        for client in clients:
            client.close()
        if activated:
            manager.shutdown()
