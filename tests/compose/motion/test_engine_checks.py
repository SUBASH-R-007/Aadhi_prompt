"""The motion engine's checks around the capture, with stand-in workers (no Chromium, no ffmpeg; fast).

* every worker's page must lay the timeline out frame by frame, not only the first one's (§7.3, §7.6);
* a failed media plan, extraction or encoder start leaves none of the chunk's extracted frames behind (§7.7);
* a remedy capture holds a CPU slot of its own, cancel-aware (§7.6, §7.12): the capture's slots were released when
  it ended;
* overlay clips are not required before the capture while the stage does not draw overlays (B6); scene clips are.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aadhi.compose.dynamic.stage_page import StageError, StageInfo, StageScene
from aadhi.compose.ffmpeg import FFmpegError
from aadhi.compose.motion import engine as engine_mod
from aadhi.compose.motion.chunks import Chunk, scene_frame_bounds, total_frames
from aadhi.compose.motion.encode import EncoderSpec
from aadhi.compose.motion.engine import MotionConfig, MotionError, render_motion
from aadhi.compose.motion.resources import CpuSlots
from aadhi.compose.motion.stage import FrameRegistry
from aadhi.config import Settings
from aadhi.jobs.base import JobCancelled
from aadhi.schemas.timeline import Timeline

from .fixtures import CLIP_KEY, SimpleCtx, dynamic_timeline

OVERLAY_KEY = "video-" + "e" * 40
SIZE = (320, 180)


class _Captured(Exception):
    """A stand-in worker was asked for its first chunk's media plan: the capture started."""


def _layout(tl: Timeline, *, extra_frames: int = 0, shift: int = 0) -> StageInfo:
    """``info()`` of a page that lays ``tl`` out as the engine does (``scene_frame_bounds``), or with ``extra_frames``
    at the end, or with the first scene boundary moved by ``shift`` frames (the same total)."""
    bounds = scene_frame_bounds(tl)
    scenes = []
    for i, (f0, f1) in enumerate(bounds):
        f0 = f0 + shift if i == 1 else f0
        f1 = f1 + shift if i == 0 else f1
        scenes.append(StageScene(i, f0, f1 + (extra_frames if i == len(bounds) - 1 else 0)))
    return StageInfo(SIZE[0], SIZE[1], int(tl.fps), total_frames(bounds) + extra_frames, tuple(scenes))


class _Session:
    """A capture worker (stands in for ``stage.StageSession``): its page's ``info()`` is ``layout``; asked for a
    media plan, it stops the render (:class:`_Captured`)."""

    def __init__(self, layout: StageInfo, *, label: str = "remedy", on_start: Any = None) -> None:
        self.layout = layout
        self.label = label
        self.on_start = on_start
        self.started = self.closed = False
        self.chromium_version = "test-shell"
        self.webgl = False
        self.pages_opened = 1
        self.frames = FrameRegistry()

    async def start(self) -> _Session:
        self.started = True
        if self.on_start is not None:
            self.on_start()
        return self

    async def close(self) -> None:
        self.closed = True

    async def info(self) -> StageInfo:
        return self.layout

    async def media_plan(self, f0: int, f1: int) -> list[dict[str, Any]]:
        raise _Captured

    def messages(self) -> tuple[list[str], list[str]]:
        return [], []


def _engine(monkeypatch: pytest.MonkeyPatch, layouts: dict[str, StageInfo]) -> list[_Session]:
    """``render_motion`` with stand-in workers (``layouts``: label -> the layout its page reports), no ffmpeg (the
    audio master never finishes; it is cancelled with the render). Returns the workers it made."""
    made: list[_Session] = []

    def session(*, label: str, **kwargs: Any) -> _Session:
        made.append(_Session(layouts[label], label=label))
        return made[-1]

    async def version(settings: Any, **kwargs: Any) -> tuple[int, int]:
        return (6, 1)

    async def master(*args: Any, **kwargs: Any) -> str:
        await asyncio.Event().wait()
        return "master.m4a"

    monkeypatch.setattr(engine_mod, "StageSession", session)
    monkeypatch.setattr(engine_mod, "ffmpeg_version", version)
    monkeypatch.setattr(engine_mod, "_build_master", master)
    return made


