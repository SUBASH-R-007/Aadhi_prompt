"""Encoded motion chunks cached in the render workspace (``aadhi.compose.workspace``), by content address.

Layout::

    render-<id>/chunks/<key>.ts    an encoded chunk (key = ``chunks.chunk_key``)
    render-<id>/chunks/<key>.ok    written last: {"frames", "bytes", "keys"} (a chunk without it is ignored)

The workspace survives a failed attempt (``RENDER_KEEP_FAILED_HOURS``), so a retry captures only the chunks the
failed attempt did not finish; it is removed with the workspace when the render succeeds or is cancelled. Chunks
enter the cache by an atomic ``os.replace`` of a hard link (or copy) of the attempt's file, then the marker; a
chunk is reused only when its marker, its size and its packet count (checked by the caller) agree. Blocking: call
through ``asyncio.to_thread``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CHUNKS = "chunks"
_KEY_RE = re.compile(r"^[0-9a-f]{16,64}$")


def _link_or_copy(src: Path, dst: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


@dataclass
class ChunkCache:
    """Content-addressed chunk store in ``directory`` (normally ``<workspace>/chunks``)."""

    directory: Path

    @classmethod
    def in_workspace(cls, root: Path) -> ChunkCache:
        """The cache of the render workspace ``root``."""
        return cls(Path(root) / CHUNKS)

    def _paths(self, key: str) -> tuple[Path, Path]:
        if not _KEY_RE.match(key):
            raise ValueError("invalid chunk key")
        return self.directory / f"{key}.ts", self.directory / f"{key}.ok"

    def cached(self, key: str, *, frames: int) -> Path | None:
        """The cached chunk when its marker agrees with the file and the expected frame count."""
        ts, ok = self._paths(key)
        try:
            marker = json.loads(ok.read_text(encoding="utf-8"))
            size = ts.stat().st_size
        except (OSError, ValueError):
            return None
        if not isinstance(marker, dict) or marker.get("frames") != frames or marker.get("bytes") != size or size == 0:
            return None
        return ts

    def marker(self, key: str) -> dict | None:
        """The marker of a cached chunk (None when absent or unreadable)."""
        _, ok = self._paths(key)
        try:
            data = json.loads(ok.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def adopt(self, key: str, dest: Path) -> None:
        """Make the cached chunk ``key`` available at ``dest`` (hard link, else copy)."""
        ts, _ = self._paths(key)
        _link_or_copy(ts, dest)

    def store(self, key: str, src: Path, *, frames: int, keys_digest: str = "") -> bool:
        """Publish the encoded chunk ``src`` as ``key`` (best effort; False when it could not be cached)."""
        ts, ok = self._paths(key)
        tag = secrets.token_hex(4)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            part = ts.with_name(f".{ts.name}.{tag}.part")
            _link_or_copy(src, part)
            os.replace(part, ts)
            marker = ok.with_name(f".{ok.name}.{tag}.part")
            marker.write_text(json.dumps({"frames": frames, "bytes": ts.stat().st_size, "keys": keys_digest}),
                              encoding="utf-8")
            os.replace(marker, ok)
            return True
        except OSError as exc:
            log.debug("could not cache chunk %s: %s", key[:12], type(exc).__name__)
            for leftover in self.directory.glob(f".*.{tag}.part"):
                with contextlib.suppress(OSError):
                    leftover.unlink()
            return False

    def discard(self, key: str) -> None:
        """Forget a cached chunk (e.g. its packet count disagreed)."""
        for path in self._paths(key):
            with contextlib.suppress(OSError):
                path.unlink()
