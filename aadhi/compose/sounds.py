"""Deterministic UI sound effects (quiz countdown tick, reveal ding).

The WAVs are synthesised with numpy (no randomness, so the bytes are identical everywhere) and
stored once through the content-addressed ``AssetStore`` under fixed keys. The web player gets
their URLs from ``Timeline.branding`` (filled by ``resolve_urls``); the MP4 renderer mixes the
local files.
"""

from __future__ import annotations

import asyncio
import io
import wave
from dataclasses import dataclass

import numpy as np

from ..storage.assets import AssetStore, Produced, compute_key

SAMPLE_RATE = 48_000
SOUND_VERSION = "1"
SOUND_KIND = "scene_audio"
SOUND_NAMES = ("tick", "ding")


def sound_key(name: str) -> str:
    """Fixed content address of a built-in sound."""
    if name not in SOUND_NAMES:
        raise KeyError(name)
    return compute_key(SOUND_KIND, {"sfx": name, "sr": SAMPLE_RATE}, version=f"sfx{SOUND_VERSION}")


TICK_KEY = sound_key("tick")
DING_KEY = sound_key("ding")


def _to_wav(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Mono float samples in [-1, 1] -> 16-bit PCM WAV bytes."""
    pcm = np.clip(samples, -1.0, 1.0)
    data = (pcm * 32767.0).round().astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data)
    return buf.getvalue()


def _envelope(n: int, attack_s: float, decay_tau_s: float, sample_rate: int) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / sample_rate
    attack = np.clip(t / max(attack_s, 1e-6), 0.0, 1.0)
    env = attack * np.exp(-t / decay_tau_s)
    fade = min(n, int(0.01 * sample_rate))  # 10 ms fade-out: no click at the end
    if fade > 0:
        env[-fade:] *= np.linspace(1.0, 0.0, fade)
    return env


def tick_wav(sample_rate: int = SAMPLE_RATE) -> bytes:
    """A short, soft wood-block style tick (90 ms)."""
    n = int(0.09 * sample_rate)
    t = np.arange(n, dtype=np.float64) / sample_rate
    tone = 0.7 * np.sin(2 * np.pi * 1850.0 * t) + 0.3 * np.sin(2 * np.pi * 3700.0 * t)
    return _to_wav(0.55 * tone * _envelope(n, 0.002, 0.018, sample_rate), sample_rate)


def ding_wav(sample_rate: int = SAMPLE_RATE) -> bytes:
    """A bell-like reveal ding (1.2 s) built from inharmonic partials."""
    n = int(1.2 * sample_rate)
    t = np.arange(n, dtype=np.float64) / sample_rate
    partials = ((880.0, 1.0, 0.45), (1760.0, 0.45, 0.30), (2637.0, 0.25, 0.18), (3520.0, 0.12, 0.10))
    sig = np.zeros(n)
    for freq, amp, tau in partials:
        sig += amp * np.sin(2 * np.pi * freq * t) * np.exp(-t / tau)
    sig /= max(1e-9, float(np.max(np.abs(sig))))
    return _to_wav(0.6 * sig * _envelope(n, 0.004, 10.0, sample_rate), sample_rate)


_GENERATORS = {"tick": tick_wav, "ding": ding_wav}


@dataclass(frozen=True)
class SoundAsset:
    name: str
    asset_key: str
    storage_key: str


def _produced(name: str) -> Produced:
    data = _GENERATORS[name]()
    return Produced(data=data, mime="audio/wav", duration_s=round((len(data) - 44) / 2 / SAMPLE_RATE, 3),
                    meta={"sfx": name, "version": SOUND_VERSION})


def ensure_sounds(store: AssetStore) -> dict[str, SoundAsset]:
    """Store the built-in sounds once (idempotent, blocking) and return them by name."""
    existing = store.get_many([sound_key(n) for n in SOUND_NAMES])
    out: dict[str, SoundAsset] = {}
    for name in SOUND_NAMES:
        key = sound_key(name)
        asset = existing.get(key)
        if asset is None or (store.storage.name == "local" and not store.storage.exists(asset.storage_key)):
            asset = store.put(key, SOUND_KIND, _produced(name))
        out[name] = SoundAsset(name=name, asset_key=key, storage_key=asset.storage_key)
    return out


async def ensure_sounds_async(store: AssetStore) -> dict[str, SoundAsset]:
    """Async variant of :func:`ensure_sounds` (in-process de-duplicated via ``get_or_create``)."""
    out: dict[str, SoundAsset] = {}
    for name in SOUND_NAMES:
        async def producer(n: str = name) -> Produced:
            return await asyncio.to_thread(_produced, n)

        asset, _ = await store.get_or_create(sound_key(name), SOUND_KIND, producer)
        out[name] = SoundAsset(name=name, asset_key=asset.key, storage_key=asset.storage_key)
    return out
