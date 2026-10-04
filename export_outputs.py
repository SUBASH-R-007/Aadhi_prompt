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

Phase 22: a lesson rendered frame by frame on the server (renders.py, render_worker.mjs) has a silent
video and the render timeline: every sound the page started, on the video's clock. mix_render() builds
its sound from that and makes the final MP4 (see "rendered lessons" below).
"""
import json
import math
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


# ---- rendered lessons (Phase 22): the sound mix and the final MP4 -------------------------------------------------------
#
# The render timeline's `audio` lists every sound the page started, in video seconds (render_worker.mjs merges the ranges):
#   {t, kind: narration | logo | video-voice | music | sfx, src, offset (seconds into the source), rate (playbackRate),
#    volume, loop, end (video time it stopped) or duration (video seconds it played), clipDuration, synth (sfx)}
# The mix rebuilds that sound exactly as the page played it:
#   bed      a silent track of exactly frames / 30 seconds (48 kHz stereo): the sound is never longer or shorter than the video
#   lanes    sounds that never overlap share a lane: the kinds the page plays one at a time (narration, the logo, an AI clip's
#            own voice: a new one stops the last) have one lane each; sound effects and music take the first lane of their
#            kind that is free. Each sound is its source from `offset` (atrim), at the page's speed with its pitch kept
#            (atempo = rate), at its volume, resampled, cut or padded to exactly its place in samples; the silences between
#            are exact too, and a lane is those pieces joined (concat). This puts every sound where an adelay would, at a
#            cost that grows with the lesson's length instead of with (sounds x length): a 100-minute lesson has hundreds of
#            narration segments, and summing hundreds of delayed inputs (amix) over it would take far longer than the render
#   music    looped (aloop) until it stopped or the video ends
#   sfx      synthesised from the page's oscillator settings (aevalsrc): linear attack, exponential decay to `floor`, an
#            optional exponential frequency sweep
#   mix      the bed and the lanes summed as they are (amix normalize=0); AAC 192 kbit/s
# Sources are linked (or copied) into the render's workspace under short names and opened with amovie, so the graph (kept in
# a file) never meets the 32 K command-line limit or path quoting.

SAMPLE_RATE = 48000
MIX_KINDS = ("narration", "logo", "video-voice", "music", "sfx")
ONE_AT_A_TIME = ("narration", "logo", "video-voice")
WAVES = {"sine": "sin(2*PI*({p}))", "square": "if(gte(sin(2*PI*({p})),0),1,-1)",
         "triangle": "2/PI*asin(sin(2*PI*({p})))", "sawtooth": "2*(({p})-floor(({p})+0.5))"}
FORMAT = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
MAX_SOUNDS = 20000
SHORT_EXT = re.compile(r"\.[a-z0-9]{1,5}")


class MixError(Exception):
    pass


def _num(value):
    """A number as ffmpeg reads it, always written the same way (at most 6 decimals, no exponent)."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _clamp(value, low, high, default):
    value = _number(value)
    return default if value is None else min(max(value, low), high)


def clean_synth(value):
    """A sound effect's oscillator settings, or None when unusable."""
    if not isinstance(value, dict):
        return None
    freq, duration = _number(value.get("freq")), _number(value.get("duration"))
    if not freq or not duration or freq <= 0 or duration <= 0:
        return None
    duration = min(duration, 10.0)
    end = _number(value.get("freqEnd"))
    synth = {"type": value.get("type") if value.get("type") in WAVES else "sine", "freq": min(freq, 20000.0),
             "freqEnd": min(end, 20000.0) if end and end > 0 else None, "gain": _clamp(value.get("gain"), 0, 2, 0.3),
             "duration": duration, "attack": _clamp(value.get("attack"), 0.0005, duration, min(0.05, duration * 0.05)),
             "floor": _clamp(value.get("floor"), 0.00001, 1, 0.01), "sweep": _clamp(value.get("sweep"), 0.001, duration, duration)}
    # the page's filter (the whoosh's lowpass sweep), as one fixed filter at the sweep's middle (geometric)
    flt = value.get("filter")
    if isinstance(flt, dict) and flt.get("type") in ("lowpass", "highpass"):
        f0, f1 = _number(flt.get("freq")), _number(flt.get("freqEnd"))
        if f0 and f0 > 0:
            synth["filter"] = {"type": flt["type"], "freq": min(math.sqrt(f0 * f1) if f1 and f1 > 0 else f0, 20000.0)}
    return synth


