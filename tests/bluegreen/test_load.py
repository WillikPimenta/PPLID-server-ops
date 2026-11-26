"""Short transport stress at 150 clients; not a corporate capacity benchmark."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import requests

from deploy.bluegreen.store import atomic_json


def test_150_synthetic_clients_during_promotion_and_rollback(laboratory, make_release):
    config, manager, _ = laboratory
    first = make_release(config, '3')
    second = make_release(config, '4')
    manager.prepare(first)
    manager.activate()
    stop = threading.Event()
    barrier = threading.Barrier(151)
    def client(number):
        durations, errors = [], []
        session = requests.Session()
        session.trust_env = False
        session.mount('http://', requests.adapters.HTTPAdapter(max_retries=0))
        path = '/api/echo' if number % 2 else '/'
        url = f'http://127.0.0.1:{config.frontend_port}{path}'
        try:
            barrier.wait(timeout=30)
            while not stop.is_set():
                started = time.perf_counter()
                try:
                    response = session.get(url, timeout=5)
                    response.raise_for_status()
                    if path != '/':
                        assert response.json()['sha'] in (first, second)
                    else:
                        assert response.headers['X-Pplid-Release'] in (first, second)
                    durations.append((time.perf_counter() - started) * 1000)
                except Exception as error:
                    errors.append(type(error).__name__ + ': ' + str(error))
                stop.wait(.5)
        finally:
            session.close()
        return durations, errors
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=150) as pool:
        clients = [pool.submit(client, i) for i in range(150)]
        try:
            barrier.wait(timeout=30)
            time.sleep(3)
            manager.promote(second)
            manager.rollback()
            manager.rollback()
            manager.rollback()
            remaining = 30 - (time.perf_counter() - started)
            if remaining > 0:
                time.sleep(remaining)
        finally:
            stop.set()
        results = [future.result() for future in clients]
    durations = sorted(value for samples, _ in results for value in samples)
    errors = [value for _, failures in results for value in failures]
    summary = {'clients': 150, 'durationSeconds': round(time.perf_counter() - started, 2),
               'samples': len(durations), 'errors': len(errors), 'errorExamples': errors[:5],
               'syntheticTransportOnly': True, 'retries': 0,
               'p50Ms': durations[int((len(durations)-1)*.50)] if durations else None,
               'p95Ms': durations[int((len(durations)-1)*.95)] if durations else None,
               'maxMs': max(durations) if durations else None}
    atomic_json(config.base / 'load-150-summary.json', summary)
    print(json.dumps(summary))
    assert len(results) == 150 and durations and not errors
