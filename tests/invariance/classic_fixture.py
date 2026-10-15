"""Classic-mode invariance fixtures: the screenplays, a deterministic synthetic asset manifest, pinned settings,
and the narration / media files a render of them needs (docs/DYNAMIC_VIDEO_DESIGN.md section 3.6).

The manifest stands in for the asset stage: real TTS durations depend on the provider and on the ffmpeg build
that encodes the narration, so they cannot be part of a platform-independent golden. Here every beat speaks for
``BEAT_LEAD_SECONDS + SECONDS_PER_WORD * words`` with evenly spaced word timings, every visible scene gets its
content hash (``aadhi.pipeline.assets.scene_hash``), and media follow the asset stage's rules
(``_Builder.scene_media``): board and panel figures, image / Manim / GIF side panels, the simulation's Manim clip,
the AI video and an interactive scene's poster override.

Standalone-safe: module-level imports are the standard library only and ``aadhi`` (with numpy and Pillow) is
imported inside the functions, so ``render_classic.py`` can load this file by path after putting another code
root (the merge base) first on ``sys.path``. Nothing here may depend on a module that the merge base lacks
beyond the long-standing classic APIs (screenplay / manifest schemas, ``convert_legacy``, ``scene_hash``,
``compute_key``, ``Settings``).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INVARIANCE_DIR = Path(__file__).resolve().parent
REPO_ROOT = INVARIANCE_DIR.parents[1]
GOLDEN_DIR = INVARIANCE_DIR / "goldens"

# Fixture name -> screenplay file. ``showcase_ohms_law`` is the v1 showcase (converted by the importer) that the
# design names; ``classic_kitchen_sink`` is a v2 screenplay with every scene type, a figure, a quiz with reveal
# beats, a hidden scene and a held scene (``min_seconds``).
FIXTURES: dict[str, Path] = {
    "showcase_ohms_law": REPO_ROOT / "tests" / "core" / "fixtures" / "showcase_ohms_law.json",
    "classic_kitchen_sink": INVARIANCE_DIR / "fixtures" / "classic_kitchen_sink.json",
}
RENDER_FIXTURE = "showcase_ohms_law"  # the differential pixel test renders this one
# Degraded assets, as when a provider failed: scenes whose media are missing (the timeline's fallbacks) and
# scenes without narration.
DEGRADED: dict[str, dict[str, frozenset[str]]] = {
    "classic_kitchen_sink": {"no_media": frozenset({"s-sim-fallback", "s-video-none"}),
                             "no_audio": frozenset({"s-noaudio"})},
}

INVARIANCE_VERSION = 1  # part of every synthetic asset key; bump only together with the goldens
BEAT_LEAD_SECONDS = 0.2
SECONDS_PER_WORD = 0.3
AUDIO_RATE = 24_000
VIDEO_SECONDS = {"veo": 8.0, "manim": 6.0}
VIDEO_SIZE = (1280, 720)
IMAGE_SIZE = (1024, 576)
FIGURE_SIZE = (640, 480)
GIF_URL = "https://media.giphy.com/media/aadhi-invariance/giphy.gif"  # live-only: blocked in render mode


# --- screenplays -----------------------------------------------------------------------------------------------


def fixture_data(name: str) -> Any:
    """The fixture file's JSON (unchanged)."""
    return json.loads(FIXTURES[name].read_text(encoding="utf-8"))


