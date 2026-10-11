"""The chunk cache in the render workspace (a retry reuses the chunks a failed attempt finished), and which chunk
failures are captured again on a fresh page."""

from __future__ import annotations

from pathlib import Path

import pytest

from aadhi.compose.dynamic.stage_page import StageError
from aadhi.compose.ffmpeg import FFmpegError
from aadhi.compose.motion.cache import ChunkCache
from aadhi.compose.motion.engine import ChunkCountError, MotionError, _retryable
from aadhi.compose.workspace import RenderWorkspace
from aadhi.config import Settings

KEY = "ab" * 32


def test_store_cached_adopt_discard(tmp_path: Path) -> None:
    cache = ChunkCache(tmp_path / "chunks")
    assert cache.cached(KEY, frames=10) is None and cache.marker(KEY) is None
    src = tmp_path / "chunk_0000.ts"
    src.write_bytes(b"\x47" * 188 * 4)
    assert cache.store(KEY, src, frames=10, keys_digest="d1")
    assert cache.cached(KEY, frames=10) == tmp_path / "chunks" / f"{KEY}.ts"
    assert cache.cached(KEY, frames=11) is None  # another frame count is another chunk
    assert cache.marker(KEY) == {"frames": 10, "bytes": 752, "keys": "d1"}
    dest = tmp_path / "attempt" / "chunk_0000.ts"
    dest.parent.mkdir()
    cache.adopt(KEY, dest)
    assert dest.read_bytes() == src.read_bytes()
    (tmp_path / "chunks" / f"{KEY}.ts").write_bytes(b"\x47" * 188)  # a truncated file: the marker disagrees
    assert cache.cached(KEY, frames=10) is None
    cache.discard(KEY)
    assert not list((tmp_path / "chunks").iterdir())
    assert not list((tmp_path / "chunks").glob(".*.part"))


def test_keys_are_validated(tmp_path: Path) -> None:
    cache = ChunkCache(tmp_path)
    for bad in ("../x", "ABCDEF0123456789", "", "z" * 64):
        with pytest.raises(ValueError):
            cache.cached(bad, frames=1)


def test_cache_lives_in_the_render_workspace(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, render_work_dir=str(tmp_path / "work"))
    ws = RenderWorkspace.open(settings, 41, 1)
    cache = ChunkCache.in_workspace(ws.root)
    src = ws.attempt_dir / "chunk_0000.ts"
    src.write_bytes(b"\x47" * 188)
    assert cache.store(KEY, src, frames=1)
    ws.finish(keep=True)  # a failed attempt: the workspace (and its chunks) stays for the retry
    retry = RenderWorkspace.open(settings, 41, 2)
    assert ChunkCache.in_workspace(retry.root).cached(KEY, frames=1) is not None
    retry.finish(keep=False)  # success: everything goes
    assert not ws.root.exists()


def test_which_chunk_failures_are_captured_again() -> None:
    assert _retryable(ChunkCountError("x", code="chunk_frames", retryable=True))
    assert _retryable(FFmpegError("encoder died"))
    assert _retryable(StageError("crashed", code="render_page_timeout"))
    assert _retryable(StageError("threw", code="stage_call_failed", retryable=False))  # one more try on a fresh page
    assert not _retryable(StageError("broken", code="stage_contract", retryable=False))
    assert not _retryable(MotionError("contract", code="stage_contract"))
    assert not _retryable(MotionError("missing", code="asset_missing"))
