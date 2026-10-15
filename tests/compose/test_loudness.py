"""``aadhi.compose.loudness``: the ebur128 / loudnorm parsers and filter builders (pure), and real measurements
and two-pass normalisation of generated audio (slow: runs the local ffmpeg; ``container``: the Docker test image
runs them on ffmpeg 5.1)."""

from __future__ import annotations

import asyncio
import json
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from aadhi.compose.ffmpeg import run_ffmpeg
from aadhi.compose.loudness import (
    LoudnessError,
    LoudnessTarget,
    LoudnormStats,
    R128Summary,
    ebur128,
    ebur128_filter,
    limiter_filter,
    loudnorm_filter,
    loudnorm_measure,
    loudnorm_normalize,
    parse_ebur128,
    parse_loudnorm,
    static_gain_db,
)
from aadhi.config import Settings

EBUR128_LOG = """\
Input #0, wav, from 'tone.wav':
  Duration: 00:00:05.00, bitrate: 1536 kb/s
  Stream #0:0: Audio: pcm_s16le ([1][0][0][0] / 0x0001), 48000 Hz, 2 channels, s16, 1536 kb/s
[Parsed_ebur128_0 @ 0x5581c0e2a380] Summary:

  Integrated loudness:
    I:         -20.0 LUFS
    Threshold: -30.0 LUFS

  Loudness range:
    LRA:         0.4 LU
    Threshold: -40.0 LUFS
    LRA low:   -20.2 LUFS
    LRA high:  -19.8 LUFS

  Sample peak:
    Peak:      -20.0 dBFS

  True peak:
    Peak:      -19.9 dBFS
[out#0/null @ 0x5581c0e29e40] video:0KiB audio:938KiB subtitle:0KiB other streams:0KiB global headers:0KiB
size=N/A time=00:00:05.00 bitrate=N/A speed= 167x
"""

SILENT_LOG = """\
[Parsed_ebur128_0 @ 0000023fb334f100] Summary:

  Integrated loudness:
    I:         -70.0 LUFS
    Threshold:   0.0 LUFS

  Loudness range:
    LRA:         0.0 LU
    Threshold:   0.0 LUFS
    LRA low:     0.0 LUFS
    LRA high:    0.0 LUFS

  Sample peak:
    Peak:       -inf dBFS

  True peak:
    Peak:       -inf dBFS
"""

LOUDNORM_LOG = """\
Stream mapping:
  Stream #0:0 -> #0:0 (pcm_s16le (native) -> pcm_s16le (native))
[Parsed_loudnorm_0 @ 000001e72f190900]
{
\t"input_i" : "-20.05",
\t"input_tp" : "-20.00",
\t"input_lra" : "0.00",
\t"input_thresh" : "-30.05",
\t"output_i" : "-15.95",
\t"output_tp" : "-15.96",
\t"output_lra" : "0.00",
\t"output_thresh" : "-25.95",
\t"normalization_type" : "dynamic",
\t"target_offset" : "-0.05"
}
[out#0/null @ 000001e72f18f800] video:0KiB audio:3750KiB subtitle:0KiB other streams:0KiB global headers:0KiB
"""


# --- parsers -----------------------------------------------------------------------------------------


def test_parse_ebur128_summary() -> None:
    s = parse_ebur128(EBUR128_LOG)
    assert s == R128Summary(integrated=-20.0, threshold=-30.0, lra=0.4, lra_threshold=-40.0, lra_low=-20.2,
                            lra_high=-19.8, sample_peak=-20.0, true_peak=-19.9)
    assert not s.silent
    assert s.as_dict() == {"integrated_lufs": -20.0, "threshold_lufs": -30.0, "lra_lu": 0.4,
                           "lra_threshold_lufs": -40.0, "lra_low_lufs": -20.2, "lra_high_lufs": -19.8,
                           "sample_peak_dbfs": -20.0, "true_peak_dbtp": -19.9}
    json.dumps(s.as_dict(), allow_nan=False)  # JSON-ready


