"""ffmpeg commands of the motion engine (DYNAMIC_VIDEO_DESIGN §7.5, §7.7, §2.8). Pure builders: argument lists and
side-file texts, no I/O.

* **Chunk encoder.** The stage's JPEG frames (CDP capture: full-range BT.601 with an sRGB profile) arrive on stdin
  and leave as one MPEG-TS chunk with identical parameters in every chunk, so every chunk starts with an IDR frame
  and the concat demuxer can join them with ``-c copy``::

      -f image2pipe -c:v mjpeg -framerate 30 -i pipe:0
      -vf scale=in_color_matrix=bt601:in_range=full:out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=...
      -c:v libx264 -profile:v high -preset ${MOTION_X264_PRESET} -crf ${MOTION_X264_CRF} -maxrate 12M -bufsize 24M
      -g 15 -keyint_min 15 -sc_threshold 0 -bf 2 -x264-params open-gop=0 -an -f mpegts chunk_NNNN.ts

  (plus ``-frames:v N``, ``-threads`` :data:`ENCODER_THREADS` and a ``scale`` to the output size first when it
  differs from the stage). The thread count is fixed, not derived from the host or the worker count: x264's output
  depends on it, and a chunk must encode to the same bytes on every attempt (its cache key covers the arguments).
  Measured on the development laptop (i7-13650HX, 1080p JPEG input, ``veryfast``): x264's default (1.5 threads per
  logical CPU) took 765 MB per encoder and 4 parallel encoders reached 67 fps together; ``-threads 4`` took 290 MB
  and reached 156 fps (B1 benchmark, §7.10).
* **Media frames** (§7.7): ``-ss <exact> -i <mezzanine> -frames:v N -vf scale=W:H -q:v 3`` into ``f_%06d.jpg``
  numbered by source frame (``-start_number``). Seeking before ``-i`` is frame-accurate when transcoding; the seek
  lands half a frame before the wanted frame, so timestamp rounding can never pick its neighbour.
* **Audio** ("the classic mix for now", §7.11): per scene, the narration placed at ``audio_offset`` plus the quiz
  ticks and ding, as PCM of exactly the scene's frame count (``ffmpeg.audio_mix_filter``); the scenes' WAVs are
  joined by the concat demuxer, the branding BGM is ducked under them (``ffmpeg.bgm_filter``) and the master track
  is encoded once (AAC-LC 48 kHz stereo 384 kbps, ``master_audio_command``) while the frames are captured (§7.11:
  the master is encoded before the mux). Measured in the B1 integration on the development laptop: AAC at 384 kbps
  takes 17-26 s for a 5-minute stereo master, which the final mux no longer waits for.
* **Final mux.** Chunks joined (concat demuxer, ``-c copy``) and remuxed once with the master audio (``-c copy``),
  ffmetadata chapters and, for a clean file, a ``mov_text`` caption track on by default, with
  ``-movflags +faststart+negative_cts_offsets`` so video and audio both start at 0 (§2.8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Settings
from ..ffmpeg import (
    AUDIO_RATE,
    PROTOCOLS,
    AudioClip,
    Command,
    Input,
    audio_mix_filter,
    bgm_filter,
    concat_input,
    concat_safe,
    ft,
    video_input,
)

CHUNK_VF = ("scale=in_color_matrix=bt601:in_range=full:out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709")
AUDIO_BITRATE = "384k"
ENCODER_THREADS = 4  # x264 threads per chunk encoder (module docstring)
MEDIA_JPEG_QUALITY = 3
FINAL_MOVFLAGS = "+faststart+negative_cts_offsets"


def ffmpeg_pipe_base(settings: Settings) -> list[str]:
    """Global flags for an ffmpeg that reads frames on stdin (``ffmpeg.ffmpeg_base`` without ``-nostdin``)."""
    return [settings.ffmpeg_path, "-hide_banner", "-y", "-loglevel", "error", "-nostats"]


@dataclass(frozen=True)
class EncoderSpec:
    """Chunk encoder parameters (identical for every chunk of a render)."""

    fps: int = 30
    preset: str = "veryfast"
    crf: int = 17
    maxrate: str = "12M"
    bufsize: str = "24M"
    gop: int = 15
    bframes: int = 2
    width: int | None = None  # output size; None = the captured (stage) size
    height: int | None = None
    threads: int | None = ENCODER_THREADS  # None = x264's default (one encoder per machine only)

    @classmethod
    def from_settings(cls, settings: Settings, fps: int, output: tuple[int, int] | None = None) -> EncoderSpec:
        """``MOTION_X264_PRESET`` / ``MOTION_X264_CRF`` at ``fps``, scaled to ``output`` when given."""
        w, h = output if output is not None else (None, None)
        return cls(fps=int(fps), preset=str(settings.motion_x264_preset), crf=int(settings.motion_x264_crf),
                   width=w, height=h)

    def vf(self, capture: tuple[int, int]) -> str:
        """The colour conversion, after a ``scale`` to the output size when it differs from ``capture``."""
        if self.width and self.height and (self.width, self.height) != tuple(capture):
            return f"scale={self.width}:{self.height}:flags=lanczos," + CHUNK_VF
        return CHUNK_VF

    def codec_args(self) -> list[str]:
        """The x264 parameters (closed GOP, identical in every chunk)."""
        args = ["-c:v", "libx264", "-profile:v", "high", "-preset", self.preset, "-crf", str(self.crf),
                "-maxrate", self.maxrate, "-bufsize", self.bufsize, "-g", str(self.gop), "-keyint_min", str(self.gop),
                "-sc_threshold", "0", "-bf", str(self.bframes), "-x264-params", "open-gop=0"]
        return [*args, "-threads", str(self.threads)] if self.threads else args

    def chunk_args(self, *, frames: int, output: str, capture: tuple[int, int]) -> list[str]:
        """Arguments after the global flags: JPEG frames on stdin -> ``output`` (MPEG-TS) of exactly ``frames``."""
        if not concat_safe(output) or "/" in output:
            raise ValueError(f"unsafe chunk name {output!r}")
        return ["-protocol_whitelist", PROTOCOLS, "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", str(self.fps),
                "-i", "pipe:0", "-vf", self.vf(capture), *self.codec_args(), "-frames:v", str(int(frames)), "-an",
                "-f", "mpegts", output]

    def fingerprint(self, capture: tuple[int, int]) -> dict[str, Any]:
        """Everything that changes the encoded bytes (part of the chunk cache key)."""
        return {"fps": self.fps, "vf": self.vf(capture), "codec": self.codec_args(), "container": "mpegts"}


# ---------------------------------------------------------------------------
# Media frame extraction
# ---------------------------------------------------------------------------


def frame_runs(indices: Sequence[int], *, max_gap: int = 0) -> list[tuple[int, int]]:
    """``(first, count)`` runs of the sorted unique source frame ``indices``; runs ``max_gap`` frames apart or
    closer are merged (decoding a few unused frames is cheaper than another seek)."""
    out: list[tuple[int, int]] = []
    for i in sorted(set(int(x) for x in indices if int(x) >= 0)):
        if out and i <= out[-1][0] + out[-1][1] + max_gap:
            out[-1] = (out[-1][0], i - out[-1][0] + 1)
        else:
            out.append((i, 1))
    return out


def extract_args(src: str | Path, *, first: int, count: int, src_fps: float, size: tuple[int, int],
                 out_dir: str | Path, resample: bool = False) -> list[str]:
    """Arguments after ``ffmpeg_base``: source frames ``first .. first + count - 1`` of ``src`` (at ``src_fps``)
    scaled to cover ``size`` (aspect kept), as ``<out_dir>/f_<index>.jpg``. ``resample`` adds an ``fps`` filter for
    a file whose real rate differs from ``src_fps`` (the seek is then exact instead of half a frame early).

    The engine no longer resamples: the ``fps`` filter anchors its grid to the run's first kept frame, so a frame's
    pixels would depend on where its run starts (§7.4). It extracts at the file's own rate and maps the page's
    indices to file frames instead (``media.native_frames``)."""
    if count < 1 or src_fps <= 0:
        raise ValueError("extraction needs count >= 1 and src_fps > 0")
    if resample:
        seek = first / src_fps
    else:
        seek = max(0.0, (first - 0.5) / src_fps)
    w, h = max(2, int(size[0])), max(2, int(size[1]))
    vf = f"scale=w={w}:h={h}:force_original_aspect_ratio=increase:flags=bicubic"
    if resample:
        vf = f"fps={src_fps:g}," + vf
    pattern = (Path(out_dir) / "f_%06d.jpg").as_posix()
    return ["-ss", f"{seek:.6f}", "-protocol_whitelist", PROTOCOLS, "-i", str(src), "-map", "0:v:0", "-an", "-sn",
            "-dn", "-vf", vf, "-frames:v", str(int(count)), "-q:v", str(MEDIA_JPEG_QUALITY),
            "-start_number", str(int(first)), "-f", "image2", pattern]


# ---------------------------------------------------------------------------
# Audio (the classic mix: narration + quiz ticks + ding per scene, BGM in the final mux)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneAudioSpec:
    """One scene's PCM: ``clips`` are (file, placement) pairs; ``samples`` = the scene's frames at 48 kHz."""

    name: str
    samples: int
    clips: tuple[tuple[str, AudioClip], ...] = ()


def scene_audio_command(spec: SceneAudioSpec, *, script_flag: str) -> Command:
    """``<name>.wav``: the clips mixed (no normalisation) into exactly ``spec.samples`` of 48 kHz stereo PCM."""
    if not concat_safe(spec.name) or "/" in spec.name:
        raise ValueError(f"unsafe audio name {spec.name!r}")
    inputs: list[Input] = []
    index: dict[str, int] = {}
    clips: list[AudioClip] = []
    for path, clip in spec.clips:
        if path not in index:
            index[path] = len(inputs)
            inputs.append(Input(path))
        clips.append(AudioClip(index[path], delay=clip.delay, duration=clip.duration, fade_out=clip.fade_out,
                               volume=clip.volume))
    script = f"{spec.name}.filtergraph"
    args: list[str] = []
    for inp in inputs:  # none for a silent scene: the graph's anullsrc produces the silence
        args += inp.args()
    args += [script_flag, script, "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2",
             "-vn", f"{spec.name}.wav"]
    return Command(args, {script: audio_mix_filter(clips, total_samples=spec.samples, out_label="aout") + "\n"})


# ---------------------------------------------------------------------------
# Final mux
# ---------------------------------------------------------------------------


@dataclass
class MasterAudioSpec:
    """The master audio track: the scenes' WAVs (+ the ducked branding BGM), encoded once (paths relative to the work
    dir)."""

    audio_list: str  # ffconcat of the scenes' WAVs
    total_seconds: float
    output: str = "master.m4a"
    bgm: str | None = None
    bgm_volume: float = 0.06
    bgm_delay: float = 0.0
    audio_bitrate: str = AUDIO_BITRATE


def master_audio_command(spec: MasterAudioSpec, *, filter_script: str = "master.filtergraph",
                         script_flag: str) -> Command:
    """The scenes' audio (+ BGM) -> AAC-LC 48 kHz stereo at ``audio_bitrate``, exactly ``total_seconds`` long."""
    inputs = [concat_input(spec.audio_list)]
    if spec.bgm:
        inputs.append(video_input(spec.bgm, loop=True))
        graph = bgm_filter(narration="0:a", bgm="1:a", volume=spec.bgm_volume, delay=spec.bgm_delay,
                           total=spec.total_seconds)
    else:
        graph = f"[0:a]aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[aout]"
    args: list[str] = []
    for inp in inputs:
        args += inp.args()
    args += [script_flag, filter_script, "-map", "[aout]", "-c:a", "aac", "-b:a", spec.audio_bitrate,
             "-ar", str(AUDIO_RATE), "-ac", "2", "-t", ft(spec.total_seconds), "-vn", "-f", "mp4", spec.output]
    return Command(args, {filter_script: graph + "\n"})


