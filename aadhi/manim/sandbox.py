"""Sandboxed Manim runners.

* :class:`SubprocessRunner` (development): ``<python> -m manim render ...`` in a private temp dir with
  an environment allow-list (no secrets), a wall-clock timeout, a memory ceiling and process-tree
  kill (psutil), a work-dir disk watchdog, and output capped at :data:`OUTPUT_CAP` bytes. On Windows
  the render is put in a kill-on-close Job Object (:mod:`aadhi.manim.winjob`) so it can never outlive
  the worker (with a loose committed-memory backstop: the RSS poll is the enforced ceiling); on POSIX
  an ``RLIMIT_CPU`` backstop of :data:`CPU_BACKSTOP_FACTOR` x the timeout stops an orphaned runaway
  loop (the wall-clock watchdog always fires first). BLAS runs single-threaded (Manim's matrices are
  tiny; per-thread buffers only cost memory). LaTeX runs with
  ``AADHI_TEX_FLAGS`` (MiKTeX: ``-disable-installer -disable-write18`` so a missing package fails
  fast instead of opening the "install package?" dialog; TeX Live: ``-no-shell-escape``) and
  dvisvgm with ``AADHI_DVISVGM_FLAGS`` (MiKTeX: ``--miktex-disable-installer``).
* :class:`DockerRunner` (production): the same command inside ``docker run --network none
  --read-only --tmpfs /tmp --memory ... --cpus ... --pids-limit 256 --ulimit fsize=... --user
  1000:1000 --cap-drop ALL --security-opt no-new-privileges`` with only the work dir mounted. The
  container carries an ``aadhi.manim`` label and a deadline, and the command is wrapped in
  ``timeout -s KILL`` so a sibling container dies even if the docker handle is lost.

Both are used through :func:`get_runner` and expose ``await runner.run(script, scene, ...)``.
Blocking process handling happens in a worker thread (``asyncio.to_thread``) so it works on any
event loop; cancelling the awaiting task kills the process tree and waits until it is gone (so the
Manim concurrency slot and the work directory are released only after the process has exited).
:func:`find_output` accepts only a real, contained, non-symlink video; oversized output is rejected.
:func:`sweep_orphans` removes leftover work dirs (owner markers) and expired containers; call it from
worker maintenance. Work dirs are created inside the app's scratch root (``SCRATCH_DIR``, :mod:`aadhi.scratch`)
and the sweep looks only there, never at other folders of the system temp dir. :func:`scrub_paths` replaces host paths (work/temp dirs, the Python install, the
home directory) in logs before they are shown to users or sent to a model.
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

import psutil

from ..config import Settings
from ..scratch import make_temp_dir, scratch_root
from .base import ManimError

OUTPUT_CAP = 200_000  # bytes of combined stdout/stderr kept (the tail)
DEFAULT_MEMORY_LIMIT_MB = 2048
DEFAULT_MAX_PROCESSES = 64  # Windows job process-count limit: enough for LaTeX's helper processes
# RLIMIT_CPU = timeout * this + 30 CPU-s: manim encodes on extra threads (measured 1.5-1.9x CPU/wall time), so
# the CPU limit (all threads) only stops an orphaned runaway and never pre-empts the wall-clock watchdog
CPU_BACKSTOP_FACTOR = 4
SCRIPT_NAME = "scene.py"
OUTPUT_NAME = "out"
# Scene class names are passed to the manim CLI (argv list, never a shell): Python identifiers in ASCII.
SCENE_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_ERROR_BLOCK = re.compile(r"\[AADHI_ERROR\]\r?\n(.*?)\r?\n\[/AADHI_ERROR\]", re.S)
_STATS_FRAMES = re.compile(r"\[AADHI_STATS\] frames=(\d+)")
_LIMIT_TAG = re.compile(r"\[AADHI_LIMIT\] (\w+)")
_BLOCKED_TAG = re.compile(r"\[AADHI_BLOCKED\] ")
# Hardening-block tags -> SandboxResult.category (see runtime/hardening.py and base.FAILURE_MESSAGES).
_LIMIT_CATEGORY = {"frames": "frame_limit", "profile": "profile_limit"}

# Environment variables passed to the subprocess (everything else, incl. API keys, is dropped).
ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
    "USERPROFILE", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "FONTCONFIG_FILE",
    "FONTCONFIG_PATH", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_DIRS", "DISPLAY",
)


@dataclass
class SandboxResult:
    """Outcome of one sandboxed render."""

    ok: bool
    returncode: int | None
    video_path: Path | None
    log: str
    timed_out: bool = False
    killed_reason: str = ""  # timeout | memory | output_limit | cancelled
    elapsed: float = 0.0
    command: list[str] = field(default_factory=list)
    peak_memory_mb: float = 0.0  # peak RSS of the render tree
    frames: int = 0  # frames written (from the runtime's [AADHI_STATS] line)

    @property
    def error_report(self) -> str:
        """Compact error: the runtime's ``[AADHI_ERROR]`` block, else the last log lines."""
        if self.killed_reason == "timeout":
            return "render timed out (infinite loop or too much work); keep scenes simple and bounded"
        if self.killed_reason == "memory":
            return "render exceeded the memory limit; use fewer/smaller mobjects"
        if self.killed_reason == "output_limit":
            return "render wrote more than the allowed amount of data; keep scenes short and simple"
        limit = _LIMIT_TAG.search(self.log)
        blocks = _ERROR_BLOCK.findall(self.log)
        tex = latex_errors(self.log)
        if blocks:
            return (blocks[-1].strip() + ("\n" + tex if tex else "")).strip()
        if limit:
            return f"the render was stopped by the sandbox ({limit.group(1)} limit)"
        if _BLOCKED_TAG.search(self.log):
            return "the animation tried an operation the sandbox does not allow"
        if not self.ok and self.returncode == 0 and self.video_path is None:
            return "manim finished without producing a video (did construct() play any animation?)"
        return log_tail(self.log, 40)

    @property
    def category(self) -> str:
        """Why the render failed (:data:`aadhi.manim.base.FAILURE_CATEGORIES`); ``""`` when it succeeded."""
        if self.ok:
            return ""
        if self.killed_reason in ("timeout", "cancelled", "output_limit"):
            return self.killed_reason
        if self.killed_reason == "memory":
            return "memory_limit"
        limit = _LIMIT_TAG.search(self.log)
        if limit:
            return _LIMIT_CATEGORY.get(limit.group(1), "render_failed")
        if _BLOCKED_TAG.search(self.log):
            return "blocked"
        if latex_errors(self.log):
            return "latex"
        if self.returncode == 0 and self.video_path is None:
            return "invalid_output"
        return "render_failed"


