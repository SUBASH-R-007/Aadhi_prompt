"""B3-0 audio spike: measurements the B3 plan's policy table (§P) needs, run ONCE by the owner on the owner's own keys.

Owner decision O0 (2026-10-17): the owner runs this script; nobody else does, no test or CI job does, and no key ever
enters a session. It reads ``OPENAI_API_KEY`` and ``GEMINI_API_KEY`` from the environment, never prints, logs or stores
a key, and writes WAV samples plus ``report.json`` to the folder given on the command line (default ``C:/t/b3_spike``,
outside the repository). The lead then analyses the report and listens.

Keys: ``OPENAI_API_KEY`` is needed for items a-h and for item j's OpenAI part; ``GEMINI_API_KEY`` for item i and item
j's Gemini part. A key is required only by the items chosen: ``--only i`` runs on the Gemini key alone, and ``--only j``
runs on either key (the part whose key is missing is recorded as skipped). A run that could make no request at all
refuses before it starts.

    python scripts/b3_spike.py --dry-run          # no request at all: the plan, its cost estimate, a self-test
    python scripts/b3_spike.py                    # everything (a..j): an estimated $0.30, cents of it on Gemini
    python scripts/b3_spike.py --only a,g,h       # some items only
    python scripts/b3_spike.py --out D:/spike     # another output folder

Items (B3 plan §A B3-0 task 6):

* a - which of the design's 13 OpenAI voices ``gpt-4o-mini-tts`` accepts (one request per voice, the 45-word text);
* b - ``speed`` 0.90 / 1.00 / 1.10: natural re-pacing or a stretch (duration, speech and pause ratios, spectral
  centroid, F0 median; R-37 forbids stretching; the owner listens too);
* c - ``instructions`` "about N words per minute" for N = 140, 155, 170, three repeats each: measured wpm and spread;
* d - the design's §5.6 delivery styles as ``instructions``: wpm and loudness shift per row against no instruction;
* e - one 4-sentence beat synthesised per sentence vs per beat: requests, wall time, per-sentence loudness and pitch
  at each sentence start (prosody resets), loudness jumps, and the silence-detected sentence boundaries B3c's fallback
  would use; both versions saved for listening;
* f - an Indian-English accent instruction on every accepted voice (samples for owner decision O1);
* g - integrated loudness and sample-peak spread across the voices of item a (sizes the R-41 gains and headroom);
* h - byte-determinism of two identical requests;
* i - with ``GEMINI_API_KEY``: the same texts on ``gemini-3.8-flash-tts`` with a style (owner decision O2);
* j - the successor candidates (O2): OpenAI's model list and its Realtime speech path (``gpt-realtime-2.1-mini``, the
  replacement OpenAI names; voices accepted, style control, word timings, usage, a 4-sentence sample each) and Gemini
  TTS (model list; its samples are item i's). The report keeps what the run measured apart from what it only takes
  from the providers' documentation: documented facts sit under ``assumed_not_measured`` and must be checked before
  they go into the plan's §P.

Money: before any request the script estimates the cost of what it is about to do and refuses past ``--max-usd``
(default 1.00); while it runs it keeps a running estimate and stops making requests past the same cap. The estimate
uses the published per-minute prices this script names in ``PRICES`` (check them on the providers' pricing pages: they
are recorded in the report, not fetched). A failed item is recorded and the run goes on; the report is rewritten after
every item, so an interrupted run keeps what it measured.

Self-contained on purpose: Python 3.11, ``numpy`` and ``httpx`` (both in requirements.txt); ``websockets`` for the
Realtime probe and ``scipy`` for faster filtering are used when installed. It imports nothing from ``aadhi``.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import math
import os
import platform
import sys
import time
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_OUT = "C:/t/b3_spike"
ITEMS = ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j")
OPENAI_ITEMS = frozenset("abcdefgh")  # need OPENAI_API_KEY (g analyses item a's OpenAI samples)
GEMINI_ITEMS = frozenset("i")  # need GEMINI_API_KEY
EITHER_ITEMS = frozenset("j")  # an OpenAI part and a Gemini part: runs the part whose key is set
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model={model}"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
TTS_MODEL = "gpt-4o-mini-tts"
REALTIME_MODEL = "gpt-realtime-2.1-mini"
GEMINI_MODEL = "gemini-3.8-flash-tts"
SAMPLE_RATE = 24_000  # OpenAI speech WAV / PCM, Realtime pcm16 and Gemini L16 are all 24 kHz mono 16-bit

# The design's 13 OpenAI voices (§5.1: "13"; voices.py catalogues 9 of them).
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse",
                 "marin", "cedar")
PAIR = ("nova", "onyx")  # one female, one male voice that every OpenAI speech model has offered
REALTIME_SAMPLE_VOICES = ("marin", "cedar")
GEMINI_VOICES = ("Kore", "Charon")  # female, male (aadhi's catalogue)

# The calibration-like text (45 words, the length the plan's calibration uses).
CAL_TEXT = ("Waves carry energy from one place to another without carrying the matter along with them. When you drop "
            "a stone into a still pond, the ripples spread outwards, but the water itself only moves up and down. "
            "The speed of a wave depends on its medium.")
BEAT_SENTENCES = ("A wave carries energy, not matter.",
                  "Its speed is the frequency multiplied by the wavelength.",
                  "In air, sound travels at about three hundred and forty metres per second.",
                  "In steel, it travels more than fifteen times faster.")
BEAT_TEXT = " ".join(BEAT_SENTENCES)
SHORT_TEXT = "This short sentence checks whether two identical requests return identical audio."
SPEEDS = (0.90, 1.00, 1.10)
WPM_TARGETS = (140, 155, 170)
WPM_REPEATS = 3
# The design's §5.6 delivery styles (segment -> style text).
STYLES = {
    "hook": "Curious, energetic, a warm smile.",
    "explanation": "Warm, clear, measured, conversational.",
    "definition": "Clear, deliberate.",
    "formula": "Patient, step-by-step.",
    "misconception": "Emphatic, slower on the correction.",
    "question": "Inviting.",
    "recap": "Upbeat, confident.",
    "aadhi": "Friendly, bright, welcoming.",
}
ACCENT_INSTRUCTION = ("Speak English with a natural Indian accent, the way a friendly Indian teacher speaks to a class: "
                      "clear, warm and measured.")
REALTIME_INSTRUCTIONS = ("You are a text-to-speech voice. Read the user's text aloud exactly as written, word for word, "
                         "adding nothing. Speak in a warm, clear, measured teaching voice.")

# Published prices used for the estimate (USD; check them: they are recorded in the report, not fetched).
PRICES = {
    "openai_speech_per_minute": 0.015,  # gpt-4o-mini-tts, OPERATIONS §3.3 ("about $0.015 per minute")
    "openai_speech_per_million_chars": 16.0,  # the plan's per-character proxy (pricing.py)
    "openai_realtime_per_minute": 0.06,  # a deliberately high guess for the mini Realtime model's audio output
    "gemini_tts_per_minute": 0.0136,  # $0.068 per 5-minute lesson (design §5.1; doubles on 2027-01-01)
}
WORDS_PER_MINUTE_GUESS = 150.0


# --- secrets ------------------------------------------------------------------------------------------------------


class Secrets:
    """The keys, read from the environment once; :meth:`redact` removes them from any text that leaves the script."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        env = dict(os.environ if env is None else env)
        self.openai = (env.get("OPENAI_API_KEY") or "").strip()
        self.gemini = (env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or "").strip()

    def presence(self) -> dict[str, str]:
        return {"OPENAI_API_KEY": "present" if self.openai else "absent",
                "GEMINI_API_KEY": "present" if self.gemini else "absent"}

    def redact(self, text: Any) -> str:
        out = str(text)
        for key in (self.openai, self.gemini):
            if key:
                out = out.replace(key, "<redacted>")
                if len(key) > 12:
                    out = out.replace(key[:12], "<redacted>")
        return out


