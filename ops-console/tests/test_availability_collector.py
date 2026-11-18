import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server_monitoring as monitoring


class AvailabilityCollectorTests(unittest.TestCase):
    def test_scheduler_discounts_work_and_skips_missed_ticks(self):
        clock = [0.0]
        starts, waits = [], []
        stop = Mock()
        stop.is_set.return_value = False

        def work():
            starts.append(clock[0])
            clock[0] += [7, 65, 1][len(starts) - 1]

        def wait(seconds):
            waits.append(seconds)
            clock[0] += seconds
            return len(waits) == 3

        stop.wait.side_effect = wait
        with patch.object(monitoring, '_COLLECTOR_STOP', stop), patch.object(monitoring.time, 'monotonic', side_effect=lambda: clock[0]):
            monitoring._run_periodic(work, 30)
        self.assertEqual(starts, [0, 30, 120])
        self.assertEqual(waits, [23, 25, 29])

    def test_slow_environment_does_not_block_others_and_errors_are_isolated(self):
        main_started, dev_done = threading.Event(), threading.Event()
        store = Mock()

        def collect(_config, env, *_args):
            if env == 'MAIN':
                main_started.set()
                self.assertTrue(dev_done.wait(2))
                raise RuntimeError('probe unavailable')
            self.assertTrue(main_started.wait(2))
            dev_done.set()

        with patch.object(monitoring, '_import_ops_store', return_value=store), \
             patch.object(monitoring, 'resolve_ops_store_path', return_value=Path('unused')), \
             patch.object(monitoring, 'get_monitoring_settings', return_value={}), \
             patch.object(monitoring, '_collect_availability', side_effect=collect) as probe:
            monitoring._availability_tick({'MAIN': {}, 'DEV': {}, 'HOM': {'enabled': False}})
        self.assertEqual(probe.call_count, 2)
        self.assertTrue(dev_done.is_set())

    def test_samples_are_atomic_deduplicated_and_timeouts_never_reuse_success(self):
        store = Mock()
        snapshot = {'checkedAt': '2026-11-18T10:00:00.000Z', 'reachable': True,
                    'timedOut': True, 'lastValidUsed': True, 'backendPortUp': True, 'durationMs': 3000}
        coordinator = SimpleNamespace(get_snapshot=Mock(return_value=snapshot))
        with patch('health_probe.get_coordinator', return_value=coordinator), \
             patch.object(monitoring, '_LAST_AVAILABILITY_CHECK', {}), \
             patch.object(monitoring, '_detect_health_spike'), \
             patch.object(monitoring, 'get_monitoring_settings', return_value={'availabilityIntervalSec': 30}):
            monitoring._collect_availability({}, 'MAIN', store, Path('unused'))
            monitoring._collect_availability({}, 'MAIN', store, Path('unused'))
        store.insert_monitor_samples_batch.assert_called_once()
        rows = store.insert_monitor_samples_batch.call_args.args[0]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row['recorded_at'] == snapshot['checkedAt'] for row in rows))
        self.assertEqual(next(row['value'] for row in rows if row['metric_key'] == 'health_reachable'), 0)
        self.assertTrue(all(row['labels']['intervalSec'] == 30 for row in rows))
        coordinator.get_snapshot.assert_called_with({}, 'MAIN', wait=True, force=True)

    def test_stale_snapshot_does_not_create_observation(self):
        store = Mock()
        with patch('health_probe.get_coordinator', return_value=SimpleNamespace(
                get_snapshot=Mock(return_value={'checkedAt': '2026-11-18T10:00:00Z', 'stale': True}))):
            monitoring._collect_availability({}, 'MAIN', store, Path('unused'))
        store.insert_monitor_samples_batch.assert_not_called()

    def test_http_5xx_and_unhealthy_readiness_are_failures_even_when_reachable(self):
        for code, status, expected in [(200, 'healthy', 1), (200, 'degraded', 1),
                                       (503, 'unhealthy', 0), (200, 'unhealthy', 0)]:
            store = Mock()
            health = {'checkedAt': '2026-11-18T12:00:00Z', 'reachable': True, 'httpStatus': code, 'status': status}
            with patch('health_probe.get_coordinator', return_value=SimpleNamespace(get_snapshot=Mock(return_value=health))), \
                 patch.object(monitoring, '_LAST_AVAILABILITY_CHECK', {}), \
                 patch.object(monitoring, '_detect_health_spike'), \
                 patch.object(monitoring, 'get_monitoring_settings', return_value={}):
                monitoring._collect_availability({}, 'MAIN', store, Path('unused'))
            rows = store.insert_monitor_samples_batch.call_args.args[0]
            self.assertEqual(next(row['value'] for row in rows if row['metric_key'] == 'health_reachable'), expected)


if __name__ == '__main__':
    unittest.main()
