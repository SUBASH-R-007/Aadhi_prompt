"""Motion render resources: the cgroup-aware worker count (fake cgroup files) and the host-wide CPU slots (lock files,
across processes)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aadhi.compose.motion.resources import (
    MAX_AUTO_WORKERS,
    CpuBudget,
    CpuSlots,
    cgroup_cpu_quota,
    cpu_slot_count,
    motion_workers,
    parse_cfs,
    parse_cpu_max,
    slot_dir,
)
from aadhi.compose.workspace import work_root
from aadhi.config import ROOT_DIR, Settings


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_parse_cpu_max_and_cfs() -> None:
    assert parse_cpu_max("400000 100000\n") == 4.0
    assert parse_cpu_max("150000 100000") == 1.5
    assert parse_cpu_max("200000") == 2.0  # period defaults to 100 ms
    assert parse_cpu_max("max 100000") is None
    assert parse_cpu_max("") is None and parse_cpu_max("garbage x") is None and parse_cpu_max("0 100000") is None
    assert parse_cfs("800000", "100000") == 8.0
    assert parse_cfs("-1", "100000") is None
    assert parse_cfs("x", "100000") is None and parse_cfs("100000", "0") is None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def test_cgroup_v2_quota_tightest_limit_from_own_cgroup_upwards(tmp_path: Path) -> None:
    root = tmp_path / "cgroup"
    proc = tmp_path / "proc_self_cgroup"
    _write(proc, "0::/system.slice/render.service\n")
    _write(root / "system.slice" / "render.service" / "cpu.max", "max 100000\n")
    _write(root / "system.slice" / "cpu.max", "300000 100000\n")
    _write(root / "cpu.max", "800000 100000\n")
    assert cgroup_cpu_quota(root, proc) == 3.0
    # a container with its own cgroup namespace sees "0::/" and its limit at the root
    _write(proc, "0::/\n")
    assert cgroup_cpu_quota(root, proc) == 8.0


def test_cgroup_v2_unlimited_and_missing(tmp_path: Path) -> None:
    root = tmp_path / "cgroup"
    proc = tmp_path / "proc_self_cgroup"
    _write(proc, "0::/user.slice\n")
    _write(root / "user.slice" / "cpu.max", "max 100000\n")
    assert cgroup_cpu_quota(root, proc) is None
    assert cgroup_cpu_quota(tmp_path / "nothing", tmp_path / "no_proc") is None  # not Linux / no cgroup fs


def test_cgroup_v1_quota(tmp_path: Path) -> None:
    root = tmp_path / "cgroup"
    proc = tmp_path / "proc_self_cgroup"
    _write(proc, "12:memory:/docker/abc\n4:cpu,cpuacct:/docker/abc\n1:name=systemd:/docker/abc\n")
    mount = root / "cpu,cpuacct"
    _write(mount / "cpu.cfs_quota_us", "600000\n")  # the container's own cgroup is the mount root (no cgroupns)
    _write(mount / "cpu.cfs_period_us", "100000\n")
    assert cgroup_cpu_quota(root, proc) == 6.0
    _write(mount / "docker" / "abc" / "cpu.cfs_quota_us", "250000\n")  # host view: the nested path is tighter
    _write(mount / "docker" / "abc" / "cpu.cfs_period_us", "100000\n")
    assert cgroup_cpu_quota(root, proc) == 2.5
    _write(mount / "cpu.cfs_quota_us", "-1\n")
    _write(mount / "docker" / "abc" / "cpu.cfs_quota_us", "-1\n")
    assert cgroup_cpu_quota(root, proc) is None


@pytest.mark.parametrize(("physical", "quota", "affinity", "expected"), [
    (14, None, None, MAX_AUTO_WORKERS),  # the development laptop: capped at 6
    (4, None, None, 3),  # an "8-vCPU" server with 4 physical cores (§7.6)
    (16, 8.0, None, 6),  # the motion compose profile (8 CPUs): min(6, 7, 15)
    (16, 4.0, None, 3),  # the default render-worker profile (4 CPUs)
    (16, 2.5, None, 1),  # floor(2.5) - 1
    (16, 1.0, None, 1),  # never below one worker
    (1, None, None, 1),
    (16, None, 3, 2),  # a cpuset of 3 CPUs
])
def test_auto_workers(physical: int, quota: float | None, affinity: int | None, expected: int) -> None:
    assert CpuBudget(physical=physical, quota=quota, affinity=affinity).auto_workers() == expected


def test_worker_override_and_slot_count() -> None:
    budget = CpuBudget(physical=8, quota=4.0)
    assert motion_workers(_settings(), budget) == 3
    assert motion_workers(_settings(render_motion_workers=5), budget) == 5  # the override wins
    assert cpu_slot_count(_settings(), budget) == 7  # physical cores - 1
    assert cpu_slot_count(_settings(render_cpu_slots=12), budget) == 12
    assert cpu_slot_count(_settings(), CpuBudget(physical=1)) == 1
    assert CpuBudget.detect().physical >= 1


def test_slot_dir_default_and_override(tmp_path: Path) -> None:
    settings = _settings(render_work_dir=str(tmp_path / "work"))
    assert slot_dir(settings) == work_root(settings) / "cpu-slots"
    assert slot_dir(_settings(render_slot_dir=str(tmp_path / "slots"))) == (tmp_path / "slots").resolve()


def test_slots_in_one_process(tmp_path: Path) -> None:
    slots = CpuSlots(tmp_path / "slots", 3)
    first = slots.try_acquire(2)
    assert first.count == 2 and first.slots == [0, 1]
    second = slots.try_acquire(5)  # only one is left; two leases in one process compete like two processes
    assert second.slots == [2]
    assert slots.try_acquire(1).count == 0 and slots.free() == 0
    first.release()
    first.release()  # idempotent
    assert slots.free() == 2
    with slots.try_acquire(3) as lease:
        assert lease.slots == [0, 1]
    second.release()
    assert slots.free() == 3
    assert sorted(p.name for p in (tmp_path / "slots").iterdir()) == ["slot-000.lock", "slot-001.lock",
                                                                      "slot-002.lock"]


_HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from aadhi.compose.motion.resources import CpuSlots
lease = CpuSlots(Path(sys.argv[2]), int(sys.argv[3])).try_acquire(int(sys.argv[4]))
print(lease.count, flush=True)
time.sleep(float(sys.argv[5]))
"""