def _render(tmp: Path, tl: Timeline, *, workers: int = 2, local: dict[str, Path] | None = None) -> Any:
    settings = Settings(_env_file=None, render_bgm=False, web_dir=tmp / "web")
    cfg = MotionConfig(workers=workers, chunk_min=30, chunk_max=60, use_slots=False, shared_driver=False,
                       sample_memory_seconds=0)
    return asyncio.run(render_motion(SimpleCtx(), tl, tmp / "out.mp4", settings, token="t", work_dir=tmp / "work",
                                     config=cfg, local_files=local))


# --- every worker's page is checked (§7.3) -----------------------------------------------------------------------


@pytest.mark.parametrize(("other", "error", "code"), [
    ({"extra_frames": 1}, StageError, "stage_contract"),  # another frame count
    ({"shift": 3}, MotionError, "stage_contract"),  # the same total, scene ranges moved
])
def test_every_worker_page_must_lay_out_the_timeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                     other: dict[str, int], error: type, code: str) -> None:
    """Each worker captures its own chunks on its own page: a second page that disagrees with the timeline would put
    its frames at the wrong times. Only the first page was checked; now the render stops before any capture."""
    tl = dynamic_timeline([2.0, 2.0], size=SIZE, narration=False)
    made = _engine(monkeypatch, {"worker-0": _layout(tl), "worker-1": _layout(tl, **other)})
    with pytest.raises(error) as ei:
        _render(tmp_path, tl)
    assert ei.value.code == code and not ei.value.retryable
    assert [s.label for s in made] == ["worker-0", "worker-1"] and all(s.started and s.closed for s in made)


def test_a_page_without_scene_ranges_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A page whose ``info()`` lists no scenes passed on its total frame count alone."""
    tl = dynamic_timeline([2.0, 2.0], size=SIZE, narration=False)
    bare = StageInfo(SIZE[0], SIZE[1], 30, 120, ())
    _engine(monkeypatch, {"worker-0": bare, "worker-1": bare})
    with pytest.raises(MotionError) as ei:
        _render(tmp_path, tl)
    assert ei.value.code == "stage_contract" and "no scene frame ranges" in str(ei.value)


def test_pages_that_agree_start_the_capture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tl = dynamic_timeline([2.0, 2.0], size=SIZE, narration=False)
    made = _engine(monkeypatch, {"worker-0": _layout(tl), "worker-1": _layout(tl)})
    with pytest.raises(_Captured):
        _render(tmp_path, tl)
    assert len(made) == 2 and all(s.closed for s in made)


# --- overlays are not drawn before B6: their clips are not required ---------------------------------------------


def _with_overlay(tl: Timeline) -> Timeline:
    data = tl.model_dump(mode="json")
    data["scenes"][1]["overlays"] = [{"id": "o1", "start": 0.5, "end": 1.5,
                                      "media": {"kind": "video", "asset_key": OVERLAY_KEY, "duration": 1.0}}]
    return Timeline.model_validate(data)


def test_a_missing_overlay_clip_does_not_stop_the_render(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The stage draws no overlay before B6 (its media plan lists scene and side-panel clips only), so an overlay
    whose clip is gone from storage cannot fail the render: the capture starts. A missing scene clip still fails
    before any capture (``asset_missing``)."""
    tl = _with_overlay(dynamic_timeline([2.0, 2.0], size=SIZE, narration=False, media_scene=0))
    assert engine_mod.video_media_keys(tl) == [CLIP_KEY]
    assert engine_mod.video_media_keys(tl, overlays=True) == sorted([CLIP_KEY, OVERLAY_KEY])  # B6
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00")
    _engine(monkeypatch, {"worker-0": _layout(tl), "worker-1": _layout(tl)})
    with pytest.raises(_Captured):
        _render(tmp_path / "a", tl, local={CLIP_KEY: clip})
    made = _engine(monkeypatch, {"worker-0": _layout(tl), "worker-1": _layout(tl)})
    with pytest.raises(MotionError) as ei:
        _render(tmp_path / "b", tl)  # the scene's own clip is missing
    assert ei.value.code == "asset_missing" and CLIP_KEY[:20] in str(ei.value) and not made


# --- a failed chunk leaves no media frames behind (§7.7) ----------------------------------------------------------

PLAN = [{"key": "v-cccccccc-320x180", "asset_key": CLIP_KEY, "kind": "video", "srcFps": 30, "frames": [0, 1, 2],
         "size": [320, 180]}]


