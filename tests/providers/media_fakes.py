"""Test-only media provider stand-ins with injectable failures (never part of production code).

* ``ScriptedImage`` / ``ScriptedVideo``: named providers that raise the scripted exceptions in order,
  then produce media ("fake" and "fake-alt" give distinguishable keys and provenance).
* ``ResumableVideo``: a paid, resumable provider like Veo with an operation store shared between
  instances (as the real provider's jobs live at Google), a submit counter and modes:
  ``vanish`` (the job is gone when resumed), ``fail`` (the job fails on the provider), ``release`` (a
  graceful release while polling: the abandoned estimate is billed and noted, then the worker stops).
  Every charge is noted on the checkpoint (``usage_recorded``) like the real adapter does.
* ``Crash``: a BaseException that stops a "worker" mid-flight like a kill (nothing catches it).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from aadhi.providers.base import ImageResult, OperationLost, ProviderError, Usage, VideoResult


class Crash(BaseException):
    """Simulated process death (not an Exception: production code never catches it)."""


def gradient_png(width: int = 64, height: int = 48, seed: int = 0) -> bytes:
    """A small non-blank PNG (a gradient, so the blank heuristic does not flag it)."""
    img = Image.new("RGB", (width, height))
    px = img.load()
    for x in range(width):
        for y in range(height):
            px[x, y] = ((x * 4 + seed) % 256, (y * 5) % 256, (x + y + seed) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def flat_png(width: int = 64, height: int = 48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class ScriptedImage:
    name: str = "fake"
    failures: list[BaseException] = field(default_factory=list)  # raised one per call, in order
    paid: bool = False
    aspects: frozenset[str] = frozenset({"16:9", "4:3", "1:1", "9:16"})
    warnings: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    async def generate(self, prompt: str, *, aspect: str = "16:9", on_usage: Any = None) -> ImageResult:
        self.calls.append(prompt)
        if self.failures:
            raise self.failures.pop(0)
        if on_usage is not None:
            on_usage(Usage(provider=self.name, model="", operation="image", units=1))
        return ImageResult(data=gradient_png(seed=len(self.name)), mime="image/png", width=64, height=48,
                           warnings=list(self.warnings))


@dataclass
class ScriptedVideo:
    name: str = "fake"
    failures: list[BaseException] = field(default_factory=list)
    paid: bool = False
    calls: list[str] = field(default_factory=list)

    async def generate(self, prompt: str, *, aspect: str = "16:9", reference_image: Any = None, timeout_s: int = 420,
                       on_usage: Any = None) -> VideoResult:
        self.calls.append(prompt)
        if self.failures:
            raise self.failures.pop(0)
        if on_usage is not None:
            on_usage(Usage(provider=self.name, model="", operation="video", seconds=8))
        return VideoResult(data=b"\x00\x00\x00\x18ftypmp42" + self.name.encode(), mime="video/mp4", duration=8.0,
                           width=1280, height=720)


@dataclass
class OperationService:
    """The provider side: jobs keep existing across worker restarts."""

    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    submits: int = 0

    def submit(self, prompt: str) -> str:
        self.submits += 1
        name = f"operations/op-{self.submits}"
        self.jobs[name] = {"prompt": prompt, "polls": 0}
        return name


@dataclass
class ResumableVideo:
    """Paid, resumable ``VideoProvider`` (the Veo contract) backed by an ``OperationService``."""

    service: OperationService
    name: str = "veo"
    paid: bool = True
    resumable: bool = True
    aspects: frozenset[str] = frozenset({"16:9", "9:16"})
    fingerprint: str = "fp-server"
    mode: str = ""  # "" | "vanish" | "fail" | "release"
    crash_after_checkpoint: bool = False  # Crash right after the checkpoint was saved (a kill mid-poll)
    crash_before_answer: bool = False  # Crash after submitting, before the provider's answer is known
    resumes: list[str] = field(default_factory=list)
    usage: list[Usage] = field(default_factory=list)

    def _result(self, name: str) -> VideoResult:
        return VideoResult(data=b"\x00\x00\x00\x18ftypmp42" + name.encode(), mime="video/mp4", duration=8.0,
                           width=1280, height=720)

    def _bill(self, on_usage: Any, status: str) -> None:
        usage = Usage(provider=self.name, model="veo-x", operation="video", seconds=8.0, units=1,
                      meta={"status": status, "estimated": True})
        self.usage.append(usage)
        if on_usage is not None:
            on_usage(usage)

    async def generate(self, prompt: str, *, aspect: str = "16:9", reference_image: Any = None, timeout_s: int = 420,
                       on_usage: Any = None, checkpoint: Any = None) -> VideoResult:
        name = self.service.submit(prompt)
        if self.crash_before_answer:
            raise Crash("killed before the answer was saved")
        if checkpoint is not None:
            await checkpoint.submitted(name, self.fingerprint)
        if self.crash_after_checkpoint:
            raise Crash("killed while polling")
        if self.mode == "release":  # the worker is released while polling: the likely charge is reported
            self._bill(on_usage, "abandoned")
            if checkpoint is not None:
                await checkpoint.usage_recorded()
            raise Crash("released while polling")
        if self.mode == "fail":
            raise ProviderError("veo: video generation failed: internal error", provider="veo")
        self._bill(on_usage, "generated")
        if checkpoint is not None:
            await checkpoint.usage_recorded()
        return self._result(name)

    async def resume(self, operation: str, key_fingerprint: str, *, timeout_s: int = 420, on_usage: Any = None,
                     usage_recorded: bool = False, checkpoint: Any = None) -> VideoResult:
        self.resumes.append(operation)
        if key_fingerprint != self.fingerprint or operation not in self.service.jobs or self.mode == "vanish":
            raise OperationLost("veo: the video job started earlier no longer exists", status=404, provider="veo")
        if not usage_recorded:
            self._bill(on_usage, "generated")
            if checkpoint is not None:
                await checkpoint.usage_recorded()
        return self._result(operation)