def synth_expression(synth):
    """The aevalsrc expression of one sound effect (t in seconds from its start)."""
    g, a, d, fl = synth["gain"], synth["attack"], synth["duration"], synth["floor"]
    decay = max(d - a, 0.0005)
    env = f"if(lt(t,{_num(a)}),{_num(g)}*t/{_num(a)},{_num(g)}*pow({_num(fl)},(t-{_num(a)})/{_num(decay)}))"
    f0, f1, sweep = synth["freq"], synth["freqEnd"], synth["sweep"]
    if not f1 or abs(f1 - f0) < 1e-9:
        phase = f"{_num(f0)}*t"
    else:
        k = math.log(f1 / f0)
        scale = f0 * sweep / k
        phase = (f"if(lt(t,{_num(sweep)}),{_num(scale)}*(exp({_num(k / sweep)}*t)-1),"
                 f"{_num(scale * (f1 / f0 - 1))}+{_num(f1)}*(t-{_num(sweep)}))")
    return f"{env}*{WAVES[synth['type']].format(p=phase)}"


def clean_sounds(audio, duration):
    """The timeline's sounds that can be mixed, in order: [{t, kind, src, offset, rate, volume, loop, length, synth}], with
    `length` the video seconds it played when known (else None: until the next sound of its lane, or the end)."""
    sounds = []
    for item in (audio if isinstance(audio, list) else [])[:MAX_SOUNDS]:
        if not isinstance(item, dict) or item.get("kind") not in MIX_KINDS:
            continue
        t = _number(item.get("t"))
        if t is None or t < 0 or t >= duration:
            continue
        kind = item["kind"]
        synth = clean_synth(item.get("synth")) if kind == "sfx" else None
        src = item.get("src") if isinstance(item.get("src"), str) and item.get("src") else None
        if not synth and not src:
            continue
        rate = _clamp(item.get("rate"), 0.25, 4.0, 1.0)
        offset = _clamp(item.get("offset"), 0, 1e7, 0.0)
        loop = item.get("loop") is not False if kind == "music" else bool(item.get("loop"))
        end, played, clip = _number(item.get("end")), _number(item.get("duration")), _number(item.get("clipDuration"))
        if end is not None and end > t:
            length = end - t
        elif played is not None and played > 0:
            length = played
        elif synth:
            length = synth["duration"]
        elif clip and not loop and clip > offset:
            length = (clip - offset) / rate
        else:
            length = None
        sounds.append({"t": t, "kind": kind, "src": None if synth else src, "offset": offset, "rate": rate,
                       "volume": _clamp(item.get("volume"), 0, 4.0, 1.0), "loop": loop, "length": length, "synth": synth})
    order = {kind: i for i, kind in enumerate(MIX_KINDS)}
    return sorted(sounds, key=lambda s: (s["t"], order[s["kind"]]))


def sound_lanes(sounds, total_samples):
    """Sounds in lanes at exact sample positions: [[{start, length, sound}]] (samples). A one-at-a-time kind has one lane
    (a sound ends where the next of its kind starts); other kinds take the first lane of their kind that is free."""
    lanes = {}
    for sound in sounds:
        start = int(round(sound["t"] * SAMPLE_RATE))
        own_end = start + int(round(sound["length"] * SAMPLE_RATE)) if sound["length"] is not None else None
        group = lanes.setdefault(sound["kind"], [])
        if sound["kind"] in ONE_AT_A_TIME:
            lane = group[0] if group else None
        else:
            lane = next((ln for ln in group if ln[-1]["end"] is not None and ln[-1]["end"] <= start), None)
        if lane is None:
            lane = []
            group.append(lane)
        if lane and (lane[-1]["end"] is None or lane[-1]["end"] > start):
            lane[-1]["end"] = start  # the page stopped it here (the next sound of its kind began)
        lane.append({"start": start, "end": own_end, "sound": sound})
    out = []
    for kind in MIX_KINDS:
        for lane in lanes.get(kind, []):
            pieces = []
            for entry in lane:
                end = min(entry["end"] if entry["end"] is not None else total_samples, total_samples)
                if end > entry["start"]:
                    pieces.append({"start": entry["start"], "length": end - entry["start"], "sound": entry["sound"]})
            if pieces:
                out.append(pieces)
    return out


def _atempo(rate):
    """atempo filters for a playback rate (0.5 to 2 per filter here; slower or faster rates are chained)."""
    steps = []
    while rate < 0.5:
        steps.append(0.5)
        rate /= 0.5
    while rate > 2.0:
        steps.append(2.0)
        rate /= 2.0
    if abs(rate - 1.0) > 1e-9:
        steps.append(rate)
    return [f"atempo={_num(r)}" for r in steps]


def _silence(samples, label):
    return f"anullsrc=r=48000:cl=stereo,atrim=end_sample={samples},aformat=sample_fmts=fltp:channel_layouts=stereo[{label}]"


