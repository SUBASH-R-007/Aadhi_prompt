"""Extra outputs of a finished lesson export (Phase 7): subtitles, chapters and an MP4 copy.

The browser records WebM (export.js). With the upload it sends the recording's timeline: when
each scene started and each narration line was on screen (the page's own subtitle track), in
seconds from the start of the recording, and (Phase 20) which lesson and version of it was recorded,
kept in the stored timeline only. From that:

  subtitles  WebVTT from the narration lines exactly as they were shown (no speech-to-text)
  chapters   one per scene (title + start time), as a readable list and as ffmpeg metadata
  mp4        an H.264/AAC copy made by ffmpeg with the subtitles (mov_text) and chapters embedded,
             only when this server's ffmpeg has an H.264 encoder; the WebM always stays the main
             file, so a failed or impossible conversion never loses the video

Everything here is pure text building plus one ffmpeg call with an argument list (no shell), a
timeout, one conversion at a time, and a temporary file renamed into place only when it is valid.
"""
import os
import re
import shutil
import subprocess
import threading

from media import probe

LINE_CHARS = 42          # WebVTT line length that stays readable on a phone
CUE_LINES = 2            # at most two lines on screen at once
MIN_CUE_SECONDS = 0.7
MAX_CUES = 5000
MAX_SCENES = 1000
MAX_TEXT = 1000          # one caption as received; longer ones are cut before splitting
CHAPTER_TITLE = 100
DURATION_TOLERANCE = 2.0  # seconds an MP4 may differ from its WebM and still count as the same video
LESSON_REVISION = 40     # the lesson's revision token (its saved time) kept with a video
FINGERPRINT = re.compile(r"[0-9a-f]{64}")

_encoder = {"checked": False, "name": None}
_convert_lock = threading.Semaphore(1)  # conversions are CPU heavy: one at a time


class ConversionError(Exception):
    pass


# ---- timeline -----------------------------------------------------------------------------

def _number(value):
    return float(value) if isinstance(value, (int, float)) and value == value and abs(value) != float("inf") else None


def clean_lesson_link(value):
    """The lesson a video was recorded from (Phase 20): {project_id, fingerprint, revision}, or None unless all
    three are valid (a positive lesson id, a 64-character lowercase hex fingerprint, a short printable revision)."""
    if not isinstance(value, dict):
        return None
    project_id, fingerprint, revision = value.get("project_id"), value.get("fingerprint"), value.get("revision")
    if type(project_id) is not int or project_id <= 0:  # type(): True is not a lesson id
        return None
    if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
        return None
    if not isinstance(revision, str) or len(revision) > LESSON_REVISION or not revision.isprintable():
        return None
    return {"project_id": project_id, "fingerprint": fingerprint, "revision": revision}


def clean_timeline(timeline):
    """{scenes: [{t, title}], cues: [{start, end, text}]} with bad entries dropped and sizes capped, plus
    lesson: {project_id, fingerprint, revision} when the browser sent a valid link to the lesson (Phase 20).
    The link comes first, so the stored file starts with it (exports.recorded_lesson reads only the start)."""
    timeline = timeline if isinstance(timeline, dict) else {}
    lesson = clean_lesson_link(timeline.get("lesson"))
    scenes, cues = [], []
    for s in (timeline.get("scenes") or [])[:MAX_SCENES]:
        if isinstance(s, dict) and _number(s.get("t")) is not None and s["t"] >= 0:
            scenes.append({"t": float(s["t"]), "title": clean_caption(s.get("title"))[:CHAPTER_TITLE], "type": str(s.get("type") or "")[:40]})
    for c in (timeline.get("cues") or [])[:MAX_CUES]:
        if not isinstance(c, dict):
            continue
        start, end = _number(c.get("start")), _number(c.get("end"))
        text = clean_caption(c.get("text"))[:MAX_TEXT]
        if start is not None and start >= 0 and text:
            cues.append({"start": start, "end": end if end is not None and end > start else None, "text": text})
    cleaned = {"lesson": lesson} if lesson else {}
    cleaned.update(scenes=sorted(scenes, key=lambda s: s["t"]), cues=sorted(cues, key=lambda c: c["start"]))
    return cleaned