def log_tail(text: str, lines: int = 40) -> str:
    """Last ``lines`` non-empty lines of ``text``."""
    rows = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(rows[-lines:])


def latex_errors(text: str) -> str:
    """LaTeX error lines printed by manim (``! Undefined control sequence`` ...)."""
    rows = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("!") or "LaTeX Error" in ln]
    return "\n".join(dict.fromkeys(rows[-6:]))


@runtime_checkable
class ManimRunner(Protocol):
    name: str

    def has_latex(self) -> bool:
        """Whether MathTex/Tex can be compiled in this sandbox."""
        ...

    async def run(self, script: str, scene: str, *, workdir: Path, quality: str, timeout: float) -> SandboxResult:
        """Render ``scene`` from ``script`` inside ``workdir``; never raises for render failures."""
        ...


# --- TeX discovery --------------------------------------------------------------------------------


def _tex_candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    for env, rel in (
        ("LOCALAPPDATA", r"Programs\MiKTeX\miktex\bin\x64"),
        ("PROGRAMFILES", r"MiKTeX\miktex\bin\x64"),
        ("PROGRAMFILES", r"MiKTeX 2.9\miktex\bin\x64"),
    ):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / rel)
    dirs.extend(Path(p) for p in ("/Library/TeX/texbin", "/usr/local/texlive/bin", "/usr/bin"))
    return dirs


@lru_cache(maxsize=1)
def tex_bin_dir() -> Path | None:
    """Directory containing ``latex`` (PATH first, then standard MiKTeX/TeX Live locations)."""
    found = shutil.which("latex")
    if found:
        return Path(found).resolve().parent
    for d in _tex_candidate_dirs():
        for name in ("latex.exe", "latex"):
            if (d / name).is_file():
                return d
    return None


@lru_cache(maxsize=1)
def tex_available() -> bool:
    """True when both ``latex`` and ``dvisvgm`` are installed (MathTex works)."""
    d = tex_bin_dir()
    if d is None:
        return False
    return any((d / n).is_file() for n in ("dvisvgm", "dvisvgm.exe")) or shutil.which("dvisvgm") is not None


def is_miktex(bin_dir: Path | None) -> bool:
    """MiKTeX distributions ship ``initexmf``; TeX Live does not."""
    return bin_dir is not None and any((bin_dir / n).is_file() for n in ("initexmf.exe", "initexmf"))


def tex_flags(bin_dir: Path | None) -> str:
    """Extra LaTeX flags: never install packages on the fly, never run shell escapes."""
    if bin_dir is None:
        return ""
    return "-disable-installer -disable-write18" if is_miktex(bin_dir) else "-no-shell-escape"


def dvisvgm_flags(bin_dir: Path | None) -> str:
    """Extra dvisvgm flags: MiKTeX's dvisvgm must not install missing font packages on the fly either."""
    return "--miktex-disable-installer" if is_miktex(bin_dir) else ""


# --- log sanitising -------------------------------------------------------------------------------

_HOME_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/]+(?:Users|Documents and Settings)[\\/]+[^\\/\s:\"'<>|]+"),
    re.compile(r"(?<![\w.])/(?:home|Users)/[^/\s:\"'<>|]+"),
)


def _path_variants(path: str) -> list[str]:
    path = path.rstrip("\\/")
    if len(path) < 4:
        return []
    return list(dict.fromkeys([path, path.replace("\\", "/"), path.replace("/", "\\")]))


