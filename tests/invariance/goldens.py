"""Platform-independent goldens of the classic (Aadhi presenter) mode, part 1 of the invariance harness
(docs/DYNAMIC_VIDEO_DESIGN.md section 3.6).

Per fixture (``classic_fixture.FIXTURES``), in ``goldens/<fixture>/``:

* ``screenplay.json``: the stored screenplay (``model_dump(mode="json")``), byte for byte: a new field that is not
  left out at its default shows up here;
* ``scene_hashes.json``: every visible scene's ``scene_hash`` (asset staleness and cache keys);
* ``timeline.json`` / ``preview_timeline.json``: ``build_timeline`` and ``preview_timeline`` with the synthetic
  manifest and the code-default settings;
* ``segments.json``: every ffmpeg command line of the MP4 render (the intro and scene segments and the final mux)
  with their filter graphs and side files, made by the real ``render_video`` job with Chromium and ffmpeg
  replaced: the screenshot step returns the recorded ``capture.json`` and the commands are recorded, not run.
  Paths are replaced by ``<work>``, ``<branding>`` and ``<asset:KEY>``, and cross-fade PNG digests by the state
  ids they mix, so the document is the same on every machine;
* ``segments_burn.json`` / ``segments_soft.json``: the same render with the classic render dialog's "Burn captions"
  (libass filter, re-encode) and with the soft ``mov_text`` caption track (separate variants: a burned render drops
  the soft track);
* ``capture.json``: what the render page reported (states, keys, rects, notices) in a real capture, recorded by
  ``render_classic.py --capture-only`` (checked against a live capture by the slow test).

Nothing here needs ffmpeg, ffprobe or Chromium: probes of the branding clips are pinned (``BRANDING_PROBES``;
the slow test checks them against the real files) and ffmpeg is taken to be Debian's 5.1.
Regenerate after an approved classic change with ``AADHI_UPDATE_GOLDENS=1`` (see README.md).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from . import classic_fixture as fx

UPDATE_ENV = "AADHI_UPDATE_GOLDENS"
DOCUMENTS = ("screenplay", "scene_hashes", "timeline", "preview_timeline", "segments")
FFMPEG_VERSION = (5, 1)  # Debian bookworm's ffmpeg (the production image): picks the filter-script flag
RENDER_TOKEN = "invariance-render-token"
# ffprobe facts of the branding clips (all 1280x720 H.264 + AAC at 24 fps, untagged colour). The slow test
# ``test_branding_probes_match_the_files`` keeps them honest.
BRANDING_PROBES: dict[str, dict[str, Any]] = {
    "logo_animation": {"duration": 10.0},
    "logo_animation_clean": {"duration": 10.0},
    "aadhi_left": {"duration": 8.0}, "aadhi_left_clean": {"duration": 8.0},
    "aadhi_right": {"duration": 8.0}, "aadhi_right_clean": {"duration": 8.0},
    "aadhi_center": {"duration": 8.0}, "aadhi_center_clean": {"duration": 8.0},
    "aadhi_popup": {"duration": 8.0}, "aadhi_popup_clean": {"duration": 8.0},
    "no_aadhi": {"duration": 8.0}, "no_aadhi_clean": {"duration": 8.0},
}
_MIX = re.compile(r"mix-[0-9a-f]{16}\.png")


def updating() -> bool:
    return os.environ.get(UPDATE_ENV, "").strip().lower() in ("1", "true", "yes")


def golden_path(fixture: str, doc: str) -> Path:
    return fx.GOLDEN_DIR / fixture / f"{doc}.json"


def dumps(obj: Any) -> str:
    """Stable text of a golden: key order kept (it is part of the stored JSON), one value per line."""
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def read_capture(fixture: str) -> dict[str, Any]:
    return json.loads(golden_path(fixture, "capture").read_text(encoding="utf-8"))


# --- documents ---------------------------------------------------------------------------------------------------


def settings_for_goldens(**overrides: Any) -> Any:
    return fx.pinned_settings(**overrides)


def build_documents(fixture: str) -> dict[str, Any]:
    """Every golden of ``fixture`` except ``segments`` (pure: no I/O besides reading the fixture)."""
    from aadhi.compose.timeline import build_timeline, preview_timeline

    settings = settings_for_goldens()
    sp = fx.load_screenplay(fixture)
    manifest = fx.synthetic_manifest(sp, fixture=fixture)
    return {
        "screenplay": sp.model_dump(mode="json"),
        "scene_hashes": fx.scene_hashes(sp),
        "timeline": build_timeline(sp, manifest, settings=settings, version_id=1, revision=1,
                                   include_intro=True).model_dump(mode="json"),
        "preview_timeline": preview_timeline(sp, manifest, settings=settings, version_id=1,
                                             include_intro=True).model_dump(mode="json"),
    }


# --- segment command lines ---------------------------------------------------------------------------------------


class _Normalizer:
    """Replaces machine-specific paths and digests in recorded command lines."""

    def __init__(self) -> None:
        self.prefixes: list[tuple[str, str]] = []
        self.mixes: dict[str, str] = {}  # mix file name -> "mix(<old>><new>@<weight>)"

    def add(self, path: Path | str, token: str) -> None:
        p = Path(path)
        for variant in {str(p), p.as_posix(), str(p.resolve()), p.resolve().as_posix()}:
            self.prefixes.append((variant, token))
        self.prefixes.sort(key=lambda kv: -len(kv[0]))

    def __call__(self, text: str) -> str:
        for prefix, token in self.prefixes:
            if prefix in text:
                text = text.replace(prefix, token)
        text = re.sub(r"<(work|branding|asset:[^>]+)>[^\s'\"]*",
                      lambda m: m.group(0).replace("\\", "/"), text)
        return _MIX.sub(lambda m: self.mixes.get(m.group(0), m.group(0)), text)


def _probe_for(path: Path, durations: dict[str, float], total: float) -> Any:
    from aadhi.compose.ffmpeg import MediaProbe

    stem = path.stem
    if stem in BRANDING_PROBES:
        return MediaProbe(duration=BRANDING_PROBES[stem]["duration"], width=1280, height=720, fps=24.0,
                          video_codec="h264", audio_codec="aac", has_video=True, has_audio=True,
                          format_name="mov,mp4,m4a,3gp,3g2,mj2", pix_fmt="yuv420p")
    if path.name == "output.mp4":
        return MediaProbe(duration=total, width=None, height=None, has_video=True, has_audio=True,
                          video_codec="h264", audio_codec="aac", format_name="mov,mp4,m4a,3gp,3g2,mj2")
    for key, seconds in durations.items():
        if key in str(path):
            return MediaProbe(duration=seconds, width=fx.VIDEO_SIZE[0], height=fx.VIDEO_SIZE[1], fps=30.0,
                              video_codec="h264", has_video=True, format_name="mov,mp4,m4a,3gp,3g2,mj2",
                              pix_fmt="yuv420p")
    raise AssertionError(f"unexpected probe of {path.name}")


def seed_version(settings: Any, store: Any, fixture: str) -> dict[str, Any]:
    """Project version + queued render of ``fixture`` with placeholder asset files (never decoded)."""
    from aadhi.compose.timeline import build_timeline
    from aadhi.db import session_scope
    from aadhi.models import Project, ProjectVersion, Render, User
    from aadhi.storage.assets import Produced

    sp = fx.load_screenplay(fixture)
    manifest = fx.synthetic_manifest(sp, fixture=fixture)
    keys: dict[str, str] = {}
    for sa in manifest.audio.values():
        if sa.asset_key:
            keys[sa.asset_key] = sa.mime
    for item in fx.media_plan(sp, fixture):
        if item.key:
            keys[item.key] = item.mime
    plan_kinds = {m.key: m.asset_kind for m in fx.media_plan(sp, fixture) if m.key}
    for key, mime in sorted(keys.items()):
        store.put(key, plan_kinds.get(key, "scene_audio"),
                  Produced(data=f"placeholder {key}".encode(), mime=mime))
    with session_scope() as db:
        user = User(username="golden-teacher", password_hash="x", role="editor")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title=sp.session_title or "Golden", subject_name=sp.subject_name)
        db.add(project)
        db.flush()
        version = ProjectVersion(project_id=project.id, number=1, status="ready", revision=1, built_revision=1)
        version.set_screenplay(sp)
        version.set_manifest(manifest)
        version.set_timeline(build_timeline(sp, manifest, settings=settings, version_id=None, revision=1))
        db.add(version)
        db.flush()
        render = Render(version_id=version.id, status="queued", options={"burn_captions": False})
        db.add(render)
        db.flush()
        return {"version_id": version.id, "project_id": project.id, "render_id": render.id, "screenplay": sp,
                "manifest": manifest, "asset_keys": sorted(keys)}


def capture_result(doc: dict[str, Any], out_dir: Path) -> Any:
    from aadhi.compose.screenshot import CaptureResult, IntroShot, StateShot

    result = CaptureResult()
    for index, states in doc["scenes"].items():
        result.scenes[int(index)] = [
            StateShot(float(s["t"]), s["key"], out_dir / s["png"], s.get("media_rect"), s.get("panel_media_rect"),
                      s.get("media_fit"), tuple(s.get("notices") or ())) for s in states]
    result.intro = [IntroShot(float(s["t"]), out_dir / s["png"]) for s in doc.get("intro", [])]
    result.blocked_media = list(doc.get("blocked_media", []))
    result.websocket_attempts = list(doc.get("websocket_attempts", []))
    return result


def segments_document(fixture: str, *, ctx_factory: Any, store: Any, settings: Any, patch: Any,
                      burn_captions: bool = False, soft_subtitles: bool = False) -> dict[str, Any]:
    """Run ``render_video`` for ``fixture`` with the browser and ffmpeg replaced; return the commands.

    ``ctx_factory(settings=..., version_id=..., project_id=..., payload=...)`` makes the job context, ``patch(obj,
    name, value)`` replaces a module attribute for the test (``monkeypatch.setattr``). ``burn_captions`` /
    ``soft_subtitles``: the render dialog's caption options (the defaults give ``segments.json``'s payload).
    """
    from aadhi.compose import video as video_mod
    from aadhi.compose.capabilities import RenderCapabilities
    from aadhi.compose.sounds import ensure_sounds

    rows = seed_version(settings, store, fixture)
    sounds = ensure_sounds(store)  # stored now (the job finds them) so their paths can be normalised
    capture_doc = read_capture(fixture)
    normalize = _Normalizer()
    normalize.add(Path(settings.branding_dir), "<branding>")
    for key in [*rows["asset_keys"], *(snd.asset_key for snd in sounds.values())]:
        asset = store.get(key)
        local = store.storage.local_path(asset.storage_key)
        if local is not None:
            normalize.add(local, f"<asset:{key}>")
    durations = {m.key: float(m.duration) for m in fx.media_plan(rows["screenplay"], fixture)
                 if m.key and m.kind == "video" and m.duration}
    commands: list[dict[str, Any]] = []
    side_files: dict[str, str] = {}
    state: dict[str, Any] = {"total": 0.0, "work": None}

    async def fake_capture(*_: Any, **kwargs: Any) -> Any:
        tl = kwargs["timeline"]
        if len(capture_doc["scenes"]) != len(tl.scenes):
            raise AssertionError(f"{fixture}: capture.json has {len(capture_doc['scenes'])} scenes, the timeline "
                                 f"{len(tl.scenes)}: record it again (README.md)")
        state["total"] = tl.total_duration
        return capture_result(capture_doc, Path(kwargs["out_dir"]))

    # The replacements accept extra arguments: the classic job's internals may grow parameters (a format-aware
    # stage size, say) without the recorded commands changing.
    async def fake_version(*_: Any, **__: Any) -> tuple[int, int]:
        return FFMPEG_VERSION

    async def fake_probe(path: Any, *_: Any, **__: Any) -> Any:
        return _probe_for(Path(path), durations, float(state["total"]))

    def record_mixes(mixes: Any, *_: Any, **__: Any) -> int:
        for m in mixes:
            old, new = Path(m.old).name, Path(m.new).name
            normalize.mixes[Path(m.out).name] = f"mix({old}>{new}@{m.weight:.4f}).png"
        return 0

    async def fake_run(cmd: Any, ctx: Any, work: Path, *_: Any, **__: Any) -> None:
        if state["work"] is None:
            state["work"] = work
            normalize.add(work, "<work>")
        name = next((k[: -len(".filtergraph")] for k in cmd.files if k.endswith(".filtergraph")), "?")
        commands.append({"name": name, "args": list(cmd.args), "files": dict(cmd.files)})
        if "output.mp4" in cmd.args:
            (work / "output.mp4").write_bytes(b"placeholder mp4")

    real_write_files = video_mod._write_files

    async def recording_write_files(work: Path, files: dict[str, str], *args: Any, **kwargs: Any) -> None:
        side_files.update(files)
        await real_write_files(work, files, *args, **kwargs)

    patch(video_mod, "_mint_token", lambda *a, **k: RENDER_TOKEN)
    patch(video_mod, "capture_render_frames", fake_capture)
    patch(video_mod, "render_capabilities", lambda *a, **k: RenderCapabilities(ok=True, burn_captions=True))
    patch(video_mod, "ffmpeg_version", fake_version)
    patch(video_mod, "probe", fake_probe)
    patch(video_mod, "_prepare_frames", lambda *_, **__: 0)
    patch(video_mod, "write_state_mixes", record_mixes)
    patch(video_mod, "_run", fake_run)
    patch(video_mod, "_write_files", recording_write_files)
    # Burned captions use the vendored fonts (web/vendor/fonts, there after `npm ci` and in the image): taken as
    # present, so the burn variant does not depend on the checkout having run `npm ci`.
    patch(video_mod, "_copy_fonts", lambda _settings, dest, *_, **__: dest)
    payload: dict[str, Any] = {"render_id": rows["render_id"], "burn_captions": burn_captions, "include_intro": True}
    if soft_subtitles:
        payload["soft_subtitles"] = True
    ctx = ctx_factory(settings=settings, version_id=rows["version_id"], project_id=rows["project_id"],
                      payload=payload)
    asyncio.run(video_mod.render_video(ctx))

    def order(c: dict[str, Any]) -> tuple[int, str]:
        return (0 if c["name"] == "intro" else 2 if c["name"] == "final" else 1, c["name"])

    out_cmds = []
    for c in sorted(commands, key=order):
        out_cmds.append({"name": c["name"], "args": [normalize(a) for a in c["args"]],
                         "files": {k: normalize(v).splitlines() for k, v in sorted(c["files"].items())}})
    return {
        "ffmpeg_version": list(FFMPEG_VERSION),
        "settings": {k: getattr(settings, k) for k in (
            "render_width", "render_height", "render_fps", "render_crf", "render_preset", "render_bgm",
            "render_color_bt709", "render_mascot_continuity", "render_glass_panels", "render_include_intro",
            "render_burn_captions", "render_soft_subtitles")},
        "commands": out_cmds,
        "side_files": {k: normalize(v).splitlines() for k, v in sorted(side_files.items())},
    }


# --- live capture vs golden ----------------------------------------------------------------------------------------

RECT_TOLERANCE_PX = 2.0  # rects come from the page's layout: allow sub-pixel and font-metric wobble across systems


def compare_capture(golden: dict[str, Any], live: dict[str, Any], *,
                    rect_tolerance: float = RECT_TOLERANCE_PX) -> list[str]:
    """Differences between a recorded and a live capture: state times, keys, screenshot names, notices and fits
    must be equal; reported rects may move by ``rect_tolerance`` px. Empty when they agree."""
    problems: list[str] = []
    gs, ls = golden.get("scenes", {}), live.get("scenes", {})
    if sorted(gs) != sorted(ls):
        problems.append(f"scene indices differ: {sorted(gs)} vs {sorted(ls)}")
    for index in sorted(set(gs) & set(ls), key=int):
        a, b = gs[index], ls[index]
        if len(a) != len(b):
            problems.append(f"scene {index}: {len(a)} states recorded, {len(b)} now")
            continue
        for n, (x, y) in enumerate(zip(a, b)):
            for key in ("t", "key", "png", "notices", "media_fit"):
                if x.get(key) != y.get(key):
                    problems.append(f"scene {index} state {n}: {key} {x.get(key)!r} -> {y.get(key)!r}")
            for rect in ("media_rect", "panel_media_rect"):
                r1, r2 = x.get(rect), y.get(rect)
                if (r1 is None) != (r2 is None):
                    problems.append(f"scene {index} state {n}: {rect} {r1!r} -> {r2!r}")
                elif r1 is not None and (set(r1) != set(r2)
                                         or any(abs(float(r1[k]) - float(r2[k])) > rect_tolerance for k in r1)):
                    problems.append(f"scene {index} state {n}: {rect} {r1!r} -> {r2!r}")
    for key in ("intro", "blocked_media", "websocket_attempts"):
        if golden.get(key) != live.get(key):
            problems.append(f"{key}: {golden.get(key)!r} -> {live.get(key)!r}")
    return problems
