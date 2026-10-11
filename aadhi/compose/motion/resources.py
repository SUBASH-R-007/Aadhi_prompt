"""Motion render resources: how many capture workers a render runs, and the host-wide CPU slots.

**Workers** (DYNAMIC_VIDEO_DESIGN §7.6): ``W = min(6, floor(CPU quota) - 1, physical cores - 1)``, at least 1;
``RENDER_MOTION_WORKERS`` overrides it. Inside a container Python's ``os.cpu_count()`` and psutil report the host's
cores and ignore the container's CPU quota, so the quota is read from cgroup v2 ``cpu.max`` (or v1
``cpu.cfs_quota_us`` / ``cpu.cfs_period_us``), from the process's own cgroup up to the root; the tightest limit
wins. A cpuset (``sched_getaffinity``, Linux) bounds the count as well. Going from 4 to 6 workers gained only 12%
on the development laptop [MR D15], hence the cap of 6.

**CPU slots.** Every motion worker on the host holds one of ``RENDER_CPU_SLOTS`` slots (default: physical cores -
1), so concurrent renders in several containers never oversubscribe the machine. A slot is the lock file
``slot-NNN.lock`` in ``RENDER_SLOT_DIR`` (default ``<render work dir>/cpu-slots``) held with an exclusive lock:
``flock`` on POSIX, ``msvcrt.locking`` on Windows. Both locks belong to the open file, so two renders in one
process compete like two processes, and the operating system drops them when the process dies: a crashed render
never leaks a slot. The directory must live on a local filesystem shared by the render containers of the host (a
bind mount or a named volume of that host); network filesystems do not honour these locks reliably. A render takes
as many free slots as it has workers (at least one, waiting for it) and runs one worker per slot it holds.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from ...config import Settings
from ..workspace import work_root

MAX_AUTO_WORKERS = 6
SLOT_POLL_SECONDS = 1.0
CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")
_V1_CPU_MOUNTS = ("cpu,cpuacct", "cpu", "cpuacct,cpu")


# ---------------------------------------------------------------------------
# CPU quota (cgroup v2 / v1)
# ---------------------------------------------------------------------------


def parse_cpu_max(text: str) -> float | None:
    """CPUs allowed by a cgroup v2 ``cpu.max`` (``"400000 100000"`` -> 4.0); None for ``max`` or garbage."""
    parts = (text or "").split()
    if not parts or parts[0] == "max":
        return None
    try:
        quota = float(parts[0])
        period = float(parts[1]) if len(parts) > 1 else 100000.0
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return quota / period


def parse_cfs(quota_text: str, period_text: str) -> float | None:
    """CPUs allowed by cgroup v1 ``cpu.cfs_quota_us`` / ``cpu.cfs_period_us`` (quota -1 = unlimited -> None)."""
    try:
        quota = float((quota_text or "").strip())
        period = float((period_text or "").strip())
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return quota / period


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None


def _upward(base: Path, relative: str) -> list[Path]:
    """``base/relative`` and each of its parents up to ``base`` (the cgroup hierarchy, innermost first)."""
    rel = [p for p in relative.strip().split("/") if p and p not in (".", "..")]
    out = [base.joinpath(*rel[:n]) for n in range(len(rel), -1, -1)]
    return list(dict.fromkeys(out))


def cgroup_cpu_quota(root: Path = CGROUP_ROOT, proc_cgroup: Path = PROC_SELF_CGROUP) -> float | None:
    """The tightest CPU quota (in CPUs) of this process's cgroups, None when unlimited or unknown.

    cgroup v2: ``cpu.max`` of the process's cgroup (``0::<path>`` in ``/proc/self/cgroup``) and every parent.
    cgroup v1: ``cpu.cfs_quota_us`` / ``cpu.cfs_period_us`` under the ``cpu`` controller's mount, at the process's
    path and at the mount root (inside a container the mount root is the container's own cgroup).
    """
    text = _read(proc_cgroup) or ""
    limits: list[float] = []
    v2_path: str | None = None
    v1_path: str | None = None
    for line in text.splitlines():
        parts = line.strip().split(":", 2)
        if len(parts) != 3:
            continue
        hier, controllers, path = parts
        if hier == "0" and controllers == "":
            v2_path = path
        elif "cpu" in controllers.split(","):
            v1_path = path
    if v2_path is not None or (root / "cpu.max").is_file():
        for d in _upward(root, v2_path or "/"):
            raw = _read(d / "cpu.max")
            value = parse_cpu_max(raw) if raw is not None else None
            if value is not None:
                limits.append(value)
    for mount in _V1_CPU_MOUNTS:
        base = root / mount
        if not base.is_dir():
            continue
        for d in [*_upward(base, v1_path or "/"), base]:
            quota, period = _read(d / "cpu.cfs_quota_us"), _read(d / "cpu.cfs_period_us")
            if quota is None or period is None:
                continue
            value = parse_cfs(quota, period)
            if value is not None:
                limits.append(value)
        break
    return min(limits) if limits else None


def physical_cores() -> int:
    """Physical CPU cores of the host (logical CPUs when unknown), at least 1."""
    with contextlib.suppress(Exception):
        n = psutil.cpu_count(logical=False)
        if n:
            return int(n)
    return max(1, int(os.cpu_count() or 1))


def affinity_cpus() -> int | None:
    """CPUs this process may run on (a container's cpuset; Linux only), None when unknown."""
    getter = getattr(os, "sched_getaffinity", None)
    if getter is None:
        return None
    try:
        return len(getter(0)) or None
    except OSError:
        return None


@dataclass(frozen=True)
class CpuBudget:
    """What bounds the worker count (recorded with every render's result)."""

    physical: int
    quota: float | None = None
    affinity: int | None = None

    @classmethod
    def detect(cls) -> CpuBudget:
        """Measure this process's host and container (blocking, cheap)."""
        return cls(physical=physical_cores(), quota=cgroup_cpu_quota(), affinity=affinity_cpus())

    def auto_workers(self) -> int:
        """``min(6, floor(quota) - 1, physical - 1, cpuset - 1)``, at least 1."""
        bounds = [MAX_AUTO_WORKERS, self.physical - 1]
        if self.quota is not None:
            bounds.append(math.floor(self.quota + 1e-9) - 1)
        if self.affinity is not None:
            bounds.append(self.affinity - 1)
        return max(1, min(bounds))


def motion_workers(settings: Settings, budget: CpuBudget | None = None) -> int:
    """Capture workers for one motion render: ``RENDER_MOTION_WORKERS`` when set, else :meth:`CpuBudget.auto_workers`."""
    override = int(getattr(settings, "render_motion_workers", 0) or 0)
    if override > 0:
        return override
    return (budget or CpuBudget.detect()).auto_workers()


def cpu_slot_count(settings: Settings, budget: CpuBudget | None = None) -> int:
    """Host-wide CPU slots: ``RENDER_CPU_SLOTS`` when set, else physical cores - 1 (at least 1)."""
    configured = int(getattr(settings, "render_cpu_slots", 0) or 0)
    if configured > 0:
        return configured
    return max(1, (budget.physical if budget is not None else physical_cores()) - 1)


def slot_dir(settings: Settings) -> Path:
    """Directory of the slot lock files: ``RENDER_SLOT_DIR``, else ``<render work dir>/cpu-slots``."""
    raw = (getattr(settings, "render_slot_dir", "") or "").strip()
    return Path(raw).expanduser().resolve() if raw else work_root(settings) / "cpu-slots"


# ---------------------------------------------------------------------------
# Host-wide CPU slots (lock files)
# ---------------------------------------------------------------------------


def _lock_fd(fd: int) -> bool:
    """Exclusive, non-blocking lock of an open file; False when another holder has it."""
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # one byte at offset 0 (locking past the end is allowed)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_fd(fd: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _open_slot(path: Path) -> int | None:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(path, flags, 0o666)
    except PermissionError:  # created by another uid without write access: a read handle can hold the lock too
        try:
            return os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0))
        except OSError:
            return None
    except OSError:
        return None


