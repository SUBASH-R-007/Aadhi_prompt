"""Durable render workspace: a retried render reuses the segments it already encoded.

Layout under ``RENDER_WORK_DIR`` (default ``<DATA_DIR>/render-work``)::

    render-<id>/manifest.json         fingerprint of the timeline + encoder setup (changes clear segments/)
    render-<id>/segments/<key>.mp4    encoded scene segments, content-addressed (key = segment_key)
    render-<id>/segments/<key>.wav
    render-<id>/segments/<key>.ok     written last: {"frames", "samples"} (a segment without it is ignored)
    render-<id>/attempt-<n>-<host>-<pid>-<rand>/   this attempt's screenshots, inputs, side files, output

Each attempt works in its own directory (ffmpeg's cwd), so two workers that briefly hold the same
render after a lost lease never share files; segments enter ``segments/`` by an atomic
``os.replace`` and the ``.ok`` marker, and are reused only when the marker and the files agree
(the caller also probes the MP4's frame count). A segment key covers the exact ffmpeg command
(with file names normalised), the content of every input (asset keys are content addresses,
screenshots are hashed, branding files by size + mtime) and the ffmpeg version, so a changed
lecture, setting or ffmpeg never reuses a stale segment.

Lifecycle (``aadhi.compose.video``): the attempt directory is always removed; the whole workspace
is removed when the render succeeds or the user cancels it, and kept after a failure for
``RENDER_KEEP_FAILED_HOURS`` so a retry (automatic or ``POST /api/jobs/{id}/retry``) resumes it.
:func:`sweep_workspaces` (run at the start of every render; usable from the cleanup job) removes
workspaces of finished / deleted renders and expired failures, plus ``aadhi-render-*`` directories inside
the app's scratch root (``SCRATCH_DIR``, :mod:`aadhi.scratch`). The system temp dir itself is never swept:
other programs' (or another checkout's) folders there are never touched, whatever their names. Blocking:
call through ``asyncio.to_thread``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
import time
import wave
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from ..scratch import scratch_root
from .ffmpeg import Command

log = logging.getLogger(__name__)

SEGMENT_CACHE_VERSION = 1
MANIFEST = "manifest.json"
SEGMENTS = "segments"
_WORKSPACE_RE = re.compile(r"^render-(\d+)$")
_LEGACY_PREFIX = "aadhi-render-"
_HOST = re.sub(r"[^A-Za-z0-9_.-]", "_", socket.gethostname())[:40] or "host"
_SEGMENT_SUFFIXES = (".mp4", ".wav", ".filtergraph", ".states.ffconcat", ".fades.ffconcat")


def work_root(settings: Settings) -> Path:
    """Absolute root of the render workspaces (``RENDER_WORK_DIR`` or ``<DATA_DIR>/render-work``)."""
    raw = (getattr(settings, "render_work_dir", "") or "").strip()
    root = Path(raw).expanduser() if raw else Path(settings.data_dir) / "render-work"
    return root.resolve()


def free_gb(path: Path) -> float:
    """Free space (GB) on the filesystem holding ``path`` (its nearest existing parent)."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free / 1e9


