"""Post-render container checks of a motion render and their remedies (DYNAMIC_VIDEO_DESIGN §7.12): the remedy loop
and the stored QA document without ffmpeg; then real MPEG-TS chunks and MP4 muxes (the §7.5 encoder) for exact frame
counts, the first video PTS, the A/V start, colour tags, the caption track, the sound and loudness."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aadhi.compose.dynamic.container import (
    QA_SCHEMA,
    ChunkFile,
    ContainerCheckFailed,
    ContainerProblem,
    ContainerQA,
    grid_step,
    inspect_container,
    motion_qa,
    record_qa,
    verify_container,
)
from aadhi.compose.dynamic.preflight import PreflightResult
from aadhi.compose.loudness import LoudnessTarget
from aadhi.config import Settings
from aadhi.jobs.base import FatalJobError

from .helpers import encode_chunk, ffmpeg, join_and_mux, jpeg_frames, tools

CLASSIC_KEYS = {"ok", "expected_frames", "frames", "fps", "has_audio", "audible", "on_grid", "black_edges", "color",
                "subtitle_tracks", "problems", "warnings"}  # docs/API.md RenderSummary.options.qa


# --- without ffmpeg ---------------------------------------------------------------------------------------------


def test_grid_step() -> None:
    assert grid_step("1/15360", 30) == 512
    assert grid_step("1/90000", 30) == 3000
    assert grid_step("1/1000", 30) is None  # 33.3 units per frame: no integer grid
    assert grid_step("bad", 30) is None and grid_step("1/0", 30) is None and grid_step("1/30", 0) is None


def _qa(*problems: ContainerProblem) -> ContainerQA:
    return ContainerQA(expected_frames=300, fps=30, frames=300, problems=list(problems))


class FakeRemedies:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    async def rerender_chunks(self, indices):
        self.calls.append(("rerender_chunks", tuple(indices)))

    async def reencode_audio(self):
        self.calls.append(("reencode_audio", ()))

    async def remux(self):
        self.calls.append(("remux", ()))


def _inspector(*results: ContainerQA):
    queue = list(results)

    async def inspect() -> ContainerQA:
        return queue.pop(0)

    return inspect


async def test_verify_container_clean() -> None:
    fix = FakeRemedies()
    qa = await verify_container(_inspector(_qa()), fix)
    assert qa.ok and qa.remedies == [] and fix.calls == []


async def test_verify_container_applies_each_remedy_once_in_order() -> None:
    chunks = ContainerProblem("chunk_frames", "chunk 2: 99 of 100", "rerender_chunks", (2,))
    chunks5 = ContainerProblem("chunk_frames", "chunk 5: 0 of 100", "rerender_chunks", (5, 2))
    start = ContainerProblem("video_start", "first frame at 0.0667 s", "remux")
    audio = ContainerProblem("loudness", "-20 LUFS", "reencode_audio")
    fix = FakeRemedies()
    lines: list[str] = []
    qa = await verify_container(_inspector(_qa(chunks, chunks5, start, audio), _qa(start, audio), _qa(start), _qa()),
                                fix, log_line=lines.append)
    assert fix.calls == [("rerender_chunks", (2, 5)), ("reencode_audio", ()), ("remux", ())]
    assert [r["remedy"] for r in qa.remedies] == ["rerender_chunks", "reencode_audio", "remux"]
    assert qa.remedies[0]["chunks"] == [2, 5] and qa.ok and len(lines) == 3


async def test_verify_container_skips_remedies_an_earlier_one_made_unnecessary() -> None:
    chunks = ContainerProblem("chunk_frames", "chunk 0", "rerender_chunks", (0,))
    start = ContainerProblem("video_start", "late", "remux")
    fix = FakeRemedies()
    qa = await verify_container(_inspector(_qa(chunks, start), _qa()), fix)  # the re-render re-muxed too
    assert fix.calls == [("rerender_chunks", (0,))] and qa.ok


async def test_a_problem_that_survives_its_remedy_is_an_engine_bug() -> None:
    start = ContainerProblem("video_start", "the first video frame is shown at 0.0667 s, not at 0", "remux")
    fix = FakeRemedies()
    with pytest.raises(ContainerCheckFailed) as ei:
        await verify_container(_inspector(_qa(start), _qa(start)), fix)
    assert isinstance(ei.value, FatalJobError) and ei.value.code == "render_invalid"
    assert "0.0667" in str(ei.value) and fix.calls == [("remux", ())]
    assert ei.value.qa.remedies == [{"remedy": "remux", "problems": [start.message]}]


def test_motion_qa_document() -> None:
    qa = ContainerQA(expected_frames=300, fps=30, frames=300, has_video=True, has_audio=True, video_start=0.0,
                     raw_video_start=0.0, audio_start=0.01, declared_cfr=True, on_grid=True, audible=True,
                     color={"color_space": "bt709"}, warnings=["black bars at the top edge(s)"],
                     remedies=[{"remedy": "remux", "problems": ["x"]}],
                     chunks=[{"index": 0, "expected": 300, "frames": 300}])
    doc = motion_qa(qa)
    assert CLASSIC_KEYS <= set(doc) and doc["ok"] is True and doc["engine"] == "motion" and doc["schema"] == QA_SCHEMA
    assert doc["starts"] == {"video": 0.0, "video_raw": 0.0, "audio": 0.01, "av_offset": 0.01}
    assert doc["constant_frame_rate"] is True and doc["remedies"][0]["remedy"] == "remux" and "preflight" not in doc
    pre = PreflightResult(format="landscape", frames=300, fps=30)
    with_pre = motion_qa(qa, pre)
    assert with_pre["preflight"]["schema"] == 1 and with_pre["ok"] is True
    blocked = pre.summary() | {"ok": False, "problems": ["P (Shorten text)"], "warnings": ["W"]}
    merged = motion_qa(qa, blocked)
    assert merged["ok"] is False and merged["problems"] == ["P (Shorten text)"]
    assert merged["warnings"] == ["W", "black bars at the top edge(s)"]
    assert motion_qa(None, pre)["ok"] is True and motion_qa(None)["engine"] == "motion"
    bad = ContainerQA(expected_frames=300, fps=30, problems=[ContainerProblem("audio", "no sound", "reencode_audio")])
    assert motion_qa(bad)["ok"] is False and motion_qa(bad)["problems"] == ["no sound"]


def test_record_qa_merges_into_the_render_options(job_ctx) -> None:
    from aadhi.models import Project, ProjectVersion, Render, User

    with job_ctx.session() as db:
        user = User(username="qa-teacher", password_hash="x", role="editor")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="QA", language="en-IN")
        db.add(project)
        db.flush()
        version = ProjectVersion(project_id=project.id, number=1, status="ready", language="en-IN", revision=1)
        db.add(version)
        db.flush()
        render = Render(version_id=version.id, status="running", options={"burn_captions": True, "qa": {"old": 1}})
        db.add(render)
        db.flush()
        rid = render.id
    record_qa(job_ctx, rid, {"ok": True, "engine": "motion"})
    with job_ctx.session() as db:
        assert db.get(Render, rid).options == {"burn_captions": True, "qa": {"ok": True, "engine": "motion"}}


# --- real ffmpeg ------------------------------------------------------------------------------------------------

SIZES = (45, 60, 37)


def _chunks(work: Path, sizes=SIZES, *, short: int | None = None) -> list[ChunkFile]:
    out, start = [], 0
    for n, size in enumerate(sizes):
        made = size - 1 if short == n else size
        path = encode_chunk(jpeg_frames(made, start=start), work / f"chunk_{n:04d}.ts", preset="ultrafast")
        out.append(ChunkFile(n, path, size))
        start += size
    return out


@pytest.fixture()
def ff_settings() -> Settings:
    ffmpeg_path, ffprobe_path = tools()
    return Settings(_env_file=None, ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path)


def _inspect(path: Path, settings: Settings, chunks=(), **kw):
    kw = {"fps": 30, "expected_frames": sum(SIZES), "expect_audio": True, "size": (320, 180)} | kw
    return asyncio.run(inspect_container(path, settings=settings, chunks=chunks, **kw))


@pytest.mark.slow
@pytest.mark.container
def test_a_correct_mux_passes_every_check(tmp_path: Path, ff_settings: Settings) -> None:
    chunks = _chunks(tmp_path)
    out = join_and_mux([c.path for c in chunks], tmp_path / "out.mp4", frames=sum(SIZES))
    qa = _inspect(out, ff_settings, chunks)
    assert qa.ok, [p.message for p in qa.problems]
    assert qa.frames == sum(SIZES) and qa.chunks == [{"index": n, "expected": s, "frames": s}
                                                     for n, s in enumerate(SIZES)]
    assert qa.video_start == pytest.approx(0.0, abs=1e-6) and qa.raw_video_start == pytest.approx(0.0, abs=1e-6)
    assert qa.av_offset is not None and abs(qa.av_offset) < 1 / 30
    assert qa.color == {"color_space": "bt709", "color_primaries": "bt709", "color_transfer": "bt709",
                        "color_range": "tv"}
    assert qa.declared_cfr and qa.on_grid and qa.audible and qa.black_edges == []
    doc = motion_qa(qa)
    assert doc["ok"] and doc["starts"]["video"] == 0.0 and doc["constant_frame_rate"]


@pytest.mark.slow
@pytest.mark.container
def test_without_negative_cts_offsets_the_start_is_late_and_a_remux_fixes_it(tmp_path: Path,
                                                                             ff_settings: Settings) -> None:
    chunks = _chunks(tmp_path)
    out = join_and_mux([c.path for c in chunks], tmp_path / "out.mp4", frames=sum(SIZES), negative_cts=False)
    qa = _inspect(out, ff_settings, chunks)
    assert [(p.check, p.remedy) for p in qa.problems] == [("video_start", "remux")]
    assert qa.video_start == pytest.approx(0.0, abs=1e-3)  # the edit list hides it from most players ...
    assert qa.raw_video_start > 0.5 / 30  # ... but not from those that ignore it (2 frames late with -bf 2)

    class Remux(FakeRemedies):
        async def remux(self):
            await super().remux()
            await asyncio.to_thread(join_and_mux, [c.path for c in chunks], out, frames=sum(SIZES))

    fix = Remux()
    final = asyncio.run(verify_container(lambda: inspect_container(
        out, settings=ff_settings, fps=30, expected_frames=sum(SIZES), expect_audio=True, chunks=chunks), fix))
    assert final.ok and fix.calls == [("remux", ())] and final.raw_video_start == pytest.approx(0.0, abs=1e-6)


@pytest.mark.slow
@pytest.mark.container
def test_a_short_chunk_is_rendered_again(tmp_path: Path, ff_settings: Settings) -> None:
    chunks = _chunks(tmp_path, short=1)
    out = join_and_mux([c.path for c in chunks], tmp_path / "out.mp4", frames=sum(SIZES))
    qa = _inspect(out, ff_settings, chunks)
    assert qa.problems[0].check == "chunk_frames" and qa.problems[0].chunks == (1,)
    assert qa.chunks[1] == {"index": 1, "expected": 60, "frames": 59} and qa.frames == sum(SIZES) - 1
    assert not any(p.check == "frame_count" for p in qa.problems)  # the chunk is the cause: no separate remux

    class Rerender(FakeRemedies):
        async def rerender_chunks(self, indices):
            await super().rerender_chunks(indices)
            for i in indices:
                start = sum(SIZES[:i])
                await asyncio.to_thread(encode_chunk, jpeg_frames(SIZES[i], start=start), chunks[i].path,
                                        preset="ultrafast")
            await asyncio.to_thread(join_and_mux, [c.path for c in chunks], out, frames=sum(SIZES))

    fix = Rerender()
    final = asyncio.run(verify_container(lambda: inspect_container(
        out, settings=ff_settings, fps=30, expected_frames=sum(SIZES), expect_audio=True, chunks=chunks), fix))
    assert final.ok and fix.calls == [("rerender_chunks", (1,))] and final.frames == sum(SIZES)


@pytest.mark.slow
@pytest.mark.container
def test_late_audio_wrong_tags_missing_tracks(tmp_path: Path, ff_settings: Settings) -> None:
    chunks = _chunks(tmp_path)
    paths = [c.path for c in chunks]
    late = join_and_mux(paths, tmp_path / "late.mp4", frames=sum(SIZES), audio_delay=0.1)
    qa = _inspect(late, ff_settings)
    assert [(p.check, p.remedy) for p in qa.problems] == [("av_start", "remux")]
    assert qa.av_offset == pytest.approx(0.1, abs=0.03)

    plain, start = [], 0
    for n, size in enumerate(SIZES):  # converted without the BT.709 setparams: an untagged stream
        plain.append(encode_chunk(jpeg_frames(size, start=start), tmp_path / f"plain_{n}.ts", preset="ultrafast",
                                  vf="format=yuv420p"))
        start += size
    untagged = join_and_mux(plain, tmp_path / "untagged.mp4", frames=sum(SIZES))
    qa = _inspect(untagged, ff_settings)
    assert [(p.check, p.remedy) for p in qa.problems] == [("colour_tags", "remux")]
    assert "color_range=pc" in qa.problems[0].message  # the JPEGs' full-range BT.601 leaked through

    silent = join_and_mux(paths, tmp_path / "silent.mp4", frames=sum(SIZES), audio=False)
    qa = _inspect(silent, ff_settings, expect_subtitles=True)
    assert {(p.check, p.remedy) for p in qa.problems} == {("audio", "reencode_audio"), ("caption_track", "remux")}
    assert _inspect(silent, ff_settings, expect_audio=False).ok

    wrong_count = _inspect(join_and_mux(paths, tmp_path / "ok.mp4", frames=sum(SIZES)), ff_settings,
                           expected_frames=sum(SIZES) + 3)
    assert [(p.check, p.remedy) for p in wrong_count.problems] == [("frame_count", "remux")]


@pytest.mark.slow
@pytest.mark.container
def test_loudness_target(tmp_path: Path, ff_settings: Settings) -> None:
    chunks = _chunks(tmp_path)
    out = join_and_mux([c.path for c in chunks], tmp_path / "out.mp4", frames=sum(SIZES))
    measured = _inspect(out, ff_settings, loudness=LoudnessTarget(-16.0, -1.5, 9.0))
    level = measured.loudness["integrated_lufs"]
    assert level is not None and measured.audible
    near = _inspect(out, ff_settings, loudness=LoudnessTarget(round(level, 1) + 0.5, -1.5, 9.0))
    assert near.ok, [p.message for p in near.problems]
    far = _inspect(out, ff_settings, loudness=LoudnessTarget(max(-70.0, level - 6.0), -1.5, 9.0))
    assert [(p.check, p.remedy) for p in far.problems] == [("loudness", "reencode_audio")]
    loud = tmp_path / "loud.mp4"
    ffmpeg("-i", str(out), "-c:v", "copy", "-af", "volume=24dB", "-c:a", "aac", "-movflags",
           "+faststart+negative_cts_offsets", str(loud))
    hot = _inspect(loud, ff_settings, loudness=LoudnessTarget(-16.0, -1.5, 9.0))
    assert ("true_peak", "reencode_audio") in [(p.check, p.remedy) for p in hot.problems]
