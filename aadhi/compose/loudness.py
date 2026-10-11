"""EBU R128 loudness with ffmpeg: ``ebur128`` measurements and two-pass ``loudnorm`` JSON helpers.

The Dynamic audio stages build on these (DYNAMIC_VIDEO_DESIGN §5.4 speaker gains, §5.10 music intake, §5.12
master chain, §7.12 preflight and post-render loudness checks). The classic renderer does not use them.

* :func:`ebur128` measures a file: integrated loudness (LUFS) and its gate threshold, loudness range (LU) with its
  threshold and low/high bounds, sample peak (dBFS) and true peak (dBTP), parsed from the filter's summary
  (:func:`parse_ebur128`).
* :func:`loudnorm_measure` runs the first ``loudnorm`` pass (``print_format=json``) and returns its statistics
  (:func:`parse_loudnorm`); :func:`loudnorm_filter` builds the first- or second-pass filter, and
  :func:`loudnorm_normalize` runs both passes and writes the normalised audio. The second pass reports whether
  ``linear=true`` held (``LoudnormStats.linear``); when it fell back to dynamic normalisation the caller can use
  :func:`static_gain_db` plus :func:`limiter_filter` instead (§5.12).

Values are floats; ffmpeg's ``-inf`` (silence) becomes ``-math.inf``, and the ``as_dict()`` JSON forms write
None for any non-finite value. Option ranges follow ffmpeg 5.1 (Debian bookworm, the Docker image) through 8.x:
``loudnorm`` I -70..-5 LUFS, TP -9..0 dBTP, LRA 1..20 LU.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from ..config import Settings
from .ffmpeg import AUDIO_RATE, PROTOCOLS, FFmpegError, run_process

SILENCE_LUFS = -70.0  # the absolute gate: integrated loudness at or below this is silence
# loudnorm's measured_* option bounds (values outside are refused by ffmpeg). A measured LRA of exactly 0 (a
# constant level, e.g. a tone or a bed) is ffmpeg's "not measured" sentinel, which silently disables linear mode,
# so it is passed as 0.01 LU.
_MEASURED_BOUNDS = {"I": (-99.0, 0.0), "TP": (-99.0, 99.0), "LRA": (0.01, 99.0), "thresh": (-99.0, 0.0),
                    "offset": (-99.0, 99.0)}
_NUMBER = r"(-?inf|-?nan|-?\d+(?:\.\d+)?)"
_SECTION = re.compile(r"^\s*(Integrated loudness|Loudness range|Sample peak|True peak):\s*$")
_VALUE = re.compile(r"^\s*(I|Threshold|LRA|LRA low|LRA high|Peak):\s*" + _NUMBER + r"\s*(LUFS|LU|dBFS|dBTP)?\s*$")
_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


class LoudnessError(FFmpegError):
    """A loudness measurement could not be made or read (or the audio is silent where loudness is needed)."""


def _number(text: Any) -> float:
    """``"-23.5"`` / ``"-inf"`` -> float (``nan`` for anything unreadable)."""
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return math.nan


def _json_number(value: float | None) -> float | None:
    return round(value, 2) if value is not None and math.isfinite(value) else None


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoudnessTarget:
    """A ``loudnorm`` target: integrated loudness (LUFS), true-peak ceiling (dBTP) and loudness range (LU)."""

    integrated: float = -16.0
    true_peak: float = -1.5
    lra: float = 9.0

    def __post_init__(self) -> None:
        for name, value, lo, hi in (("integrated", self.integrated, -70.0, -5.0),
                                    ("true_peak", self.true_peak, -9.0, 0.0), ("lra", self.lra, 1.0, 20.0)):
            if not (math.isfinite(value) and lo <= value <= hi):
                raise ValueError(f"loudness target {name}={value!r} is outside {lo:g}..{hi:g}")


@dataclass(frozen=True)
class R128Summary:
    """The ``ebur128`` summary of one audio stream (see the module docstring)."""

    integrated: float  # LUFS
    threshold: float = math.nan  # LUFS, the relative gate of the integrated loudness
    lra: float = math.nan  # LU
    lra_threshold: float = math.nan  # LUFS
    lra_low: float = math.nan  # LUFS
    lra_high: float = math.nan  # LUFS
    sample_peak: float = math.nan  # dBFS (nan when not measured)
    true_peak: float = math.nan  # dBTP (nan when not measured)

    @property
    def silent(self) -> bool:
        """True when nothing passed the absolute gate (or nothing could be measured)."""
        return not math.isfinite(self.integrated) or self.integrated <= SILENCE_LUFS

    def as_dict(self) -> dict[str, float | None]:
        """JSON-ready values (rounded to 0.01; None where ffmpeg reported ``-inf`` or nothing)."""
        return {
            "integrated_lufs": _json_number(self.integrated),
            "threshold_lufs": _json_number(self.threshold),
            "lra_lu": _json_number(self.lra),
            "lra_threshold_lufs": _json_number(self.lra_threshold),
            "lra_low_lufs": _json_number(self.lra_low),
            "lra_high_lufs": _json_number(self.lra_high),
            "sample_peak_dbfs": _json_number(self.sample_peak),
            "true_peak_dbtp": _json_number(self.true_peak),
        }


@dataclass(frozen=True)
class LoudnormStats:
    """The ``loudnorm`` JSON report of one pass (``input_*`` measured, ``output_*`` after the filter)."""

    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    output_i: float = math.nan
    output_tp: float = math.nan
    output_lra: float = math.nan
    output_thresh: float = math.nan
    normalization_type: str = ""  # "linear" | "dynamic" ("" when the report had none)
    target_offset: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def linear(self) -> bool:
        """True when the pass used linear (static gain) normalisation."""
        return self.normalization_type == "linear"

    @property
    def measurable(self) -> bool:
        """True when the input has loudness to normalise (finite and above the absolute gate)."""
        return math.isfinite(self.input_i) and self.input_i > SILENCE_LUFS and all(
            math.isfinite(v) for v in (self.input_tp, self.input_lra, self.input_thresh))

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready values (None for non-finite numbers)."""
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name == "raw":
                continue
            value = getattr(self, f.name)
            out[f.name] = value if isinstance(value, str) else _json_number(value)
        return out