def scrub_paths(text: str, roots: Iterable[tuple[str | os.PathLike[str] | None, str]] = ()) -> str:
    """Replace host paths in ``text`` with placeholders (``<work>``, ``<tmp>``, ``<python>``, ``<home>``).

    ``roots`` are extra ``(path, placeholder)`` pairs (e.g. the render work dir); longer paths are
    replaced first. Logs then reveal neither the OS user name nor the host layout when they reach a
    model or a user.
    """
    if not text:
        return text
    defaults: list[tuple[str | os.PathLike[str] | None, str]] = [
        (tempfile.gettempdir(), "<tmp>"),
        (sys.prefix, "<python>"),
        (sys.base_prefix, "<python>"),
        (sys.exec_prefix, "<python>"),
        (Path.home(), "<home>"),
    ]
    pairs: list[tuple[str, str]] = []
    for root, label in [*roots, *defaults]:
        if root:
            pairs.extend((variant, label) for variant in _path_variants(os.fspath(root)))
    for variant, label in sorted(pairs, key=lambda p: len(p[0]), reverse=True):
        text = re.sub(re.escape(variant), lambda _m, label=label: label, text, flags=re.IGNORECASE)
    for pattern in _HOME_PATTERNS:
        text = pattern.sub("<home>", text)
    return text


# --- process helpers ------------------------------------------------------------------------------


