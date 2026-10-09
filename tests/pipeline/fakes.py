"""Offline fakes for pipeline tests (LLM, TTS, image, video, GIF, Manim templates/renderer)."""

from __future__ import annotations

import hashlib
import io
import math
import struct
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from aadhi.manim.base import ManimRenderRequest, ManimRenderResult, TemplateInfo
from aadhi.providers.base import GifResult, ImageResult, ProviderError, SpeechResult, Usage, VideoResult, WordTiming
from aadhi.storage.assets import Produced, compute_key

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

Responder = Callable[[str, type[BaseModel]], Any]


class ScriptedLLM:
    """``LLMProvider`` fake: responders by schema name (fallback: ``fake_content``), real re-ask loop."""

    name = "fake"

    def __init__(self, responders: dict[str, Responder] | None = None, *, use_fake_content: bool = True) -> None:
        from aadhi.pipeline import fake_content

        self.responders: dict[str, Responder] = dict(responders or {})
        self.fallback = fake_content.responder_for if use_fake_content else (lambda _n: None)
        self.calls: list[dict[str, Any]] = []

    def on(self, schema_name: str, fn: Responder) -> None:
        self.responders[schema_name] = fn

    def calls_for(self, prefix: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["schema"].startswith(prefix)]

    async def generate_json(self, *, model: str, system: str, prompt: str, schema: type[BaseModel], files=(), images=(),
                            temperature: float = 0.4, max_output_tokens: int | None = None, on_usage=None,
                            validate=None, validation_retries: int = 2) -> Any:
        name = schema.__name__
        fn = self.responders.get(name) or self.fallback(name)
        if fn is None:
            raise ProviderError(f"no fake response for {name}", provider="fake")
        problems: list[str] = []
        for attempt in range(validation_retries + 1):
            p = prompt if not problems else prompt + "\n\n## Problems with your previous answer\n" + "\n".join(problems)
            self.calls.append({"schema": name, "model": model, "system": system, "prompt": p, "attempt": attempt,
                               "files": len(files)})
            raw = fn(p, schema)
            if isinstance(raw, Exception):
                raise raw
            if on_usage is not None:
                on_usage(Usage(provider="fake", model=model, operation="llm", input_tokens=len(p) // 4, output_tokens=200))
            try:
                obj = raw if isinstance(raw, schema) else schema.model_validate(raw)
            except ValidationError as exc:
                problems = [str(exc)[:2000]]
                continue
            problems = list(validate(obj)) if validate is not None else []
            if not problems:
                return obj
        raise ProviderError("invalid output after retries: " + "; ".join(problems)[:500], provider="fake")

    async def generate_text(self, **kwargs: Any) -> str:  # pragma: no cover - unused by the pipeline
        return ""


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

TTS_RATE = 24_000
LEAD, TRAIL, WORD, GAP = 0.20, 0.30, 0.25, 0.06


def tone_wav(words: list[str], *, lead: float = LEAD, trail: float = TRAIL) -> tuple[bytes, list[WordTiming], float]:
    """Deterministic WAV: lead silence, a 440 Hz tone per word with short gaps, trail silence."""
    frames = bytearray()
    timings: list[WordTiming] = []

    def silence(sec: float) -> None:
        frames.extend(b"\x00\x00" * int(round(sec * TTS_RATE)))

    def tone(sec: float) -> None:
        n = int(round(sec * TTS_RATE))
        for i in range(n):
            frames.extend(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / TTS_RATE))))

    silence(lead)
    t = lead
    for k, w in enumerate(words):
        timings.append(WordTiming(text=w, start=round(t, 4), end=round(t + WORD, 4)))
        tone(WORD)
        t += WORD
        if k < len(words) - 1:
            silence(GAP)
            t += GAP
    silence(trail)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TTS_RATE)
        wf.writeframes(bytes(frames))
    return buf.getvalue(), timings, len(frames) / 2 / TTS_RATE