# --- audio analysis (numpy only; scipy speeds up the filters when present) -------------------------------------------


def parse_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Mono float samples in [-1, 1] and the rate of a 16-bit PCM WAV (a streamed header with a bogus data size is
    accepted: the data runs to the end of the file)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, rate, channels, bits, pcm = 12, SAMPLE_RATE, 1, 16, b""
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        if cid == b"fmt ":
            channels = int.from_bytes(data[body + 2:body + 4], "little")
            rate = int.from_bytes(data[body + 4:body + 8], "little")
            bits = int.from_bytes(data[body + 14:body + 16], "little")
        elif cid == b"data":
            end = len(data) if size in (0, 0xFFFFFFFF) or body + size > len(data) else body + size
            pcm = data[body:end]
            break
        pos = body + size + (size & 1)
    if bits != 16:
        raise ValueError(f"{bits}-bit WAV not supported")
    return pcm_to_float(pcm, channels), rate


def pcm_to_float(pcm: bytes, channels: int = 1) -> np.ndarray:
    pcm = pcm[: len(pcm) - len(pcm) % (2 * channels)]
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x


def to_wav(x: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _biquad(b: np.ndarray, a: np.ndarray, x: np.ndarray) -> np.ndarray:
    try:
        from scipy.signal import lfilter  # optional, much faster

        return lfilter(b, a, x)
    except ImportError:
        y = np.zeros_like(x)
        x1 = x2 = y1 = y2 = 0.0
        b0, b1, b2 = (float(v) for v in b)
        a1, a2 = float(a[1]), float(a[2])
        for i, xi in enumerate(x.tolist()):
            yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, xi, y1, yi
            y[i] = yi
        return y


def k_weight(x: np.ndarray, rate: int) -> np.ndarray:
    """ITU-R BS.1770-4 K-weighting (the pre-filter shelf and the RLB high-pass) designed for ``rate``."""
    f0, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = 10.0 ** (g / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    b = np.array([(vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0])
    a = np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0])
    y = _biquad(b, a, x)
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    a0 = 1.0 + k / q + k * k
    b = np.array([1.0, -2.0, 1.0])
    a = np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0])
    return _biquad(b, a, y)


def lufs(x: np.ndarray, rate: int) -> float | None:
    """Integrated loudness (BS.1770-4, one channel, gated), or None for too short or silent audio."""
    if len(x) < int(0.4 * rate):
        return None
    y = k_weight(x, rate)
    block, hop = int(0.4 * rate), int(0.1 * rate)
    z = np.array([np.mean(y[i:i + block] ** 2) for i in range(0, len(y) - block + 1, hop)])
    with np.errstate(divide="ignore"):
        loud = -0.691 + 10.0 * np.log10(z)
    gated = z[loud > -70.0]
    if not len(gated):
        return None
    rel = -0.691 + 10.0 * math.log10(float(np.mean(gated))) - 10.0
    final = z[(loud > -70.0) & (loud > rel)]
    return round(-0.691 + 10.0 * math.log10(float(np.mean(final))), 2) if len(final) else None


def peak_dbfs(x: np.ndarray) -> float | None:
    p = float(np.max(np.abs(x))) if len(x) else 0.0
    return round(20.0 * math.log10(p), 2) if p > 0 else None


def frame_db(x: np.ndarray, rate: int, frame_s: float = 0.010) -> np.ndarray:
    n = max(1, int(frame_s * rate))
    frames = len(x) // n
    if frames == 0:
        return np.array([-120.0])
    rms = np.sqrt(np.mean(x[: frames * n].reshape(frames, n) ** 2, axis=1))
    with np.errstate(divide="ignore"):
        return 20.0 * np.log10(np.maximum(rms, 1e-9))


def speech_mask(x: np.ndarray, rate: int, threshold_db: float = -40.0) -> np.ndarray:
    return frame_db(x, rate) > threshold_db


