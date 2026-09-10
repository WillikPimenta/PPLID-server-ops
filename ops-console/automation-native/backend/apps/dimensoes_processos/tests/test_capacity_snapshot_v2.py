from __future__ import annotations

from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.dimensoes_processos.models import CapacityDailySnapshot
from apps.dimensoes_processos.services.capacity import (
    CAPACITY_PERIOD_METRIC_VERSION,
    calculate_capacity_period,
)


def _period_payload(on_date: date, *, metric_version=CAPACITY_PERIOD_METRIC_VERSION):
    return {
        "series": {
            "date": on_date.isoformat(),
            "metric_version": metric_version,
            "exact_fte_before_rounding": "10.0000",
        },
        "blockers": [],
        "warnings": [],
    }


class CapacityDailySnapshotV2ModelTests(TestCase):
    def _create_snapshot(self, **overrides):
        now = timezone.now()
        values = {
            "calculation_date": date(2026, 8, 24),
            "scenario_id": "planejamento",
            "metric_version": CAPACITY_PERIOD_METRIC_VERSION,
            "source_fingerprint": "source-a",
            "payload": _period_payload(date(2026, 8, 24)),
            "generated_at": now,
            "valid_until": now + timedelta(hours=1),
            "generation_id": uuid4(),
        }
        values.update(overrides)
        return CapacityDailySnapshot.objects.create(**values)

    def test_identity_allows_same_date_for_scenario_version_or_source(self):
        self._create_snapshot()
        self._create_snapshot(scenario_id="operacao")
        self._create_snapshot(metric_version="future-version")
        self._create_snapshot(source_fingerprint="source-b")

        self.assertEqual(CapacityDailySnapshot.objects.count(), 4)

    def test_identity_rejects_an_exact_duplicate(self):
        self._create_snapshot()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_snapshot(generation_id=uuid4())

    def test_is_compatible_requires_validity_version_and_fingerprint(self):
        now = timezone.now()
        snapshot = self._create_snapshot()

        self.assertTrue(
            snapshot.is_compatible(
                metric_version=CAPACITY_PERIOD_METRIC_VERSION,
                source_fingerprint="source-a",
                at=now,
            )
        )
        self.assertFalse(
            snapshot.is_compatible(
                metric_version="old-version",
                source_fingerprint="source-a",
                at=now,
            )
        )
        self.assertFalse(
            snapshot.is_compatible(
                metric_version=CAPACITY_PERIOD_METRIC_VERSION,
                source_fingerprint="source-b",
                at=now,
            )
        )
        self.assertFalse(
            snapshot.is_compatible(
                metric_version=CAPACITY_PERIOD_METRIC_VERSION,
                source_fingerprint="source-a",
                at=snapshot.valid_until,
            )
        )