@dataclass
class FakeTTS:
    name: str = "fake"
    supports_word_timings: bool = True
    with_words: bool = True
    texts: list[str] = field(default_factory=list)

    def default_voice(self, language: str) -> str:
        return f"fake-{language}"

    def voices(self, language: str | None = None) -> list[dict[str, str]]:
        return [{"id": "fake-en-IN", "label": "Fake", "language": "en-IN", "gender": "Female"}]

    async def synthesize(self, text: str, *, voice: str, language: str, rate: str = "+0%", on_usage=None) -> SpeechResult:
        self.texts.append(text)
        data, words, duration = tone_wav(text.split())
        if on_usage is not None:
            on_usage(Usage(provider="fake", model="", operation="tts", characters=len(text)))
        return SpeechResult(audio=data, mime="audio/wav", duration=duration, words=words if self.with_words else [],
                            voice=voice, provider="fake", sample_rate=TTS_RATE)


# ---------------------------------------------------------------------------
# images / video / gifs
# ---------------------------------------------------------------------------


def png_bytes(w: int = 64, h: int = 48, seed: str = "x") -> bytes:
    from PIL import Image

    d = hashlib.sha256(seed.encode()).digest()
    img = Image.new("RGB", (w, h), (d[0], d[1], d[2]))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class FakeImage:
    name: str = "fake"
    prompts: list[str] = field(default_factory=list)

    async def generate(self, prompt: str, *, aspect: str = "16:9", on_usage=None) -> ImageResult:
        self.prompts.append(prompt)
        if on_usage is not None:
            on_usage(Usage(provider="fake", model="", operation="image", units=1))
        return ImageResult(data=png_bytes(seed=prompt), mime="image/png", width=64, height=48)


@dataclass
class FakeVideo:
    name: str = "fake"
    prompts: list[str] = field(default_factory=list)
    fail: bool = False

    async def generate(self, prompt: str, *, aspect: str = "16:9", reference_image=None, timeout_s: int = 420,
                       on_usage=None) -> VideoResult:
        if self.fail:
            raise ProviderError("video backend down", provider="fake")
        self.prompts.append(prompt)
        if on_usage is not None:
            on_usage(Usage(provider="fake", model="", operation="video", seconds=8))
        return VideoResult(data=b"\x00\x00\x00\x18ftypmp42" + prompt.encode()[:32], mime="video/mp4", duration=8.0,
                           width=1280, height=720)


@dataclass
class FakeGif:
    name: str = "fake"

    async def search(self, query: str, *, rating: str = "g", on_usage=None) -> GifResult:
        return GifResult(url=f"https://media.giphy.com/media/{abs(hash(query)) % 1000}/giphy.gif", width=200, height=150)


# ---------------------------------------------------------------------------
# Manim templates + renderer
# ---------------------------------------------------------------------------


class EquationStepsParams(BaseModel):
    title: str = ""
    steps: list[str] = Field(default_factory=list, description="LaTeX lines, one per animation step")


class FakeEquationSteps:
    name = "equation_steps"
    params_model = EquationStepsParams

    def step_count(self, params: BaseModel) -> int:
        return len(params.steps)  # type: ignore[attr-defined]

    def info(self) -> TemplateInfo:
        return TemplateInfo(name=self.name, title="Equation steps", description="Step-by-step algebra.",
                            params_schema=EquationStepsParams.model_json_schema(),
                            example_params={"title": "Solve", "steps": ["V = IR", "I = V/R", "I = 2"]},
                            steps_hint="one step per item of `steps`")

    def render_source(self, params: BaseModel, *, target: str, language: str) -> str:  # pragma: no cover
        return ""


FAKE_TEMPLATES = {"equation_steps": FakeEquationSteps()}