# ---------------------------------------------------------------------------
# Parsers (pure)
# ---------------------------------------------------------------------------


def parse_ebur128(text: str) -> R128Summary | None:
    """The last ``ebur128`` summary in ffmpeg's log ``text``; None when there is none."""
    _, sep, tail = (text or "").rpartition("Summary:")
    if not sep:
        return None
    values: dict[str, float] = {}
    section = ""
    for line in tail.splitlines():
        if m := _SECTION.match(line):
            section = m.group(1)
            continue
        m = _VALUE.match(line)
        if m is None:
            if line.strip().startswith("["):  # the next log line of another filter or the muxer
                break
            continue
        name, value = m.group(1), _number(m.group(2))
        key = {("Integrated loudness", "I"): "integrated", ("Integrated loudness", "Threshold"): "threshold",
               ("Loudness range", "LRA"): "lra", ("Loudness range", "Threshold"): "lra_threshold",
               ("Loudness range", "LRA low"): "lra_low", ("Loudness range", "LRA high"): "lra_high",
               ("Sample peak", "Peak"): "sample_peak", ("True peak", "Peak"): "true_peak"}.get((section, name))
        if key is not None:
            values[key] = value
    if "integrated" not in values:
        return None
    return R128Summary(**values)


def parse_loudnorm(text: str) -> LoudnormStats | None:
    """The last ``loudnorm`` JSON report in ffmpeg's log ``text``; None when there is none or it is unreadable."""
    blocks = _LOUDNORM_JSON.findall(text or "")
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    numbers = {k: _number(data.get(k)) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "output_i",
                                                  "output_tp", "output_lra", "output_thresh", "target_offset")}
    if math.isnan(numbers["input_i"]):
        return None
    offset = numbers.pop("target_offset")
    return LoudnormStats(**numbers, normalization_type=str(data.get("normalization_type") or "").strip().lower(),
                         target_offset=offset if math.isfinite(offset) else 0.0, raw=data)


# ---------------------------------------------------------------------------
# Filter builders (pure)
# ---------------------------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return min(hi, max(lo, value))


def ebur128_filter(*, true_peak: bool = True) -> str:
    """``ebur128`` with sample (and true) peaks; per-frame lines go to the verbose log level, so only the summary
    is printed at ``info``."""
    return f"ebur128=peak={'true+sample' if true_peak else 'sample'}:framelog=verbose"


def loudnorm_filter(target: LoudnessTarget, measured: LoudnormStats | None = None, *, linear: bool = True,
                    report: bool = True) -> str:
    """``loudnorm`` for the first pass (``measured`` None) or the second pass (the first pass's statistics).

    ``report`` adds ``print_format=json`` so the pass logs :class:`LoudnormStats`. A second pass needs a
    measurable first pass (:attr:`LoudnormStats.measurable`); otherwise :class:`LoudnessError` is raised.
    """
    opts = [f"I={_fmt(target.integrated)}", f"TP={_fmt(target.true_peak)}", f"LRA={_fmt(target.lra)}"]
    if measured is not None:
        if not measured.measurable:
            raise LoudnessError("the audio is silent: there is no loudness to normalise")
        opts += [f"measured_I={_fmt(_clamp(measured.input_i, _MEASURED_BOUNDS['I']))}",
                 f"measured_TP={_fmt(_clamp(measured.input_tp, _MEASURED_BOUNDS['TP']))}",
                 f"measured_LRA={_fmt(_clamp(measured.input_lra, _MEASURED_BOUNDS['LRA']))}",
                 f"measured_thresh={_fmt(_clamp(measured.input_thresh, _MEASURED_BOUNDS['thresh']))}",
                 f"offset={_fmt(_clamp(measured.target_offset, _MEASURED_BOUNDS['offset']))}",
                 f"linear={'true' if linear else 'false'}"]
    if report:
        opts.append("print_format=json")
    return "loudnorm=" + ":".join(opts)