def file_digest(path: Path, *, chunk: int = 1 << 20) -> str:
    """sha256 of a file's content (hex)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def stat_digest(path: Path) -> str:
    """Cheap identity of a large, rarely changing file (branding clips): name, size, mtime."""
    st = path.stat()
    return f"file:{path.name}:{st.st_size}:{st.st_mtime_ns}"


def segment_key(cmd: Command, name: str, digests: Mapping[str, str], extra: Mapping[str, Any] | None = None) -> str:
    """Content address of a segment encode (see the module docstring).

    ``digests`` maps every input path string used in ``cmd`` (absolute inputs, work-dir-relative
    screenshot paths) to a content digest; ``name`` (the segment's file stem) is normalised away.
    """
    own = {f"{name}{suffix}": f"@{suffix}" for suffix in _SEGMENT_SUFFIXES}
    ordered = sorted(digests.items(), key=lambda kv: len(kv[0]), reverse=True)

    def norm(text: str) -> str:
        if text in own:
            return own[text]
        for path, digest in ordered:
            if path and path in text:
                text = text.replace(path, f"<{digest}>")
        return text

    doc = {
        "v": SEGMENT_CACHE_VERSION,
        "args": [norm(a) for a in cmd.args],
        "files": {norm(k): norm(v) for k, v in sorted(cmd.files.items())},
        "extra": dict(extra or {}),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:32]


def wav_frames(path: Path) -> int | None:
    """Sample frames of a PCM WAV file (None if unreadable)."""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes()
    except (OSError, EOFError, wave.Error):
        return None


def _link_or_copy(src: Path, dst: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


@dataclass
class RenderWorkspace:
    """One render's workspace and this attempt's directory (see the module docstring)."""

    root: Path
    attempt_dir: Path

    @property
    def segments_dir(self) -> Path:
        """Content-addressed segment cache."""
        return self.root / SEGMENTS

    @classmethod
    def open(cls, settings: Settings, render_id: int, attempt: int) -> RenderWorkspace:
        """Create (or reopen) the render's workspace and a fresh attempt directory."""
        root = work_root(settings) / f"render-{int(render_id)}"
        tag = f"attempt-{max(1, int(attempt))}-{_HOST}-{os.getpid()}-{secrets.token_hex(3)}"
        attempt_dir = root / tag
        (root / SEGMENTS).mkdir(parents=True, exist_ok=True)
        attempt_dir.mkdir(parents=True, exist_ok=False)
        return cls(root=root, attempt_dir=attempt_dir.resolve())

    def prepare(self, fingerprint: Mapping[str, Any]) -> bool:
        """Record the render's fingerprint; clear cached segments when it changed. True = resumable."""
        path = self.root / MANIFEST
        wanted = json.loads(json.dumps(dict(fingerprint), sort_keys=True, default=str))
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        if current == wanted:
            return True
        if current is not None:
            shutil.rmtree(self.segments_dir, ignore_errors=True)
            self.segments_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{MANIFEST}.{secrets.token_hex(4)}"
        tmp.write_text(json.dumps(wanted, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        return False

    def _paths(self, key: str) -> tuple[Path, Path, Path]:
        base = self.segments_dir / key
        return base.with_suffix(".mp4"), base.with_suffix(".wav"), base.with_suffix(".ok")

    def cached(self, key: str, *, frames: int, samples: int) -> Path | None:
        """The cached segment's MP4 when its marker and WAV agree with the expected length."""
        mp4, wav, ok = self._paths(key)
        try:
            marker = json.loads(ok.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if marker.get("frames") != frames or marker.get("samples") != samples:
            return None
        if not mp4.is_file() or mp4.stat().st_size == 0 or wav_frames(wav) != samples:
            return None
        return mp4

    def adopt(self, key: str, name: str) -> None:
        """Make the cached segment ``key`` available as ``<attempt>/<name>.mp4|.wav``."""
        mp4, wav, _ = self._paths(key)
        _link_or_copy(mp4, self.attempt_dir / f"{name}.mp4")
        _link_or_copy(wav, self.attempt_dir / f"{name}.wav")

    def store(self, key: str, name: str, *, frames: int, samples: int) -> bool:
        """Publish ``<attempt>/<name>.mp4|.wav`` as cached segment ``key`` (best effort)."""
        mp4, wav, ok = self._paths(key)
        tag = secrets.token_hex(4)
        try:
            for src, dst in ((self.attempt_dir / f"{name}.mp4", mp4), (self.attempt_dir / f"{name}.wav", wav)):
                part = dst.with_name(f".{dst.name}.{tag}.part")
                _link_or_copy(src, part)
                os.replace(part, dst)
            part = ok.with_name(f".{ok.name}.{tag}.part")
            part.write_text(json.dumps({"frames": frames, "samples": samples}), encoding="utf-8")
            os.replace(part, ok)
            return True
        except OSError as exc:
            log.debug("could not cache segment %s: %s", key[:12], type(exc).__name__)
            for leftover in self.segments_dir.glob(f".*.{tag}.part"):
                with contextlib.suppress(OSError):
                    leftover.unlink()
            return False

    def finish(self, *, keep: bool) -> None:
        """Remove this attempt's directory, and the whole workspace unless ``keep``."""
        shutil.rmtree(self.attempt_dir, ignore_errors=True)
        if not keep:
            shutil.rmtree(self.root, ignore_errors=True)


def _age_hours(path: Path, now: float) -> float:
    try:
        return max(0.0, now - path.stat().st_mtime) / 3600.0
    except OSError:
        return 0.0


def workspace_ids(root: Path) -> dict[int, Path]:
    """Render id -> workspace directory under ``root``."""
    out: dict[int, Path] = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        m = _WORKSPACE_RE.match(entry.name)
        if m and entry.is_dir():
            out[int(m.group(1))] = entry
    return out


def sweep_workspaces(root: Path, statuses: Mapping[int, str | None], *, keep_failed_hours: float,
                     exclude: Iterable[int] = (), now: float | None = None) -> list[Path]:
    """Remove workspaces that cannot be resumed any more; returns the removed directories.

    ``statuses`` maps render ids (of the workspaces found) to the Render row's status, None when
    the row is gone. Queued/running renders are never touched (they may be in progress on another
    worker); succeeded and deleted renders are removed; failed/cancelled ones once their workspace
    is older than ``keep_failed_hours``.
    """
    now = time.time() if now is None else now
    skip = set(exclude)
    removed: list[Path] = []
    for rid, path in workspace_ids(root).items():
        if rid in skip:
            continue
        status = statuses.get(rid)
        if status in ("queued", "running"):
            continue
        if status in ("failed", "cancelled") and _age_hours(path, now) < keep_failed_hours:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed


def sweep_legacy_temp(*, max_age_hours: float, now: float | None = None, temp_dir: Path | None = None) -> list[Path]:
    """Remove ``aadhi-render-*`` directories older than ``max_age_hours`` from ``temp_dir`` (default: the
    default scratch root; never the system temp dir itself, see the module docstring)."""
    now = time.time() if now is None else now
    base = temp_dir or scratch_root()
    removed: list[Path] = []
    try:
        entries = list(base.iterdir())
    except OSError:
        return removed
    for entry in entries:
        if entry.name.startswith(_LEGACY_PREFIX) and entry.is_dir() and _age_hours(entry, now) >= max_age_hours:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry)
    return removed


def sweep_stale(settings: Settings, statuses: Callable[[list[int]], Mapping[int, str | None]], *,
                exclude: Iterable[int] = ()) -> int:
    """Best-effort housekeeping of this host's render files; returns how many directories were removed.

    :func:`sweep_workspaces` under :func:`work_root` (``statuses(render_ids)`` looks up the Render rows)
    plus :func:`sweep_legacy_temp` inside the scratch root. Used at the start of every render, by the worker's maintenance
    thread and by the ``cleanup`` job. Never raises (housekeeping must not fail its caller).
    """
    try:
        root = work_root(settings)
        skip = set(exclude)
        ids = sorted(i for i in workspace_ids(root) if i not in skip)
        removed = sweep_workspaces(root, statuses(ids) if ids else {}, exclude=skip,
                                   keep_failed_hours=max(0, settings.render_keep_failed_hours))
        removed += sweep_legacy_temp(max_age_hours=max(24, settings.render_keep_failed_hours),
                                     temp_dir=scratch_root(settings))
    except Exception as exc:  # noqa: BLE001 - housekeeping only
        log.warning("render workspace sweep failed: %s", type(exc).__name__)
        return 0
    if removed:
        log.info("removed %d stale render workspace(s)", len(removed))
    return len(removed)