class _PlanSession:
    """A worker whose page plans one clip for the chunk."""

    def __init__(self) -> None:
        self.frames = FrameRegistry()

    async def media_plan(self, f0: int, f1: int) -> list[dict[str, Any]]:
        return PLAN


def _run(tmp: Path) -> engine_mod._Run:
    settings = Settings(_env_file=None)
    tl = dynamic_timeline([2.0], size=SIZE, narration=False, media_scene=0)
    return engine_mod._Run(timeline=tl, settings=settings, ctx=None, work=tmp, enc=EncoderSpec.from_settings(
        settings, 30, SIZE), capture_size=SIZE, total=60, keys={0: "k"}, cache=None,
        files=engine_mod._Files(None, settings, tmp / "inputs", {}), cfg=MotionConfig(), queue=[],
        media_allowed=frozenset({CLIP_KEY}))


@pytest.mark.parametrize("fails", ["extraction", "encoder_start"])
async def test_a_failed_chunk_leaves_no_media_frames(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                     fails: str) -> None:
    """The cleanup (the worker's frame registry, the chunk's media directory) covered only the capture loop: frames
    extracted before a failed extraction or a failed encoder start stayed on disk, and served, until the attempt
    directory was removed."""

    async def extract(plan: list[Any], files: Any, directory: Path, **kwargs: Any) -> Any:
        target = directory / plan[0].slug
        target.mkdir(parents=True)
        frame = target / "f_000000.jpg"
        frame.write_bytes(b"\xff\xd8\xff")
        if fails == "extraction":
            raise MotionError("a later run of frames is unreadable", code="media_unreadable")
        return {(plan[0].key, 0): frame}, 1, []

    async def no_encoder(self: Any) -> None:
        raise FFmpegError("ffmpeg not found")

    monkeypatch.setattr(engine_mod, "_extract_media", extract)
    monkeypatch.setattr(engine_mod._PipeEncoder, "start", no_encoder)
    run, session = _run(tmp_path), _PlanSession()
    with pytest.raises(MotionError if fails == "extraction" else FFmpegError):
        await engine_mod._capture_chunk(run, session, Chunk(0, 0, 60, (0,)), 0)  # type: ignore[arg-type]
    assert not (tmp_path / "media" / "chunk_0000").exists()
    assert session.frames.get(PLAN[0]["key"], 0) is None  # nothing left to serve
    assert run.done_frames == 0


# --- a remedy capture holds a CPU slot (§7.6, §7.12) --------------------------------------------------------------


def _assembly(tmp: Path, monkeypatch: pytest.MonkeyPatch, slots: CpuSlots | None, ctx: SimpleCtx,
              seen: list[Any]) -> tuple[engine_mod._Assembly, list[_Session]]:
    """The render's join + remedies after the capture, with stand-in captures and mux (``seen`` records the slots
    free at each step)."""
    tl = dynamic_timeline([2.0, 2.0], size=SIZE, narration=False)
    bounds = scene_frame_bounds(tl)
    run = SimpleNamespace(ctx=ctx, cache=None, keys={0: "a", 1: "b"}, done_frames=120, stats={},
                          capture_size=SIZE, timeline=tl, total=120, settings=Settings(_env_file=None), work=tmp)

    def free() -> int | None:
        return slots.free() if slots is not None else None

    async def capture(run_: Any, session: Any, chunk: Chunk, worker: int) -> engine_mod.ChunkStat:
        seen.append(("capture", chunk.index, free()))
        return engine_mod.ChunkStat(chunk.index, chunk.f0, chunk.f1, worker)

    async def mux(*args: Any, **kwargs: Any) -> None:
        seen.append(("mux", free()))

    monkeypatch.setattr(engine_mod, "_capture_chunk", capture)
    monkeypatch.setattr(engine_mod, "_run_cmd", mux)
    opened: list[_Session] = []

    def reopen() -> _Session:
        opened.append(_Session(_layout(tl), on_start=lambda: seen.append(("start", free()))))
        return opened[-1]

    chunks = [Chunk(0, 0, 60, (0,)), Chunk(1, 60, 120, (1,))]
    return engine_mod._Assembly(run=run, chunks=chunks, bounds=bounds, script_flag="", master=None,  # type: ignore[arg-type]
                                mux_args=[], side={}, reopen=reopen, slots=slots), opened


