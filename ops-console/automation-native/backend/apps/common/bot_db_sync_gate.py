# -*- coding: utf-8 -*-
"""Filas high/low: um sync por lane (advisory lock PostgreSQL)."""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from typing import Iterator
from uuid import UUID, uuid4

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.common.bot_db_sync_lanes import LANE_HIGH, LANE_MID, LANE_LOW, normalize_lane
from apps.common.models import BotDbSyncJob, BotDbSyncLockLease

log = logging.getLogger(__name__)

# Chaves estáveis — não colidir com escala_flex (0x4E5354xx).
BOT_DB_SYNC_HOST_HEAVY_LOCK_KEY = 88442200
BOT_DB_SYNC_ADVISORY_LOCK_KEYS = {
    LANE_HIGH: 88442201,
    LANE_MID: 88442203,
    LANE_LOW: 88442202,
}

_fallback_host_heavy_lock = threading.Lock()
_fallback_locks = {
    LANE_HIGH: threading.Lock(),
    LANE_MID: threading.Lock(),
    LANE_LOW: threading.Lock(),
}
_fallback_slot_locks = {
    LANE_HIGH: [_fallback_locks[LANE_HIGH]]
    + [threading.Lock() for _ in range(9)],
    LANE_LOW: [_fallback_locks[LANE_LOW]]
    + [threading.Lock() for _ in range(19)],
    LANE_MID: [_fallback_locks[LANE_MID]]
    + [threading.Lock() for _ in range(9)],
}
_fallback_host_slot_locks = [_fallback_host_heavy_lock] + [
    threading.Lock() for _ in range(9)
]
# Compat com testes antigos que liberam o lock high.
_fallback_lock = _fallback_locks[LANE_HIGH]


class BotDbSyncQueueTimeoutError(Exception):
    """Não conseguiu slot na fila de sync dentro do timeout."""


def _queue_wait_s() -> float:
    from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config

    return float(get_bot_db_sync_runtime_config().queue_wait_s)


def _poll_s() -> float:
    return max(0.05, float(getattr(settings, "BOT_DB_SYNC_POLL_MS", 500)) / 1000.0)


def _lane_concurrency(lane: str) -> int:
    from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config

    cfg = get_bot_db_sync_runtime_config()
    if lane == LANE_HIGH:
        return int(cfg.high_concurrency)
    if lane == LANE_MID:
        return int(cfg.mid_concurrency)
    return int(cfg.low_concurrency)


def _slot_lock_key(lane: str, slot_index: int) -> int:
    if slot_index == 0:
        return BOT_DB_SYNC_ADVISORY_LOCK_KEYS[lane]
    base = 88442300 if lane == LANE_HIGH else 88442600 if lane == LANE_MID else 88442400
    return base + slot_index


def _host_slot_lock_key(slot_index: int) -> int:
    if slot_index == 0:
        return BOT_DB_SYNC_HOST_HEAVY_LOCK_KEY
    return 88442500 + slot_index


def _lease_lane(lane: str, slot_index: int) -> str:
    return f"{lane}:{slot_index}"


def _lease_lane_parts(value: str) -> tuple[str, int]:
    lane_value, _, slot_value = (value or "").partition(":")
    lane = normalize_lane(lane_value)
    try:
        slot_index = max(0, int(slot_value or 0))
    except ValueError:
        slot_index = 0
    return lane, slot_index


def _is_postgresql() -> bool:
    return connection.vendor == "postgresql"


def _try_acquire_pg(lock_connection, lock_key: int) -> bool:
    with lock_connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_key])
        row = cursor.fetchone()
    return bool(row and row[0])


def _release_pg(lock_connection, lock_key: int) -> None:
    with lock_connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_key])


def _open_lock_connection(owner_token: UUID, lane: str):
    """Open a connection used only by the advisory lock lifecycle."""
    import psycopg

    db = dict(settings.DATABASES["default"])
    options = dict(db.pop("OPTIONS", {}) or {})
    kwargs = {
        "dbname": db.get("NAME"),
        "user": db.get("USER"),
        "password": db.get("PASSWORD"),
        "host": db.get("HOST"),
        "port": db.get("PORT"),
        "application_name": f"pplid-bot-sync-lock-{lane}-{owner_token}",
    }
    if options.get("connect_timeout") is not None:
        kwargs["connect_timeout"] = options["connect_timeout"]
    lock_connection = psycopg.connect(
        **{key: value for key, value in kwargs.items() if value is not None}
    )
    # Keep the lock session idle (never idle-in-transaction) while the sync
    # performs work on Django's normal connection.
    lock_connection.autocommit = True
    return lock_connection