def _hold(tmp_path: Path, total: int, want: int, seconds: float) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(ROOT_DIR), str(tmp_path / "slots"), str(total),
                             str(want), str(seconds)], stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    assert int(proc.stdout.readline().strip()) == want  # the child holds its slots now
    return proc


def test_slots_are_shared_across_processes(tmp_path: Path) -> None:
    proc = _hold(tmp_path, 3, 2, 60)
    try:
        slots = CpuSlots(tmp_path / "slots", 3)
        lease = slots.try_acquire(3)
        assert lease.count == 1  # the other process holds two of the three
        lease.release()
    finally:
        proc.kill()
        proc.wait(timeout=30)
    deadline = time.monotonic() + 10
    while CpuSlots(tmp_path / "slots", 3).free() != 3 and time.monotonic() < deadline:  # the OS drops a dead holder's locks
        time.sleep(0.1)
    assert CpuSlots(tmp_path / "slots", 3).free() == 3


def test_acquire_waits_for_a_free_slot_and_honours_cancel(tmp_path: Path) -> None:
    proc = _hold(tmp_path, 1, 1, 1.5)
    try:
        waits: list[int] = []
        started = time.monotonic()
        lease = asyncio.run(CpuSlots(tmp_path / "slots", 1).acquire(4, on_wait=lambda: waits.append(1), poll=0.1))
        assert lease.count == 1 and waits == [1] and time.monotonic() - started >= 0.5
        lease.release()
    finally:
        proc.wait(timeout=30)

    class Stop(Exception):
        pass

    holder = CpuSlots(tmp_path / "slots", 1).try_acquire(1)
    calls: list[int] = []

    def check() -> None:
        calls.append(1)
        if len(calls) > 2:
            raise Stop

    with pytest.raises(Stop):
        asyncio.run(CpuSlots(tmp_path / "slots", 1).acquire(1, check_cancelled=check, poll=0.05))
    holder.release()