def load_screenplay(name: str, *, max_scenes: int | None = None) -> Any:
    """The fixture as a ``Screenplay``: v1 files through the importer (``convert_legacy``), v2 files validated.

    ``max_scenes`` keeps only the first scenes (quick local renders; never used by the goldens or CI).
    """
    from aadhi.legacy.convert import convert_legacy, is_legacy
    from aadhi.schemas.screenplay import Screenplay

    data = fixture_data(name)
    sp = convert_legacy(data)[0] if is_legacy(data) else Screenplay.model_validate(data)
    if max_scenes is not None and max_scenes < len(sp.scenes):
        dumped = sp.model_dump(mode="json")
        dumped["scenes"] = dumped["scenes"][:max(1, max_scenes)]
        kept = {s["id"] for s in dumped["scenes"]}
        chapters = []
        for ch in dumped.get("chapters") or []:
            ids = [i for i in ch.get("scene_ids") or [] if i in kept]
            if ids:
                chapters.append({**ch, "scene_ids": ids})
        dumped["chapters"] = chapters
        sp = Screenplay.model_validate(dumped)
    return sp


def visible_scenes(sp: Any) -> list[Any]:
    return [s for s in sp.scenes if not getattr(s, "hidden", False)]


# --- settings --------------------------------------------------------------------------------------------------

_PINNED_CLASS: Any = None


def pinned_settings(**overrides: Any) -> Any:
    """``Settings`` from the code defaults plus ``overrides`` only: no environment variable and no ``.env`` file
    can change what the goldens are built with (a developer's shell, CI or the container)."""
    global _PINNED_CLASS
    if _PINNED_CLASS is None:
        from aadhi.config import Settings

        class PinnedSettings(Settings):
            @classmethod
            def settings_customise_sources(cls, settings_cls: Any, init_settings: Any, env_settings: Any,
                                           dotenv_settings: Any, file_secret_settings: Any) -> tuple[Any, ...]:
                return (init_settings,)

        _PINNED_CLASS = PinnedSettings
    return _PINNED_CLASS(**overrides)


# --- media plan ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaItem:
    """One medium the asset stage would attach to a scene."""

    scene_id: str
    slot: str  # "main" | "side_panel" | "poster" | "figure:<board item id>"
    kind: str  # MediaInfo.kind: video | image | gif
    source: str  # MediaInfo.source
    asset_kind: str  # AssetStore kind of the file (video, manim, image, figure, poster); "" when not stored
    key: str | None
    mime: str
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    external_url: str | None = None


def _key(asset_kind: str, scene_id: str, slot: str) -> str:
    from aadhi.storage.assets import compute_key

    return compute_key(asset_kind, {"invariance": INVARIANCE_VERSION, "scene": scene_id, "slot": slot})


def _figure(sp: Any, scene_id: str, slot: str, figure_id: str | None) -> MediaItem | None:
    fig = next((f for f in sp.figures if f.id == figure_id), None)
    if fig is None or not fig.asset_key:
        return None
    return MediaItem(scene_id, slot, "image", "figure", "figure", fig.asset_key, "image/png",
                     fig.width or FIGURE_SIZE[0], fig.height or FIGURE_SIZE[1])


def _video(scene_id: str, slot: str, source: str) -> MediaItem:
    asset_kind = "manim" if source == "manim" else "video"
    return MediaItem(scene_id, slot, "video", source, asset_kind, _key(asset_kind, scene_id, slot), "video/mp4",
                     *VIDEO_SIZE, VIDEO_SECONDS[source])


def _degraded(fixture: str | None, what: str) -> frozenset[str]:
    return DEGRADED.get(fixture or "", {}).get(what, frozenset())