def gaps(mask: np.ndarray, min_frames: int = 12) -> list[tuple[int, int]]:
    """Silent runs (frames) of at least ``min_frames`` inside the speech span (10 ms frames: 12 = 120 ms)."""
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    out, start = [], None
    for i in range(int(idx[0]), int(idx[-1]) + 1):
        if not mask[i] and start is None:
            start = i
        elif mask[i] and start is not None:
            if i - start >= min_frames:
                out.append((start, i))
            start = None
    return out


def spectral_centroid(x: np.ndarray, rate: int, mask: np.ndarray) -> float | None:
    n, hop = 2048, int(0.010 * rate)
    window = np.hanning(n)
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    values = []
    for i in range(0, len(x) - n, hop * 4):
        if not mask[min(len(mask) - 1, i // hop)]:
            continue
        mag = np.abs(np.fft.rfft(x[i:i + n] * window))
        if mag.sum() > 0:
            values.append(float((freqs * mag).sum() / mag.sum()))
    return round(float(np.median(values)), 1) if values else None


def f0_track(x: np.ndarray, rate: int, *, fmin: float = 70.0, fmax: float = 400.0) -> list[tuple[float, float]]:
    """(time s, F0 Hz) of voiced 40 ms frames every 10 ms (normalised autocorrelation peak above 0.5), all frames at
    once through the FFT."""
    n, hop = int(0.040 * rate), int(0.010 * rate)
    lo, hi = int(rate / fmax), min(int(rate / fmin), n - 1)
    count = (len(x) - n) // hop + 1 if len(x) >= n else 0
    if count <= 0 or hi <= lo:
        return []
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop][:count]
    frames = frames - frames.mean(axis=1, keepdims=True)
    size = 1 << (2 * n - 1).bit_length()
    spec = np.fft.rfft(frames, size, axis=1)
    ac = np.fft.irfft(spec * np.conj(spec), size, axis=1)[:, :n]
    energy = ac[:, 0]
    lags = np.argmax(ac[:, lo:hi], axis=1) + lo
    strength = ac[np.arange(count), lags] / np.maximum(energy, 1e-12)
    voiced = (energy > 1e-6) & (strength > 0.5)
    return [(i * hop / rate, rate / int(lags[i])) for i in np.flatnonzero(voiced)]


def analyse(x: np.ndarray, rate: int, words: int) -> dict[str, Any]:
    """Duration, speech span, wpm over the span, pause share, loudness, peak, centroid and F0 of one sample."""
    mask = speech_mask(x, rate)
    idx = np.flatnonzero(mask)
    span = (int(idx[-1]) - int(idx[0]) + 1) * 0.010 if len(idx) else 0.0
    silent = sum(b - a for a, b in gaps(mask)) * 0.010
    f0 = [f for _, f in f0_track(x, rate)]
    return {
        "duration_s": round(len(x) / rate, 3), "speech_span_s": round(span, 3),
        "pause_s": round(silent, 3), "pause_share": round(silent / span, 3) if span else None,
        "words": words, "wpm": round(words / (span / 60.0), 1) if span else None,
        "lufs": lufs(x, rate), "peak_dbfs": peak_dbfs(x),
        "centroid_hz": spectral_centroid(x, rate, mask), "f0_median_hz": round(float(np.median(f0)), 1) if f0 else None,
    }


def words_in(text: str) -> int:
    return len(text.split())


def self_test() -> dict[str, Any]:
    """The analysis on synthetic audio (offline): a 997 Hz sine at full scale reads -3.01 LUFS (BS.1770's reference);
    two 'words' of tone around a 300 ms pause give the expected span, gap and wpm."""
    t = np.arange(int(3 * SAMPLE_RATE)) / SAMPLE_RATE
    sine = np.sin(2 * math.pi * 997.0 * t)
    tone = 0.3 * np.sin(2 * math.pi * 200.0 * np.arange(int(0.5 * SAMPLE_RATE)) / SAMPLE_RATE)
    pause = np.zeros(int(0.3 * SAMPLE_RATE))
    lead = np.zeros(int(0.2 * SAMPLE_RATE))
    speechish = np.concatenate([lead, tone, pause, tone, lead])
    a = analyse(speechish, SAMPLE_RATE, 2)
    return {"sine_997hz_0dbfs_lufs": lufs(sine, SAMPLE_RATE), "tone_pair": a,
            "tone_f0_hz": a["f0_median_hz"], "scipy": _has("scipy"), "websockets": _has("websockets")}


def _has(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


# --- the plan -----------------------------------------------------------------------------------------------------


@dataclass
class Planned:
    item: str
    provider: str  # openai | openai_realtime | gemini | listing | analysis
    what: str
    chars: int = 0
    instructions_chars: int = 0

    def minutes(self) -> float:
        """Audio minutes the text makes (about 5.5 characters per word at WORDS_PER_MINUTE_GUESS)."""
        return (self.chars / 5.5) / WORDS_PER_MINUTE_GUESS if self.chars else 0.0

    def usd(self) -> float:
        m = self.minutes()
        if self.provider == "openai":
            return max(m * PRICES["openai_speech_per_minute"],
                       self.chars * PRICES["openai_speech_per_million_chars"] / 1e6)
        if self.provider == "openai_realtime":
            return m * PRICES["openai_realtime_per_minute"]
        if self.provider == "gemini":
            return m * PRICES["gemini_tts_per_minute"]
        return 0.0


def build_plan(items: Iterable[str], *, gemini: bool, openai: bool = True) -> list[Planned]:
    """Every request the run would make (voices assumed accepted: the real run skips refused ones). The requests whose
    key is missing are left out (only item j's parts can be: every other item refuses to run without its key)."""
    plan: list[Planned] = []
    chosen = set(items)
    if "a" in chosen or "g" in chosen:
        plan += [Planned("a", "openai", f"speech {v} (45 words)", len(CAL_TEXT)) for v in OPENAI_VOICES]
    if "b" in chosen:
        plan += [Planned("b", "openai", f"speech {v} speed={s:.2f}", len(CAL_TEXT)) for v in PAIR for s in SPEEDS]
    if "c" in chosen:
        plan += [Planned("c", "openai", f"speech nova wpm={n} #{r + 1}", len(CAL_TEXT), len(_wpm_instruction(n)))
                 for n in WPM_TARGETS for r in range(WPM_REPEATS)]
    if "d" in chosen:
        plan += [Planned("d", "openai", f"speech nova style={row}", len(CAL_TEXT), len(text))
                 for row, text in STYLES.items()]
        if "a" not in chosen:
            plan.append(Planned("d", "openai", "speech nova baseline", len(CAL_TEXT)))
    if "e" in chosen:
        for v in PAIR:
            plan.append(Planned("e", "openai", f"speech {v} per beat", len(BEAT_TEXT), len(STYLES["explanation"])))
            plan += [Planned("e", "openai", f"speech {v} sentence {k + 1}", len(s), len(STYLES["explanation"]))
                     for k, s in enumerate(BEAT_SENTENCES)]
    if "f" in chosen:
        plan += [Planned("f", "openai", f"speech {v} Indian English", len(CAL_TEXT), len(ACCENT_INSTRUCTION))
                 for v in OPENAI_VOICES]
    if "g" in chosen:
        plan.append(Planned("g", "analysis", "loudness and peak spread of item a's samples (no request)"))
    if "h" in chosen:
        plan += [Planned("h", "openai", f"speech nova identical #{k + 1}", len(SHORT_TEXT)) for k in range(2)]
    if "i" in chosen and gemini:
        plan += [Planned("i", "gemini", f"{GEMINI_MODEL} {v} {what}", len(text) + len(STYLES["explanation"]))
                 for v in GEMINI_VOICES for what, text in (("45 words", CAL_TEXT), ("4 sentences", BEAT_TEXT))]
    if "j" in chosen:
        if openai:
            plan.append(Planned("j", "listing", "GET /v1/models (free)"))
            plan += [Planned("j", "openai_realtime", f"{REALTIME_MODEL} voice check {v} (session only)")
                     for v in OPENAI_VOICES]
            plan += [Planned("j", "openai_realtime", f"{REALTIME_MODEL} {v} 4 sentences", len(BEAT_TEXT),
                             len(REALTIME_INSTRUCTIONS)) for v in REALTIME_SAMPLE_VOICES]
        if gemini:
            plan.append(Planned("j", "listing", "GET Gemini models (free)"))
    return plan


def _wpm_instruction(n: int) -> str:
    return f"Speak at a steady pace of about {n} words per minute."


# --- the run ------------------------------------------------------------------------------------------------------


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Run:
    out: Path
    secrets: Secrets
    max_usd: float
    client: Any = None
    report: dict[str, Any] = field(default_factory=dict)
    spent_usd: float = 0.0
    requests: int = 0
    log: Callable[[str], None] = print

    @property
    def samples(self) -> Path:
        return self.out / "samples"

    def save_report(self) -> None:
        self.report["spent_estimate_usd"] = round(self.spent_usd, 4)
        self.report["requests"] = self.requests
        text = self.secrets.redact(json.dumps(self.report, indent=1, ensure_ascii=False, default=str))
        (self.out / "report.json").write_text(text + "\n", encoding="utf-8")

    def save(self, name: str, wav: bytes) -> str:
        self.samples.mkdir(parents=True, exist_ok=True)
        (self.samples / name).write_bytes(wav)
        return f"samples/{name}"

    def charge(self, planned: Planned) -> None:
        cost = planned.usd()
        if self.spent_usd + cost > self.max_usd:
            raise BudgetExceeded(f"the next request would pass --max-usd {self.max_usd:.2f}")
        self.spent_usd += cost
        self.requests += 1

    # -- OpenAI speech --
    def speech(self, item: str, voice: str, text: str, *, instructions: str = "", speed: float | None = None,
               model: str = TTS_MODEL) -> tuple[bytes | None, dict[str, Any]]:
        """One ``/v1/audio/speech`` request (WAV); ``(wav or None, {status, error, seconds, ...})``."""
        self.charge(Planned(item, "openai", voice, len(text), len(instructions)))
        body: dict[str, Any] = {"model": model, "voice": voice, "input": text, "response_format": "wav"}
        if instructions:
            body["instructions"] = instructions
        if speed is not None:
            body["speed"] = speed
        headers = {"Authorization": f"Bearer {self.secrets.openai}"}
        return self._post(OPENAI_SPEECH_URL, body, headers, binary=True)

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str], *,
              binary: bool) -> tuple[Any, dict[str, Any]]:
        meta: dict[str, Any] = {}
        for attempt in range(4):
            t0 = time.monotonic()
            try:
                r = self.client.post(url, json=body, headers=headers, timeout=180.0)
            except Exception as exc:  # network trouble: recorded, redacted
                meta = {"status": None, "error": self.secrets.redact(f"{type(exc).__name__}: {exc}")[:500]}
                time.sleep(2.0 * (attempt + 1))
                continue
            meta = {"status": r.status_code, "seconds": round(time.monotonic() - t0, 3)}
            if r.status_code == 200:
                return (r.content if binary else r.json()), meta
            meta["error"] = self.secrets.redact(r.text)[:500]
            if r.status_code not in (429, 500, 502, 503, 504):
                return None, meta
            time.sleep(min(20.0, 2.0 ** (attempt + 1)))
        return None, meta

    def get(self, url: str, headers: dict[str, str]) -> tuple[Any, dict[str, Any]]:
        """A free listing request (``/v1/models``, Gemini's model list)."""
        try:
            r = self.client.get(url, headers=headers, timeout=60.0)
        except Exception as exc:  # network trouble: recorded, redacted
            return None, {"status": None, "error": self.secrets.redact(f"{type(exc).__name__}: {exc}")[:500]}
        self.requests += 1
        if r.status_code != 200:
            return None, {"status": r.status_code, "error": self.secrets.redact(r.text)[:500]}
        return r.json(), {"status": 200}