def test_parse_ebur128_silence_and_missing_values() -> None:
    s = parse_ebur128(SILENT_LOG)
    assert s is not None and s.silent and s.integrated == -70.0
    assert s.true_peak == -math.inf and s.sample_peak == -math.inf
    d = s.as_dict()
    assert d["true_peak_dbtp"] is None and d["sample_peak_dbfs"] is None and d["integrated_lufs"] == -70.0
    json.dumps(d, allow_nan=False)
    # a summary without the peak sections (ebur128 without peak=): peaks are unknown, not 0
    no_peaks = parse_ebur128(SILENT_LOG.split("  Sample peak:")[0])
    assert no_peaks is not None and math.isnan(no_peaks.true_peak) and no_peaks.as_dict()["true_peak_dbtp"] is None
    # the LAST summary wins (several runs in one log)
    assert parse_ebur128(SILENT_LOG + EBUR128_LOG).integrated == -20.0
    assert parse_ebur128("") is None and parse_ebur128("no summary here") is None
    assert parse_ebur128("Summary:\n  Loudness range:\n    LRA: 3.0 LU\n") is None  # no integrated loudness


def test_parse_loudnorm_json() -> None:
    st = parse_loudnorm(LOUDNORM_LOG)
    assert st is not None
    assert (st.input_i, st.input_tp, st.input_lra, st.input_thresh) == (-20.05, -20.0, 0.0, -30.05)
    assert (st.output_i, st.output_tp, st.target_offset) == (-15.95, -15.96, -0.05)
    assert st.normalization_type == "dynamic" and not st.linear and st.measurable
    assert st.raw["input_i"] == "-20.05"
    assert st.as_dict()["input_i"] == -20.05 and st.as_dict()["normalization_type"] == "dynamic"
    linear = parse_loudnorm(LOUDNORM_LOG.replace('"dynamic"', '"Linear"'))
    assert linear is not None and linear.linear
    # the last report wins; silence is not measurable and serialises without NaN/inf
    silent = parse_loudnorm(LOUDNORM_LOG + LOUDNORM_LOG.replace('"-20.05"', '"-inf"')
                            .replace('"-20.00"', '"-inf"').replace('"-0.05"', '"inf"'))
    assert silent is not None and silent.input_i == -math.inf and not silent.measurable
    assert silent.target_offset == 0.0
    json.dumps(silent.as_dict(), allow_nan=False)
    assert parse_loudnorm("") is None and parse_loudnorm("{ not json }") is None
    assert parse_loudnorm('{"input_i" : }') is None
    assert parse_loudnorm('{"input_i" : "x"}') is None


# --- filter builders ---------------------------------------------------------------------------------


def test_targets_are_validated() -> None:
    assert LoudnessTarget() == LoudnessTarget(-16.0, -1.5, 9.0)
    LoudnessTarget(-14.0, -1.0, 9.0)
    for bad in [dict(integrated=-4.0), dict(integrated=-71.0), dict(true_peak=0.5), dict(true_peak=-10.0),
                dict(lra=0.5), dict(lra=21.0), dict(integrated=math.nan)]:
        with pytest.raises(ValueError):
            LoudnessTarget(**bad)


