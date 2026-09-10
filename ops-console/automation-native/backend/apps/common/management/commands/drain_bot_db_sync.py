# -*- coding: utf-8 -*-
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.common.bot_db_sync_lanes import LANES, normalize_lane
from apps.common.bot_db_sync_queue import (
    acquire_drain_process_lock,
    drain_pending_jobs,
    release_drain_process_lock,
    spawn_drain_worker,
)
from apps.common.models import BotDbSyncJob


class Command(BaseCommand):
    help = (
        "Processa a fila de sync bot→banco fora do worker HTTP. "
        "Use --lane high|mid|low (default: todas em sequência neste processo)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max",
            type=int,
            default=10,
            help="Máximo de jobs a processar por lane nesta execução (default: 10).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Processa no máximo 1 job por lane.",
        )
        parser.add_argument(
            "--lane",
            choices=[*LANES, "all"],
            default="all",
            help="Fila a drenar: high, mid, low ou all (default: all).",
        )

    def handle(self, *args, **options):
        max_jobs = 1 if options["once"] else max(1, int(options["max"]))
        lane_opt = options["lane"]
        lanes = list(LANES) if lane_opt == "all" else [normalize_lane(lane_opt)]

        for lane in lanes:
            if not acquire_drain_process_lock(lane):
                self.stdout.write(
                    self.style.WARNING(
                        f"Outro drain já está em execução na fila {lane}; pulando."
                    )
                )
                continue
            result = None
            try:
                result = drain_pending_jobs(max_jobs=max_jobs, lane=lane)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"drain_bot_db_sync lane={lane}: "
                        f"processed={result['processed']} done={result['done']} "
                        f"skipped={result['skipped']} failed={result['failed']} "
                        f"stale_requeued={result.get('stale_requeued', 0)} "
                        f"stale_failed={result.get('stale_failed', 0)}"
                    )
                )
            finally:
                release_drain_process_lock(lane)
            if (
                result
                and not result.get("deferred_memory")
                and BotDbSyncJob.objects.filter(
                    lane=lane,
                    status=BotDbSyncJob.STATUS_PENDING,
                ).exists()
            ):
                spawn_drain_worker(max_jobs=max_jobs, lane=lane)