class CapacitySnapshotSyncV2Tests(TestCase):
    def _run(self, **overrides):
        options = {
            "date_from": "2026-08-24",
            "date_to": "2026-08-25",
            "ttl_minutes": 60,
            "scenario": "operacao",
            "source_fingerprint": "sla-sync-42",
            "stdout": StringIO(),
        }
        options.update(overrides)
        call_command("sync_capacity_snapshots", **options)

    @patch(
        "apps.dimensoes_processos.management.commands.sync_capacity_snapshots."
        "materialize_capacity_hourly_profiles",
        return_value={"available": True},
    )
    @patch(
        "apps.dimensoes_processos.management.commands.sync_capacity_snapshots."
        "calculate_capacity_period_day"
    )
    def test_operation_sync_is_idempotent_and_publishes_one_generation(
        self, calculate_mock, _profiles_mock
    ):
        calculate_mock.side_effect = lambda on_date, **kwargs: _period_payload(on_date)

        self._run()
        first_generation = CapacityDailySnapshot.objects.values_list(
            "generation_id", flat=True
        ).first()
        self._run()

        rows = list(CapacityDailySnapshot.objects.order_by("calculation_date"))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.scenario_id for row in rows}, {"operacao"})
        self.assertEqual(
            {row.metric_version for row in rows}, {CAPACITY_PERIOD_METRIC_VERSION}
        )
        self.assertEqual({row.source_fingerprint for row in rows}, {"sla-sync-42"})
        self.assertEqual(len({row.generation_id for row in rows}), 1)
        self.assertNotEqual(rows[0].generation_id, first_generation)
        self.assertTrue(
            all(call.kwargs["scenario_id"] == "operacao" for call in calculate_mock.call_args_list)
        )

    @patch(
        "apps.dimensoes_processos.management.commands.sync_capacity_snapshots."
        "materialize_capacity_hourly_profiles",
        return_value={"available": True},
    )
    @patch(
        "apps.dimensoes_processos.management.commands.sync_capacity_snapshots."
        "calculate_capacity_period_day"
    )
    def test_derived_fingerprint_is_stable(self, calculate_mock, _profiles_mock):
        calculate_mock.side_effect = lambda on_date, **kwargs: _period_payload(on_date)

        self._run(source_fingerprint=None)
        first = list(
            CapacityDailySnapshot.objects.values_list("source_fingerprint", flat=True)
        )
        self._run(source_fingerprint=None)

        self.assertEqual(CapacityDailySnapshot.objects.count(), 2)
        self.assertEqual(
            list(CapacityDailySnapshot.objects.values_list("source_fingerprint", flat=True)),
            first,
        )
        self.assertEqual(len(first[0]), 64)

    @patch(
        "apps.dimensoes_processos.management.commands.sync_capacity_snapshots."
        "materialize_capacity_hourly_profiles",
        return_value={"available": True},
    )
    @patch(
        "apps.dimensoes_processos.management.commands.sync_capacity_snapshots."
        "calculate_capacity_period_day"
    )
    def test_metric_mismatch_aborts_without_publishing(
        self, calculate_mock, _profiles_mock
    ):
        calculate_mock.side_effect = lambda on_date, **kwargs: _period_payload(
            on_date, metric_version="old-version"
        )

        with self.assertRaisesMessage(CommandError, "versao da metrica divergente"):
            self._run()

        self.assertFalse(CapacityDailySnapshot.objects.exists())

    @patch(
        "apps.dimensoes_processos.management.commands.sync_capacity_snapshots."
        "materialize_capacity_hourly_profiles",
        return_value={"available": True},
    )
    @patch(
        "apps.dimensoes_processos.management.commands.sync_capacity_snapshots."
        "calculate_capacity_period_day"
    )
    def test_calculation_failure_preserves_previous_generation(
        self, calculate_mock, _profiles_mock
    ):
        now = timezone.now()
        previous = CapacityDailySnapshot.objects.create(
            calculation_date=date(2026, 8, 24),
            scenario_id="operacao",
            metric_version=CAPACITY_PERIOD_METRIC_VERSION,
            source_fingerprint="sla-sync-42",
            payload={"previous": True},
            generated_at=now,
            valid_until=now + timedelta(hours=1),
            generation_id=uuid4(),
        )
        previous_generation = previous.generation_id

        def calculate(on_date, **kwargs):
            if on_date == date(2026, 8, 25):
                raise RuntimeError("calculation failed")
            return _period_payload(on_date)

        calculate_mock.side_effect = calculate

        with self.assertRaisesMessage(RuntimeError, "calculation failed"):
            self._run()

        previous.refresh_from_db()
        self.assertEqual(previous.generation_id, previous_generation)
        self.assertEqual(previous.payload, {"previous": True})
        self.assertEqual(CapacityDailySnapshot.objects.count(), 1)


class CapacitySnapshotReaderV2Tests(TestCase):
    def _create_snapshot(self, on_date, *, fingerprint, generation_id):
        now = timezone.now()
        return CapacityDailySnapshot.objects.create(
            calculation_date=on_date,
            scenario_id="planejamento",
            metric_version=CAPACITY_PERIOD_METRIC_VERSION,
            source_fingerprint=fingerprint,
            payload=_period_payload(on_date),
            generated_at=now,
            valid_until=now + timedelta(hours=1),
            generation_id=generation_id,
        )

    @patch(
        "apps.dimensoes_processos.services.capacity._summarize_capacity_period",
        side_effect=lambda _from, _to, days, **_kwargs: {"days": days},
    )
    @patch(
        "apps.dimensoes_processos.services.capacity.calculate_capacity_period_day",
        side_effect=lambda on_date, **_kwargs: {"live": on_date.isoformat()},
    )
    @patch(
        "apps.dimensoes_processos.services.capacity.current_capacity_source_fingerprint",
        return_value="source-current",
    )
    def test_reader_rejects_a_fresh_snapshot_with_stale_fingerprint(
        self, _fingerprint, live_calculation, _summarize
    ):
        on_date = date(2026, 8, 24)
        self._create_snapshot(
            on_date,
            fingerprint="source-stale",
            generation_id=uuid4(),
        )

        result = calculate_capacity_period(on_date, on_date)

        self.assertEqual(result["days"], [{"live": on_date.isoformat()}])
        live_calculation.assert_called_once()

    @patch(
        "apps.dimensoes_processos.services.capacity._summarize_capacity_period",
        side_effect=lambda _from, _to, days, **_kwargs: {"days": days},
    )
    @patch(
        "apps.dimensoes_processos.services.capacity.calculate_capacity_period_day",
        side_effect=lambda on_date, **_kwargs: {"live": on_date.isoformat()},
    )
    @patch(
        "apps.dimensoes_processos.services.capacity.current_capacity_source_fingerprint",
        return_value="source-current",
    )
    def test_reader_does_not_mix_an_incomplete_generation(
        self, _fingerprint, live_calculation, _summarize
    ):
        date_from = date(2026, 8, 24)
        date_to = date_from + timedelta(days=1)
        self._create_snapshot(
            date_from,
            fingerprint="source-current",
            generation_id=uuid4(),
        )

        result = calculate_capacity_period(date_from, date_to)

        self.assertEqual(
            result["days"],
            [{"live": date_from.isoformat()}, {"live": date_to.isoformat()}],
        )
        self.assertEqual(live_calculation.call_count, 2)
