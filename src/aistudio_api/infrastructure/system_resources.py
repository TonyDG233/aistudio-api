"""Cheap process resource sampling for the advanced memory card."""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import time
from pathlib import Path
from typing import Any

_PROCESS_STARTED = time.monotonic()
_PROCESS_CPU_STARTED = time.process_time()


def _linux_memory() -> tuple[float | None, float | None]:
    try:
        with Path("/proc/self/statm").open(encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        return pages * page_size / 1024 / 1024, total_pages * page_size / 1024 / 1024
    except (OSError, ValueError, IndexError):
        return None, None


def _posix_memory() -> tuple[float | None, float | None]:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        process_mb = usage / 1024 / 1024 if sys.platform == "darwin" else usage / 1024
        total_mb = None
        with contextlib.suppress(OSError, ValueError):
            total_mb = (
                os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 / 1024
            )
        return process_mb, total_mb
    except (OSError, ValueError):
        return None, None


def _windows_memory() -> tuple[float | None, float | None]:
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        windll: Any = ctypes.windll  # pyright: ignore[reportAttributeAccessIssue]
        kernel32 = windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        process = kernel32.GetCurrentProcess()
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        get_info = None
        for library_name, function_name in (
            ("psapi", "GetProcessMemoryInfo"),
            ("kernel32", "K32GetProcessMemoryInfo"),
        ):
            library = getattr(windll, library_name, None)
            get_info = (
                getattr(library, function_name, None) if library is not None else None
            )
            if get_info is not None:
                break
        if get_info is None:
            return None, None
        get_info.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        get_info.restype = ctypes.c_bool
        if not get_info(process, ctypes.byref(counters), counters.cb):
            return None, None
        total = MemoryStatusEx()
        total.dwLength = ctypes.sizeof(MemoryStatusEx)
        total_mb = None
        global_status = kernel32.GlobalMemoryStatusEx
        global_status.argtypes = [ctypes.POINTER(MemoryStatusEx)]
        global_status.restype = ctypes.c_bool
        if global_status(ctypes.byref(total)):
            total_mb = total.ullTotalPhys / 1024 / 1024
        return counters.WorkingSetSize / 1024 / 1024, total_mb
    except (AttributeError, OSError):
        return None, None


def sample_process_resources() -> dict[str, float | bool | None]:
    if sys.platform == "win32":
        process_mb, total_mb = _windows_memory()
    elif sys.platform.startswith("linux"):
        process_mb, total_mb = _linux_memory()
    else:
        process_mb, total_mb = _posix_memory()

    elapsed = max(time.monotonic() - _PROCESS_STARTED, 0.001)
    cpu_percent = (time.process_time() - _PROCESS_CPU_STARTED) / elapsed * 100
    return {
        "available": process_mb is not None,
        "cpu_percent": round(cpu_percent, 1),
        "memory_mb": round(process_mb, 1) if process_mb is not None else None,
        "memory_total_mb": round(total_mb, 1) if total_mb is not None else None,
    }


__all__ = ["sample_process_resources"]