class _LeaseHeartbeat:
    def __init__(self, owner_token: UUID):
        self.owner_token = owner_token
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=f"bot-sync-lease-{owner_token}",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stop.wait(15):
            try:
                close_old_connections()
                BotDbSyncLockLease.objects.filter(owner_token=self.owner_token).update(
                    heartbeat_at=timezone.now()
                )
            except Exception:
                log.exception("bot-db-sync lease heartbeat failed token=%s", self.owner_token)
            finally:
                close_old_connections()


def _create_lease(*, lane: str, owner_token: UUID, backend_pid: int | None, label: str) -> None:
    BotDbSyncLockLease.objects.update_or_create(
        lane=lane,
        defaults={
            "owner_token": owner_token,
            "backend_pid": backend_pid,
            "process_pid": os.getpid(),
            "label": label[:128],
            "acquired_at": timezone.now(),
            "heartbeat_at": timezone.now(),
        },
    )


def _delete_lease(owner_token: UUID) -> None:
    BotDbSyncLockLease.objects.filter(owner_token=owner_token).delete()


def reconcile_stale_bot_db_sync_locks() -> dict[str, int]:
    """Terminate only idle, expired lock leases with no running job in the lane."""
    if not _is_postgresql():
        return {"recovered": 0, "ignored": 0, "missing": 0}

    from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config

    cutoff = timezone.now() - timedelta(
        minutes=max(1, int(get_bot_db_sync_runtime_config().stale_minutes))
    )
    result = {"recovered": 0, "ignored": 0, "missing": 0}
    leases = BotDbSyncLockLease.objects.filter(heartbeat_at__lt=cutoff)
    for lease in leases.iterator():
        lane, slot_index = _lease_lane_parts(lease.lane)
        # A live job is allowed to outlast the queue wait timeout. Its heartbeat
        # is the authoritative proof that the lock is still owned legitimately.
        if BotDbSyncJob.objects.filter(
            lane=lane,
            status=BotDbSyncJob.STATUS_RUNNING,
        ).exists():
            result["ignored"] += 1
            continue

        application_name = f"pplid-bot-sync-lock-{lane}-{lease.owner_token}"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.pid, a.state
                  FROM pg_locks l
                  JOIN pg_stat_activity a ON a.pid = l.pid
                 WHERE l.locktype = 'advisory'
                   AND l.granted
                   AND l.objid = %s
                   AND a.application_name = %s
                """,
                [_slot_lock_key(lane, slot_index), application_name],
            )
            owner = cursor.fetchone()
            if owner is None:
                lease.delete()
                result["missing"] += 1
                continue
            pid, state = owner
            if state != "idle":
                result["ignored"] += 1
                continue
            cursor.execute("SELECT pg_terminate_backend(%s)", [pid])
            terminated_row = cursor.fetchone()
            if not terminated_row or not terminated_row[0]:
                result["ignored"] += 1
                continue
        lease.delete()
        result["recovered"] += 1
        log.warning(
            "bot-db-sync stale lock recovered lane=%s backend_pid=%s token=%s",
            lease.lane,
            pid,
            lease.owner_token,
        )
    return result


def bot_db_sync_lock_snapshot() -> dict[str, dict]:
    """Return lease diagnostics without attempting recovery."""
    now = timezone.now()
    return {
        lease.lane: {
            "owner_token": str(lease.owner_token),
            "backend_pid": lease.backend_pid,
            "process_pid": lease.process_pid,
            "label": lease.label,
            "acquired_at": lease.acquired_at.isoformat(),
            "heartbeat_at": lease.heartbeat_at.isoformat(),
            "heartbeat_age_s": max(0, int((now - lease.heartbeat_at).total_seconds())),
        }
        for lease in BotDbSyncLockLease.objects.all()
    }


def _try_acquire_pair(
    *,
    use_pg: bool,
    lock_connection=None,
    host_key: int | None,
    lane_key: int,
    host_fallback: threading.Lock | None,
    lane_fallback: threading.Lock,
) -> tuple[bool, bool]:
    """
    Ordem fixa host → lane. Retorna (host_held, lane_held).
    Se lane falhar após host, libera host imediatamente.
    """
    host_held = False
    if host_key is not None:
        if use_pg:
            host_held = _try_acquire_pg(lock_connection, host_key)
        else:
            assert host_fallback is not None
            host_held = host_fallback.acquire(blocking=False)
        if not host_held:
            return False, False

    if use_pg:
        lane_held = _try_acquire_pg(lock_connection, lane_key)
    else:
        lane_held = lane_fallback.acquire(blocking=False)

    if not lane_held and host_held:
        if use_pg:
            _release_pg(lock_connection, host_key)  # type: ignore[arg-type]
        else:
            assert host_fallback is not None
            host_fallback.release()
        return False, False

    return host_held, lane_held


def _release_pair(
    *,
    use_pg: bool,
    lock_connection=None,
    host_key: int | None,
    lane_key: int,
    host_held: bool,
    lane_held: bool,
    host_fallback: threading.Lock | None,
    lane_fallback: threading.Lock,
) -> None:
    # Ordem inversa: lane → host.
    if lane_held:
        if use_pg:
            _release_pg(lock_connection, lane_key)
        else:
            lane_fallback.release()
    if host_held and host_key is not None:
        if use_pg:
            _release_pg(lock_connection, host_key)
        else:
            assert host_fallback is not None
            host_fallback.release()


@contextmanager
def bot_db_sync_slot(label: str = "sync", *, lane: str = LANE_HIGH) -> Iterator[None]:
    """
    Aguarda um slot exclusivo na fila `lane` (high ou low).

    Em PostgreSQL usa advisory lock (cross-process). Em outros backends,
    cai para threading.Lock (só serializa no processo atual).

    Lane high também adquire lock host (88442200) — ordem host→lane —
    para serializar syncs pesados entre drain e caminhos HTTP.
    Low permanece independente (1 low pode coexistir com 1 high).
    """
    lane = normalize_lane(lane)
    need_host = lane == LANE_HIGH
    concurrency = max(1, _lane_concurrency(lane))
    wait_s = _queue_wait_s()
    poll_s = _poll_s()
    deadline = time.monotonic() + wait_s
    host_held = False
    lane_held = False
    use_pg = _is_postgresql()
    owner_token = uuid4()
    lock_connection = None
    heartbeat = None
    slot_index = 0
    lock_key = _slot_lock_key(lane, slot_index)
    fallback = _fallback_slot_locks[lane][slot_index]
    host_key = _host_slot_lock_key(slot_index) if need_host else None
    host_fallback = _fallback_host_heavy_lock if need_host else None

    if use_pg:
        lock_connection = _open_lock_connection(owner_token, lane)

    log.info(
        "bot-db-sync fila: aguardando slot lane=%s label=%s wait_s=%.0f host=%s",
        lane,
        label,
        wait_s,
        need_host,
    )
    while True:
        for candidate in range(concurrency):
            candidate_lock_key = _slot_lock_key(lane, candidate)
            candidate_fallback = _fallback_slot_locks[lane][candidate]
            candidate_host_key = _host_slot_lock_key(candidate) if need_host else None
            candidate_host_fallback = (
                _fallback_host_slot_locks[candidate] if need_host else None
            )
            host_held, lane_held = _try_acquire_pair(
                use_pg=use_pg,
                lock_connection=lock_connection,
                host_key=candidate_host_key,
                lane_key=candidate_lock_key,
                host_fallback=candidate_host_fallback,
                lane_fallback=candidate_fallback,
            )
            if lane_held:
                slot_index = candidate
                lock_key = candidate_lock_key
                fallback = candidate_fallback
                host_key = candidate_host_key
                host_fallback = candidate_host_fallback
                break
        if lane_held:
            break
        if time.monotonic() >= deadline:
            if lock_connection is not None:
                lock_connection.close()
            raise BotDbSyncQueueTimeoutError(
                f"Fila de sincronização ocupada (lane={lane}, label={label}). "
                "Aguarde o sync em andamento e tente novamente."
            )
        time.sleep(poll_s)

    if use_pg:
        try:
            _create_lease(
                lane=_lease_lane(lane, slot_index),
                owner_token=owner_token,
                backend_pid=getattr(lock_connection.info, "backend_pid", None),
                label=label,
            )
            heartbeat = _LeaseHeartbeat(owner_token)
            heartbeat.start()
        except Exception:
            _release_pair(
                use_pg=True,
                lock_connection=lock_connection,
                host_key=host_key,
                lane_key=lock_key,
                host_held=host_held,
                lane_held=lane_held,
                host_fallback=host_fallback,
                lane_fallback=fallback,
            )
            lock_connection.close()
            raise

    log.info("bot-db-sync fila: slot adquirido lane=%s label=%s", lane, label)
    try:
        yield
    finally:
        if heartbeat is not None:
            heartbeat.close()
        try:
            if use_pg:
                _delete_lease(owner_token)
            _release_pair(
                use_pg=use_pg,
                lock_connection=lock_connection,
                host_key=host_key,
                lane_key=lock_key,
                host_held=host_held,
                lane_held=lane_held,
                host_fallback=host_fallback,
                lane_fallback=fallback,
            )
        except Exception:
            log.exception(
                "bot-db-sync fila: falha ao liberar slot lane=%s label=%s",
                lane,
                label,
            )
        else:
            log.info("bot-db-sync fila: slot liberado lane=%s label=%s", lane, label)
        finally:
            if lock_connection is not None:
                lock_connection.close()