def test_loudnorm_filters() -> None:
    target = LoudnessTarget(-14.0, -1.5, 9.0)
    assert loudnorm_filter(target) == "loudnorm=I=-14:TP=-1.5:LRA=9:print_format=json"
    assert loudnorm_filter(target, report=False) == "loudnorm=I=-14:TP=-1.5:LRA=9"
    first = parse_loudnorm(LOUDNORM_LOG)
    assert loudnorm_filter(target, first) == (
        "loudnorm=I=-14:TP=-1.5:LRA=9:measured_I=-20.05:measured_TP=-20:measured_LRA=0.01:"
        "measured_thresh=-30.05:offset=-0.05:linear=true:print_format=json")
    assert ":linear=false:" in loudnorm_filter(target, first, linear=False)
    # out-of-range measurements are clamped to what ffmpeg accepts
    loud = LoudnormStats(input_i=-0.5, input_tp=120.0, input_lra=150.0, input_thresh=-10.0, target_offset=-120.0)
    assert "measured_TP=99:measured_LRA=99" in loudnorm_filter(target, loud) and "offset=-99" in loudnorm_filter(
        target, loud)
    silent = LoudnormStats(input_i=-math.inf, input_tp=-math.inf, input_lra=0.0, input_thresh=-70.0)
    with pytest.raises(LoudnessError, match="silent"):
        loudnorm_filter(target, silent)


def test_ebur128_limiter_and_static_gain() -> None:
    assert ebur128_filter() == "ebur128=peak=true+sample:framelog=verbose"
    assert ebur128_filter(true_peak=False) == "ebur128=peak=sample:framelog=verbose"
    assert limiter_filter() == "alimiter=limit=0.84:attack=5:release=50:level=0"
    with pytest.raises(ValueError):
        limiter_filter(limit=1.5)
    target = LoudnessTarget(-16.0)
    assert static_gain_db(R128Summary(integrated=-23.4), target) == pytest.approx(7.4)
    assert static_gain_db(parse_loudnorm(LOUDNORM_LOG), target) == pytest.approx(4.05)
    assert static_gain_db(R128Summary(integrated=-70.0), target) == 0.0
    assert static_gain_db(R128Summary(integrated=-math.inf), target) == 0.0


# --- real ffmpeg on generated audio (slow) -----------------------------------------------------------


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


def write_tone(path: Path, *, amplitude: float, seconds: float = 5.0, freq: float = 1000.0, rate: int = 48000,
               channels: int = 2) -> Path:
    """A sine at ``amplitude`` (peak, linear) in every channel: stereo at A dBFS measures about A LUFS."""
    t = np.arange(int(seconds * rate)) / rate
    mono = np.round(amplitude * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    data = np.repeat(mono[:, None], channels, axis=1).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return path


def db(amplitude: float) -> float:
    return 20 * math.log10(amplitude)


@pytest.mark.slow
@pytest.mark.container
def test_ebur128_measures_a_known_tone(tmp_path: Path, settings: Settings) -> None:
    tone = write_tone(tmp_path / "tone.wav", amplitude=0.1)  # -20 dBFS stereo sine = -20 LUFS
    s = asyncio.run(ebur128(tone, settings))
    assert s.integrated == pytest.approx(-20.0, abs=0.3)
    assert s.sample_peak == pytest.approx(db(0.1), abs=0.2)
    assert s.true_peak == pytest.approx(db(0.1), abs=0.5)
    assert s.lra == pytest.approx(0.0, abs=0.5) and not s.silent
    quieter = asyncio.run(ebur128(write_tone(tmp_path / "q.wav", amplitude=0.05), settings, true_peak=False))
    assert quieter.integrated == pytest.approx(s.integrated + db(0.5), abs=0.3)  # -6 dB
    assert math.isnan(quieter.true_peak)


@pytest.mark.slow
@pytest.mark.container
def test_ebur128_on_silence_and_on_video_files(tmp_path: Path, settings: Settings) -> None:
    silence = write_tone(tmp_path / "silence.wav", amplitude=0.0, seconds=2.0)
    s = asyncio.run(ebur128(silence, settings))
    assert s.silent and s.true_peak == -math.inf
    # an MP4 with a picture: only the first audio stream is decoded
    mp4 = tmp_path / "av.mp4"
    asyncio.run(run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=3", "-i",
                            str(write_tone(tmp_path / "t.wav", amplitude=0.1, seconds=3.0)), "-c:v", "libx264",
                            "-preset", "ultrafast", "-c:a", "aac", "-b:a", "192k", "-shortest", str(mp4)],
                           settings=settings))
    assert asyncio.run(ebur128(mp4, settings)).integrated == pytest.approx(-20.0, abs=0.5)
    picture_only = tmp_path / "v.mp4"
    asyncio.run(run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=1", "-c:v", "libx264", "-preset",
                            "ultrafast", str(picture_only)], settings=settings))
    with pytest.raises(LoudnessError):
        asyncio.run(ebur128(picture_only, settings))


