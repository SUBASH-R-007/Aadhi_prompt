"""Scene frame ranges, the chunk planner and the chunk cache key (DYNAMIC_VIDEO_DESIGN §7.6)."""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from aadhi.compose.motion.chunks import (
    CHUNK_CACHE_VERSION,
    CHUNK_MAX_FRAMES,
    CHUNK_MIN_FRAMES,
    Chunk,
    assignment_order,
    background_clock,
    bundle_digest,
    chunk_key,
    chunk_slices,
    media_keys,
    plan_chunks,
    scene_frame_bounds,
    total_frames,
)
from aadhi.compose.video import segment_frames

from .fixtures import CLIP_KEY, dynamic_timeline


def _bounds(lengths: list[int]) -> list[tuple[int, int]]:
    out, t = [], 0
    for n in lengths:
        out.append((t, t + n))
        t += n
    return out


def _check_cover(chunks: list[Chunk], bounds: list[tuple[int, int]]) -> None:
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].f0 == 0 and chunks[-1].f1 == bounds[-1][1]
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.f1 == b.f0  # every frame exactly once
    for c in chunks:
        assert c.frames >= 1
        assert c.scenes == tuple(k for k, (x, y) in enumerate(bounds) if x < c.f1 and y > c.f0)


def test_scene_frame_bounds_match_the_classic_rounding() -> None:
    tl = dynamic_timeline([3.0, 4.0333, 2.51, 0.02, 5.0])
    bounds = scene_frame_bounds(tl)
    assert bounds == segment_frames(tl, 30)  # the classic per-scene rounding (no intro in a Dynamic timeline)
    assert bounds[0][0] == 0 and total_frames(bounds) == round(tl.total_duration * 30)
    assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:], strict=False))
    assert all(f1 - f0 >= 1 for f0, f1 in bounds)  # a 0.02 s scene still gets a frame


def test_plan_groups_short_scenes_into_scene_aligned_chunks() -> None:
    bounds = _bounds([100, 100, 100, 150, 400, 90])
    chunks = plan_chunks(bounds)
    _check_cover(chunks, bounds)
    # 300 frames close the first chunk; 150 + 400 join (550 <= 600); the 90-frame tail cannot join (640 > 600)
    assert [(c.f0, c.f1) for c in chunks] == [(0, 300), (300, 850), (850, 940)]
    # every boundary is a scene boundary
    starts = {a for a, _ in bounds}
    assert all(c.f0 in starts for c in chunks)


def test_plan_splits_long_scenes_evenly() -> None:
    bounds = _bounds([1500])
    chunks = plan_chunks(bounds)
    _check_cover(chunks, bounds)
    assert [c.frames for c in chunks] == [500, 500, 500]
    assert all(c.scenes == (0,) for c in chunks)
    # a short run before a long scene is absorbed into the split instead of staying a tiny chunk
    bounds = _bounds([50, 1000])
    chunks = plan_chunks(bounds)
    _check_cover(chunks, bounds)
    assert [c.frames for c in chunks] == [525, 525]
    assert chunks[0].scenes == (0, 1)


def test_plan_tail_joins_its_predecessor_when_it_fits() -> None:
    bounds = _bounds([300, 100])
    assert [(c.f0, c.f1) for c in plan_chunks(bounds)] == [(0, 400)]
    bounds = _bounds([550, 100])  # 650 would pass the maximum: the short tail stays alone
    assert [(c.f0, c.f1) for c in plan_chunks(bounds)] == [(0, 550), (550, 650)]
    assert [(c.f0, c.f1) for c in plan_chunks(_bounds([30]))] == [(0, 30)]  # a whole short video


@pytest.mark.parametrize("seed", range(40))
def test_plan_properties_on_random_lessons(seed: int) -> None:
    rnd = random.Random(seed)
    lengths = [rnd.choice([rnd.randint(1, 120), rnd.randint(100, 700), rnd.randint(600, 2500)])
               for _ in range(rnd.randint(1, 30))]
    bounds = _bounds(lengths)
    chunks = plan_chunks(bounds)
    _check_cover(chunks, bounds)
    assert all(c.frames <= CHUNK_MAX_FRAMES for c in chunks)
    total = bounds[-1][1]
    if total >= CHUNK_MIN_FRAMES:
        # only a chunk closed by a scene that could not join it is short, never two short ones in a row
        short = [i for i, c in enumerate(chunks) if c.frames < CHUNK_MIN_FRAMES]
        assert all(b - a > 1 for a, b in zip(short, short[1:], strict=False))
    # a chunk boundary inside a scene only splits a long scene
    starts = {a for a, _ in bounds}
    for c in chunks:
        if c.f0 not in starts:
            scene = next(k for k, (a, b) in enumerate(bounds) if a < c.f0 < b)
            assert bounds[scene][1] - bounds[scene][0] > CHUNK_MAX_FRAMES or c.f0 - bounds[scene][0] <= CHUNK_MAX_FRAMES


def test_plan_rejects_bad_sizes() -> None:
    with pytest.raises(ValueError):
        plan_chunks(_bounds([10]), min_frames=0)
    with pytest.raises(ValueError):
        plan_chunks(_bounds([10]), min_frames=50, max_frames=40)


