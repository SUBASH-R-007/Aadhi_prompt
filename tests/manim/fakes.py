"""Test doubles for the Manim subsystem: scripted LLM, fake sandbox runner, fake media helpers."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aadhi.manim.media import VideoInfo
from aadhi.manim.sandbox import SandboxResult
from aadhi.providers.base import ProviderError, Usage

# 1x1 transparent PNG
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
FAKE_MP4_HEADER = bytes.fromhex("0000001866747970") + b"mp42"


class ScriptedLLM:
    """``LLMProvider`` double: answers come from per-schema queues, with the real re-ask semantics."""

    name = "scripted"

    def __init__(self) -> None:
        self.queues: dict[str, list[Any]] = {}
        self.calls: list[dict[str, Any]] = []

    def queue(self, schema_name: str, *responses: Any) -> None:
        """Queue responses (dicts, models, callables(prompt) -> dict, or exceptions to raise).

        The last queued response is repeated once the queue is down to one item.
        """
        self.queues.setdefault(schema_name, []).extend(responses)

    def _next(self, schema_name: str, prompt: str) -> Any:
        items = self.queues.get(schema_name) or []
        if not items:
            raise ProviderError(f"no scripted response for {schema_name}", provider="scripted")
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, BaseException):
            raise item
        return item(prompt) if callable(item) else item

    async def generate_json(self, *, model, system, prompt, schema, files=(), images=(), temperature=0.4,
                            max_output_tokens=None, on_usage=None, validate=None, validation_retries=2):
        await asyncio.sleep(0)
        for attempt in range(validation_retries + 1):
            self.calls.append({"model": model, "system": system, "prompt": prompt, "schema": schema.__name__,
                               "images": len(images), "attempt": attempt})
            raw = self._next(schema.__name__, prompt)
            if on_usage is not None:
                on_usage(Usage(provider="fake", model=model, operation="vision" if images else "llm",
                               input_tokens=100, output_tokens=20))
            obj = raw if isinstance(raw, schema) else schema.model_validate(raw)
            problems = validate(obj) if validate else []
            if not problems:
                return obj
            prompt = prompt + "\n\nFix these problems:\n" + "\n".join(problems)
        raise ProviderError("invalid output after re-asks", provider="scripted")

    async def generate_text(self, **kwargs: Any) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def calls_for(self, schema_name: str) -> list[dict[str, Any]]:
        """Recorded calls for one response schema."""
        return [c for c in self.calls if c["schema"] == schema_name]


@dataclass
class FakeRunner:
    """Sandbox double: writes a placeholder mp4 and succeeds unless ``fail_when(script)`` returns a log.

    Like the real runners, the video lands at ``<workdir>/out.mp4``.
    """

    latex: bool = True
    fail_when: Callable[[str], str | None] = lambda script: None
    scripts: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    name: str = "subprocess"
    killed_reason: str = ""

    def has_latex(self) -> bool:
        return self.latex

    async def run(self, script: str, scene: str, *, workdir: Path, quality: str, timeout: float) -> SandboxResult:
        await asyncio.sleep(0)
        self.scripts.append(script)
        self.calls.append({"scene": scene, "workdir": Path(workdir), "quality": quality, "timeout": timeout})
        workdir.mkdir(parents=True, exist_ok=True)
        if self.killed_reason:
            return SandboxResult(ok=False, returncode=None, video_path=None, log="partial output",
                                 killed_reason=self.killed_reason, timed_out=self.killed_reason == "timeout")
        error = self.fail_when(script)
        if error is not None:
            return SandboxResult(ok=False, returncode=1, video_path=None, log=error)
        out = workdir / "out.mp4"
        out.write_bytes(FAKE_MP4_HEADER + scene.encode() + bytes(64))
        return SandboxResult(ok=True, returncode=0, video_path=out, log="rendered " + scene)


def error_log(message: str, line: int = 5) -> str:
    """A sandbox log containing the runtime's compact error block."""
    return (
        "Traceback noise...\n[AADHI_ERROR]\n"
        f"  line {line}, in construct: self.play(Create(Circl()))\n{message}\n[/AADHI_ERROR]\n"
    )


def install_fast_media(monkeypatch, width: int = 854, height: int = 480) -> dict[str, Any]:
    """Replace ffmpeg-based helpers with pure-Python fakes; returns a dict recording calls."""
    from aadhi.manim import media

    record: dict[str, Any] = {"conform": [], "frames": []}

    async def conform(src: Path, dst: Path, total: float, settings) -> VideoInfo:
        shutil.copyfile(src, dst)
        record["conform"].append((Path(src), Path(dst), total))
        return VideoInfo(duration=round(total, 3), width=width, height=height, fps=15.0)

    async def probe(path: Path, settings) -> VideoInfo:
        total = record["conform"][-1][2] if record["conform"] else 5.0
        return VideoInfo(duration=total, width=width, height=height, fps=15.0)

    async def extract_frames(src: Path, times: list[float], settings, max_width: int = 960) -> list[bytes]:
        record["frames"].append(list(times))
        return [TINY_PNG for _ in times]

    monkeypatch.setattr(media, "conform", conform)
    monkeypatch.setattr(media, "probe", probe)
    monkeypatch.setattr(media, "extract_frames", extract_frames)
    return record


def make_test_video(path: Path, seconds: float, size: str = "320x180", fps: int = 15) -> Path:
    """Real H.264 mp4 made by ffmpeg's lavfi test source (slow tests only)."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}:duration={seconds}",
           "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)  # noqa: S603
    return path