@dataclass
class MotionFinalSpec:
    """Inputs and options of the final mux (paths relative to the work dir)."""

    video_list: str  # ffconcat of the MPEG-TS chunks
    audio: str  # the master audio track (``master_audio_command``), stream-copied
    metadata: str  # ffmetadata (chapters, title)
    total_seconds: float
    output: str
    soft_subtitles: str | None = None  # .srt as a mov_text track, on by default (a clean file)
    subtitle_language: str = "und"


def final_mux_command(spec: MotionFinalSpec) -> Command:
    """Chunks and the master audio (both stream copies) + chapters (+ the caption track), starting at 0 (module
    docstring)."""
    inputs = [concat_input(spec.video_list), Input(spec.audio, "mp4")]
    meta_index = len(inputs)
    inputs.append(Input(spec.metadata, "ffmetadata"))
    subtitle_args: list[str] = []
    if spec.soft_subtitles:
        sub_index = len(inputs)
        inputs.append(Input(spec.soft_subtitles, "srt"))
        subtitle_args = ["-map", f"{sub_index}:s:0", "-c:s", "mov_text",
                         "-metadata:s:s:0", f"language={spec.subtitle_language or 'und'}", "-disposition:s:0", "default"]
    args: list[str] = []
    for inp in inputs:
        args += inp.args()
    args += ["-map", "0:v", "-c:v", "copy", "-map", "1:a", "-c:a", "copy", *subtitle_args,
             "-map_metadata", str(meta_index), "-map_chapters", str(meta_index),
             "-t", ft(spec.total_seconds), "-movflags", FINAL_MOVFLAGS, spec.output]
    return Command(args, {})
