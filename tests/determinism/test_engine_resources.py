"""The motion engine's resources as a system (DYNAMIC_VIDEO_DESIGN §7.6), complementing the unit tests in
``tests/compose/motion/test_resources.py``:

* W from fake cgroup files: the design's deployment examples from the files a container sees (cgroup v2 with and
  without a cgroup namespace, cgroup v1) through ``motion_workers`` with the physical-core bound;
* slot locking across processes under contention: four processes take and release slots of a three-slot directory
  in a loop for a few seconds; their logs prove no slot was ever held twice and never more than three at once, and a
  holder killed mid-hold gives its slots back.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aadhi.config import ROOT_DIR, Settings

resources = pytest.importorskip("aadhi.compose.motion.resources")

pytestmark = pytest.mark.slow


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def _workers(root: Path, proc: Path, physical: int, **settings) -> int:
    budget = resources.CpuBudget(physical=physical, quota=resources.cgroup_cpu_quota(root, proc), affinity=None)
    return resources.motion_workers(Settings(_env_file=None, **settings), budget)


def test_workers_from_the_files_a_container_sees(tmp_path: Path) -> None:
    root, proc = tmp_path / "cgroup", tmp_path / "proc_self_cgroup"
    # the `motion` compose profile (cpus: 8) on a 16-core host, cgroup v2 with its own namespace
    _write(proc, "0::/\n")
    _write(root / "cpu.max", "800000 100000\n")
    assert _workers(root, proc, physical=16) == 6
    # the shipped render-worker profile (RENDER_WORKER_CPUS 4)
    _write(root / "cpu.max", "400000 100000\n")
    assert _workers(root, proc, physical=16) == 3
    # an "8-vCPU" server with 4 physical cores and no quota (§7.6: W = 3)
    _write(root / "cpu.max", "max 100000\n")
    assert _workers(root, proc, physical=4) == 3
    # docker --cpus=1.5: never below one worker; RENDER_MOTION_WORKERS overrides
    _write(root / "cpu.max", "150000 100000\n")
    assert _workers(root, proc, physical=16) == 1
    assert _workers(root, proc, physical=16, render_motion_workers=4) == 4
    # without a cgroup namespace the process sits in a nested cgroup whose parent holds the tighter limit
    _write(proc, "0::/docker/7f3a/render\n")
    _write(root / "docker" / "7f3a" / "render" / "cpu.max", "max 100000\n")
    _write(root / "docker" / "7f3a" / "cpu.max", "300000 100000\n")
    _write(root / "cpu.max", "max 100000\n")
    assert _workers(root, proc, physical=16) == 2


def test_workers_from_cgroup_v1_files(tmp_path: Path) -> None:
    root, proc = tmp_path / "cgroup", tmp_path / "proc_self_cgroup"
    _write(proc, "11:cpuset:/docker/abc\n4:cpu,cpuacct:/docker/abc\n")
    _write(root / "cpu,cpuacct" / "cpu.cfs_quota_us", "450000\n")
    _write(root / "cpu,cpuacct" / "cpu.cfs_period_us", "100000\n")
    assert _workers(root, proc, physical=8) == 3  # floor(4.5) - 1
    _write(root / "cpu,cpuacct" / "cpu.cfs_quota_us", "-1\n")
    assert _workers(root, proc, physical=8) == 6  # unlimited: min(6, 8 - 1)


_CONTENDER = """
import asyncio, json, os, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from aadhi.compose.motion.resources import CpuSlots

slots = CpuSlots(Path(sys.argv[2]), int(sys.argv[3]))
until = time.time() + float(sys.argv[4])
log = open(sys.argv[5], "w", encoding="utf-8")
while time.time() < until:
    lease = asyncio.run(slots.acquire(2, poll=0.01))
    got = time.time()
    time.sleep(0.02)
    held = list(lease.slots)
    released = time.time()
    lease.release()
    log.write(json.dumps({"pid": os.getpid(), "slots": held, "t0": got, "t1": released}) + "\\n")
    log.flush()
    time.sleep(0.005)
log.close()
"""

_HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from aadhi.compose.motion.resources import CpuSlots
lease = CpuSlots(Path(sys.argv[2]), int(sys.argv[3])).try_acquire(int(sys.argv[4]))
print(lease.count, flush=True)
time.sleep(120)
"""


def test_slot_locking_across_processes_under_contention(tmp_path: Path) -> None:
    total, seconds = 3, 4.0
    procs = [subprocess.Popen([sys.executable, "-c", _CONTENDER, str(ROOT_DIR), str(tmp_path / "slots"), str(total),
                               str(seconds), str(tmp_path / f"log{n}.jsonl")]) for n in range(4)]
    for p in procs:
        assert p.wait(timeout=120) == 0
    holds = [json.loads(line) for n in range(4) for line in (tmp_path / f"log{n}.jsonl").read_text().splitlines()]
    assert len({h["pid"] for h in holds}) == 4 and len(holds) > 40  # every process got slots, many times
    events = sorted([(h["t0"], 1, h) for h in holds] + [(h["t1"], -1, h) for h in holds], key=lambda e: (e[0], e[1]))
    held: dict[int, int] = {}
    peak = 0
    for _, kind, hold in events:
        for slot in hold["slots"]:
            if kind == 1:
                assert slot not in held, f"slot {slot} held by {held.get(slot)} and {hold['pid']} at once"
                held[slot] = hold["pid"]
            else:
                held.pop(slot, None)
        peak = max(peak, len(held))
    assert peak <= total and peak >= 2


def test_a_killed_holder_gives_its_slots_back(tmp_path: Path) -> None:
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(ROOT_DIR), str(tmp_path / "slots"), "3", "3"],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and int(proc.stdout.readline().strip()) == 3
        assert resources.CpuSlots(tmp_path / "slots", 3).free() == 0
    finally:
        proc.kill()
        proc.wait(timeout=30)
    deadline = time.monotonic() + 10
    while resources.CpuSlots(tmp_path / "slots", 3).free() != 3 and time.monotonic() < deadline:
        time.sleep(0.1)
    assert resources.CpuSlots(tmp_path / "slots", 3).free() == 3
