"""Local, bounded Waitress evidence collection. Never calls the application API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import threading
import time

LOG = logging.getLogger('ops.congestion')
STATES = {'enabled', 'congestion_start', 'congestion_ongoing', 'recovered'}
MAX_LINE = 256 * 1024
READ_BUDGET = 4 * 1024 * 1024
PAGE_BUDGET = 4 * 1024 * 1024
_thread = None
_start_lock = threading.Lock()
_collect_lock = threading.Lock()


@contextmanager
def connection(store, db):
    conn = store.get_connection(db)
    try:
        with conn:
            yield conn
    finally:
        conn.close()



def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds')


def parse_time(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ValueError('Horário inválido ou sem fuso') from None


def dependencies(config):
    import server_monitoring as monitoring
    return monitoring._import_ops_store(config), monitoring.resolve_ops_store_path(config)


def settings(config):
    import server_monitoring as monitoring
    raw = monitoring.get_monitoring_settings(config)
    enabled = raw.get('congestionEnabled', True)
    budget = raw.get('congestionMaxStorageMb', 256)
    if not isinstance(enabled, bool) or isinstance(budget, bool):
        raise ValueError('congestionEnabled deve ser booleano e congestionMaxStorageMb deve ser numérico')
    budget = float(budget)
    if not math.isfinite(budget):
        raise ValueError('Orçamento de congestionamento inválido')
    return {
        'enabled': enabled,
        'budget': max(1, min(4096, budget)) * 1024 * 1024,
        'days': 7,
    }


def source_paths(config, env):
    import server_monitoring as monitoring
    base = next(path for _, stream, path in monitoring._service_log_sources(config, env) if stream == 'queue')
    return [Path(f'{base}.{n}') for n in (3, 2, 1)] + [base]


def _int(value, maximum=2**53 - 1):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError('Contagem inválida')
    return value


def _route(value):
    # Templates may contain <str:token>, but runtime query strings are never kept.
    if not isinstance(value, str) or not value.startswith('/'):
        raise ValueError('Rota inválida')
    return re.sub(r'[\x00-\x1f\x7f]', '', value.split('?', 1)[0].split('#', 1)[0])[:255]


def decode_line(line, environment):
    prefix, marker, body = line.partition(' WARNING waitress.queue ')
    if not marker:
        raise ValueError('Linha sem diagnóstico estruturado')
    data = json.loads(body)
    if not isinstance(data, dict) or data.get('state') not in STATES or data.get('environment') != environment:
        raise ValueError('Estado ou ambiente inválido')
    stamp = iso(parse_time(data.get('event_at') or prefix))
    pid = _int(data.get('pid'), 2**32)
    clean = {'event_at': stamp, 'environment': environment, 'pid': pid, 'state': data['state']}
    if data['state'] == 'enabled':
        interval = float(data.get('interval_seconds', 5))
        if not math.isfinite(interval) or not 0.1 <= interval <= 3600:
            raise ValueError('Intervalo inválido')
        clean.update(interval_seconds=interval, waitress_version=str(data.get('waitress_version', ''))[:32])
    else:
        for field in ('dispatcher_queued_channels', 'waitress_queue_depth', 'threads',
                      'pending_requests', 'running_requests', 'unlisted_requests', 'diagnostic_failures'):
            clean[field] = _int(data.get(field, 0))
        for field in ('pending_routes_partial', 'running_routes_partial', 'slow_executions_partial', 'request_totals_partial'):
            if not isinstance(data.get(field, False), bool):
                raise ValueError('Indicador de parcialidade inválido')
            clean[field] = data.get(field, False)
        for field in ('pending_routes', 'running_routes', 'slow_executions'):
            values = data.get(field, [])
            if not isinstance(values, list) or len(values) > 100:
                raise ValueError('Lista de rotas inválida')
            clean[field] = []
            for row in values:
                method = row['method']
                if method not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT', 'OTHER'}:
                    raise ValueError('Método inválido')
                item = {'method': method, 'route': _route(row['route'])}
                if field == 'slow_executions':
                    request_id = str(row['id'])
                    if not re.fullmatch(r'\d+-\d+', request_id):
                        raise ValueError('ID inválido')
                    item.update(id=request_id, duration_ms=_int(row['duration_ms']), wait_ms=_int(row['wait_ms']),
                                client_disconnected=row.get('client_disconnected') is True)
                else:
                    item.update(count=_int(row['count']), max_ms=_int(row['max_ms']))
                clean[field].append(item)
    serialized = json.dumps(clean, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    return digest, clean, serialized


def _apply_event(conn, row):
    env, pid, stamp = row['environment'], row['pid'], row['event_at']
    process = conn.execute('SELECT * FROM congestion_process WHERE environment=? AND pid=?', (env, pid)).fetchone()
    session = process['session'] if process else f'unknown:{pid}'
    episode = process['open_episode'] if process else None
    if row['state'] == 'enabled':
        session, episode = row['id'], None
    else:
        if row['state'] == 'congestion_start' or not episode:
            episode = row['id']
            conn.execute('INSERT INTO congestion_episodes VALUES(?,?,?,?,?,?,NULL,?,0,0,0,0)',
                         (episode, env, pid, session, stamp, stamp, int(row['state'] == 'congestion_start')))
        conn.execute('''UPDATE congestion_episodes SET last_at=?,
            end_at=CASE WHEN ?='recovered' THEN ? ELSE end_at END,
            max_pending=MAX(max_pending,?), max_running=MAX(max_running,?),
            snapshot_count=snapshot_count+1,partial=MAX(partial,?) WHERE id=?''',
            (stamp, row['state'], stamp, row['pending'], row['running'], row['partial'], episode))
        conn.execute('UPDATE congestion_snapshots SET episode_id=? WHERE id=?', (episode, row['id']))
        if row['state'] == 'recovered':
            episode = None
    conn.execute('INSERT OR REPLACE INTO congestion_process VALUES(?,?,?,?,?)', (env, pid, stamp, session, episode))


def _rebuild_process(conn, env, pid):
    conn.execute('DELETE FROM congestion_process WHERE environment=? AND pid=?', (env, pid))
    conn.execute('DELETE FROM congestion_episodes WHERE environment=? AND pid=?', (env, pid))
    cursor = conn.execute("""SELECT * FROM congestion_snapshots WHERE environment=? AND pid=? ORDER BY event_at,
        CASE state WHEN 'enabled' THEN 0 WHEN 'congestion_start' THEN 1 WHEN 'congestion_ongoing' THEN 2 ELSE 3 END,id""", (env, pid))
    while rows := cursor.fetchmany(256):
        for row in rows:
            _apply_event(conn, row)


def insert_snapshot(conn, decoded, ingested_at):
    digest, data, serialized = decoded
    partial = int(any(data.get(key) for key in ('pending_routes_partial', 'running_routes_partial',
                      'slow_executions_partial', 'request_totals_partial', 'diagnostic_failures', 'unlisted_requests')))
    inserted = conn.execute('''INSERT OR IGNORE INTO congestion_snapshots
        (id,environment,pid,event_at,ingested_at,state,payload,payload_bytes,pending,running,threads,partial)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (digest, data['environment'], data['pid'], data['event_at'],
        ingested_at, data['state'], serialized, len(serialized.encode()),data.get('pending_requests',0),
        data.get('running_requests',0),data.get('threads',0),partial)).rowcount
    return bool(inserted)


def _read_source(conn, env, path, budget, now):
    status = {'file': path.name, 'status': 'available', 'remaining_bytes': 0}
    touched = {}
    invalid = 0
    consumed = 0
    try:
        with path.open('rb') as stream:
            from os import fstat
            stat = fstat(stream.fileno())
            identity = f'{stat.st_dev}:{stat.st_ino}' if stat.st_ino else str(path.resolve())
            checkpoint = conn.execute('SELECT * FROM congestion_checkpoints WHERE environment=? AND identity=?', (env, identity)).fetchone()
            offset, skipping = (checkpoint['offset'], checkpoint['skipping']) if checkpoint else (0, 0)
            if checkpoint:
                prefix = stream.read(checkpoint['prefix_len'])
                if offset > stat.st_size or hashlib.sha256(prefix).hexdigest() != checkpoint['prefix']:
                    offset, skipping = 0, 0
                    status['reset'] = True
            stream.seek(offset)
            while consumed < budget:
                position = stream.tell()
                raw = stream.readline(min(MAX_LINE + 1, budget - consumed))
                if not raw:
                    break
                consumed += len(raw)
                if not raw.endswith(b'\n'):
                    if len(raw) > MAX_LINE or skipping:
                        skipping = 1
                        offset = stream.tell()
                        continue
                    stream.seek(position)
                    if position + len(raw) >= stat.st_size:
                        status['partial_line'] = True
                    break
                offset = stream.tell()
                if skipping or len(raw) > MAX_LINE:
                    skipping = 0
                    invalid += 1
                    continue
                try:
                    decoded = decode_line(raw.decode('utf-8-sig').strip(), env)
                    if parse_time(decoded[1]['event_at']) < now - timedelta(days=7):
                        continue
                    if insert_snapshot(conn, decoded, iso(now)):
                        pid, stamp = decoded[1]['pid'], decoded[1]['event_at']
                        touched[pid] = min(touched.get(pid, stamp), stamp)
                        # One owner writes the original local evidence to the existing log stream.
                        safe_line = f'{stamp} WARNING waitress.queue {decoded[2]}'
                        log_id = conn.execute('INSERT INTO service_log_lines(environment,service,stream,line,logged_at) VALUES(?,?,?,?,?)',
                                              (env, 'backend', 'queue', safe_line, iso(now))).lastrowid
                        conn.execute('UPDATE congestion_snapshots SET raw_log_id=?,payload_bytes=payload_bytes+? WHERE id=?',
                                     (log_id, len(safe_line.encode()), decoded[0]))
                except (ValueError, TypeError, KeyError, UnicodeError):
                    invalid += 1
            prefix_len = min(offset, 256)
            stream.seek(0)
            prefix = hashlib.sha256(stream.read(prefix_len)).hexdigest()
            conn.execute('INSERT OR REPLACE INTO congestion_checkpoints VALUES(?,?,?,?,?,?,?,?)',
                         (env, identity, str(path), offset, prefix, prefix_len, iso(now), skipping))
            status['remaining_bytes'] = max(0, stat.st_size - offset)
            status['status'] = 'importing' if status['remaining_bytes'] and not status.get('partial_line') else 'available'
    except FileNotFoundError:
        status['status'] = 'missing'
    except OSError:
        status['status'] = 'unreadable'
    return status, consumed, invalid, touched


def _update_processes(conn, env, pids):
    for pid, earliest in pids.items():
        process = conn.execute('SELECT * FROM congestion_process WHERE environment=? AND pid=?', (env, pid)).fetchone()
        if process and earliest <= process['last_at']:
            _rebuild_process(conn, env, pid)
            continue
        rows = conn.execute('''SELECT * FROM congestion_snapshots WHERE environment=? AND pid=?
            AND event_at>? ORDER BY event_at,
            CASE state WHEN 'enabled' THEN 0 WHEN 'congestion_start' THEN 1 WHEN 'congestion_ongoing' THEN 2 ELSE 3 END,id''',
            (env, pid, process['last_at'] if process else '')).fetchall()
        for row in rows:
            _apply_event(conn, row)


def purge(conn, now, budget):
    affected = set()
    cutoff = iso(now - timedelta(days=7))
    rows = conn.execute('SELECT DISTINCT episode_id FROM congestion_snapshots WHERE event_at<? AND episode_id IS NOT NULL', (cutoff,)).fetchall()
    affected.update(row[0] for row in rows)
    conn.execute('DELETE FROM service_log_lines WHERE id IN (SELECT raw_log_id FROM congestion_snapshots WHERE event_at<?)', (cutoff,))
    conn.execute('DELETE FROM congestion_snapshots WHERE event_at<?', (cutoff,))
    total = conn.execute('SELECT COALESCE(SUM(payload_bytes),0) FROM congestion_snapshots').fetchone()[0]
    dropped = {}
    while total > budget:
        rows = conn.execute('SELECT id,environment,episode_id,payload_bytes,raw_log_id FROM congestion_snapshots ORDER BY event_at,id LIMIT 256').fetchall()
        if not rows:
            break
        for row in rows:
            if total <= budget:
                break
            conn.execute('DELETE FROM congestion_snapshots WHERE id=?', (row['id'],))
            conn.execute('DELETE FROM service_log_lines WHERE id=?', (row['raw_log_id'],))
            if row['episode_id']:
                affected.add(row['episode_id'])
            dropped[row['environment']] = dropped.get(row['environment'], 0) + 1
            total -= row['payload_bytes']
    for env, count in dropped.items():
        conn.execute('UPDATE congestion_collection SET dropped_records=dropped_records+? WHERE environment=?', (count, env))
    # Retention must not replay a week-long process on every five-second tick.
    # Keep episode IDs stable and repair only the aggregates touched by pruning.
    for episode_id in affected:
        first = conn.execute('SELECT event_at,state FROM congestion_snapshots WHERE episode_id=? ORDER BY event_at LIMIT 1', (episode_id,)).fetchone()
        if not first:
            conn.execute('DELETE FROM congestion_episodes WHERE id=?', (episode_id,))
            conn.execute('UPDATE congestion_process SET open_episode=NULL WHERE open_episode=?', (episode_id,))
            continue
        stats = conn.execute('SELECT COUNT(*),MAX(pending),MAX(running),MAX(event_at) FROM congestion_snapshots WHERE episode_id=?', (episode_id,)).fetchone()
        conn.execute('''UPDATE congestion_episodes SET start_at=?,last_at=?,snapshot_count=?,
            max_pending=?,max_running=?,start_known=CASE WHEN ?='congestion_start' THEN start_known ELSE 0 END,
            partial=1 WHERE id=?''', (first['event_at'],stats[3],stats[0],stats[1],stats[2],first['state'],episode_id))
    conn.execute('DELETE FROM congestion_process WHERE last_at<? AND open_episode IS NULL', (cutoff,))
    conn.execute('DELETE FROM congestion_checkpoints WHERE last_seen<?', (cutoff,))
    return total


def collect_once(config):
    if not _collect_lock.acquire(blocking=False):
        return
    try:
        opts = settings(config)
        store, db = dependencies(config)
        store.init_store(db)
        now = utcnow()
        with connection(store, db) as conn:
            for env in ('MAIN', 'DEV', 'HOM'):
                if not isinstance(config.get(env), dict) or not config[env].get('enabled', True):
                    continue
                statuses, invalid, budget, pids = [], 0, READ_BUDGET, {}
                if opts['enabled']:
                    for path in source_paths(config, env):
                        if budget <= 0:
                            statuses.append({'file': path.name, 'status': 'waiting', 'remaining_bytes': None})
                            continue
                        status, used, errors, touched = _read_source(conn, env, path, budget, now)
                        statuses.append(status)
                        invalid += errors
                        budget -= used
                        for pid, stamp in touched.items():
                            pids[pid] = min(pids.get(pid, stamp), stamp)
                _update_processes(conn, env, pids)
                conn.execute('''INSERT INTO congestion_collection(environment,polled_at,sources,invalid_lines,pending_import)
                    VALUES(?,?,?,?,?) ON CONFLICT(environment) DO UPDATE SET polled_at=excluded.polled_at,
                    sources=excluded.sources,invalid_lines=invalid_lines+excluded.invalid_lines,error=NULL,
                    pending_import=excluded.pending_import''',
                    (env, iso(now), json.dumps(statuses), invalid, int(any(row['status'] in ('importing', 'waiting') for row in statuses))))
            purge(conn, now, opts['budget'])
    finally:
        _collect_lock.release()


def start(config, stop):
    global _thread
    with _start_lock:
        if _thread and _thread.is_alive():
            return
        def run():
            last_error = -float('inf')
            while not stop.is_set():
                try:
                    collect_once(config)
                except Exception:
                    if time.monotonic()-last_error>=60:
                        LOG.exception('Coleta local de congestionamento falhou')
                        last_error=time.monotonic()
                stop.wait(5)
        _thread = threading.Thread(target=run, name='ops-congestion-collector', daemon=True)
        _thread.start()


def interval(since=None, until=None, window='24h'):
    now = utcnow()
    if since or until:
        start, end = parse_time(since), parse_time(until)
    else:
        durations = {'1h': 1, '6h': 6, '24h': 24, '7d': 168}
        if window not in durations:
            raise ValueError('Período inválido')
        start, end = now - timedelta(hours=durations[window]), now
    if end <= start or end - start > timedelta(days=7) or end > now + timedelta(seconds=5):
        raise ValueError('Informe um intervalo passado de até sete dias')
    return iso(start), iso(end)


def _page(limit, offset):
    try:
        return max(1, min(500, int(limit))), max(0, int(offset))
    except (TypeError, ValueError):
        raise ValueError('Paginação inválida') from None


def _episode(row):
    data = dict(row)
    data['duration_ms'] = max(0, round((parse_time(data['end_at']) - parse_time(data['start_at'])).total_seconds() * 1000)) if data['end_at'] and data['start_known'] else None
    data['start_known'], data['partial'] = bool(data['start_known']), bool(data['partial'])
    return data


def _snapshot(row):
    return {'id': row['id'], 'episode_id': row['episode_id'], 'ingested_at': row['ingested_at'], **json.loads(row['payload'])}


def export_evidence(config, env, start, end, *, max_bytes=32 * 1024 * 1024, max_lines=10000):
    """Use event time so late ingestion survives missing/rotated source files."""
    store, db = dependencies(config)
    result = []
    used = 0
    with connection(store, db) as conn:
        cursor = conn.execute('''SELECT event_at,ingested_at,payload FROM congestion_snapshots
            WHERE environment=? AND event_at>=? AND event_at<? ORDER BY event_at,id LIMIT ?''',
            (env, iso(start), iso(end), max_lines + 1))
        for row in cursor:
            line = f"{row['event_at']} WARNING waitress.queue {row['payload']} [ingestão={row['ingested_at']}]"
            used += len(line.encode('utf-8'))
            if used > max_bytes or len(result) >= max_lines:
                result.append('Extração estruturada parcial: limite de bytes ou registros atingido.')
                break
            result.append(line)
    return result or ['Nenhum snapshot estruturado disponível no intervalo.']


def overview(config, env, *, since=None, until=None, window='24h', limit=100, offset=0):
    start_at, end_at = interval(since, until, window)
    limit, offset = _page(limit, offset)
    store, db = dependencies(config)
    with connection(store, db) as conn:
        status = conn.execute('SELECT * FROM congestion_collection WHERE environment=?', (env,)).fetchone()
        latest = conn.execute("SELECT * FROM congestion_snapshots WHERE environment=? AND state!='enabled' ORDER BY event_at DESC,CASE state WHEN 'enabled' THEN 0 WHEN 'congestion_start' THEN 1 WHEN 'congestion_ongoing' THEN 2 ELSE 3 END DESC,id DESC LIMIT 1", (env,)).fetchone()
        enabled = conn.execute("SELECT payload FROM congestion_snapshots WHERE environment=? AND pid=? AND state='enabled' AND event_at<=? ORDER BY event_at DESC LIMIT 1",
                               (env, latest['pid'], latest['event_at'])).fetchone() if latest else None
        episode_rows = conn.execute('''SELECT * FROM congestion_episodes WHERE environment=? AND start_at<?
            AND last_at>=? ORDER BY start_at DESC,id DESC LIMIT ? OFFSET ?''', (env, end_at, start_at, limit + 1, offset)).fetchall()
        oldest = conn.execute('SELECT event_at FROM congestion_snapshots WHERE environment=? ORDER BY event_at LIMIT 1', (env,)).fetchone()
        newest = conn.execute('SELECT event_at FROM congestion_snapshots WHERE environment=? ORDER BY event_at DESC LIMIT 1', (env,)).fetchone()
        count = conn.execute('SELECT COUNT(*) FROM congestion_snapshots WHERE environment=?', (env,)).fetchone()[0]
        coverage = (oldest[0] if oldest else None, newest[0] if newest else None, count)
    collection = dict(status) if status else {'polled_at': None, 'invalid_lines': 0, 'dropped_records': 0, 'pending_import': False}
    collection['sources'] = json.loads(collection.get('sources') or '[]')
    collection['enabled'] = settings(config)['enabled']
    collection['collector_stale'] = not collection['polled_at'] or (utcnow() - parse_time(collection['polled_at'])).total_seconds() > 20
    collection['thread_alive'] = bool(_thread and _thread.is_alive())
    return {'environment': env, 'since': start_at, 'until': end_at, 'collection': collection,
            'enabled_environments': [name for name in ('MAIN', 'DEV', 'HOM') if isinstance(config.get(name), dict) and config[name].get('enabled', True)],
            'latest_interval_seconds': json.loads(enabled['payload']).get('interval_seconds', 5) if enabled else 5,
            'latest': _snapshot(latest) if latest else None,
            'coverage': {'oldest_event_at': coverage[0], 'latest_event_at': coverage[1], 'snapshots': coverage[2], 'retention_days': 7},
            'episodes': [_episode(row) for row in episode_rows[:limit]],
            'next_offset': offset + limit if len(episode_rows) > limit else None}


def detail(config, env, *, episode_id, limit=100, offset=0, snapshot_id=None):
    limit, offset = _page(limit, offset)
    store, db = dependencies(config)
    with connection(store, db) as conn:
        episode = conn.execute('SELECT * FROM congestion_episodes WHERE environment=? AND id=?', (env, episode_id)).fetchone()
        if not episode:
            raise ValueError('Episódio não encontrado ou fora da retenção')
        cursor = conn.execute("SELECT * FROM congestion_snapshots WHERE episode_id=? ORDER BY event_at,CASE state WHEN 'enabled' THEN 0 WHEN 'congestion_start' THEN 1 WHEN 'congestion_ongoing' THEN 2 ELSE 3 END,id LIMIT ? OFFSET ?",
                              (episode_id, limit + 1, offset))
        rows, used, has_more, byte_limited = [], 0, False, False
        for row in cursor:
            if len(rows) >= limit or (rows and used + len(row['payload']) > PAGE_BUDGET):
                has_more = True
                byte_limited = len(rows) < limit
                break
            rows.append(row)
            used += len(row['payload'])
        latest = conn.execute("SELECT * FROM congestion_snapshots WHERE episode_id=? ORDER BY event_at DESC,CASE state WHEN 'enabled' THEN 0 WHEN 'congestion_start' THEN 1 WHEN 'congestion_ongoing' THEN 2 ELSE 3 END DESC,id DESC LIMIT 1", (episode_id,)).fetchone()
        selected = None
        if snapshot_id:
            selected = conn.execute('SELECT * FROM congestion_snapshots WHERE episode_id=? AND id=?', (episode_id, snapshot_id)).fetchone()
            if not selected:
                raise ValueError('Snapshot não encontrado neste episódio')
        # Bucket in SQLite so even a seven-day episode does not fill Python memory.
        seconds = max(0.001, (parse_time(episode['last_at']) - parse_time(episode['start_at'])).total_seconds())
        width = max(1, math.ceil(seconds / 300))
        series = conn.execute('''SELECT MIN(event_at) AS at,MAX(event_at) AS last_at,COUNT(*) AS samples,
            MAX(running) AS running, MIN(running) AS running_min,
            MAX(pending) AS pending, MIN(pending) AS pending_min,
            MAX(threads) AS threads,
            MIN(id) AS snapshot_id FROM congestion_snapshots WHERE episode_id=?
            GROUP BY CAST((julianday(event_at)-julianday(?))*86400/? AS INTEGER) ORDER BY at''',
            (episode_id, episode['start_at'], width)).fetchall()
        # For a small episode expose every real point, including 71ms transitions.
        if episode['snapshot_count'] <= 500:
            series = [{'at': row['event_at'], 'last_at': row['event_at'], 'samples': 1, 'snapshot_id': row['id'],
                       'state': row['state'], 'running':row['running'], 'pending':row['pending'], 'threads':row['threads']}
                      for row in conn.execute("SELECT id,event_at,state,running,pending,threads FROM congestion_snapshots WHERE episode_id=? ORDER BY event_at,CASE state WHEN 'enabled' THEN 0 WHEN 'congestion_start' THEN 1 WHEN 'congestion_ongoing' THEN 2 ELSE 3 END,id", (episode_id,))]
        else:
            series = [{**dict(row), 'aggregated': True, 'snapshot_id': None} for row in series]
            for row in conn.execute("SELECT * FROM congestion_snapshots WHERE episode_id=? AND state IN ('congestion_start','recovered') ORDER BY event_at,id", (episode_id,)):
                payload = json.loads(row['payload'])
                series.append({'at': row['event_at'], 'last_at': row['event_at'], 'samples': 1, 'snapshot_id': row['id'],
                               'state': row['state'], 'running': payload['running_requests'],
                               'pending': payload['pending_requests'], 'threads': payload['threads']})
            series.sort(key=lambda point: (point['at'], bool(point.get('aggregated'))))
        enabled = conn.execute("SELECT payload FROM congestion_snapshots WHERE id=? AND state='enabled'", (episode['session'],)).fetchone()
    return {'environment': env, 'episode': _episode(episode), 'snapshots': [_snapshot(row) for row in rows[:limit]],
            'latest_snapshot': _snapshot(latest) if latest else None,
            'selected': _snapshot(selected) if selected else None,
            'next_offset': offset + len(rows) if has_more else None, 'page_limited_by_bytes': byte_limited,
            'series': [dict(row) for row in series], 'series_aggregated': episode['snapshot_count'] > 500,
            'expected_interval_seconds': json.loads(enabled['payload']).get('interval_seconds', 5) if enabled else 5,
            'session_known': not episode['session'].startswith('unknown:')}
