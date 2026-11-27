"""Authenticated HTTP journeys against a disposable local application lab.

This is a load generator, not a server sizing claim. Cookies are supplied in
memory; reports contain aggregate metrics and no credentials or response bodies.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import shutil
import threading
import time
from urllib.parse import urlparse

import psutil
import requests

from .resources import available_commit_bytes


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * q))], 2)


def stage(*, backend, frontend, cookies, cookie_name, journeys, users, duration,
          output: Path, name, roots=(), events=(), think_seconds=5, minimum_available_mb=768,
          minimum_commit_mb=768, minimum_disk_mb=1024, sample_extra=None):
    """events = [(seconds, label, callback)]; callbacks run outside client threads."""
    for address in (backend, frontend):
        parsed = urlparse(address)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.port < 1024:
            raise ValueError('Load is restricted to unprivileged loopback HTTP listeners')
    if not 1 <= users <= len(cookies) or duration <= 0 or think_seconds < 0:
        raise ValueError('Invalid load parameters')
    if len(set(cookies[:users])) != users:
        raise ValueError('Each virtual user requires an independent authenticated session')
    if (not journeys or any(not j.get('path', '').startswith('/') or j['path'].startswith('//')
                            or j.get('weight', 1) <= 0 for j in journeys)
            or min(minimum_available_mb, minimum_commit_mb, minimum_disk_mb) <= 0):
        raise ValueError('Explicit local journeys and positive resource floors are required')
    output.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    stop = threading.Event()
    barrier = threading.Barrier(users + 1)
    samples = defaultdict(list)
    statuses = Counter()
    errors = Counter()
    host = []
    timeline = []
    sequence = 0
    completed_at = []
    saturation = []
    abort_reasons = []
    started = time.monotonic()
    deadline = started + duration
    weights = [j.get('weight', 1) for j in journeys]
    def client(index):
        nonlocal sequence
        rng = random.Random(1701 + index)
        session = requests.Session()
        session.trust_env = False
        session.mount('http://', requests.adapters.HTTPAdapter(max_retries=0))
        session.cookies.set(cookie_name, cookies[index], domain='127.0.0.1', path='/')
        barrier.wait(timeout=60)
        # Spread arrivals; all users retain independent sessions and think time.
        stop.wait(rng.uniform(0, min(think_seconds, 2)))
        try:
            while not stop.is_set() and time.monotonic() < deadline:
                journey = rng.choices(journeys, weights=weights, k=1)[0]
                base = frontend if journey.get('origin') == 'frontend' else backend
                path = journey['path'].replace('{variant}', str(index % 5 + 1))
                begin = time.monotonic()
                status, problem = 0, None
                response = None
                try:
                    response = session.get(base + path, timeout=(5, 30), allow_redirects=False)
                    status = response.status_code
                    if status != journey.get('status', 200):
                        problem = f'HTTP {status}'
                    elif journey.get('jsonKey') or journey.get('jsonEquals'):
                        payload = response.json()
                        if journey.get('jsonKey') and journey['jsonKey'] not in payload:
                            problem = 'Missing expected JSON key'
                        elif any(payload.get(key) != value for key, value in journey.get('jsonEquals', {}).items()):
                            problem = 'Unexpected JSON value'
                    if journey.get('contentType') and not response.headers.get('Content-Type', '').startswith(journey['contentType']):
                        problem = 'Unexpected response content type'
                except requests.RequestException as error:
                    problem = type(error).__name__
                except (ValueError, TypeError, AttributeError):
                    problem = 'Invalid response JSON'
                finally:
                    if response is not None:
                        response.close()
                elapsed = (time.monotonic() - begin) * 1000
                with lock:
                    samples[journey['name']].append(elapsed)
                    statuses[f"{journey['name']}:{status}"] += 1
                    if problem:
                        errors[f"{journey['name']}:{problem}"] += 1
                    sequence += 1
                    completed_at.append(time.monotonic() - started)
                    raw.write(json.dumps({'at': round(time.monotonic() - started, 3),
                                          'journey': journey['name'], 'status': status,
                                          'ms': round(elapsed, 2), 'error': problem}) + '\n')
                stop.wait(think_seconds * rng.uniform(.8, 1.2))
        finally:
            session.close()
    def monitor():
        high_since = None
        psutil.cpu_percent()
        while not stop.wait(5):
            now = time.monotonic()
            memory = psutil.virtual_memory()
            item = {'at': round(now - started, 2), 'cpuPercent': psutil.cpu_percent(),
                    'availableMb': memory.available // 2**20, 'treesRssMb': 0, 'processes': 0,
                    'treeCpuSeconds': 0, 'treeIoReadBytes': 0, 'treeIoWriteBytes': 0}
            try:
                item['availableCommitMb'] = available_commit_bytes() // 2**20
                item['diskFreeMb'] = shutil.disk_usage(output).free // 2**20
            except OSError:
                abort_reasons.append('Host commit/disk telemetry unavailable')
                stop.set()
                return
            seen = set()
            try:
                identities = roots() if callable(roots) else roots
            except Exception:
                abort_reasons.append('Process telemetry unavailable')
                stop.set()
                return
            for identity in identities:
                try:
                    owner = psutil.Process(identity['pid'])
                    if owner.create_time() != identity['created']:
                        continue
                    for process in [owner, *owner.children(recursive=True)]:
                        if process.pid not in seen:
                            seen.add(process.pid)
                            item['treesRssMb'] += process.memory_info().rss / 2**20
                            item['processes'] += 1
                            cpu = process.cpu_times()
                            io = process.io_counters()
                            item['treeCpuSeconds'] += cpu.user + cpu.system
                            item['treeIoReadBytes'] += io.read_bytes
                            item['treeIoWriteBytes'] += io.write_bytes
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    item['ownershipSampleIncomplete'] = True
            item['treesRssMb'] = round(item['treesRssMb'], 2)
            if sample_extra:
                try:
                    item.update(sample_extra())
                except Exception:
                    abort_reasons.append('Database telemetry unavailable')
                    stop.set()
            host.append(item)
            if memory.available // 2**20 < minimum_available_mb:
                saturation.append('Available memory below configured laboratory floor')
                stop.set()
            if item['availableCommitMb'] < minimum_commit_mb:
                saturation.append('Available commit below configured laboratory floor')
                stop.set()
            if item['diskFreeMb'] < minimum_disk_mb:
                saturation.append('Free disk below configured laboratory floor')
                stop.set()
            high_since = high_since or now if item['cpuPercent'] >= 95 else None
            if high_since is not None and now - high_since >= 60:
                saturation.append('Host CPU >=95% for 60 seconds; generator/host limit reached')
                stop.set()
            with lock:
                if sequence >= 100 and sum(errors.values()) / sequence > .01:
                    abort_reasons.append('Application error rate exceeds 1%; stop before increasing load')
                    stop.set()
    with (output / (name + '-requests.jsonl')).open('w', encoding='utf-8') as raw:
        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        with ThreadPoolExecutor(max_workers=users) as pool:
            futures = [pool.submit(client, i) for i in range(users)]
            barrier.wait(timeout=60)
            event_index = 0
            try:
                while not stop.wait(.25) and time.monotonic() < deadline:
                    if event_index < len(events) and time.monotonic() - started >= events[event_index][0]:
                        _, label, callback = events[event_index]
                        began = time.monotonic()
                        record = {'action': label, 'at': round(began - started, 2)}
                        try:
                            callback()
                            record['ok'] = True
                        except Exception as error:
                            record.update(ok=False, error=type(error).__name__)
                            errors['transition:' + label] += 1
                            stop.set()
                        record['seconds'] = round(time.monotonic() - began, 3)
                        timeline.append(record)
                        event_index += 1
            finally:
                stop.set()
                for future in futures:
                    future.result(timeout=40)
        watcher.join(10)
    all_values = [v for values in samples.values() for v in values]
    gaps = [b - a for a, b in zip(sorted(completed_at), sorted(completed_at)[1:])]
    report = {'name': name, 'users': users, 'plannedSeconds': duration,
              'actualSeconds': round(time.monotonic() - started, 2), 'thinkSeconds': think_seconds,
              'requests': sequence, 'errors': dict(errors), 'statuses': dict(statuses),
              'p50Ms': percentile(all_values, .5), 'p95Ms': percentile(all_values, .95),
              'p99Ms': percentile(all_values, .99), 'maxMs': round(max(all_values), 2) if all_values else None,
              'maxCompletionGapSeconds': round(max(gaps), 3) if gaps else None,
              'journeys': {key: {'count': len(values), 'p95Ms': percentile(values, .95)} for key, values in samples.items()},
              'host': host, 'events': timeline, 'saturation': saturation,
              'abortReasons': abort_reasons,
              'resourceFloorsMb': {'physical': minimum_available_mb, 'commit': minimum_commit_mb,
                                  'disk': minimum_disk_mb},
              'ok': not errors and not saturation and not abort_reasons and bool(sequence)}
    (output / (name + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