def install_fake_templates(monkeypatch: Any) -> None:
    """Point ``aadhi.pipeline.integrations`` template helpers at FAKE_TEMPLATES."""
    from aadhi.pipeline import integrations

    def get_template(name: str | None) -> Any:
        return FAKE_TEMPLATES.get(name or "")

    def validate_params(name: str, params: dict[str, Any]) -> tuple[BaseModel | None, list[str]]:
        tpl = FAKE_TEMPLATES.get(name)
        if tpl is None:
            return None, [f"unknown template {name}"]
        try:
            return tpl.params_model.model_validate(params), []
        except ValidationError as exc:
            return None, [str(exc)[:200]]

    monkeypatch.setattr(integrations, "list_templates", lambda: [t.info() for t in FAKE_TEMPLATES.values()])
    monkeypatch.setattr(integrations, "get_template", get_template)
    monkeypatch.setattr(integrations, "templates_available", lambda: True)
    monkeypatch.setattr(integrations, "validate_template_params", validate_params)
    monkeypatch.setattr(integrations, "manim_spec_problems", lambda spec, n_beats: None)  # use pipeline fallback checks


@dataclass
class FakeRenderer:
    requests: list[ManimRenderRequest] = field(default_factory=list)
    fail: bool = False
    fail_category: str = "render_failed"  # aadhi.manim.base.FAILURE_CATEGORIES

    async def __call__(self, ctx: Any, req: ManimRenderRequest) -> ManimRenderResult:
        from aadhi.manim.base import ManimError

        self.requests.append(req)
        if self.fail:
            raise ManimError("render crashed", log_tail="Traceback ...", category=self.fail_category)
        key = compute_key("manim", {"spec": req.spec.model_dump(mode="json"), "beats": req.beat_times,
                                    "total": req.total_duration, "target": req.target})
        asset = ctx.assets.put(key, "manim", Produced(data=b"\x00\x00\x00\x18ftypmp42manim", mime="video/mp4",
                                                      duration_s=req.total_duration, width=1280, height=720))
        return ManimRenderResult(asset_key=asset.key, storage_key=asset.storage_key, duration=req.total_duration,
                                 width=1280, height=720, final_spec=req.spec)


@dataclass
class Providers:
    llm: ScriptedLLM
    tts: FakeTTS
    image: FakeImage
    video: FakeVideo
    gif: FakeGif
    renderer: FakeRenderer


def install_providers(monkeypatch: Any, llm: ScriptedLLM | None = None, *, real_timeline: bool = False) -> Providers:
    """Patch every provider seam in ``aadhi.pipeline.integrations`` (timeline builder off unless asked)."""
    from aadhi.pipeline import integrations

    p = Providers(llm=llm or ScriptedLLM(), tts=FakeTTS(), image=FakeImage(), video=FakeVideo(), gif=FakeGif(),
                  renderer=FakeRenderer())
    monkeypatch.setattr(integrations, "get_llm", lambda settings, engine=None: p.llm)
    monkeypatch.setattr(integrations, "get_tts", lambda name, settings: p.tts)
    monkeypatch.setattr(integrations, "get_image", lambda settings: p.image)
    monkeypatch.setattr(integrations, "get_video", lambda settings: p.video)
    monkeypatch.setattr(integrations, "get_gif", lambda settings: p.gif)
    monkeypatch.setattr(integrations, "render_manim_fn", lambda: p.renderer)
    if not real_timeline:
        monkeypatch.setattr(integrations, "build_timeline_fn", lambda: None)
    return p


# ---------------------------------------------------------------------------
# audio without ffmpeg (WAV decode via the wave module; dummy MP3 bytes)
# ---------------------------------------------------------------------------


async def wav_decode(data: bytes, mime: str, *, ffmpeg: str = "ffmpeg") -> Any:
    import numpy as np

    with wave.open(io.BytesIO(data)) as wf:
        assert wf.getframerate() == TTS_RATE and wf.getnchannels() == 1
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype=np.int16).copy()


async def dummy_mp3(pcm: Any, *, ffmpeg: str = "ffmpeg") -> bytes:
    return b"ID3\x03\x00fake" + hashlib.sha256(pcm.tobytes()).digest()


def install_fast_audio(monkeypatch: Any) -> None:
    """Replace ffmpeg decode/encode in ``aadhi.pipeline.audio`` with in-process fakes."""
    from aadhi.pipeline import audio

    monkeypatch.setattr(audio, "decode_pcm", wav_decode)
    monkeypatch.setattr(audio, "encode_mp3", dummy_mp3)