async def test_a_remedy_capture_holds_a_cpu_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``rerender_chunks`` launches a Chromium and an encoder after the capture released its slots: it takes one of
    the host's slots for them and gives it back once its browser is closed (before the mux)."""
    slots, seen = CpuSlots(tmp_path / "slots", 3), []
    assembly, opened = _assembly(tmp_path, monkeypatch, slots, SimpleCtx(), seen)
    await assembly.rerender_chunks([1])
    assert seen == [("start", 2), ("capture", 1, 2), ("mux", 3)]
    assert opened[0].closed and slots.free() == 3 and assembly.applied == ["rerender_chunks"]


SLOT_WAIT = "Waiting for a free render slot on this server"


def _slot_waits(ctx: SimpleCtx) -> int:
    """How many times the job reported that a remedy waits for a CPU slot (``CpuSlots.acquire`` calls ``on_wait``
    once per acquire, so each waiting remedy adds one)."""
    return sum(e["message"] == SLOT_WAIT for e in ctx.events)


async def _until(cond: Callable[[], bool], task: asyncio.Future[Any], what: str) -> None:
    """Poll ``cond`` every 0.05 s, for at most 10 s (the bound of the ``wait_for`` calls), instead of sleeping a fixed
    time: ``on_wait`` fires only after the first ``try_acquire`` came back empty, and that call runs in a worker thread
    (``asyncio.to_thread``: pool start plus a lock-file open), which a loaded runner can delay past any fixed sleep.
    Returns early when ``task`` ended, so that the caller's ``not task.done()`` reports it at once."""
    for _ in range(200):
        if cond() or task.done():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"no {what} within 10 s")


async def test_a_remedy_waits_for_a_free_slot_and_honours_cancel(monkeypatch: pytest.MonkeyPatch,
                                                                 tmp_path: Path) -> None:
    """While another render holds the host's only slot, a remedy reports the wait and opens no browser; it goes on
    once the slot is free, and a cancel during the wait ends it without a browser or a slot (§7.6, §7.12)."""
    slots, seen, ctx = CpuSlots(tmp_path / "slots", 1), [], SimpleCtx()
    assembly, opened = _assembly(tmp_path, monkeypatch, slots, ctx, seen)
    other = slots.try_acquire(1)  # another render holds the host's only slot
    task = asyncio.ensure_future(assembly.rerender_chunks([0]))
    await _until(lambda: _slot_waits(ctx) >= 1, task, "slot-wait message")
    assert not opened and not task.done()  # no browser while every slot is taken
    other.release()
    await asyncio.wait_for(task, 10)
    assert opened and seen[:2] == [("start", 0), ("capture", 0, 0)] and slots.free() == 1

    seen.clear()
    other = slots.try_acquire(1)
    task = asyncio.ensure_future(assembly.rerender_chunks([0]))
    await _until(lambda: _slot_waits(ctx) >= 2, task, "second slot-wait message")
    assert not task.done()  # the remedy is waiting, not merely not started yet
    ctx.cancelled = True  # the teacher cancels while the remedy waits
    with pytest.raises(JobCancelled):
        await asyncio.wait_for(task, 10)
    other.release()
    assert len(opened) == 1 and not seen and slots.free() == 1


async def test_without_slots_a_remedy_takes_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[Any] = []
    assembly, opened = _assembly(tmp_path, monkeypatch, None, SimpleCtx(), seen)  # MotionConfig.use_slots off
    await assembly.rerender_chunks([0, 1])
    assert seen == [("start", None), ("capture", 0, None), ("capture", 1, None), ("mux", None)] and opened[0].closed


async def test_the_remedy_page_is_checked_like_the_workers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[Any] = []
    assembly, _ = _assembly(tmp_path, monkeypatch, CpuSlots(tmp_path / "slots", 1), SimpleCtx(), seen)
    tl = assembly.run.timeline
    assembly.reopen = lambda: _Session(_layout(tl, extra_frames=2))
    with pytest.raises(StageError) as ei:
        await assembly.rerender_chunks([0])
    assert ei.value.code == "stage_contract" and not any(s[0] == "capture" for s in seen)
    assert CpuSlots(tmp_path / "slots", 1).free() == 1  # the slot is given back on failure too
