"""Windows Job Object for the local Manim runner (imported only when ``os.name == "nt"``).

A render process is assigned to a job created with ``KILL_ON_JOB_CLOSE``: when the worker closes
the job handle (normally, or because the worker process died or was killed) Windows terminates
every process still in the job, including grandchildren such as LaTeX. The job also enforces a
committed-memory backstop and a process count limit in the kernel, and reports CPU time and peak
committed memory for the render metrics. The backstop is not MANIM_MEMORY_LIMIT_MB itself: committed
memory is several times RSS (per-thread BLAS / x264 buffers), so the caller passes a looser value and
keeps its RSS poll as the enforced ceiling (``sandbox._job_commit_backstop_mb``).

The process is assigned right after ``subprocess.Popen`` returns; it has not started any child
process yet at that point (the Python interpreter and the Manim import take well over a second),
so its whole tree ends up in the job. Nested jobs (a worker already running inside a job, e.g. in
some terminals) need Windows 8 or later; when assignment fails the caller keeps its psutil
watchdog and simply has no job.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

JobObjectBasicAccountingInformation = 1
JobObjectExtendedLimitInformation = 9

JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _kernel32() -> ctypes.WinDLL:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # private instance: argtypes stay local
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD)]
    k32.QueryInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


@dataclass
class JobStats:
    cpu_seconds: float = 0.0
    peak_memory_mb: float = 0.0  # peak committed memory of the whole job
    processes: int = 0  # processes ever started in the job


class JobObject:
    """A kill-on-close job with optional committed-memory (a backstop, see the module docstring) and
    process-count limits."""

    def __init__(self, *, memory_limit_mb: float | None = None, max_processes: int | None = None) -> None:
        self._k32 = _kernel32()
        handle = self._k32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        self.handle = handle
        self.memory_limit_mb = float(memory_limit_mb) if memory_limit_mb else None
        info = _ExtendedLimits()
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        if self.memory_limit_mb:
            flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = int(self.memory_limit_mb * 1024 * 1024)
        if max_processes:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = int(max_processes)
        info.BasicLimitInformation.LimitFlags = flags
        if not self._k32.SetInformationJobObject(handle, JobObjectExtendedLimitInformation, ctypes.byref(info),
                                                 ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "SetInformationJobObject failed")

    def assign(self, process_handle: int) -> None:
        """Put the process (``Popen._handle``) into the job."""
        if not self._k32.AssignProcessToJobObject(self.handle, int(process_handle)):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def stats(self) -> JobStats:
        """CPU seconds, peak committed memory and process count of everything that ran in the job."""
        if not self.handle:
            return JobStats()
        acct = _BasicAccounting()
        limits = _ExtendedLimits()
        out = JobStats()
        if self._k32.QueryInformationJobObject(self.handle, JobObjectBasicAccountingInformation, ctypes.byref(acct),
                                               ctypes.sizeof(acct), None):
            out.cpu_seconds = (acct.TotalUserTime + acct.TotalKernelTime) / 1e7  # 100 ns units
            out.processes = int(acct.TotalProcesses)
        if self._k32.QueryInformationJobObject(self.handle, JobObjectExtendedLimitInformation, ctypes.byref(limits),
                                               ctypes.sizeof(limits), None):
            out.peak_memory_mb = limits.PeakJobMemoryUsed / (1024 * 1024)
        return out

    def hit_memory_limit(self) -> bool:
        """True when the job's peak committed memory reached (95 % of) its limit."""
        return bool(self.memory_limit_mb) and self.stats().peak_memory_mb >= 0.95 * float(self.memory_limit_mb)

    def terminate(self, exit_code: int = 1) -> None:
        """Kill every process in the job (best effort)."""
        if self.handle:
            self._k32.TerminateJobObject(self.handle, exit_code)

    def close(self) -> None:
        """Close the handle; because of KILL_ON_JOB_CLOSE this ends any process still in the job."""
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None
