"""Async subprocess execution with a timeout and process-tree kill (ffmpeg / ffprobe)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import psutil

from .base import ProviderError

_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


@dataclass
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def kill_tree(pid: int) -> None:
    """Kill ``pid`` and all of its children (best effort)."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.kill()
    psutil.wait_procs(procs, timeout=5)


def _run_blocking(args: Sequence[str], data: bytes | None, timeout: float) -> ProcessResult:
    proc = subprocess.Popen(  # fixed argv, never a shell
        list(args),
        stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_CREATIONFLAGS,
    )
    try:
        out, err = proc.communicate(input=data, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        proc.communicate()
        raise
    return ProcessResult(proc.returncode or 0, out, err)


async def run_process(args: Sequence[str], *, data: bytes | None = None, timeout: float = 60.0) -> ProcessResult:
    """Run ``args`` (no shell) and return its output.

    Uses asyncio subprocesses; falls back to a worker thread when the running loop cannot spawn
    subprocesses (e.g. a selector loop on Windows). Raises ``ProviderError`` when the executable
    is missing or the timeout expires (the whole process tree is killed).
    """
    exe = args[0]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_CREATIONFLAGS,
        )
    except NotImplementedError:
        try:
            return await asyncio.to_thread(_run_blocking, args, data, timeout)
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"{os.path.basename(exe)} timed out after {timeout:.0f}s", provider="local") from exc
        except FileNotFoundError as exc:
            raise ProviderError(f"{os.path.basename(exe)} is not installed or not on PATH", provider="local") from exc
    except FileNotFoundError as exc:
        raise ProviderError(f"{os.path.basename(exe)} is not installed or not on PATH", provider="local") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(input=data), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        await asyncio.to_thread(kill_tree, proc.pid)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise ProviderError(f"{os.path.basename(exe)} timed out after {timeout:.0f}s", provider="local") from exc
    return ProcessResult(proc.returncode or 0, out, err)
