"""Shared fixtures of the motion engine tests: Dynamic timelines, synthetic media and frame decoding."""

from __future__ import annotations

import io
import subprocess
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from aadhi.compose.ffmpeg import subprocess_env
from aadhi.schemas.timeline import Timeline

BITS = 16
TOKEN = "motion-test-token"
AUDIO_KEY = "scene_audio-" + "a" * 40
CLIP_KEY = "video-" + "c" * 40


def _run(cmd: list[str], *, data: bytes | None = None) -> bytes:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(cmd, input=data, capture_output=True, timeout=300, env=subprocess_env(), check=False,
                          **kwargs)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    return proc.stdout


def presentation(fmt: str = "landscape", seconds: float = 10.0, **extra: Any) -> dict[str, Any]:
    return {"style": "dynamic", "format": fmt, "content": {"ink": "#ffffff", "ink2": "#d0d0d0"},
            "fonts": {"display": "inter", "body": "inter"}, "personality": "crisp", "caption_style": "clean_pill",
            "cue_term_style": "marker", "captions_burn": False, "sfx": False, "motion": "standard",
            "generated_seconds": seconds, **extra}


def look(i: int) -> dict[str, Any]:
    return {"template": "board_generic", "tokens": {"struct.accent": "#ffcc00", "bg": "#101020"},
            "background": {"preset": "mesh_drift" if i % 2 == 0 else "colour_block", "params": {}, "seed": 7 + i,
                           "frozen_at": None},
            "transition_in": {"kind": "cut" if i == 0 else "crossfade", "seconds": 0.0 if i == 0 else 0.4}}


def dynamic_timeline(durations: list[float], *, size: tuple[int, int] = (320, 180), fmt: str = "landscape",
                     media_scene: int | None = None, clip_seconds: float = 2.0, narration: bool = True,
                     quiz_scene: int | None = None, captions: bool = True) -> Timeline:
    """A valid Dynamic timeline of scenes with the given durations (absolute starts), narration on every scene."""
    scenes: list[dict[str, Any]] = []
    t = 0.0
    for i, d in enumerate(durations):
        scene: dict[str, Any] = {"scene_id": f"s{i}", "index": i, "type": "content", "title": f"Scene {i}",
                                 "start": round(t, 6), "duration": d, "audio_offset": 0.5, "look": look(i)}
        if narration:
            scene["audio_asset_key"] = AUDIO_KEY
            scene["audio_duration"] = min(1.0, max(0.1, d - 0.6))
        if media_scene == i:
            scene["media"] = {"kind": "video", "asset_key": CLIP_KEY, "duration": clip_seconds, "width": 320,
                              "height": 180, "src_fps": 30.0}
        if quiz_scene == i:
            scene["quiz"] = {"question": "Q?", "options": ["a", "b"], "correct_index": 0, "feedback_wrong": ["", ""],
                             "explanation": "e", "countdown_start": 0.5, "countdown_seconds": 2, "reveal_start": 2.6}
        scenes.append(scene)
        t += d
    data = {
        "version_id": 7, "fps": 30, "width": size[0], "height": size[1], "language": "en-IN",
        "meta": {"session_title": "Motion test"}, "scenes": scenes, "total_duration": round(t, 6),
        "chapters": [{"start": 0.0, "title": "Start"}, {"start": round(durations[0], 6), "title": "Middle"}],
        "captions": ([{"start": 0.6, "end": 1.4, "text": "Hello motion"}, {"start": t - 1.0, "end": t - 0.2,
                                                                          "text": "Goodbye"}] if captions else []),
        "presentation": presentation(fmt, t),
    }
    return Timeline.model_validate(data)


def make_wav(path: Path, seconds: float, freq: float = 440.0, rate: int = 24000) -> Path:
    n = int(seconds * rate)
    pcm = (0.3 * np.sin(2 * np.pi * freq * np.arange(n) / rate) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return path


def bits_image(index: int, size: tuple[int, int], *, color: tuple[int, int, int] = (40, 90, 160)) -> Image.Image:
    """A frame whose top quarter shows ``index`` as 16 black/white blocks (MSB first)."""
    w, h = size
    im = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(im)
    for b in range(BITS):
        on = (index >> (BITS - 1 - b)) & 1
        draw.rectangle((round(b * w / BITS), 0, round((b + 1) * w / BITS) - 1, h // 4 - 1),
                       fill=(255, 255, 255) if on else (0, 0, 0))
    return im


def make_bits_video(ffmpeg: str, path: Path, frames: int, size: tuple[int, int] = (320, 180), fps: int = 30) -> Path:
    """An H.264 clip (with B-frames) whose frame ``k`` shows ``k`` in its top quarter (``bits_image``)."""
    buf = io.BytesIO()
    for k in range(frames):
        bits_image(k, size).save(buf, format="PNG")
    _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-c:v", "png", "-framerate", str(fps),
          "-i", "pipe:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "6", "-bf", "3", "-pix_fmt", "yuv420p",
          "-g", "30", str(path)], data=buf.getvalue())  # B-frames: the extraction's seek must still be frame-exact
    return path


def gray_frames(ffmpeg: str, path: Path, size: tuple[int, int]) -> list[np.ndarray]:
    """Every decoded frame of ``path`` as full-range 8-bit gray arrays (h, w)."""
    w, h = size
    raw = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0", "-vf",
                f"scale={w}:{h}:in_range=tv:out_range=pc,format=gray", "-f", "rawvideo", "-"])
    arr = np.frombuffer(raw, dtype=np.uint8)
    n = arr.size // (w * h)
    return [arr[i * w * h:(i + 1) * w * h].reshape(h, w) for i in range(n)]


def decode_bits(gray: np.ndarray, *, x0: float, y0: float, w: float, h: float) -> int:
    """The 16-bit number drawn as blocks in the box (x0, y0, w, h) of a gray frame (block centres sampled)."""
    value = 0
    for b in range(BITS):
        cx = int(x0 + (b + 0.5) * w / BITS)
        cy = int(y0 + h / 2)
        patch = gray[max(0, cy - 2):cy + 3, max(0, cx - 1):cx + 2]
        value = (value << 1) | int(patch.mean() > 128)
    return value


@dataclass
class SimpleCtx:
    """The parts of a job context the engine uses (no database)."""

    payload: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    assets: Any = None
    storage: Any = None

    def check_cancelled(self) -> None:
        if self.cancelled:
            from aadhi.jobs.base import JobCancelled

            raise JobCancelled("cancelled by user", reason="cancelled")

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        self.events.append({"type": "progress", "stage": stage, "progress": fraction, "message": message})

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        self.events.append({"type": "log", "level": level, "message": message})
