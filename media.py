"""Media inspection shared by video exports (exports.py) and the asset library (assets.py).

File types are decided from the file's own bytes and ffprobe, never from a client-supplied
name or Content-Type.
"""
import json
import os
import re
import shutil
import subprocess


class MediaError(ValueError):
    """The file is not usable media of a supported type."""


def sniff_container(path):
    """'webm' or 'mp4' for the containers MediaRecorder produces, else None."""
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[4:8] == b"ftyp":
        return "mp4"
    return None


# (mime, extension, kind) from magic bytes. Containers that may hold video or only sound
# (WebM, MP4, Ogg) are settled by probing the streams.
def _sniff(head):
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png", "image"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg", "image"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif", "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "webp", "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav", "wav", "audio"
    if head[:3] == b"ID3" or (head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio/mpeg", "mp3", "audio"
    if head[:4] == b"fLaC":
        return "audio/flac", "flac", "audio"
    if head[:4] == b"OggS":
        return "audio/ogg", "ogg", "container"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm", "webm", "container"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4", "m4a", "audio"
        if brand == b"qt  ":
            return "video/quicktime", "mov", "container"
        return "video/mp4", "mp4", "container"
    return None


def probe(path):
    """Streams, size and duration via ffprobe. None when ffprobe is not installed;
    ValueError when the file is not readable media."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        result = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", path],
            capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        raise ValueError((result.stderr or "unreadable media").strip().splitlines()[-1][:300])
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    duration = data.get("format", {}).get("duration")
    return {
        "has_video": video is not None,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "duration": float(duration) if duration not in (None, "N/A") else None,
    }


def inspect_media(path):
    """Type and metadata of an image, audio or video file, or MediaError if it is not one.

    Returns {kind, mime_type, ext, width, height, duration, has_audio, verified}; `verified` is
    False when ffprobe is unavailable and only the file signature could be checked."""
    with open(path, "rb") as f:
        head = f.read(16)
    sniffed = _sniff(head) if len(head) >= 12 else None
    if not sniffed:
        raise MediaError("not a supported image, audio or video file")
    mime, ext, kind = sniffed
    try:
        info = probe(path)
    except ValueError as e:
        raise MediaError(f"the file is damaged or unreadable ({e})")
    if info is None:
        if kind == "container":
            kind = "video"
        return {"kind": kind, "mime_type": mime, "ext": ext, "width": None, "height": None,
                "duration": None, "has_audio": None, "verified": False}

    if kind == "container":
        if info["has_video"]:
            kind = "video"
        elif info["has_audio"]:
            kind = "audio"
            mime = {"video/webm": "audio/webm", "video/mp4": "audio/mp4"}.get(mime, mime)
            ext = {"mp4": "m4a"}.get(ext, ext)
        else:
            raise MediaError("the file has neither picture nor sound")
    if kind == "image" and not (info["width"] and info["height"]):
        raise MediaError("the image could not be decoded")
    if kind == "audio" and not info["has_audio"]:
        raise MediaError("the audio file has no sound track")
    if kind == "video" and not info["has_video"]:
        raise MediaError("the video file has no picture")
    return {
        "kind": kind,
        "mime_type": mime,
        "ext": ext,
        "width": info["width"],
        "height": info["height"],
        "duration": info["duration"] if kind != "image" else None,
        "has_audio": info["has_audio"] if kind == "video" else None,
        "verified": True,
    }


def audio_peak_db(path):
    """Loudest point of the first audio track in dB, or None when unknown. A recording of a muted
    tab has an audio track that is pure silence (about -91 dB); decoding only the audio is fast."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    try:
        result = subprocess.run([exe, "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
                                capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"max_volume: (-?[\d.]+|-inf) dB", result.stderr or "")
    return float(match.group(1)) if match else None


def remux(src, dst):
    """Rewrites a MediaRecorder file without re-encoding. Browser recordings carry no duration
    or seek index; the copy has both, so players show the length and can seek."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    args = [exe, "-v", "error", "-y", "-i", src, "-map", "0", "-c", "copy"]
    if dst.endswith(".mp4"):
        args += ["-movflags", "+faststart"]
    try:
        result = subprocess.run(args + [dst], capture_output=True, timeout=3600)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0