def static_gain_db(measured: R128Summary | LoudnormStats, target: LoudnessTarget) -> float:
    """The constant gain (dB) that moves the measured integrated loudness onto the target (0 for silence)."""
    value = measured.integrated if isinstance(measured, R128Summary) else measured.input_i
    if not math.isfinite(value) or value <= SILENCE_LUFS:
        return 0.0
    return round(target.integrated - value, 2)


def limiter_filter(*, limit: float = 0.84, attack_ms: float = 5.0, release_ms: float = 50.0) -> str:
    """``alimiter`` with a fixed ceiling (``limit`` as linear amplitude; 0.84 is about -1.5 dBFS) and no automatic
    output levelling (``level=0``): the static-gain fallback of the master chain (§5.12)."""
    if not 0.0625 <= limit <= 1.0:
        raise ValueError("limit must be between 0.0625 and 1")
    return f"alimiter=limit={_fmt(limit)}:attack={_fmt(attack_ms)}:release={_fmt(release_ms)}:level=0"


# ---------------------------------------------------------------------------
# Measuring and normalising files
# ---------------------------------------------------------------------------


def _measure_cmd(settings: Settings, path: Path, af: str) -> list[str]:
    return [settings.ffmpeg_path, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
            "-protocol_whitelist", PROTOCOLS, "-i", str(path), "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-af", af, "-f", "null", "-"]


async def ebur128(path: str | Path, settings: Settings, *, true_peak: bool = True, timeout: float = 600,
                  check_cancelled: Callable[[], None] | None = None) -> R128Summary:
    """EBU R128 summary of the first audio stream of ``path`` (decodes audio only). Raises
    :class:`LoudnessError` when ffmpeg fails or prints no summary."""
    try:
        _, tail = await run_process(_measure_cmd(settings, Path(path), ebur128_filter(true_peak=true_peak)),
                                    timeout=timeout, settings=settings, check_cancelled=check_cancelled)
    except FFmpegError as exc:
        raise LoudnessError(f"loudness measurement failed: {exc}", returncode=exc.returncode,
                            stderr_tail=exc.stderr_tail) from exc
    summary = parse_ebur128(tail)
    if summary is None:
        raise LoudnessError("ffmpeg printed no ebur128 summary", stderr_tail=tail)
    return summary


async def loudnorm_measure(path: str | Path, settings: Settings, target: LoudnessTarget | None = None, *,
                           timeout: float = 600, check_cancelled: Callable[[], None] | None = None) -> LoudnormStats:
    """First ``loudnorm`` pass over the first audio stream of ``path``: its JSON statistics."""
    af = loudnorm_filter(target or LoudnessTarget())
    try:
        _, tail = await run_process(_measure_cmd(settings, Path(path), af), timeout=timeout, settings=settings,
                                    check_cancelled=check_cancelled)
    except FFmpegError as exc:
        raise LoudnessError(f"loudnorm measurement failed: {exc}", returncode=exc.returncode,
                            stderr_tail=exc.stderr_tail) from exc
    stats = parse_loudnorm(tail)
    if stats is None:
        raise LoudnessError("ffmpeg printed no loudnorm report", stderr_tail=tail)
    return stats


async def loudnorm_normalize(src: str | Path, dest: str | Path, settings: Settings,
                             target: LoudnessTarget | None = None, *, measured: LoudnormStats | None = None,
                             linear: bool = True, sample_rate: int = AUDIO_RATE,
                             codec_args: Sequence[str] = ("-c:a", "pcm_s16le"), timeout: float = 900,
                             check_cancelled: Callable[[], None] | None = None,
                             ) -> tuple[LoudnormStats, LoudnormStats]:
    """Two-pass ``loudnorm`` of the first audio stream of ``src`` into ``dest`` (resampled to ``sample_rate``;
    ``loudnorm`` works at 192 kHz internally). Returns ``(first pass, second pass)`` statistics; the second
    pass's :attr:`LoudnormStats.linear` tells whether linear normalisation held. ``measured`` reuses an earlier
    first pass. Silent input raises :class:`LoudnessError` before anything is written."""
    target = target or LoudnessTarget()
    first = measured or await loudnorm_measure(src, settings, target, timeout=timeout,
                                               check_cancelled=check_cancelled)
    af = f"{loudnorm_filter(target, first, linear=linear)},aresample={int(sample_rate)}"
    cmd = [settings.ffmpeg_path, "-hide_banner", "-nostdin", "-nostats", "-y", "-loglevel", "info",
           "-protocol_whitelist", PROTOCOLS, "-i", str(src), "-map", "0:a:0", "-vn", "-sn", "-dn", "-af", af,
           *codec_args, str(dest)]
    try:
        _, tail = await run_process(cmd, timeout=timeout, settings=settings, check_cancelled=check_cancelled)
    except FFmpegError as exc:
        raise LoudnessError(f"loudness normalisation failed: {exc}", returncode=exc.returncode,
                            stderr_tail=exc.stderr_tail) from exc
    second = parse_loudnorm(tail)
    if second is None:
        raise LoudnessError("ffmpeg printed no loudnorm report for the second pass", stderr_tail=tail)
    return first, second