def media_plan(sp: Any, fixture: str | None = None) -> list[MediaItem]:
    """Media of every visible scene, in the asset stage's order (figures, side panel, main medium, poster)."""
    from aadhi.schemas.screenplay import AIVideoScene, BoardScene, InteractiveScene, SimulationScene

    out: list[MediaItem] = []
    no_media = _degraded(fixture, "no_media")
    for scene in visible_scenes(sp):
        sid = scene.id
        if sid in no_media:
            continue
        if isinstance(scene, BoardScene):
            for item in scene.board:
                if str(getattr(item.kind, "value", item.kind)) == "figure" and item.figure_id:
                    fig = _figure(sp, sid, f"figure:{item.id}", item.figure_id)
                    if fig is not None:
                        out.append(fig)
        p = scene.side_panel
        if p is not None:
            if p.kind == "figure":
                fig = _figure(sp, sid, "side_panel", p.figure_id)
                if fig is not None:
                    out.append(fig)
            elif p.kind == "image" and p.image_prompt:
                out.append(MediaItem(sid, "side_panel", "image", "image", "image", _key("image", sid, "side_panel"),
                                     "image/png", *IMAGE_SIZE))
            elif p.kind == "manim" and p.manim is not None:
                out.append(_video(sid, "side_panel", "manim"))
            elif p.kind == "gif" and p.gif_query:
                out.append(MediaItem(sid, "side_panel", "gif", "gif", "", None, "image/gif", external_url=GIF_URL))
        if isinstance(scene, SimulationScene):
            out.append(_video(sid, "main", "manim"))
        if isinstance(scene, AIVideoScene):
            out.append(_video(sid, "main", "veo"))
        if isinstance(scene, InteractiveScene) and scene.poster_override_asset_key:
            out.append(MediaItem(sid, "poster", "image", "poster", "poster", scene.poster_override_asset_key,
                                 "image/png", *IMAGE_SIZE))
    return out


# --- narration -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BeatTiming:
    beat_id: str
    phase: str
    offset: float
    speech: float
    pause_after: float
    text: str
    words: tuple[tuple[str, float, float], ...]


@dataclass(frozen=True)
class NarrationLayout:
    beats: tuple[BeatTiming, ...]
    duration: float
    countdown_start: float | None
    countdown_seconds: int | None


def narration_layout(scene: Any) -> NarrationLayout:
    """Deterministic narration timing of a scene (quiz: the countdown silence sits before the reveal beats)."""
    from aadhi.schemas.manifest import INTER_BEAT_GAP_SECONDS
    from aadhi.schemas.screenplay import QuizScene

    quiz = isinstance(scene, QuizScene)
    reveal = {b.id for b in scene.reveal_beats} if quiz else set()
    countdown = int(scene.countdown_seconds) if quiz else None
    t, countdown_start = 0.0, None
    beats: list[BeatTiming] = []
    for b in scene.all_beats():
        if b.id in reveal and countdown_start is None and countdown:
            countdown_start = round(t, 3)
            t += countdown
        text = b.spoken or b.narration
        tokens = text.split() or [text]
        speech = round(BEAT_LEAD_SECONDS + SECONDS_PER_WORD * len(tokens), 3)
        step = speech / len(tokens)
        words = tuple((w, round(t + k * step, 3), round(t + (k + 1) * step, 3)) for k, w in enumerate(tokens))
        beats.append(BeatTiming(b.id, "reveal" if b.id in reveal else "main", round(t, 3), speech,
                                float(b.pause_after), text, words))
        t += speech + float(b.pause_after) + INTER_BEAT_GAP_SECONDS
    duration = round(t - INTER_BEAT_GAP_SECONDS, 3) if beats else 0.0
    return NarrationLayout(tuple(beats), duration, countdown_start, countdown)


def narration_key(scene_hash: str) -> str:
    from aadhi.storage.assets import compute_key

    return compute_key("scene_audio", {"invariance": INVARIANCE_VERSION, "scene_hash": scene_hash})