def kill_tree(pid: int, timeout: float = 5.0) -> None:
    """Kill ``pid`` and all its descendants (best effort, Windows + POSIX)."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        procs = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        procs = [parent]
    for proc in procs:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(procs, timeout=timeout)


def _tree_rss_mb(pid: int) -> float:
    try:
        parent = psutil.Process(pid)
        procs = [parent, *parent.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0.0
    total = 0
    for proc in procs:
        try:
            total += proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return total / (1024 * 1024)


def dir_size_mb(roots: Iterable[Path], limit_mb: float | None = None) -> float:
    """Total size in MB of every file under ``roots`` (follows no symlinks; exits early past ``limit_mb``).

    Sizes come from :func:`os.lstat` on each path, not ``DirEntry.stat()``: on Windows the directory
    enumeration caches a stale size for a file that is still open for writing (exactly the runaway
    render we want to catch), so the cached value can read as 0 while the file keeps growing.
    """
    limit_bytes = float("inf") if limit_mb is None else limit_mb * 1024 * 1024
    total = 0
    stack = [os.fspath(r) for r in roots]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += os.lstat(entry.path).st_size
                    except OSError:
                        continue
                    if total > limit_bytes:
                        return total / (1024 * 1024)
        except OSError:
            continue
    return total / (1024 * 1024)


class _CappedReader(threading.Thread):
    """Drains a pipe, keeping only the last ``cap`` bytes."""

    def __init__(self, stream, cap: int) -> None:  # noqa: ANN001 - binary pipe
        super().__init__(daemon=True)
        self.stream = stream
        self.cap = cap
        self.buffer = bytearray()
        self.dropped = 0

    def run(self) -> None:
        try:
            while True:
                chunk = self.stream.read1(65536) if hasattr(self.stream, "read1") else self.stream.read(65536)
                if not chunk:
                    break
                self.buffer += chunk
                if len(self.buffer) > self.cap:
                    extra = len(self.buffer) - self.cap
                    del self.buffer[:extra]
                    self.dropped += extra
        except (OSError, ValueError):
            pass

    def text(self) -> str:
        # Windows text-mode streams write \r\n; normalise so logs and error blocks parse the same everywhere.
        body = self.buffer.decode("utf-8", errors="replace").replace("\r\n", "\n")
        if self.dropped:
            return f"[... {self.dropped} bytes of earlier output dropped ...]\n" + body
        return body


def _posix_cpu_backstop(pid: int, timeout: float) -> None:
    """RLIMIT_CPU on the render process: an orphaned runaway dies by SIGXCPU/SIGKILL even if the worker is gone.

    The limit counts the CPU time of every thread (manim encodes with a multi-threaded libx264), so it is
    a multiple of the wall-clock timeout (:data:`CPU_BACKSTOP_FACTOR`): it never pre-empts the watchdog.
    Children forked afterwards inherit it. No RLIMIT_AS: large virtual reservations by numpy/cairo
    would make it break legitimate renders (see the gap analysis regression notes). macOS has
    ``resource`` but no ``prlimit``: no backstop there.
    """
    try:
        import resource

        soft = int(timeout * CPU_BACKSTOP_FACTOR) + 30
        hard = soft + 15
        resource.prlimit(pid, resource.RLIMIT_CPU, (soft, hard))
    except (OSError, ValueError, ImportError, AttributeError):  # best effort / non-POSIX / no prlimit (macOS)
        pass


def _job_commit_backstop_mb(limit_mb: float | None) -> float | None:
    """The Windows job's committed-memory limit for an RSS ceiling of ``limit_mb``.

    Committed memory is several times RSS (OpenBLAS and x264 reserve buffers per thread), so the job gets
    a looser backstop that only stops a runaway allocation between two RSS polls; the psutil RSS poll stays
    the enforced ceiling (MANIM_MEMORY_LIMIT_MB)."""
    return limit_mb * 2 + 2048 if limit_mb else None


def run_process(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    timeout: float,
    cancel: threading.Event | None = None,
    memory_limit_mb: float | None = None,
    on_kill: list | None = None,  # extra cleanup callables run when the process is killed
    watch_dirs: Iterable[Path] = (),  # directories whose total size must stay under disk_limit_mb
    disk_limit_mb: float | None = None,
    max_processes: int | None = None,  # Windows job process-count limit (allow LaTeX's helpers)
    metrics: dict | None = None,  # filled with peak_memory_mb / cpu_seconds / processes
) -> tuple[int | None, str, str, float]:
    """Run ``cmd`` blocking with timeout/cancel/memory/disk watchdogs. Returns (rc, output, killed_reason, elapsed).

    On Windows the process is put into a kill-on-close Job Object so it can never outlive this worker
    (crash, taskkill, OOM); on POSIX an RLIMIT_CPU backstop does the same for a runaway loop (a SIGXCPU
    exit is reported as ``timeout``). The psutil RSS poll stays the cross-platform source of the
    ``memory`` reason and friendly message; the job's committed-memory backstop only explains a failed
    exit, never a clean one. ``metrics``: ``peak_memory_mb`` (RSS), and on Windows ``peak_commit_mb``,
    ``cpu_seconds`` and ``processes``.
    """
    kwargs: dict = {}
    if os.name == "nt":
        # Normal priority on purpose: the timeout is wall-clock, so a de-prioritised render on a busy
        # host would be starved into timing out. MANIM_MAX_CONCURRENT bounds the CPU manim may use.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    else:
        kwargs["start_new_session"] = True
    start = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        cmd,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **kwargs,
    )
    # Kernel-level containment, created right after Popen (no child process has forked yet): the whole
    # tree lands in the job / under the rlimit. Best effort: on failure the psutil watchdog still runs.
    job = None
    if os.name == "nt":
        try:
            from .winjob import JobObject

            job = JobObject(memory_limit_mb=_job_commit_backstop_mb(memory_limit_mb), max_processes=max_processes)
            job.assign(int(proc._handle))  # type: ignore[attr-defined]
        except (OSError, ImportError):  # pragma: no cover - nested job without support, or old Windows
            if job is not None:
                job.close()
            job = None
    else:
        _posix_cpu_backstop(proc.pid, timeout)
    reader = _CappedReader(proc.stdout, OUTPUT_CAP)
    reader.start()
    killed = ""
    rc: int | None = None
    peak_rss = 0.0
    watch = [Path(d) for d in watch_dirs]
    next_mem_check = start
    next_disk_check = start + 1.0
    try:
        while True:
            try:
                rc = proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            if cancel is not None and cancel.is_set():
                killed = "cancelled"
            elif now - start > timeout:
                killed = "timeout"
            if not killed and now >= next_mem_check:
                next_mem_check = now + 0.5
                rss = _tree_rss_mb(proc.pid)
                peak_rss = max(peak_rss, rss)
                if memory_limit_mb and rss > memory_limit_mb:
                    killed = "memory"
            if not killed and disk_limit_mb and watch and now >= next_disk_check:
                next_disk_check = now + 1.0
                if dir_size_mb(watch, disk_limit_mb) > disk_limit_mb:
                    killed = "output_limit"
            if killed:
                for fn in on_kill or ():
                    try:
                        fn()
                    except Exception:  # pragma: no cover - best effort cleanup
                        pass
                kill_tree(proc.pid)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover - kill failed
                    pass
                rc = proc.returncode
                break
    finally:
        reader.join(timeout=5)
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:  # pragma: no cover
                pass
        if metrics is not None:
            metrics["peak_memory_mb"] = round(peak_rss, 1)  # RSS: what MANIM_MEMORY_LIMIT_MB limits
            if job is not None:
                stats = job.stats()
                metrics["peak_commit_mb"] = round(stats.peak_memory_mb, 1)
                metrics["cpu_seconds"] = round(stats.cpu_seconds, 2)
                metrics["processes"] = stats.processes
        # The job may have failed an allocation past its commit backstop before psutil saw it. That only
        # explains a failed exit: a clean exit (rc 0) is never reclassified.
        if job is not None:
            if not killed and rc != 0 and job.hit_memory_limit():
                killed = "memory"
            job.close()  # KILL_ON_JOB_CLOSE ends anything still alive
    sigxcpu = getattr(signal, "SIGXCPU", None)
    if not killed and sigxcpu is not None and rc == -sigxcpu:
        killed = "timeout"  # the RLIMIT_CPU backstop fired (never -SIGKILL: that is also the OOM killer's)
    return rc, reader.text(), killed, time.monotonic() - start


def find_output(media_dir: Path) -> Path | None:
    """The final ``out.mp4`` written by manim (ignores partial movie files, symlinks and path escapes).

    Only a real file, not a symlink and not reached through a symlinked parent, that resolves to a
    path inside ``media_dir`` is accepted: a symlinked ``out.mp4`` pointing at a host file (possible
    on the docker bind mount, where the container writes the tree) is never promoted to an asset.
    """
    if not media_dir.is_dir():
        return None
    try:
        root = media_dir.resolve()
    except OSError:  # pragma: no cover - the media dir just existed
        return None
    for path in sorted(media_dir.rglob(f"{OUTPUT_NAME}.mp4")):
        if "partial_movie_files" in path.parts or path.is_symlink():
            continue
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
        except OSError:
            continue
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _frames_from_log(log: str) -> int:
    """Frames written, from the runtime's ``[AADHI_STATS] frames=N`` line (0 when absent)."""
    matches = _STATS_FRAMES.findall(log or "")
    return int(matches[-1]) if matches else 0


