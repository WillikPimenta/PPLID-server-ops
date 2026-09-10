# -*- coding: utf-8 -*-
"""Guard de memória livre antes de syncs pesados (bot→banco)."""
from __future__ import annotations

import logging
import sys
from typing import Optional

from django.conf import settings

log = logging.getLogger(__name__)


def _min_free_mb() -> float:
    return max(0.0, float(getattr(settings, "BOT_DB_SYNC_MIN_FREE_MB", 2048)))


def free_memory_mb() -> Optional[float]:
    """Retorna MB físicos livres, ou None se a API falhar (fail-open)."""
    if sys.platform != "win32":
        try:
            # Linux: MemAvailable em /proc/meminfo
            available_kb = None
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        available_kb = int(line.split()[1])
                        break
            if available_kb is None:
                return None
            return available_kb / 1024.0
        except (OSError, ValueError):
            return None

    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return float(stat.ullAvailPhys) / (1024.0 * 1024.0)
    except Exception:
        log.debug("bot-db-sync memory: falha ao ler memória livre", exc_info=True)
        return None


def memory_ok_for_heavy_sync() -> bool:
    """
    True se há memória suficiente (ou se a checagem falhou — fail-open).
    """
    free_mb = free_memory_mb()
    if free_mb is None:
        return True
    min_mb = _min_free_mb()
    ok = free_mb >= min_mb
    if not ok:
        log.warning(
            "bot-db-sync memory: RAM livre insuficiente (%.0f MB < %.0f MB)",
            free_mb,
            min_mb,
        )
    return ok