def test_assignment_order_is_longest_first() -> None:
    chunks = plan_chunks(_bounds([100, 200, 600, 50, 300]), min_frames=10, max_frames=600)
    order = assignment_order(chunks)
    assert [c.frames for c in order] == sorted((c.frames for c in chunks), reverse=True)
    assert sorted(c.index for c in order) == [c.index for c in chunks]


# --- cache keys --------------------------------------------------------------------------------------


def _key(tl, chunk, **kw):
    args = {"bundle": "b1", "encoder": {"crf": 17}, "capture": {"q": 92}, "chromium": "153.0", "media": {}}
    args.update(kw)
    return chunk_key(tl, chunk, **args)


def test_chunk_key_covers_its_scenes_the_outgoing_scene_and_the_setup() -> None:
    tl = dynamic_timeline([3.0, 3.0, 3.0, 3.0], media_scene=2)
    chunks = plan_chunks(scene_frame_bounds(tl), min_frames=90, max_frames=90)
    assert [c.scenes for c in chunks] == [(0,), (1,), (2,), (3,)]
    base = [_key(tl, c) for c in chunks]
    assert len(set(base)) == 4 and all(len(k) == 64 for k in base)
    assert [_key(tl, c) for c in chunks] == base  # stable

    def edited(index: int, **change) -> list[str]:
        data = tl.model_dump(mode="json")
        data["scenes"][index].update(change)
        return [_key(type(tl).model_validate(data), c) for c in chunks]

    keys = edited(1, title="Another title")
    assert keys[0] == base[0] and keys[3] == base[3]  # untouched, not adjacent
    assert keys[1] != base[1]  # its own scene
    assert keys[2] != base[2]  # the next chunk paints scene 1's final state in its transition
    look = tl.scenes[3].look.model_dump(mode="json")
    look["background"]["seed"] = 99
    keys = edited(3, look=look)
    assert keys[:3] == base[:3] and keys[3] != base[3]
    assert _key(tl, chunks[0], bundle="b2") != base[0]
    assert _key(tl, chunks[0], encoder={"crf": 18}) != base[0]
    assert _key(tl, chunks[0], capture={"q": 90}) != base[0]
    assert _key(tl, chunks[0], chromium="154.0") != base[0]
    assert _key(tl, chunks[2], media={CLIP_KEY: "file:x:1:2"}) != base[2]
    assert _key(tl, chunks[0], media={CLIP_KEY: "file:x:1:2"}) == base[0]  # chunk 0 shows no media


def test_chunk_key_covers_presentation_and_captions_in_range() -> None:
    tl = dynamic_timeline([3.0, 3.0, 3.0])
    chunks = plan_chunks(scene_frame_bounds(tl), min_frames=90, max_frames=90)
    base = [_key(tl, c) for c in chunks]
    data = tl.model_dump(mode="json")
    data["presentation"]["caption_style"] = "bold_outline"
    assert all(a != b for a, b in zip(base, [_key(type(tl).model_validate(data), c) for c in chunks], strict=False))
    data = tl.model_dump(mode="json")
    data["captions"][0]["text"] = "Changed"  # the first caption is shown in the first scene only
    keys = [_key(type(tl).model_validate(data), c) for c in chunks]
    assert keys[0] != base[0] and keys[1:] == base[1:]
    slices = chunk_slices(tl, chunks[0])
    assert [s["scene_id"] for s in slices["scenes"]] == ["s0"]
    assert [s["scene_id"] for s in chunk_slices(tl, chunks[1])["scenes"]] == ["s0", "s1"]


def _with_hold(tl, scene: int, frozen_at: float | None):
    data = tl.model_dump(mode="json")
    data["scenes"][scene]["look"]["background"]["frozen_at"] = frozen_at
    return type(tl).model_validate(data)


def test_the_background_clock_mirrors_the_stage() -> None:
    """state.js backgroundTime / frozenIntervals, with web/tests/dynamic/state.test.js's numbers: a hold from 14 s to
    26 s stops the clock there; it is continuous and never runs backwards."""
    data = {"scenes": [{"start": 0.0, "duration": 12.0, "look": {"background": {"frozen_at": None}}},
                       {"start": 12.0, "duration": 14.0, "look": {"background": {"frozen_at": 2.0}}},
                       {"start": 26.0, "duration": 14.0, "look": {"background": {}}}]}
    assert [background_clock(data, t) for t in (10, 14, 20, 26, 30)] == [10, 14, 14, 14, 18]
    steps = [background_clock(data, k / 10) for k in range(400)]
    assert all(0 <= b - a <= 0.1 + 1e-9 for a, b in zip(steps, steps[1:]))
    data["scenes"][1]["look"]["background"]["frozen_at"] = -5  # clamped to the scene start
    assert background_clock(data, 20) == 12
    for at in (float("nan"), "3", True, 99.0):  # not a number, or after the scene's end: no hold
        data["scenes"][1]["look"]["background"]["frozen_at"] = at
        assert background_clock(data, 30) == 30, at
    assert background_clock({"scenes": [{"start": 0, "duration": 1}, "junk"]}, 5) == 5