def _finalize_output(video: Path | None, workdir: Path, output_limit_mb: float | None) -> tuple[Path | None, str]:
    """Move ``video`` to ``<workdir>/out.mp4``; reject it when larger than ``output_limit_mb``.

    Returns ``(final_path_or_None, reject_reason)``; a non-empty reason means the output was refused.
    """
    if video is None:
        return None, ""
    size_mb = video.stat().st_size / (1024 * 1024)
    if output_limit_mb and size_mb > output_limit_mb:
        return None, f"the rendered video is {size_mb:.0f} MB, above the {output_limit_mb:.0f} MB limit"
    dest = workdir / f"{OUTPUT_NAME}.mp4"
    dest.unlink(missing_ok=True)
    shutil.move(str(video), str(dest))
    return dest, ""


def valid_scene_name(scene: str) -> bool:
    """True when ``scene`` can be passed to the manim CLI (ASCII identifier, at most 64 characters)."""
    return isinstance(scene, str) and SCENE_NAME_PATTERN.fullmatch(scene) is not None


def failed_result(message: str, command: list[str] | None = None) -> SandboxResult:
    """A failed :class:`SandboxResult` whose log carries ``message`` as the runtime error block."""
    return SandboxResult(ok=False, returncode=None, video_path=None, command=list(command or []),
                         log=f"[AADHI_ERROR]\n{message}\n[/AADHI_ERROR]")


def manim_args(script: str, scene: str, *, media_dir: str, quality: str) -> list[str]:
    """``manim render`` arguments shared by both runners (verified against manim 0.21 CLI)."""
    if quality not in ("l", "m", "h"):
        raise ValueError(f"invalid manim quality {quality!r}")
    if not valid_scene_name(scene):
        raise ValueError(
            f"invalid scene class name {scene!r}: use ASCII letters, digits and underscores (max 64 characters)"
        )
    return [
        "render",
        f"-q{quality}",
        "--format",
        "mp4",
        "--disable_caching",
        "--media_dir",
        media_dir,
        "-o",
        OUTPUT_NAME,
        "--progress_bar",
        "none",
        "-v",
        "WARNING",
        "--silent",  # no PyPI version check (an outbound HTTPS request on every render; trips the audit hook)
        script,
        scene,
    ]


# --- subprocess runner ----------------------------------------------------------------------------


class SubprocessRunner:
    """Runs manim as a local subprocess (development; free-form code is refused in production)."""

    name = "subprocess"

    def __init__(
        self,
        python: str | None = None,
        memory_limit_mb: float | None = DEFAULT_MEMORY_LIMIT_MB,
        *,
        disk_limit_mb: float | None = None,
        output_limit_mb: float | None = None,
        max_processes: int | None = DEFAULT_MAX_PROCESSES,
        scratch_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.python = python or sys.executable
        self.memory_limit_mb = memory_limit_mb
        self.disk_limit_mb = disk_limit_mb
        self.output_limit_mb = output_limit_mb
        self.max_processes = max_processes
        self.scratch_dir = os.fspath(scratch_dir or "")  # private media dirs go here (empty = the default root)

    def has_latex(self) -> bool:
        return tex_available()

    def build_command(self, script_path: Path, scene: str, media_dir: Path, quality: str) -> list[str]:
        """Full argv: ``<python> -m manim render -q<q> --format mp4 --disable_caching ...``."""
        return [self.python, "-m", "manim", *manim_args(str(script_path), scene, media_dir=str(media_dir), quality=quality)]

    def build_env(self, workdir: Path) -> dict[str, str]:
        """Allow-listed environment (no API keys/secrets), TEMP inside the work dir, TeX flags."""
        env = {k: v for k, v in os.environ.items() if k.upper() in ENV_ALLOWLIST}
        if os.name == "nt":  # environment keys are case-insensitive on Windows
            env = {k.upper(): v for k, v in env.items()}
        tmp = workdir / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        env.update({"TEMP": str(tmp), "TMP": str(tmp), "TMPDIR": str(tmp)})
        bin_dir = tex_bin_dir()
        path = env.get("PATH", "")
        if bin_dir is not None and str(bin_dir).lower() not in path.lower():
            env["PATH"] = (path + os.pathsep if path else "") + str(bin_dir)
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "NO_COLOR": "1",
                "TERM": "dumb",
                "COLUMNS": "120",
                "AADHI_TEX_FLAGS": tex_flags(bin_dir),
                "AADHI_DVISVGM_FLAGS": dvisvgm_flags(bin_dir),
                # Manim's matrices are tiny: BLAS threads only reserve per-thread buffers (commit charge)
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
            }
        )
        return env

    def render_sync(
        self, script: str, scene: str, *, workdir: Path, quality: str, timeout: float, cancel: threading.Event
    ) -> SandboxResult:
        """Blocking render (runs in a worker thread). The video ends up at ``<workdir>/out.mp4``."""
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        script_path = workdir / SCRIPT_NAME
        script_path.write_text(script, encoding="utf-8")
        # Manim nests ~100 characters below --media_dir (videos/<module>/<quality>/partial_movie_files/
        # <Scene>/...). Render into a short private directory so deep work dirs never hit MAX_PATH on Windows.
        media = make_temp_dir(self, "aadhi-m-")
        write_owner_marker(media)
        try:
            try:
                cmd = self.build_command(script_path, scene, media, quality)
            except ValueError as exc:
                return failed_result(str(exc))
            env = self.build_env(workdir)
            metrics: dict = {}
            try:
                rc, output, killed, elapsed = run_process(
                    cmd, cwd=workdir, env=env, timeout=timeout, cancel=cancel, memory_limit_mb=self.memory_limit_mb,
                    watch_dirs=[workdir, media], disk_limit_mb=self.disk_limit_mb, max_processes=self.max_processes,
                    metrics=metrics,
                )
            except FileNotFoundError:
                return SandboxResult(ok=False, returncode=None, video_path=None, command=cmd,
                                     log=f"python interpreter {self.python!r} not found")
            video = None if killed else find_output(media)
            video, reject = _finalize_output(video, workdir, self.output_limit_mb)
            if reject:
                killed, output = "output_limit", output + f"\n[AADHI_LIMIT] output\n{reject}"
        finally:
            shutil.rmtree(media, ignore_errors=True)
        return SandboxResult(
            ok=rc == 0 and not killed and video is not None,
            returncode=rc,
            video_path=video,
            log=output,
            timed_out=killed == "timeout",
            killed_reason=killed,
            elapsed=elapsed,
            command=cmd,
            peak_memory_mb=float(metrics.get("peak_memory_mb", 0.0)),
            frames=_frames_from_log(output),
        )

    async def run(self, script: str, scene: str, *, workdir: Path, quality: str, timeout: float) -> SandboxResult:
        """Render without blocking the event loop; cancelling the caller kills the process tree."""
        cancel = threading.Event()
        return await run_in_thread(
            lambda: self.render_sync(script, scene, workdir=workdir, quality=quality, timeout=timeout, cancel=cancel),
            cancel,
        )