def _sample(run: Run, name: str, wav: bytes | None, words: int) -> dict[str, Any]:
    if wav is None:
        return {}
    x, rate = parse_wav(wav)
    return {"file": run.save(name, to_wav(x, rate)), "sha256": hashlib.sha256(wav).hexdigest(),
            **analyse(x, rate, words)}


def item_a(run: Run) -> dict[str, Any]:
    out: dict[str, Any] = {"model": TTS_MODEL, "text_words": words_in(CAL_TEXT), "voices": {}}
    for v in OPENAI_VOICES:
        wav, meta = run.speech("a", v, CAL_TEXT)
        out["voices"][v] = {"accepted": wav is not None, **meta, **_sample(run, f"a_{v}.wav", wav, words_in(CAL_TEXT))}
        run.log(f"  a {v}: {'accepted' if wav is not None else 'refused'}")
    out["accepted"] = [v for v, r in out["voices"].items() if r["accepted"]]
    return out


def item_b(run: Run) -> dict[str, Any]:
    out: dict[str, Any] = {"voices": {}}
    for v in PAIR:
        rows = {}
        for s in SPEEDS:
            wav, meta = run.speech("b", v, CAL_TEXT, speed=s)
            rows[f"{s:.2f}"] = {**meta, **_sample(run, f"b_{v}_speed{s:.2f}.wav", wav, words_in(CAL_TEXT))}
        base = rows.get("1.00", {})
        for row in rows.values():
            if base.get("speech_span_s") and row.get("speech_span_s"):
                speech_base = base["speech_span_s"] - base["pause_s"]
                row["vs_1.00"] = {
                    "span_ratio": round(row["speech_span_s"] / base["speech_span_s"], 3),
                    "voiced_ratio": round((row["speech_span_s"] - row["pause_s"]) / speech_base, 3)
                    if speech_base else None,
                    "pause_ratio": round(row["pause_s"] / base["pause_s"], 3) if base["pause_s"] else None,
                    "f0_ratio": round(row["f0_median_hz"] / base["f0_median_hz"], 3)
                    if row.get("f0_median_hz") and base.get("f0_median_hz") else None,
                    "centroid_ratio": round(row["centroid_hz"] / base["centroid_hz"], 3)
                    if row.get("centroid_hz") and base.get("centroid_hz") else None,
                }
        out["voices"][v] = rows
    out["how_to_read"] = ("A uniform time-stretch scales voiced time and pauses by the same ratio with F0 unchanged; "
                          "natural re-pacing changes pauses more than voiced time. Listen to b_*.wav before deciding.")
    return out