def mix_graph(lanes, total_samples, source_names):
    """The mix as a filter graph (text): the silent bed, every lane, and their sum as [aout]. `source_names` maps a sound's
    src to the short name amovie opens (a sound whose src maps to nothing is left out). The same input gives the same text."""
    lines = [_silence(total_samples, "bed")]
    lane_labels = []
    for j, lane in enumerate(lanes):
        parts = []
        cursor = 0
        for k, piece in enumerate(lane):
            sound = piece["sound"]
            if sound["synth"] is None and not source_names.get(sound["src"]):
                continue
            if piece["start"] > cursor:
                label = f"l{j}g{k}"
                lines.append(_silence(piece["start"] - cursor, label))
                parts.append(label)
            length = piece["length"]
            if sound["synth"] is not None:
                chain = [f"aevalsrc=exprs='{synth_expression(sound['synth'])}':s=48000:c=stereo:d={_num(sound['synth']['duration'])}"]
                if sound["synth"].get("filter"):
                    chain.append(f"{sound['synth']['filter']['type']}=f={_num(sound['synth']['filter']['freq'])}")
            else:
                chain = [f"amovie={source_names[sound['src']]}"]
                if sound["loop"]:
                    chain.append("aloop=loop=-1:size=2147483647")
                if sound["offset"] > 0:
                    chain.append(f"atrim=start={_num(sound['offset'])}")
                chain.append("asetpts=PTS-STARTPTS")
                chain += _atempo(sound["rate"])
                if abs(sound["volume"] - 1.0) > 1e-9:
                    chain.append(f"volume={_num(sound['volume'])}")
            chain += [FORMAT, f"apad=whole_len={length}", f"atrim=end_sample={length}", "asetpts=N/SR/TB"]
            label = f"l{j}p{k}"
            lines.append(",".join(chain) + f"[{label}]")
            parts.append(label)
            cursor = piece["start"] + length
        if not parts:
            continue
        if cursor < total_samples:
            label = f"l{j}end"
            lines.append(_silence(total_samples - cursor, label))
            parts.append(label)
        lane_label = f"lane{j}"
        if len(parts) == 1:
            lines.append(f"[{parts[0]}]anull[{lane_label}]")
        else:
            lines.append("".join(f"[{p}]" for p in parts) + f"concat=n={len(parts)}:v=0:a=1[{lane_label}]")
        lane_labels.append(lane_label)
    if not lane_labels:
        lines.append("[bed]anull[aout]")
    else:
        inputs = "".join(f"[{label}]" for label in ["bed"] + lane_labels)
        lines.append(f"{inputs}amix=inputs={len(lane_labels) + 1}:duration=first:dropout_transition=0:normalize=0,"
                     f"atrim=end_sample={total_samples}[aout]")
    return ";\n".join(lines) + "\n"


def _graph_option(exe):
    """-/filter_complex <file> (ffmpeg 7 and later), or the older -filter_complex_script <file>."""
    if "graph_option" not in _encoder:
        option = "-/filter_complex"
        try:
            head = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True, timeout=30).stdout
            match = re.search(r"version\s+n?(\d+)\.", head)
            if match and int(match.group(1)) < 7:
                option = "-filter_complex_script"
        except (OSError, subprocess.SubprocessError):
            pass
        _encoder["graph_option"] = option
    return _encoder["graph_option"]


def audio_stream(path):
    """True when ffprobe reads an audio stream in the file, False when it reads none or cannot read the file, None when
    there is no ffprobe to ask. A mix never opens a source without sound (amovie would stop the whole mix)."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        result = subprocess.run([exe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "audio" in (result.stdout or "")


def _link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def video_frames(path):
    """The frames (packets) of a video's first stream, counted without decoding; None when ffprobe cannot tell."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        result = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                                 "stream=nb_read_packets", "-of", "json", path], capture_output=True, text=True, timeout=600)
        stream = (json.loads(result.stdout or "{}").get("streams") or [{}])[0]
        return int(stream["nb_read_packets"]) if stream.get("nb_read_packets") else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return None


