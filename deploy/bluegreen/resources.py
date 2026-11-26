"""Read Windows commit headroom without changing paging or requiring admin."""
from __future__ import annotations

import ctypes
import os

import psutil


def available_commit_bytes() -> int:
    if os.name != 'nt':
        return int(psutil.virtual_memory().available + psutil.swap_memory().free)

    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [
            (name, ctypes.c_uint64) for name in (
                'totalPhysical', 'availablePhysical', 'totalCommit', 'availableCommit',
                'totalVirtual', 'availableVirtual', 'availableExtendedVirtual')]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = ctypes.c_int
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(status.availableCommit)