def item_c(run: Run) -> dict[str, Any]:
    out: dict[str, Any] = {"voice": "nova", "targets": {}}
    for n in WPM_TARGETS:
        measured = []
        for r in range(WPM_REPEATS):
            wav, meta = run.speech("c", "nova", CAL_TEXT, instructions=_wpm_instruction(n))
            row = {**meta, **_sample(run, f"c_nova_wpm{n}_{r + 1}.wav", wav, words_in(CAL_TEXT))}
            measured.append(row)
        wpms = [m["wpm"] for m in measured if m.get("wpm")]
        out["targets"][str(n)] = {
            "instruction": _wpm_instruction(n), "runs": measured, "wpm": wpms,
            "mean": round(float(np.mean(wpms)), 1) if wpms else None,
            "spread_pm": round((max(wpms) - min(wpms)) / 2.0, 1) if wpms else None,
        }
    return out


def item_d(run: Run, baseline: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"voice": "nova", "rows": {}}
    if not baseline:
        wav, meta = run.speech("d", "nova", CAL_TEXT)
        baseline = {**meta, **_sample(run, "d_nova_baseline.wav", wav, words_in(CAL_TEXT))}
    out["baseline"] = baseline
    for row, text in STYLES.items():
        wav, meta = run.speech("d", "nova", CAL_TEXT, instructions=text)
        r = {"instructions": text, **meta, **_sample(run, f"d_nova_{row}.wav", wav, words_in(CAL_TEXT))}
        if r.get("lufs") is not None and baseline.get("lufs") is not None:
            r["lufs_shift"] = round(r["lufs"] - baseline["lufs"], 2)
        if r.get("wpm") and baseline.get("wpm"):
            r["wpm_shift"] = round(r["wpm"] - baseline["wpm"], 1)
        out["rows"][row] = r
    return out


def _sentence_segments(x: np.ndarray, rate: int, count: int) -> list[tuple[float, float]] | None:
    """B3c's fallback: sentence boundaries from silence (gaps >= 120 ms below -40 dBFS), the ``count - 1`` longest."""
    mask = speech_mask(x, rate)
    found = sorted(gaps(mask), key=lambda g: g[1] - g[0], reverse=True)[: count - 1]
    if len(found) < count - 1:
        return None
    idx = np.flatnonzero(mask)
    edges = [int(idx[0])] + [e for g in sorted(found) for e in g] + [int(idx[-1]) + 1]
    return [(edges[i] * 0.010, edges[i + 1] * 0.010) for i in range(0, len(edges), 2)]


def _start_f0(x: np.ndarray, rate: int, at: float, length: float = 0.4) -> float | None:
    seg = x[int(at * rate): int((at + length) * rate)]
    f0 = [f for _, f in f0_track(seg, rate)]
    return round(float(np.median(f0)), 1) if f0 else None