def mix_render(video, timeline, dst, resolve, workdir, fps=30, timeout=None):
    """The final MP4 of a rendered lesson: `video` (silent H.264, the render's frames, copied) with the sound mixed from
    the render timeline, its subtitles and chapters (built from the same timeline) embedded, +faststart. resolve(src) ->
    a local file or None. dst is written only when the result is valid (frame count and length checked). Returns
    {frames, duration, sounds, skipped, has_subtitles, info}. Raises MixError."""
    exe = shutil.which("ffmpeg")
    if not exe or not h264_encoder():
        raise MixError(mp4_unavailable_reason() or "ffmpeg cannot make MP4 files on this server")
    frames = int(timeline.get("frames") or 0)
    counted = video_frames(video)
    if counted is not None:
        if frames and abs(counted - frames) > 1:
            raise MixError(f"the rendered video has {counted} frames instead of {frames}")
        frames = counted
    if frames <= 0:
        raise MixError("the rendered video has no frames")
    duration = frames / fps
    total = int(round(frames * SAMPLE_RATE / fps))
    sounds = clean_sounds(timeline.get("audio"), duration)
    mix_dir = os.path.join(workdir, "mix")
    shutil.rmtree(mix_dir, ignore_errors=True)
    os.makedirs(mix_dir, exist_ok=True)
    names, skipped, paths = {}, [], {}
    for sound in sounds:
        src = sound["src"]
        if src and src not in paths:
            path = resolve(src)
            paths[src] = path if path and os.path.isfile(path) else None
    # one source without sound (a clip with no audio track, a damaged narration file) is left out, never the whole mix
    readable = [p for p in dict.fromkeys(paths.values()) if p]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        has_audio = dict(zip(readable, pool.map(audio_stream, readable)))
    for sound in sounds:
        src = sound["src"]
        if not src or src in names:
            continue
        path = paths.get(src)
        if not path or has_audio.get(path) is False:
            names[src] = None
            skipped.append(src.split("?", 1)[0][-120:])
            continue
        ext = os.path.splitext(path)[1].lower()
        name = f"s{sum(1 for n in names.values() if n) + 1}{ext if SHORT_EXT.fullmatch(ext) else ''}"
        _link_or_copy(path, os.path.join(mix_dir, name))
        names[src] = f"mix/{name}"
    graph = mix_graph(sound_lanes(sounds, total), total, names)
    with open(os.path.join(workdir, "audio.graph"), "w", encoding="utf-8", newline="\n") as f:
        f.write(graph)
    cleaned = clean_timeline(timeline)
    vtt_text = build_vtt(cleaned["cues"], duration)
    vtt_path = os.path.join(workdir, "subtitles.vtt") if "-->" in vtt_text else None
    if vtt_path:
        with open(vtt_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(vtt_text)
    meta_path = os.path.join(workdir, "chapters.ffmeta")
    with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(chapters_ffmetadata(build_chapters(cleaned["scenes"], duration), duration))
    part = dst + ".part.mp4"
    args = [exe, "-hide_banner", "-v", "error", "-y", "-nostdin", "-i", os.path.abspath(video)]
    maps = ["-map", "0:v:0", "-map", "[aout]"]
    inputs = 1
    if vtt_path:
        args += ["-i", os.path.abspath(vtt_path)]
        maps += ["-map", f"{inputs}:s:0"]
        inputs += 1
    args += ["-i", os.path.abspath(meta_path)]
    maps += ["-map_chapters", str(inputs)]
    args += [_graph_option(exe), "audio.graph"] + maps + ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    if vtt_path:
        args += ["-c:s", "mov_text", "-metadata:s:s:0", "language=eng"]
    args += ["-movflags", "+faststart", os.path.abspath(part)]
    limit = timeout or max(600.0, duration * 2)
    with _convert_lock:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=limit, cwd=workdir)
        except subprocess.TimeoutExpired:
            _remove(part)
            raise MixError(f"the sound mix took longer than {int(limit)} seconds and was stopped")
        except OSError as e:
            _remove(part)
            raise MixError(f"ffmpeg could not be started ({e.__class__.__name__})")
    if result.returncode != 0 or not os.path.isfile(part) or os.path.getsize(part) == 0:
        _remove(part)
        detail = (result.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise MixError(f"ffmpeg could not mix the sound ({detail[0][:300]})")
    info = probe(part)
    out_frames = video_frames(part)
    if info is not None and (not info["has_video"] or not info["has_audio"]):
        _remove(part)
        raise MixError("the mixed video lacks its picture or its sound")
    if out_frames is not None and out_frames != frames:
        _remove(part)
        raise MixError(f"the mixed video has {out_frames} frames instead of {frames}")
    if info is not None and info["duration"] and abs(info["duration"] - duration) > 0.25:
        _remove(part)
        raise MixError(f"the mixed video is {info['duration']:.2f}s long instead of {duration:.2f}s")
    os.replace(part, dst)
    shutil.rmtree(mix_dir, ignore_errors=True)
    return {"frames": frames, "duration": duration, "sounds": len(sounds), "skipped": skipped, "has_subtitles": bool(vtt_path),
            "info": info}