@dataclass
class SlotLease:
    """CPU slots held by one render (release them with :meth:`release`, or use it as a context manager)."""

    directory: Path
    slots: list[int] = field(default_factory=list)
    _fds: list[int] = field(default_factory=list, repr=False)

    @property
    def count(self) -> int:
        """Number of slots held."""
        return len(self.slots)

    def release(self) -> None:
        """Release every slot (idempotent)."""
        fds, self._fds = self._fds, []
        self.slots = []
        for fd in fds:
            _unlock_fd(fd)

    def __enter__(self) -> SlotLease:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class CpuSlots:
    """The host's slot directory (see the module docstring). Blocking methods are cheap (no waiting)."""

    def __init__(self, directory: Path, total: int) -> None:
        self.directory = Path(directory)
        self.total = max(1, int(total))

    @classmethod
    def from_settings(cls, settings: Settings, budget: CpuBudget | None = None) -> CpuSlots:
        """``RENDER_SLOT_DIR`` with ``RENDER_CPU_SLOTS`` slots."""
        return cls(slot_dir(settings), cpu_slot_count(settings, budget))

    def path(self, n: int) -> Path:
        """Lock file of slot ``n``."""
        return self.directory / f"slot-{n:03d}.lock"

    def try_acquire(self, want: int) -> SlotLease:
        """Take up to ``want`` free slots now (possibly none)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        lease = SlotLease(self.directory)
        for n in range(self.total):
            if lease.count >= want:
                break
            fd = _open_slot(self.path(n))
            if fd is None:
                continue
            if _lock_fd(fd):
                lease.slots.append(n)
                lease._fds.append(fd)
            else:
                with contextlib.suppress(OSError):
                    os.close(fd)
        return lease

    def free(self) -> int:
        """Slots nobody holds right now (probes each lock; for diagnostics and tests)."""
        lease = self.try_acquire(self.total)
        try:
            return lease.count
        finally:
            lease.release()

    async def acquire(self, want: int, *, check_cancelled: Callable[[], None] | None = None,
                      on_wait: Callable[[], None] | None = None, poll: float = SLOT_POLL_SECONDS) -> SlotLease:
        """Take up to ``want`` slots, waiting (cancel-aware) until at least one is free. ``on_wait`` is called once
        when the render has to wait."""
        want = max(1, int(want))
        waited = False
        while True:
            if check_cancelled is not None:
                check_cancelled()
            lease = await asyncio.to_thread(self.try_acquire, want)
            if lease.count:
                return lease
            if not waited and on_wait is not None:
                on_wait()
            waited = True
            await asyncio.sleep(poll)
