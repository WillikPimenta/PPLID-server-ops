"""Disposable, local seven-day history benchmark; no backend/production DB access."""
from datetime import timedelta
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops-console'), str(ROOT / 'lib')]
import ops_store  # noqa: E402
import server_congestion as cg  # noqa: E402


def run(count):
    with tempfile.TemporaryDirectory(prefix='ops-congestion-benchmark-') as folder:
        db = Path(folder) / 'benchmark.db'
        ops_store.init_store(db)
        cg.dependencies = lambda _: (ops_store, db)
        cg.settings = lambda _: {'enabled': True, 'budget': 256 * 1024 * 1024, 'days': 7}
        config = {'MAIN': {'enabled': True}}
        start = cg.utcnow() - timedelta(days=7) + timedelta(seconds=10)
        span = timedelta(days=7).total_seconds() - 20
        template = {'environment': 'MAIN', 'pid': 9001, 'threads': 16, 'dispatcher_queued_channels': 1,
                    'waitress_queue_depth': 1, 'pending_requests': 1, 'running_requests': 16,
                    'pending_routes': [], 'running_routes': [], 'slow_executions': [],
                    'pending_routes_partial': False, 'running_routes_partial': False,
                    'slow_executions_partial': False, 'request_totals_partial': False,
                    'diagnostic_failures': 0, 'unlisted_requests': 0}
        ingested = cg.iso(cg.utcnow())

        def records():
            for index in range(count):
                data = {**template, 'event_at': cg.iso(start + timedelta(seconds=span * index / max(1, count - 1))),
                        'state': 'congestion_start' if index == 0 else 'recovered' if index == count - 1 else 'congestion_ongoing',
                        'pending_requests': 0 if index == count - 1 else 80 if index == count // 2 else 1}
                payload = json.dumps(data, separators=(',', ':'))
                yield (f'{index:064x}', 'MAIN', 9001, data['event_at'], ingested, data['state'], payload, len(payload), data['pending_requests'], data['running_requests'],16,0)

        started = time.perf_counter()
        with cg.connection(ops_store, db) as conn:
            conn.executemany('''INSERT INTO congestion_snapshots
                (id,environment,pid,event_at,ingested_at,state,payload,payload_bytes,pending,running,threads,partial) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', records())
            cg._rebuild_process(conn, 'MAIN', 9001)
        import_seconds = time.perf_counter() - started
        tick = time.perf_counter()
        with cg.connection(ops_store, db) as conn:
            cg.purge(conn, cg.utcnow() + timedelta(seconds=20), 256 * 1024 * 1024)
        purge_ms = (time.perf_counter() - tick) * 1000
        overview_times, detail_times = [], []
        tracemalloc.start()
        for _ in range(5):
            tick = time.perf_counter()
            overview = cg.overview(config, 'MAIN', window='7d')
            overview_times.append((time.perf_counter() - tick) * 1000)
            tick = time.perf_counter()
            detail = cg.detail(config, 'MAIN', episode_id=overview['episodes'][0]['id'])
            detail_times.append((time.perf_counter() - tick) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert detail['series_aggregated'] and max(row['pending'] for row in detail['series']) == 80
        print(json.dumps({'snapshots': count, 'history_days': 7, 'build_seconds': round(import_seconds, 3),
                          'overview_median_ms': round(statistics.median(overview_times), 2),
                          'detail_median_ms': round(statistics.median(detail_times), 2),
                          'api_python_peak_bytes': peak,
                          'retention_prune_ms': round(purge_ms, 2),
                          'sqlite_bytes': sum(path.stat().st_size for path in Path(folder).glob('benchmark.db*')),
                          'returned_snapshots': len(detail['snapshots']), 'chart_points': len(detail['series']),
                          'notes': 'Synthetic metadata only; excludes original service-log copies and real disk ingestion.'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshots', type=int, default=120960)
    args = parser.parse_args()
    if not 501 <= args.snapshots <= 1000000:
        parser.error('--snapshots must be between 501 and 1000000')
    run(args.snapshots)