def clean_caption(text):
    """Plain text of one caption: no markup, no narration markers, single spaces."""
    text = re.sub(r"</?[A-Za-z][^<>]*>", " ", str(text or ""))  # real tags only: "x < y" is kept
    text = re.sub(r"\[(?:SYNC|PAUSE(?::[\d.]+)?)\]", " ", text)
    for entity, char in (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&amp;", "&")):
        text = text.replace(entity, char)
    return " ".join(text.split())


# ---- WebVTT -------------------------------------------------------------------------------

def vtt_time(seconds):
    ms = int(round(max(0.0, seconds) * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def vtt_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(words, width=LINE_CHARS):
    lines, line = [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        lines.append(line)
    return lines


def _pieces(text):
    """The caption as screen-sized pieces of at most CUE_LINES lines, split at word boundaries."""
    lines = _wrap(text.split())
    return ["\n".join(lines[i:i + CUE_LINES]) for i in range(0, len(lines), CUE_LINES)]


def build_cues(cues, duration=None):
    """Caption cues on the video's clock: ordered, never overlapping, inside the video, and split into
    readable pieces (time shared by length). A cue without an end lasts until the next one."""
    out = []
    ordered = [c for c in cues if c.get("text")]
    for i, cue in enumerate(ordered):
        start = cue["start"]
        nxt = ordered[i + 1]["start"] if i + 1 < len(ordered) else None
        end = cue.get("end")
        if end is None:  # on screen about as long as it takes to read, until the next line at most
            end = start + min(7.0, max(MIN_CUE_SECONDS * 2, len(cue["text"]) * 0.07))
        if nxt is not None:
            end = min(end, nxt)
        if duration is not None:
            if start >= duration:
                break
            end = min(end, duration)
        if end - start < 0.05:
            continue
        pieces = _pieces(cue["text"])
        total = sum(len(p) for p in pieces) or 1
        t = start
        for j, piece in enumerate(pieces):
            piece_end = end if j == len(pieces) - 1 else t + (end - start) * len(piece) / total
            out.append({"start": round(t, 3), "end": round(piece_end, 3), "text": piece})
            t = piece_end
    return out


def build_vtt(cues, duration=None):
    lines = ["WEBVTT", ""]
    for n, cue in enumerate(build_cues(cues, duration), 1):
        lines += [str(n), f"{vtt_time(cue['start'])} --> {vtt_time(cue['end'])}", vtt_escape(cue["text"]), ""]
    return "\n".join(lines)


# ---- chapters -----------------------------------------------------------------------------

OPENING_TITLES = ("Introduction", "Opening", "Lesson start")  # the intro's chapter: the first one no scene is called


def _title_key(title):
    return " ".join(str(title or "").split()).casefold()


def _opening_title(chapters):
    """"Introduction", unless a scene's chapter is already called that (e.g. a first scene titled "Introduction"):
    then "Opening", so a chapter list never names two different moments the same."""
    taken = {_title_key(c["title"]) for c in chapters}
    for title in OPENING_TITLES:
        if _title_key(title) not in taken:
            return title
    n = 2
    while _title_key(f"Opening {n}") in taken:
        n += 1
    return f"Opening {n}"


def build_chapters(scenes, duration=None):
    """One chapter per scene: the recording starts with the lesson's intro, so the first chapter is
    the intro's ("Introduction", or "Opening" when a scene already has that title) at 0:00 unless a
    scene starts right away. Start times strictly increase (a scene starting within a second of the
    previous one replaces it) and stay inside the video; scene chapters keep their real start."""
    chapters = []
    for i, scene in enumerate(scenes):
        start = scene["t"]
        if duration is not None and start >= duration - 0.5:
            break
        title = (" ".join(str(scene.get("title") or "").split()) or f"Scene {i + 1}")[:CHAPTER_TITLE]
        if chapters and start - chapters[-1]["start"] < 1.0:
            chapters[-1] = {"start": round(start, 3), "title": title}  # the earlier scene lasted under a second
            continue
        chapters.append({"start": round(start, 3), "title": title})
    if not chapters or chapters[0]["start"] >= 1.0:
        chapters.insert(0, {"start": 0.0, "title": _opening_title(chapters)})
    else:
        chapters[0]["start"] = 0.0
    return chapters


def chapter_time(seconds):
    s = int(seconds)
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def chapters_text(chapters):
    """A readable list (also the format video sites accept in a description)."""
    lines, seen = [], set()
    for chapter in chapters:
        stamp = chapter_time(chapter["start"])
        if stamp in seen:
            continue
        seen.add(stamp)
        lines.append(f"{stamp} {chapter['title']}")
    return "\n".join(lines) + "\n"


def _meta_escape(text):
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def chapters_ffmetadata(chapters, duration):
    """ffmpeg's metadata format, used to embed the chapters in the MP4."""
    lines = [";FFMETADATA1"]
    for i, chapter in enumerate(chapters):
        end = chapters[i + 1]["start"] if i + 1 < len(chapters) else duration
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(chapter['start'] * 1000)}", f"END={int(max(end, chapter['start'] + 0.001) * 1000)}",
                  f"title={_meta_escape(chapter['title'])}"]
    return "\n".join(lines) + "\n"


# ---- MP4 ----------------------------------------------------------------------------------

def h264_encoder():
    """'libx264' when this server's ffmpeg can make H.264, else None (checked once)."""
    if not _encoder["checked"]:
        _encoder["checked"] = True
        exe = shutil.which("ffmpeg")
        if exe:
            try:
                listing = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
                if re.search(r"^\s*V\S*\s+libx264\s", listing, re.MULTILINE) and re.search(r"^\s*A\S*\s+aac\s", listing, re.MULTILINE):
                    _encoder["name"] = "libx264"
            except (OSError, subprocess.SubprocessError):
                pass
    return _encoder["name"]


def mp4_unavailable_reason():
    if not shutil.which("ffmpeg"):
        return "MP4 needs ffmpeg on the server, which is not installed; the WebM video is complete."
    if not h264_encoder():
        return "This server's ffmpeg has no H.264 encoder (libx264), so no MP4 can be made; the WebM video is complete."
    return None


def convert_to_mp4(src, dst, duration=None, vtt_path=None, ffmeta_path=None, timeout=None):
    """Writes an H.264/AAC MP4 of `src` to `dst` (subtitles and chapters embedded when given).
    Raises ConversionError; `dst` exists only after a successful, validated conversion."""
    exe = shutil.which("ffmpeg")
    encoder = h264_encoder()
    if not exe or not encoder:
        raise ConversionError(mp4_unavailable_reason())
    part = dst + ".part.mp4"
    args = [exe, "-hide_banner", "-v", "error", "-y", "-i", src]
    maps = ["-map", "0:v:0", "-map", "0:a:0?"]
    inputs = 1
    if vtt_path:
        args += ["-i", vtt_path]
        maps += ["-map", f"{inputs}:s:0"]
        inputs += 1
    if ffmeta_path:
        args += ["-i", ffmeta_path]
        maps += ["-map_chapters", str(inputs)]
    args += maps + ["-c:v", encoder, "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-threads", "2",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
    if vtt_path:
        args += ["-c:s", "mov_text", "-metadata:s:s:0", "language=eng"]
    args.append(part)
    limit = timeout or max(300.0, (duration or 0) * 6)
    with _convert_lock:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired:
            _remove(part)
            raise ConversionError(f"the conversion took longer than {int(limit)} seconds and was stopped")
        except OSError as e:
            _remove(part)
            raise ConversionError(f"ffmpeg could not be started ({e.__class__.__name__})")
    if result.returncode != 0 or not os.path.isfile(part) or os.path.getsize(part) == 0:
        _remove(part)
        detail = (result.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise ConversionError(f"ffmpeg could not convert the video ({detail[0][:200]})")
    info = probe(part)
    if info is not None:
        if not info["has_video"]:
            _remove(part)
            raise ConversionError("the converted file has no video")
        if duration and info["duration"] and abs(info["duration"] - duration) > max(DURATION_TOLERANCE, duration * 0.05):
            _remove(part)
            raise ConversionError(f"the converted video is {info['duration']:.1f}s long instead of {duration:.1f}s")
    os.replace(part, dst)
    return info


def _remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