def narration_wav(layout: NarrationLayout, *, seed: int = 0) -> bytes:
    """16-bit mono WAV of the layout: a soft tone per beat (10 ms fades), silence in gaps and the countdown."""
    import io
    import wave

    import numpy as np

    n = max(1, round(layout.duration * AUDIO_RATE))
    pcm = np.zeros(n, dtype=np.float64)
    for i, beat in enumerate(layout.beats):
        a, b = round(beat.offset * AUDIO_RATE), min(n, round((beat.offset + beat.speech) * AUDIO_RATE))
        if b <= a:
            continue
        t = np.arange(b - a, dtype=np.float64) / AUDIO_RATE
        freq = 180.0 + 15.0 * ((seed + i) % 12)
        tone = 0.22 * np.sin(2 * np.pi * freq * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4.0 * t) ** 2)
        fade = min(len(t) // 2, AUDIO_RATE // 100)
        if fade > 0:
            ramp = np.linspace(0.0, 1.0, fade)
            tone[:fade] *= ramp
            tone[-fade:] *= ramp[::-1]
        pcm[a:b] = tone
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(AUDIO_RATE)
        w.writeframes((np.clip(pcm, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes())
    return buf.getvalue()


# --- manifest --------------------------------------------------------------------------------------------------


def scene_hashes(sp: Any) -> dict[str, str]:
    """``scene_hash`` (with the screenplay's lexicon, as ``stale_scene_ids`` uses it) of every visible scene."""
    from aadhi.pipeline.assets import scene_hash

    return {s.id: scene_hash(s, sp.lexicon) for s in visible_scenes(sp)}


def synthetic_manifest(sp: Any, stored: dict[str, tuple[str, str]] | None = None, *,
                       fixture: str | None = None) -> Any:
    """The asset manifest the asset stage would write, with deterministic timings and keys.

    ``stored`` maps asset keys to ``(storage_key, mime)`` of files really stored (a render); every other asset
    gets a synthetic storage key, so the goldens never depend on the asset store's random storage names.
    ``fixture`` applies that fixture's degraded assets (``DEGRADED``).
    """
    from aadhi.schemas.manifest import AssetManifest, BeatAudio, MediaInfo, SceneAudio, SceneMedia
    from aadhi.schemas.timeline import TimedWord

    stored = stored or {}
    hashes = scene_hashes(sp)
    plan = media_plan(sp, fixture)
    no_audio = _degraded(fixture, "no_audio")
    audio: dict[str, Any] = {}
    media: dict[str, Any] = {}
    for scene in visible_scenes(sp):
        layout = narration_layout(scene)
        sa: dict[str, Any] = {"scene_id": scene.id, "provider": "fake", "voice": "invariance", "language": sp.language}
        if layout.beats and scene.id not in no_audio:
            key = narration_key(hashes[scene.id])
            storage_key, mime = stored.get(key, (f"assets/scene_audio/{key}/invariance.mp3", "audio/mpeg"))
            sa.update(asset_key=key, storage_key=storage_key, mime=mime, duration=layout.duration,
                      countdown_start=layout.countdown_start, countdown_seconds=layout.countdown_seconds,
                      beats=[BeatAudio(beat_id=b.beat_id, phase=b.phase, offset=b.offset, speech_duration=b.speech,
                                       pause_after=b.pause_after, spoken_text=b.text,
                                       words=[TimedWord(text=w, start=s, end=e) for w, s, e in b.words])
                             for b in layout.beats])
        if scene.id not in no_audio:
            audio[scene.id] = SceneAudio(**sa)
        slots: dict[str, Any] = {}
        figures: dict[str, Any] = {}
        for item in (m for m in plan if m.scene_id == scene.id):
            storage_key = None
            if item.key:
                ext = "mp4" if item.kind == "video" else "png"
                storage_key = stored.get(item.key, (f"assets/{item.asset_kind}/{item.key}/invariance.{ext}", ""))[0]
            info = MediaInfo(asset_key=item.key, storage_key=storage_key, kind=item.kind, mime=item.mime,
                             duration=item.duration, width=item.width, height=item.height,
                             external_url=item.external_url, source=item.source)
            if item.slot.startswith("figure:"):
                figures[item.slot.split(":", 1)[1]] = info
            else:
                slots[item.slot] = info
        media[scene.id] = SceneMedia(scene_id=scene.id, figures=figures, **slots)
    return AssetManifest(language=sp.language, audio=audio, media=media, scene_hashes=hashes)


# --- media files (renders only) --------------------------------------------------------------------------------


def _image_bytes(key: str, size: tuple[int, int]) -> bytes:
    """A deterministic test card: vertical gradient, colour blocks and rings derived from ``key``."""
    import io

    import numpy as np
    from PIL import Image, ImageDraw

    digest = hashlib.sha256(key.encode("utf-8")).digest()
    w, h = size
    top, bottom = np.array(list(digest[0:3]), dtype=np.float64), np.array(list(digest[3:6]), dtype=np.float64)
    k = (np.arange(h, dtype=np.float64) / max(1, h - 1))[:, None]
    rows = np.round(top + (bottom - top) * k).astype(np.uint8)
    im = Image.fromarray(np.repeat(rows[:, None, :], w, axis=1), "RGB")
    draw = ImageDraw.Draw(im)
    for i in range(6):
        c = tuple(digest[6 + 3 * i: 9 + 3 * i])
        x0, y0 = (digest[24 + i] * w) // 320, (digest[30 - i] * h) // 320
        draw.rectangle((x0, y0, x0 + w // 5, y0 + h // 6), fill=c)
        draw.ellipse((w - x0 - h // 4, h - y0 - h // 4, w - x0, h - y0), outline=tuple(reversed(c)), width=6)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def write_media(sp: Any, dest: Path, *, ffmpeg: str, fixture: str | None = None) -> dict[str, Path]:
    """Write every stored medium of ``sp`` into ``dest`` (named by asset key; existing files are kept, so a
    shared directory gives the base and the HEAD render byte-identical inputs). Returns key -> file."""
    dest.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for item in media_plan(sp, fixture):
        if not item.key or item.key in out:
            continue
        if item.kind == "image":
            path = dest / f"{item.key}.png"
            if not path.is_file():
                path.write_bytes(_image_bytes(item.key, (item.width or IMAGE_SIZE[0], item.height or IMAGE_SIZE[1])))
        else:
            path = dest / f"{item.key}.mp4"
            if not path.is_file():
                w, h = item.width or VIDEO_SIZE[0], item.height or VIDEO_SIZE[1]
                part = path.with_suffix(".part.mp4")
                subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                                f"testsrc2=s={w}x{h}:r=30:d={item.duration or 6.0}", "-c:v", "libx264", "-preset",
                                "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", "-an", "-fflags", "+bitexact",
                                "-flags:v", "+bitexact", str(part)], check=True, timeout=300,
                               stdin=subprocess.DEVNULL, capture_output=True)
                part.replace(path)
        out[item.key] = path
    return out


def store_assets(sp: Any, store: Any, media_files: dict[str, Path], *,
                 fixture: str | None = None) -> dict[str, tuple[str, str]]:
    """Put the narration WAVs and ``media_files`` into the asset store; returns key -> (storage_key, mime)."""
    from aadhi.storage.assets import Produced

    stored: dict[str, tuple[str, str]] = {}
    hashes = scene_hashes(sp)
    no_audio = _degraded(fixture, "no_audio")
    for n, scene in enumerate(visible_scenes(sp)):
        layout = narration_layout(scene)
        if not layout.beats or scene.id in no_audio:
            continue
        key = narration_key(hashes[scene.id])
        if key in stored:
            continue
        asset = store.put(key, "scene_audio", Produced(data=narration_wav(layout, seed=n), mime="audio/wav",
                                                       duration_s=layout.duration))
        stored[key] = (asset.storage_key, "audio/wav")
    for item in media_plan(sp, fixture):
        if not item.key or item.key in stored or item.key not in media_files:
            continue
        path = media_files[item.key]
        produced = Produced(path=path, mime=item.mime if item.kind != "video" else "video/mp4",
                            duration_s=item.duration, width=item.width, height=item.height)
        asset = store.put(item.key, item.asset_kind, produced)
        stored[item.key] = (asset.storage_key, asset.mime)
    return stored