def item_e(run: Run) -> dict[str, Any]:
    style = STYLES["explanation"]
    pause = np.zeros(int(0.35 * SAMPLE_RATE))
    out: dict[str, Any] = {"style": style, "join_pause_s": 0.35, "voices": {}}
    for v in PAIR:
        t0 = time.monotonic()
        wav, meta = run.speech("e", v, BEAT_TEXT, instructions=style)
        beat_wall = round(time.monotonic() - t0, 3)
        beat = {"requests": 1, "wall_s": beat_wall, **meta, **_sample(run, f"e_{v}_per_beat.wav", wav,
                                                                     words_in(BEAT_TEXT))}
        if wav is not None:
            x, rate = parse_wav(wav)
            segs = _sentence_segments(x, rate, len(BEAT_SENTENCES))
            beat["silence_boundaries"] = [[round(a, 2), round(b, 2)] for a, b in segs] if segs else None
            if segs:
                beat["sentences"] = [{"lufs": lufs(x[int(a * rate):int(b * rate)], rate),
                                      "start_f0_hz": _start_f0(x, rate, a)} for a, b in segs]
        parts, rows, t0 = [], [], time.monotonic()
        for k, s in enumerate(BEAT_SENTENCES):
            wav_s, meta_s = run.speech("e", v, s, instructions=style)
            row = {**meta_s, **_sample(run, f"e_{v}_sentence{k + 1}.wav", wav_s, words_in(s))}
            if wav_s is not None:
                xs, rate = parse_wav(wav_s)
                mask = speech_mask(xs, rate)
                idx = np.flatnonzero(mask)
                if len(idx):
                    xs = xs[int(idx[0]) * rate // 100: (int(idx[-1]) + 1) * rate // 100]
                row["start_f0_hz"] = _start_f0(xs, rate, 0.0)
                parts += [xs, pause]
            rows.append(row)
        joined = {"requests": len(BEAT_SENTENCES), "wall_s": round(time.monotonic() - t0, 3), "sentences": rows}
        if parts:
            x = np.concatenate(parts[:-1])
            joined.update(_sample(run, f"e_{v}_per_sentence_joined.wav", to_wav(x), words_in(BEAT_TEXT)))
        louds = [r.get("lufs") for r in rows if r.get("lufs") is not None]
        joined["max_lufs_jump"] = round(max(abs(a - b) for a, b in zip(louds, louds[1:])), 2) if len(louds) > 1 else None
        out["voices"][v] = {"per_beat": beat, "per_sentence": joined}
    out["how_to_read"] = ("Listen to e_*_per_beat.wav against e_*_per_sentence_joined.wav: audible joins or pitch "
                          "resets at each sentence start argue for per-beat synthesis with silence boundaries (B3c).")
    return out


def item_f(run: Run, accepted: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"instructions": ACCENT_INSTRUCTION, "voices": {}}
    for v in accepted or list(OPENAI_VOICES):
        wav, meta = run.speech("f", v, CAL_TEXT, instructions=ACCENT_INSTRUCTION)
        out["voices"][v] = {**meta, **_sample(run, f"f_{v}_indian_english.wav", wav, words_in(CAL_TEXT))}
    out["owner_listens"] = "Pick one female and one male voice whose Indian English sounds natural (O1 (b))."
    return out


def item_g(a: dict[str, Any]) -> dict[str, Any]:
    rows = {v: r for v, r in (a.get("voices") or {}).items() if r.get("lufs") is not None}
    if not rows:
        return {"error": "no accepted voice from item a"}
    louds = [r["lufs"] for r in rows.values()]
    peaks = [r["peak_dbfs"] for r in rows.values() if r.get("peak_dbfs") is not None]
    gains = {v: round(max(-12.0, min(12.0, -16.0 - r["lufs"])), 2) for v, r in rows.items()}
    capped = {v: round(min(gains[v], -1.0 - r["peak_dbfs"]), 2) for v, r in rows.items() if r.get("peak_dbfs") is not None}
    return {"lufs": {v: r["lufs"] for v, r in rows.items()}, "peak_dbfs": {v: r["peak_dbfs"] for v, r in rows.items()},
            "lufs_spread": round(max(louds) - min(louds), 2), "peak_spread": round(max(peaks) - min(peaks), 2)
            if peaks else None, "gain_to_-16": gains, "gain_with_headroom_cap": capped,
            "capped_by_headroom": sorted(v for v in capped if capped[v] < gains[v])}


def item_h(run: Run) -> dict[str, Any]:
    a, meta_a = run.speech("h", "nova", SHORT_TEXT)
    b, meta_b = run.speech("h", "nova", SHORT_TEXT)
    out: dict[str, Any] = {"requests": [meta_a, meta_b]}
    if a is not None and b is not None:
        xa, _ = parse_wav(a)
        xb, _ = parse_wav(b)
        n = min(len(xa), len(xb))
        out.update({"bytes_identical": a == b, "sha256": [hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest()],
                    "samples": [len(xa), len(xb)],
                    "max_abs_diff": round(float(np.max(np.abs(xa[:n] - xb[:n]))), 5) if n else None})
        run.save("h_nova_1.wav", a)
        run.save("h_nova_2.wav", b)
    return out


def item_i(run: Run) -> dict[str, Any]:
    if not run.secrets.gemini:
        return {"skipped": "GEMINI_API_KEY is not set"}
    out: dict[str, Any] = {"model": GEMINI_MODEL, "style": STYLES["explanation"], "voices": {}}
    for v in GEMINI_VOICES:
        rows = {}
        for what, text in (("45_words", CAL_TEXT), ("4_sentences", BEAT_TEXT)):
            pcm, meta = gemini_speech(run, v, text, STYLES["explanation"])
            rows[what] = {**meta, **(_sample(run, f"i_{v}_{what}.wav", to_wav(pcm_to_float(pcm)), words_in(text))
                                     if pcm else {})}
        out["voices"][v] = rows
    return out


def gemini_speech(run: Run, voice: str, text: str, style: str) -> tuple[bytes | None, dict[str, Any]]:
    run.charge(Planned("i", "gemini", voice, len(text) + len(style)))
    body = {"contents": [{"parts": [{"text": f"Say in this style ({style.rstrip('.')}): {text}"}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    data, meta = run._post(f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent", body,
                           {"x-goog-api-key": run.secrets.gemini}, binary=False)
    if not data:
        return None, meta
    try:
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        meta["mime"] = part.get("mimeType")
        meta["usage"] = data.get("usageMetadata")
        return base64.b64decode(part["data"]), meta
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        meta["error"] = f"unexpected response shape ({type(exc).__name__})"
        return None, meta


# What item j reports about Gemini TTS without measuring it. It was taken from Google's documentation when the script
# was written (2026-10), NOT from this run, and sits under its own key so that it is never read as a measurement.
# Check it on the provider's pages before it goes into the plan's §P (j).
GEMINI_DOCUMENTED = {
    "source": "Gemini API speech-generation documentation, read 2026-10; not measured by this run",
    "voices_offered": "30 prebuilt voices; none shares an OpenAI voice name",
    "word_timings": "none documented for generateContent audio",
}


def item_j(run: Run, a: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"openai": {}, "gemini": {}}
    if run.secrets.openai:
        models, meta = run.get(OPENAI_MODELS_URL, {"Authorization": f"Bearer {run.secrets.openai}"})
        ids = sorted(m.get("id", "") for m in (models or {}).get("data", []))
        out["openai"]["models"] = [i for i in ids if any(w in i for w in ("tts", "realtime", "audio", "speech"))]
        out["openai"]["models_request"] = meta
        out["openai"]["realtime"] = realtime_probe(run, a)
    else:
        out["openai"] = {"skipped": "OPENAI_API_KEY is not set"}
    if run.secrets.gemini:
        listed, meta = run.get(f"{GEMINI_BASE}/models?pageSize=1000", {"x-goog-api-key": run.secrets.gemini})
        names = sorted(m.get("name", "") for m in (listed or {}).get("models", []))
        out["gemini"] = {"models": [n for n in names if "tts" in n or "audio" in n], "models_request": meta,
                         "samples": "see item i", "assumed_not_measured": dict(GEMINI_DOCUMENTED)}
    else:
        out["gemini"] = {"skipped": "GEMINI_API_KEY is not set"}
    out["prices_used_for_estimates"] = PRICES
    return out


def realtime_probe(run: Run, a: dict[str, Any] | None) -> dict[str, Any]:
    """OpenAI's Realtime speech path: which voices a session accepts (no response is created: no audio billed), then a
    4-sentence sample per sample voice with the style in the session instructions."""
    try:
        from websockets.sync.client import connect
    except ImportError:
        return {"skipped": "the websockets package is not installed"}
    url = OPENAI_REALTIME_URL.format(model=REALTIME_MODEL)
    headers = {"Authorization": f"Bearer {run.secrets.openai}"}
    out: dict[str, Any] = {"model": REALTIME_MODEL, "voices": {}, "samples": {}}

    def session(ws: Any, voice: str, instructions: str) -> dict[str, Any]:
        ws.send(json.dumps({"type": "session.update", "session": {
            "type": "realtime", "output_modalities": ["audio"], "instructions": instructions,
            "audio": {"output": {"voice": voice, "format": {"type": "audio/pcm", "rate": SAMPLE_RATE}}}}}))
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            event = json.loads(ws.recv(timeout=15.0))
            if event.get("type") == "session.updated":
                return {"accepted": True}
            if event.get("type") == "error":
                return {"accepted": False, "error": run.secrets.redact(json.dumps(event.get("error")))[:400]}
        return {"accepted": False, "error": "no answer to session.update"}

    for v in OPENAI_VOICES:
        try:
            with connect(url, additional_headers=headers, open_timeout=30) as ws:
                out["voices"][v] = session(ws, v, REALTIME_INSTRUCTIONS)
        except Exception as exc:
            out["voices"][v] = {"accepted": False, "error": run.secrets.redact(f"{type(exc).__name__}: {exc}")[:400]}
        run.requests += 1
    for v in REALTIME_SAMPLE_VOICES:
        if not out["voices"].get(v, {}).get("accepted"):
            out["samples"][v] = {"skipped": "voice not accepted"}
            continue
        run.charge(Planned("j", "openai_realtime", v, len(BEAT_TEXT), len(REALTIME_INSTRUCTIONS)))
        out["samples"][v] = realtime_sample(run, connect, url, headers, v, session)
    accepted_speech = set((a or {}).get("accepted") or [])
    accepted_rt = {v for v, r in out["voices"].items() if r.get("accepted")}
    out["accepted"] = sorted(accepted_rt)
    out["intersection_with_item_a"] = sorted(accepted_rt & accepted_speech) if accepted_speech else None
    # measured: whether a session that carries free-text instructions was accepted (their effect: listen to the samples)
    out["style_control"] = {"how": "session instructions (free text)", "accepted_by_session_update": bool(accepted_rt)}
    return out


def realtime_sample(run: Run, connect: Any, url: str, headers: dict[str, str], voice: str,
                    session: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    chunks: list[bytes] = []
    transcript: list[str] = []
    kinds: set[str] = set()
    result: dict[str, Any] = {}
    t0 = time.monotonic()
    try:
        with connect(url, additional_headers=headers, open_timeout=30) as ws:
            state = session(ws, voice, REALTIME_INSTRUCTIONS + " Style: " + STYLES["explanation"])
            if not state.get("accepted"):
                return state
            ws.send(json.dumps({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": BEAT_TEXT}]}}))
            ws.send(json.dumps({"type": "response.create"}))
            deadline = time.monotonic() + 90.0
            while time.monotonic() < deadline:
                event = json.loads(ws.recv(timeout=90.0))
                kind = str(event.get("type"))
                kinds.add(kind)
                if kind in ("response.output_audio.delta", "response.audio.delta"):
                    chunks.append(base64.b64decode(event.get("delta") or ""))
                elif kind in ("response.output_audio_transcript.delta", "response.audio_transcript.delta"):
                    transcript.append(str(event.get("delta") or ""))
                elif kind == "error":
                    result["error"] = run.secrets.redact(json.dumps(event.get("error")))[:400]
                    break
                elif kind == "response.done":
                    result["usage"] = (event.get("response") or {}).get("usage")
                    break
    except Exception as exc:
        result["error"] = run.secrets.redact(f"{type(exc).__name__}: {exc}")[:400]
    result["wall_s"] = round(time.monotonic() - t0, 3)
    result["event_types"] = sorted(kinds)
    result["word_timing_events"] = sorted(k for k in kinds if "word" in k or "timestamp" in k) or None
    result["transcript"] = "".join(transcript)[:2000]
    if chunks:
        x = pcm_to_float(b"".join(chunks))
        result.update(_sample(run, f"j_realtime_{voice}_4_sentences.wav", to_wav(x), words_in(BEAT_TEXT)))
    return result


# --- main ---------------------------------------------------------------------------------------------------------


def missing_keys(items: Iterable[str], secrets: Secrets) -> str | None:
    """Why the chosen items cannot run on the keys that are set, or None when they can. A key is required only by the
    items that need it; Gemini stays optional for a run that also has OpenAI items (items i and j record it as
    skipped)."""
    chosen = set(items)
    openai_items = sorted(chosen & OPENAI_ITEMS)
    if openai_items and not secrets.openai:
        return f"OPENAI_API_KEY is not set in the environment (items {','.join(openai_items)} need it)"
    runnable = bool(secrets.openai and chosen & (OPENAI_ITEMS | EITHER_ITEMS)) or bool(
        secrets.gemini and chosen & (GEMINI_ITEMS | EITHER_ITEMS))
    if not runnable:
        needs = "GEMINI_API_KEY" if chosen <= GEMINI_ITEMS else "OPENAI_API_KEY or GEMINI_API_KEY"
        return f"{needs} is not set in the environment: no chosen item could make a request"
    return None


def parse_items(text: str | None) -> list[str]:
    if not text:
        return list(ITEMS)
    items = [t.strip().lower() for t in text.split(",") if t.strip()]
    bad = [t for t in items if t not in ITEMS]
    if bad:
        raise SystemExit(f"unknown item(s) {bad}; choose from {','.join(ITEMS)}")
    return items


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None, transport: Any = None) -> int:
    """The command line. ``env`` replaces ``os.environ`` and ``transport`` is an ``httpx`` transport: both for the
    script's own offline tests only (a fake server), never for the owner's run."""
    ap = argparse.ArgumentParser(description="B3-0 audio spike (owner-run, paid; see the module docstring).")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"output folder (default {DEFAULT_OUT})")
    ap.add_argument("--dry-run", action="store_true", help="make no request: write the plan, estimate and self-test")
    ap.add_argument("--only", help=f"comma-separated items (default all: {','.join(ITEMS)})")
    ap.add_argument("--max-usd", type=float, default=1.0, help="refuse to spend more than this estimate (USD)")
    args = ap.parse_args(argv)
    secrets = Secrets(env)
    items = parse_items(args.only)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    plan = build_plan(items, gemini=bool(secrets.gemini) or args.dry_run,
                      openai=bool(secrets.openai) or args.dry_run)
    estimate = round(sum(p.usd() for p in plan), 4)
    report: dict[str, Any] = {
        "script": "scripts/b3_spike.py", "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "dry_run": bool(args.dry_run), "items": items, "keys": secrets.presence(), "python": platform.python_version(),
        "models": {"speech": TTS_MODEL, "realtime": REALTIME_MODEL, "gemini": GEMINI_MODEL},
        "plan": [p.__dict__ | {"usd": round(p.usd(), 5)} for p in plan], "estimate_usd": estimate,
        "max_usd": args.max_usd, "prices": PRICES, "results": {},
    }
    run = Run(out=out, secrets=secrets, max_usd=args.max_usd, report=report)
    print(f"B3 spike: {len(plan)} planned requests, estimated ${estimate:.2f} (cap ${args.max_usd:.2f}); "
          f"keys: {secrets.presence()}")
    if args.dry_run:
        report["self_test"] = self_test()
        report["note"] = "dry run: no request was made"
        run.save_report()
        print(f"dry run: wrote {out / 'report.json'} (no request made)")
        return 0
    refused = missing_keys(items, secrets)
    if refused:
        print(f"{refused}: nothing was run.", file=sys.stderr)
        return 2
    if estimate > args.max_usd:
        print(f"the estimate ${estimate:.2f} is above --max-usd {args.max_usd:.2f}: nothing was run.", file=sys.stderr)
        return 3
    import httpx

    results = report["results"]
    with httpx.Client(timeout=180.0, transport=transport) as client:
        run.client = client
        steps: list[tuple[str, Callable[[], Any]]] = [
            ("a", lambda: item_a(run)), ("b", lambda: item_b(run)), ("c", lambda: item_c(run)),
            ("d", lambda: item_d(run, ((results.get("a") or {}).get("voices") or {}).get("nova"))),
            ("e", lambda: item_e(run)), ("f", lambda: item_f(run, (results.get("a") or {}).get("accepted") or [])),
            ("g", lambda: item_g(results.get("a") or {})), ("h", lambda: item_h(run)), ("i", lambda: item_i(run)),
            ("j", lambda: item_j(run, results.get("a"))),
        ]
        wanted = set(items) | ({"a"} if "g" in items else set())
        for name, step in steps:
            if name not in wanted:
                continue
            print(f"item {name} ...")
            try:
                results[name] = step()
            except BudgetExceeded as exc:
                results[name] = {"stopped": str(exc)}
                run.save_report()
                print(f"stopped: {exc}", file=sys.stderr)
                break
            except Exception as exc:  # one item failing never loses the others
                results[name] = {"error": secrets.redact(f"{type(exc).__name__}: {exc}")[:800]}
            run.save_report()
    report["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    run.save_report()
    print(f"done: {run.requests} requests, about ${run.spent_usd:.2f}; report and samples in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