# --- docker runner --------------------------------------------------------------------------------


class DockerRunner:
    """Runs manim in a locked-down container (production)."""

    name = "docker"
    WORKDIR = "/work"

    LABEL = "aadhi.manim=1"  # sweep() kills only containers carrying this label, past their deadline

    def __init__(
        self,
        image: str,
        *,
        docker: str = "docker",
        memory: str = "2g",
        cpus: str = "2",
        pids_limit: int = 256,
        user: str = "1000:1000",
        disk_limit_mb: float | None = None,
        output_limit_mb: float | None = None,
        scratch_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.image = image
        self.docker = docker
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.user = user
        self.disk_limit_mb = disk_limit_mb
        self.output_limit_mb = output_limit_mb
        # The mounted work dirs go here: it must be at the same path on the host (the default root lives
        # under TMPDIR, which the docker-sandbox deployment shares with the host).
        self.scratch_dir = os.fspath(scratch_dir or "")

    def has_latex(self) -> bool:
        return True  # the sandbox image ships TeX Live (see docker/manim-sandbox/Dockerfile)

    def build_command(self, workdir: Path, scene: str, quality: str, container_name: str,
                      timeout: float | None = None) -> list[str]:
        """``docker run`` argv with every isolation flag (network off, read-only root, caps dropped...).

        The container carries :data:`LABEL` and an ``aadhi.manim.deadline`` label and (when ``timeout``
        is given) a ``timeout -s KILL`` wrapper, so an orphaned sibling container is swept even if this
        worker dies. ``--ulimit fsize`` caps any single file the render can write.
        """
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}", container_name):
            raise ValueError("invalid container name")
        deadline = int(time.time() + (timeout or 0) + 60)
        fsize_mb = int(self.output_limit_mb * 2) if self.output_limit_mb else 1024
        inner = ["python", "-m", "manim",
                 *manim_args(f"{self.WORKDIR}/{SCRIPT_NAME}", scene, media_dir=f"{self.WORKDIR}/media", quality=quality)]
        if timeout:  # a hard in-container deadline: the render dies even if the docker CLI handle is lost
            inner = ["timeout", "-s", "KILL", str(int(timeout) + 30), *inner]
        return [
            self.docker,
            "run",
            "--rm",
            "--name",
            container_name,
            "--label",
            self.LABEL,
            "--label",
            f"aadhi.manim.deadline={deadline}",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=512m",
            "--memory",
            self.memory,
            "--memory-swap",
            self.memory,
            "--cpus",
            self.cpus,
            "--pids-limit",
            str(self.pids_limit),
            "--ulimit",
            f"fsize={fsize_mb * 1024 * 1024}",
            "--user",
            self.user,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "-v",
            f"{Path(workdir).resolve()}:{self.WORKDIR}:rw",
            "-w",
            self.WORKDIR,
            "-e",
            "HOME=/tmp",
            "-e",
            "AADHI_TEX_FLAGS=-no-shell-escape",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "NO_COLOR=1",
            self.image,
            *inner,
        ]

    def _kill_container(self, name: str) -> None:
        try:
            subprocess.run(  # noqa: S603 - fixed argv
                [self.docker, "kill", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover - best effort
            pass

    def build_env(self) -> dict[str, str]:
        """Environment of the ``docker`` CLI: the allow-list plus ``DOCKER_*`` (never API keys)."""
        return {k: v for k, v in os.environ.items() if k.upper() in ENV_ALLOWLIST or k.upper().startswith("DOCKER_")}

    def render_sync(
        self, script: str, scene: str, *, workdir: Path, quality: str, timeout: float, cancel: threading.Event
    ) -> SandboxResult:
        """Blocking render (runs in a worker thread). The video ends up at ``<workdir>/out.mp4``."""
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        # The container user (uid 1000) must write to the mount. Mount a world-writable leaf inside a
        # private 0700 directory: writable for the container, unreachable for other host users.
        outer = make_temp_dir(self, "aadhi-d-")
        write_owner_marker(outer)
        mount = outer / "w"
        cmd: list[str] = []
        try:
            mount.mkdir()
            (mount / "media").mkdir()
            (mount / SCRIPT_NAME).write_text(script, encoding="utf-8")
            if os.name != "nt":
                os.chmod(mount, 0o777)  # noqa: S103 - parent is a private 0700 temp dir
                os.chmod(mount / "media", 0o777)  # noqa: S103
                os.chmod(mount / SCRIPT_NAME, 0o644)
            name = f"aadhi-manim-{uuid.uuid4().hex[:16]}"
            try:
                cmd = self.build_command(mount, scene, quality, name, timeout=timeout)
            except ValueError as exc:
                return failed_result(str(exc))
            metrics: dict = {}
            try:
                # The container (not the docker CLI) owns the memory/pids limits, so run_process watches
                # only disk and wall-clock here; the host-side job object would track the CLI, not the render.
                rc, output, killed, elapsed = run_process(
                    cmd, cwd=outer, env=self.build_env(), timeout=timeout, cancel=cancel,
                    on_kill=[lambda: self._kill_container(name)],
                    watch_dirs=[mount], disk_limit_mb=self.disk_limit_mb, metrics=metrics,
                )
            except FileNotFoundError:
                return SandboxResult(ok=False, returncode=None, video_path=None, command=cmd,
                                     log=f"docker CLI {self.docker!r} not found; install Docker or use MANIM_SANDBOX=subprocess")
            if killed == "output_limit":
                self._kill_container(name)
            video = None if killed else find_output(mount / "media")
            video, reject = _finalize_output(video, workdir, self.output_limit_mb)
            if reject:
                killed, output = "output_limit", output + f"\n[AADHI_LIMIT] output\n{reject}"
        finally:
            shutil.rmtree(outer, ignore_errors=True)
        return SandboxResult(
            ok=rc == 0 and not killed and video is not None,
            returncode=rc,
            video_path=video,
            log=output,
            timed_out=killed == "timeout",
            killed_reason=killed,
            elapsed=elapsed,
            command=cmd,
            # The host only sees the docker CLI, not the container, so its RSS is not the render's peak;
            # the container enforces memory itself (--memory). Frames come from the container's own log.
            peak_memory_mb=0.0,
            frames=_frames_from_log(output),
        )

    async def run(self, script: str, scene: str, *, workdir: Path, quality: str, timeout: float) -> SandboxResult:
        """Render without blocking the event loop; cancelling the caller kills the container."""
        cancel = threading.Event()
        return await run_in_thread(
            lambda: self.render_sync(script, scene, workdir=workdir, quality=quality, timeout=timeout, cancel=cancel),
            cancel,
        )


async def run_in_thread(fn: Callable[[], SandboxResult], cancel: threading.Event) -> SandboxResult:
    """Run a blocking render in a worker thread; on cancellation set ``cancel`` and wait for the thread.

    The worker kills the process tree (or container) when ``cancel`` is set; waiting for it means the
    caller's concurrency slot and work directory are released only once nothing runs any more.
    """
    task = asyncio.ensure_future(asyncio.to_thread(fn))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue  # cancelled again while the process tree is being killed: keep waiting
            except Exception:  # noqa: BLE001 - the render outcome no longer matters
                break
        if not task.cancelled():
            task.exception()  # mark a worker exception as retrieved
        raise


# --- orphan sweep ---------------------------------------------------------------------------------

OWNER_MARKER = ".aadhi-owner.json"
TEMP_PREFIXES = ("aadhi-manim-", "aadhi-m-", "aadhi-d-")
ORPHAN_MAX_AGE_SECONDS = 24 * 3600  # dirs with no readable owner (or an owner on another host) older than this


@lru_cache(maxsize=1)
def _owner_identity() -> tuple[str, str]:
    """(hostname, boot_id) of this process, from :mod:`aadhi.jobs.lease` (falls back when unavailable).
    ``BOOT_ID`` is random per *process*, not per machine boot: it never says on its own that an owner is gone."""
    try:
        from ..jobs.lease import BOOT_ID, HOSTNAME

        return HOSTNAME, BOOT_ID
    except Exception:  # pragma: no cover - lease module is always importable
        import socket

        return (socket.gethostname() or "localhost"), "unknown"


def _process_started(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError, ValueError):
        return None


def write_owner_marker(path: Path) -> None:
    """Record {host, pid, boot_id, started} in ``path`` so a later :func:`sweep_orphans` can tell if the owner is
    gone (``started``: the owner process's start time, so a recycled pid is not mistaken for it)."""
    host, boot = _owner_identity()
    data = {"host": host, "pid": os.getpid(), "boot_id": boot, "created": int(time.time()),
            "started": _process_started(os.getpid())}
    try:
        (Path(path) / OWNER_MARKER).write_text(_json.dumps(data), encoding="utf-8")
    except OSError:  # pragma: no cover - best effort
        pass


def _owner_is_gone(marker: dict, now: float) -> bool:
    """Same host: the owner pid is dead, or now belongs to another process (its start time differs). A live
    pid without a recorded start time (an older marker) may be a sibling worker: removed only once stale.
    Another host (a shared TMPDIR): the pid is meaningless here, so only once clearly stale."""
    host, _boot = _owner_identity()
    stale = now - float(marker.get("created", 0)) > ORPHAN_MAX_AGE_SECONDS
    if marker.get("host") != host:
        return stale
    pid = marker.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or not psutil.pid_exists(pid):
        return True  # the owner process is gone (dead, or the machine rebooted)
    started, actual = marker.get("started"), _process_started(pid)
    if isinstance(started, (int, float)) and not isinstance(started, bool) and actual is not None:
        return abs(actual - float(started)) > 1.0  # the pid was recycled by an unrelated process
    return stale


def _sweep_temp_dirs(roots: Iterable[Path], now: float) -> int:
    removed = 0
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for entry in list(root.iterdir()):
            if entry.is_symlink() or not entry.is_dir() or not entry.name.startswith(TEMP_PREFIXES):
                continue
            marker_path = entry / OWNER_MARKER
            should_remove = False
            try:
                if marker_path.is_file():
                    should_remove = _owner_is_gone(_json.loads(marker_path.read_text(encoding="utf-8")), now)
                else:
                    should_remove = now - entry.stat().st_mtime > ORPHAN_MAX_AGE_SECONDS
            except (OSError, ValueError):
                continue
            if should_remove:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
    return removed


def _sweep_docker(settings: Settings, now: float) -> int:
    docker = "docker"
    if shutil.which(docker) is None:
        return 0
    try:
        listing = subprocess.run(  # noqa: S603 - fixed argv
            [docker, "ps", "--filter", f"label={DockerRunner.LABEL}",
             "--format", '{{.ID}} {{.Label "aadhi.manim.deadline"}}'],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - best effort
        return 0
    expired: list[str] = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                if now > float(parts[1]):  # only containers past their own deadline: safe with many workers
                    expired.append(parts[0])
            except ValueError:
                continue
    if expired:
        try:
            subprocess.run([docker, "rm", "-f", *expired],  # noqa: S603 - fixed argv
                           capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            pass
    return len(expired)


def _temp_roots(settings: Settings) -> list[Path]:
    """Where :func:`sweep_orphans` looks: only the app's scratch root (never the system temp dir itself, so
    another program's or another checkout's folders are never touched, whatever their names)."""
    return [scratch_root(settings)]


def sweep_orphans(settings: Settings, now: float | None = None) -> dict[str, int]:
    """Remove leftover Manim temp dirs and stop expired sandbox containers (safe with many workers).

    Only work dirs inside the scratch root (``SCRATCH_DIR``) are considered. They are removed when their owner
    marker says the owner is gone (same host and the pid is dead or was reused) or, lacking a marker, when they
    are older than a day; a live sibling process's dirs are kept.
    Docker containers are killed
    only once past the deadline stamped in their own label. Call at worker start and periodically.
    """
    now = time.time() if now is None else now
    removed_dirs = _sweep_temp_dirs(_temp_roots(settings), now)
    killed = _sweep_docker(settings, now) if settings.manim_sandbox == "docker" else 0
    return {"temp_dirs": removed_dirs, "containers": killed}


def get_runner(settings: Settings) -> ManimRunner:
    """Runner for ``settings.manim_sandbox`` (raises :class:`ManimError` when disabled)."""
    if settings.manim_sandbox == "docker":
        return DockerRunner(
            settings.manim_docker_image,
            memory=f"{settings.manim_memory_limit_mb}m",
            cpus=f"{settings.manim_docker_cpus:g}",
            disk_limit_mb=float(settings.manim_max_workspace_mb),
            output_limit_mb=float(settings.manim_max_output_mb),
            scratch_dir=scratch_root(settings),
        )
    if settings.manim_sandbox == "subprocess":
        return SubprocessRunner(
            memory_limit_mb=float(settings.manim_memory_limit_mb),
            disk_limit_mb=float(settings.manim_max_workspace_mb),
            output_limit_mb=float(settings.manim_max_output_mb),
            max_processes=int(settings.manim_max_processes),
            scratch_dir=scratch_root(settings),
        )
    raise ManimError("Manim rendering is disabled (MANIM_SANDBOX=disabled)")


async def run(
    script: str, scene: str, *, settings: Settings, workdir: Path, quality: str | None = None,
    timeout: float | None = None,
) -> SandboxResult:
    """Render ``scene`` from an assembled ``script`` with the configured runner (``MANIM_SANDBOX``).

    Defaults: ``MANIM_QUALITY`` and ``MANIM_TIMEOUT_SECONDS``. The video ends up at ``<workdir>/out.mp4``.
    """
    runner = get_runner(settings)
    return await runner.run(
        script,
        scene,
        workdir=Path(workdir),
        quality=quality or settings.manim_quality,
        timeout=float(timeout if timeout is not None else settings.manim_timeout_seconds),
    )