def test_chunk_key_covers_a_hold_in_an_earlier_scene() -> None:
    """A hold earlier in the lecture moves the background of every later chunk (its clock), though those chunks show
    neither that scene nor its transition (plan B2 §2 "Chunk key")."""
    tl = dynamic_timeline([3.0, 3.0, 3.0, 3.0])
    chunks = plan_chunks(scene_frame_bounds(tl), min_frames=90, max_frames=90)
    assert [c.scenes for c in chunks] == [(0,), (1,), (2,), (3,)]
    base = [_key(tl, c) for c in chunks]
    held = _with_hold(tl, 0, 1.0)
    assert chunk_slices(held, chunks[2])["background_clock"] == pytest.approx(6.0 - 2.0)  # frozen from 1 s to 3 s
    assert [s["scene_id"] for s in chunk_slices(held, chunks[3])["scenes"]] == ["s2", "s3"]  # scene 0 is not in them
    keys = [_key(held, c) for c in chunks]
    assert all(a != b for a, b in zip(keys, base, strict=True)), "every chunk: its own scene or its clock changed"
    later = _with_hold(tl, 2, 0.5)  # a hold in scene 2: chunks 0 and 1 keep their keys
    assert [_key(later, c) for c in chunks][:2] == base[:2]
    assert CHUNK_CACHE_VERSION == 3


def test_chunk_key_covers_the_chapter_title_drawn_as_the_kicker() -> None:
    """``board_generic`` draws the title of the chapter a scene is in above its title; renaming a chapter changes
    the chunks that show its scenes (and the next one, which paints the last scene's outgoing state), no other."""
    tl = dynamic_timeline([3.0, 3.0, 3.0, 3.0, 3.0])
    data = tl.model_dump(mode="json")
    data["chapters"] = [{"start": 0.0, "title": "Start"}, {"start": 3.0, "title": "Middle"},
                        {"start": 9.0, "title": "End"}]
    tl = type(tl).model_validate(data)  # "Middle" covers scenes 1 and 2
    chunks = plan_chunks(scene_frame_bounds(tl), min_frames=90, max_frames=90)
    assert [c.scenes for c in chunks] == [(0,), (1,), (2,), (3,), (4,)]
    base = [_key(tl, c) for c in chunks]
    assert chunk_slices(tl, chunks[2])["chapters"] == ["Middle", "Middle"]  # scene 2 and its outgoing scene 1
    data["chapters"][1]["title"] = "The middle part"
    renamed = [_key(type(tl).model_validate(data), c) for c in chunks]
    assert renamed[0] == base[0] and renamed[4] == base[4]
    assert renamed[1] != base[1] and renamed[2] != base[2] and renamed[3] != base[3]


def test_every_timeline_field_the_stage_reads_is_in_the_key() -> None:
    """A top-level timeline field the stage reads but the key does not hash (as ``chapters`` once was) would let a
    retry reuse chunks drawn from the old value."""
    import re

    from aadhi.compose.motion.chunks import STAGE_TIMELINE_FIELDS
    from aadhi.config import ROOT_DIR

    read: set[str] = set()
    for path in (ROOT_DIR / "web" / "js" / "dynamic").rglob("*.js"):
        read |= set(re.findall(r"\btimeline\.([A-Za-z_]+)", path.read_text(encoding="utf-8")))
    assert read and "chapters" in read
    assert read <= set(STAGE_TIMELINE_FIELDS), sorted(read - set(STAGE_TIMELINE_FIELDS))
    tl = dynamic_timeline([3.0, 3.0])
    slices = chunk_slices(tl, plan_chunks(scene_frame_bounds(tl), min_frames=60, max_frames=60)[0])
    hashed = {*slices["global"], "scenes", "captions", "chapters"}
    assert set(STAGE_TIMELINE_FIELDS) <= hashed


def test_media_keys_found_in_slices() -> None:
    tl = dynamic_timeline([3.0, 3.0], media_scene=1)
    chunk = plan_chunks(scene_frame_bounds(tl), min_frames=90, max_frames=90)[1]
    keys = media_keys(chunk_slices(tl, chunk))
    assert keys == [CLIP_KEY]  # narration (audio_asset_key) never changes pixels; the scene slice still names it
    assert media_keys({"scenes": [{"look": {"presenter": {"clip_key": "aadhi_left_clean.mp4"}}}]}) == [
        "aadhi_left_clean.mp4"]


def test_bundle_digest_follows_the_stage_files(tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "js" / "dynamic").mkdir(parents=True)
    (web / "tests").mkdir()
    (web / "js" / "dynamic" / "stage.js").write_text("export {};\n", encoding="utf-8")
    (web / "render-dynamic.html").write_text("<!doctype html>", encoding="utf-8")
    first = bundle_digest(web)
    assert bundle_digest(web) == first
    (web / "tests" / "x.test.js").write_text("// not part of the page", encoding="utf-8")
    assert bundle_digest(web) == first
    path = web / "js" / "dynamic" / "stage.js"
    path.write_text("export const x = 1;\n", encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert bundle_digest(web) != first