@pytest.mark.slow
@pytest.mark.container
def test_two_pass_loudnorm_reaches_the_target_linearly(tmp_path: Path, settings: Settings) -> None:
    src = write_tone(tmp_path / "src.wav", amplitude=0.1, rate=24000)  # TTS-like 24 kHz input
    target = LoudnessTarget(-16.0, -1.5, 9.0)
    first = asyncio.run(loudnorm_measure(src, settings, target))
    assert first.input_i == pytest.approx(-20.0, abs=0.3) and first.measurable
    out = tmp_path / "out.wav"
    measured, second = asyncio.run(loudnorm_normalize(src, out, settings, target, measured=first))
    assert measured is first
    assert second.linear, second.raw
    after = asyncio.run(ebur128(out, settings))
    assert after.integrated == pytest.approx(-16.0, abs=0.5)
    assert after.true_peak <= -1.5 + 0.2
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 48000 and w.getnchannels() == 2  # resampled back from loudnorm's 192 kHz


def write_spiky(path: Path, *, rate: int = 48000, seconds: float = 6.0) -> Path:
    """A quiet sine (0.05) with a 5 ms burst at 0.5 every half second: a high crest factor, so a gain that
    reaches -14 LUFS pushes the bursts far over a -1.5 dBTP ceiling."""
    t = np.arange(int(seconds * rate)) / rate
    signal = 0.05 * np.sin(2 * np.pi * 440 * t)
    burst = (t % 0.5) < 0.005
    signal[burst] = 0.5 * np.sin(2 * np.pi * 2000 * t[burst])
    data = np.repeat(np.round(signal * 32767).astype("<i2")[:, None], 2, axis=1).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return path


@pytest.mark.slow
@pytest.mark.container
def test_loudnorm_dynamic_fallback_and_static_gain_with_limiter(tmp_path: Path, settings: Settings) -> None:
    src = write_spiky(tmp_path / "src.wav")
    target = LoudnessTarget(-14.0, -1.5, 9.0)
    first, second = asyncio.run(loudnorm_normalize(src, tmp_path / "dyn.wav", settings, target))
    assert first.measurable and first.input_i < -20 and first.input_tp > -7
    assert not second.linear  # the linear gain would push the bursts over the true-peak ceiling
    # the master chain's fallback: a static gain, then the limiter at about -1.5 dBFS
    gain = static_gain_db(first, target)
    assert gain > 6
    limited = tmp_path / "limited.wav"
    asyncio.run(run_ffmpeg(["-i", str(src), "-af", f"volume={gain}dB,{limiter_filter()}", "-c:a", "pcm_s16le",
                            str(limited)], settings=settings))
    after = asyncio.run(ebur128(limited, settings))
    assert db(0.84) - 1.0 <= after.sample_peak <= db(0.84) + 0.3  # held at the ceiling, not just quiet
    assert after.integrated > first.input_i + gain - 4  # louder by about the gain, less what the limiter took


@pytest.mark.slow
@pytest.mark.container
def test_silent_input_is_not_normalised(tmp_path: Path, settings: Settings) -> None:
    silence = write_tone(tmp_path / "silence.wav", amplitude=0.0, seconds=2.0)
    stats = asyncio.run(loudnorm_measure(silence, settings))
    assert not stats.measurable
    out = tmp_path / "never.wav"
    with pytest.raises(LoudnessError, match="silent"):
        asyncio.run(loudnorm_normalize(silence, out, settings))
    assert not out.exists()
