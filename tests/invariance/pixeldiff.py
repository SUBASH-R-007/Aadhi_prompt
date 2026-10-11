"""Compare two classic renders (``render_classic.py`` outputs) frame by frame and sample by sample.

Part 2 of the invariance harness (docs/DYNAMIC_VIDEO_DESIGN.md section 3.6). Both renders come from the same
machine or container and share every input, but the classic state screenshots are not bit-stable: measured over
repeated captures of the same code, Chromium's antialiasing of a few rounded panel corners varies by up to 15 pixels
and 7 levels per screenshot, which the H.264 encode spreads to about 60 pixels and 14 levels in the frames shown
from it. The comparison is therefore tolerance-aware: that wobble never fails a batch, a real change always does.

* a **state screenshot** (lossless; compared when both renders kept them, ``--keep-states``, as CI does) passes
  when it is identical or at most ``state_max_pixels`` pixels differ by more than ``state_level`` over mid grey (the
  wobble: at most 2 pixels above 4 levels). A state on one side only fails;
* a **frame** passes when its decoded hash is identical, or, unless ``--exact``, when at most
  ``max_changed_pixels`` pixels differ by more than ``pixel_level`` (of 255) in some channel and the mean absolute
  difference stays within ``max_mean_abs``. Once two encodes differ anywhere, x264's decisions spread tiny
  differences over the following frames (measured: no pixel above 14 levels, mean up to 0.005); a changed glyph or
  a 1 px shift changes hundreds of pixels by far more than 24 levels;
* the **audio** passes when the decoded PCM is identical or, outside the last ``audio_tail_seconds`` (the classic
  mux's ducked-music tail is not bit-stable across runs on some ffmpeg builds), no sample differs by more than
  ``audio_max_abs``;
* frame counts, frame size and rate must be equal.

The state screenshots, the captured page states and the timeline hash are compared too, as diagnostics: they
say *where* a difference comes from. For the first failing frames ``frames/<n>_{base,head,diff}.png`` are written.

    python tests/invariance/pixeldiff.py OUT/base OUT/head --out OUT

Exit codes: 0 equal within the tolerance, 1 different.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MAX_LISTED = 25  # failing frames listed in the report
MAX_PICTURES = 4  # failing frames written as PNG triplets


@dataclass(frozen=True)
class Tolerance:
    pixel_level: int = 24  # frames: a pixel counts as changed above this channel difference (of 255)
    max_changed_pixels: int = 16  # frames: changed pixels allowed per frame
    max_mean_abs: float = 0.05  # frames: mean absolute difference allowed (encoder decisions spread the wobble)
    state_level: float = 12.0  # screenshots, composited over mid grey: a pixel counts as changed above this
    state_max_pixels: int = 16  # screenshots: changed pixels allowed per state
    audio_max_abs: int = 2
    audio_tail_seconds: float = 0.5
    exact: bool = False


# --- pure comparisons ---------------------------------------------------------------------------------------------


def frame_stats(a: Any, b: Any, *, pixel_level: int) -> dict[str, Any]:
    """Difference of two HxWx3 uint8 frames: max and mean absolute channel difference, and how many pixels
    differ by more than ``pixel_level`` in some channel."""
    import numpy as np

    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    per_pixel = d.max(axis=2)
    return {"max_abs": int(per_pixel.max()), "mean_abs": round(float(d.mean()), 4),
            "changed_pixels": int((per_pixel > pixel_level).sum())}


def frame_ok(stats: dict[str, Any], tol: Tolerance) -> bool:
    if tol.exact:
        return stats["max_abs"] == 0
    return stats["changed_pixels"] <= tol.max_changed_pixels and stats["mean_abs"] <= tol.max_mean_abs


def audio_compare(a: Any, b: Any, tol: Tolerance, *, rate: int = 48_000, channels: int = 2) -> dict[str, Any]:
    """Compare interleaved int16 PCM; the last ``audio_tail_seconds`` are reported but not judged."""
    import numpy as np

    out: dict[str, Any] = {"samples": [len(a) // channels, len(b) // channels]}
    if len(a) != len(b):
        out.update(ok=False, identical=False, reason="different lengths")
        return out
    d = np.abs(a.astype(np.int32) - b.astype(np.int32))
    if not d.any():
        out.update(ok=True, identical=True, max_abs=0)
        return out
    tail = 0 if tol.exact else min(len(d), int(round(tol.audio_tail_seconds * rate)) * channels)
    body = d[: len(d) - tail]
    where = np.nonzero(d)[0] // channels
    out.update(identical=False, max_abs=int(d.max()), max_abs_outside_tail=int(body.max()) if len(body) else 0,
               differs_from_s=round(float(where.min()) / rate, 3), differs_to_s=round(float(where.max()) / rate, 3),
               differing_samples=int(len(where)))
    out["ok"] = out["max_abs_outside_tail"] <= (0 if tol.exact else tol.audio_max_abs)
    return out


def scene_at(report: dict[str, Any], t: float) -> str:
    for s in report.get("scene_starts", []):
        if s["start"] <= t < s["start"] + s["duration"]:
            return str(s["scene_id"])
    return "intro" if t < float(report.get("intro_duration") or 0.0) else "?"


def compare_states(base: dict[str, str], head: dict[str, str]) -> dict[str, list[str]]:
    return {"changed": sorted(k for k in base.keys() & head.keys() if base[k] != head[k]),
            "only_base": sorted(base.keys() - head.keys()), "only_head": sorted(head.keys() - base.keys())}


def state_stats(a: Any, b: Any, *, level: float) -> dict[str, Any]:
    """Difference of two RGBA screenshots as they would show over a mid-grey background (transparent holes and
    antialiased edges weighted by their alpha): max difference and pixels above ``level``."""
    import numpy as np

    def over_grey(x: Any) -> Any:
        x = x.astype(np.float64)
        alpha = x[..., 3:4] / 255.0
        return x[..., :3] * alpha + 128.0 * (1.0 - alpha)

    if a.shape != b.shape:
        return {"max_abs": 255.0, "changed_pixels": int(max(a.shape[0] * a.shape[1], b.shape[0] * b.shape[1])),
                "size": [list(a.shape[:2]), list(b.shape[:2])]}
    d = np.abs(over_grey(a) - over_grey(b)).max(axis=2)
    return {"max_abs": round(float(d.max()), 1), "changed_pixels": int((d > level).sum())}


def judge_states(base: Path, head: Path, diff: dict[str, list[str]], tol: Tolerance) -> dict[str, Any]:
    """Pixel check of the changed state screenshots (lossless, so tighter than the frames) when both renders kept
    them (``--keep-states``); a state that exists on one side only always fails."""
    import numpy as np
    from PIL import Image

    failing: list[dict[str, Any]] = [{"state": n, "reason": "only in the base"} for n in diff["only_base"]]
    failing += [{"state": n, "reason": "only in HEAD"} for n in diff["only_head"]]
    checked = (base / "states").is_dir() and (head / "states").is_dir()
    within = 0
    if checked:
        for name in diff["changed"]:
            with Image.open(base / "states" / name) as ia, Image.open(head / "states" / name) as ib:
                stats = state_stats(np.asarray(ia.convert("RGBA")), np.asarray(ib.convert("RGBA")),
                                    level=0.0 if tol.exact else tol.state_level)
            limit = 0 if tol.exact else tol.state_max_pixels
            if stats["changed_pixels"] <= limit:
                within += 1
            else:
                failing.append({"state": name, **stats})
    return {**diff, "pixels_checked": checked, "within_tolerance": within, "failing": failing}


def compare_captures(base: dict[str, Any], head: dict[str, Any]) -> dict[str, Any]:
    """Scene indices whose captured state lists differ (times, keys, rects, notices)."""
    bs, hs = base.get("scenes", {}), head.get("scenes", {})
    changed = sorted((k for k in bs.keys() | hs.keys() if bs.get(k) != hs.get(k)), key=lambda k: int(k))
    return {"changed_scenes": changed, "intro_equal": base.get("intro") == head.get("intro"),
            "blocked_media_equal": base.get("blocked_media") == head.get("blocked_media")}


# --- decoding -----------------------------------------------------------------------------------------------------


def _decoder() -> Any:
    try:
        from . import render_classic  # type: ignore[import-not-found]
    except ImportError:
        import render_classic  # type: ignore[import-not-found,no-redef]
    return render_classic


def frames(ffmpeg: str, mp4: Path, width: int, height: int) -> Iterator[Any]:
    import numpy as np

    for buf in _decoder().decoded_frames(ffmpeg, mp4, width, height):
        yield np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)


def pcm(ffmpeg: str, mp4: Path) -> Any:
    import numpy as np

    return np.frombuffer(_decoder().decoded_audio(ffmpeg, mp4), dtype="<i2")


def _save_triplet(out_dir: Path, n: int, a: Any, b: Any) -> list[str]:
    import numpy as np
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    diff = np.clip(np.abs(a.astype(np.int16) - b.astype(np.int16)) * 8, 0, 255).astype(np.uint8)
    names = []
    for label, img in (("base", a), ("head", b), ("diff", diff)):
        name = f"{n:06d}_{label}.png"
        Image.fromarray(img, "RGB").save(out_dir / name)
        names.append(name)
    return names


# --- the comparison -----------------------------------------------------------------------------------------------


def _load(side: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    report = json.loads((side / "report.json").read_text(encoding="utf-8"))
    cap_path = side / "capture.json"
    capture = json.loads(cap_path.read_text(encoding="utf-8")) if cap_path.is_file() else {}
    return report, capture


def compare_dirs(base: Path, head: Path, *, tol: Tolerance, ffmpeg: str, out: Path | None = None) -> dict[str, Any]:
    """Compare two ``render_classic.py`` output directories; returns the report (``ok``)."""
    rb, cb = _load(base)
    rh, ch = _load(head)
    problems: list[str] = []
    for side, r in (("base", rb), ("head", rh)):
        if not r.get("ok") or "frame_sha256" not in r:
            problems.append(f"the {side} render did not finish: {r.get('error', 'no frames')}")
    report: dict[str, Any] = {"tolerance": asdict(tol), "problems": problems}
    if problems:
        report["ok"] = False
        return report
    for key in ("width", "height", "rate", "fixture", "settings", "max_scenes"):
        if rb.get(key) != rh.get(key):
            problems.append(f"{key} differs: {rb.get(key)!r} vs {rh.get(key)!r}")
    fb, fh = rb["frame_sha256"], rh["frame_sha256"]
    if len(fb) != len(fh):
        problems.append(f"frame count differs: {len(fb)} vs {len(fh)}")
    differing = [i for i in range(min(len(fb), len(fh))) if fb[i] != fh[i]]
    fps = float(rb.get("settings", {}).get("fps") or 30)
    failing: list[dict[str, Any]] = []
    within = 0
    pictures: list[str] = []
    if differing and rb["width"] == rh["width"] and rb["height"] == rh["height"]:
        wanted, last = set(differing), max(differing)
        stream = zip(frames(ffmpeg, base / "render.mp4", rb["width"], rb["height"]),
                     frames(ffmpeg, head / "render.mp4", rh["width"], rh["height"]))
        for n, (a, b) in enumerate(stream):
            if n > last:
                break
            if n not in wanted:
                continue
            stats = frame_stats(a, b, pixel_level=tol.pixel_level)
            if frame_ok(stats, tol):
                within += 1
                continue
            item = {"frame": n, "t": round(n / fps, 3), "scene_id": scene_at(rb, n / fps), **stats}
            if out is not None and len(pictures) < MAX_PICTURES * 3:
                pictures += _save_triplet(out / "frames", n, a, b)
            failing.append(item)
    elif differing:
        failing = [{"frame": n, "t": round(n / fps, 3), "scene_id": scene_at(rb, n / fps)} for n in differing]
    report["frames"] = {"base": len(fb), "head": len(fh), "identical": min(len(fb), len(fh)) - len(differing),
                        "within_tolerance": within, "failing": len(failing), "first_failing": failing[:MAX_LISTED],
                        "failing_scenes": sorted({f["scene_id"] for f in failing}), "pictures": pictures}
    if rb.get("audio_sha256") == rh.get("audio_sha256"):
        report["audio"] = {"ok": True, "identical": True}
    else:
        report["audio"] = audio_compare(pcm(ffmpeg, base / "render.mp4"), pcm(ffmpeg, head / "render.mp4"), tol)
    report["states"] = judge_states(base, head, compare_states(rb.get("states", {}), rh.get("states", {})), tol)
    report["capture"] = compare_captures(cb, ch)
    report["timeline_equal"] = rb.get("timeline_sha256") == rh.get("timeline_sha256")
    report["identical"] = not differing and not problems and report["audio"].get("identical", False)
    report["ok"] = (not problems and not failing and bool(report["audio"]["ok"])
                    and not report["states"]["failing"])
    return report


def markdown(report: dict[str, Any], *, title: str = "Classic invariance: differential pixel test") -> str:
    """A short summary for the CI job page."""
    verdict = "passed" if report.get("ok") else "FAILED"
    if report.get("declared_change") and not report.get("ok"):
        verdict = "differs (declared classic change: not blocking)"
    lines = [f"### {title}: {verdict}", ""]
    for side in ("base", "head"):
        info = report.get(side) or {}
        if info:
            lines.append(f"- {side}: `{info.get('commit') or info.get('root')}`")
    for p in report.get("problems", []):
        lines.append(f"- problem: {p}")
    fr = report.get("frames")
    if fr:
        lines.append(f"- frames: {fr['base']} vs {fr['head']}; identical {fr['identical']}, within tolerance "
                     f"{fr['within_tolerance']}, failing {fr['failing']}"
                     + (f" (scenes {', '.join(fr['failing_scenes'])})" if fr["failing_scenes"] else ""))
        for f in fr["first_failing"][:8]:
            extra = (f", max {f['max_abs']}, mean {f['mean_abs']}, {f['changed_pixels']} px"
                     if "max_abs" in f else "")
            lines.append(f"  - frame {f['frame']} (t {f['t']} s, {f['scene_id']}{extra})")
    au = report.get("audio")
    if au:
        lines.append("- audio: " + ("identical" if au.get("identical") else
                                    f"{'ok' if au.get('ok') else 'FAILED'} ({json.dumps(au, sort_keys=True)})"))
    st = report.get("states")
    if st and (st["changed"] or st["only_base"] or st["only_head"]):
        lines.append(f"- state screenshots: changed {st['changed'][:12]}, only base {st['only_base'][:6]}, "
                     f"only head {st['only_head'][:6]}; within tolerance {st['within_tolerance']}"
                     + ("" if st["pixels_checked"] else " (pixels not kept: --keep-states)"))
        for f in st["failing"][:8]:
            lines.append(f"  - FAILED {json.dumps(f, sort_keys=True)}")
    cap = report.get("capture")
    if cap and cap["changed_scenes"]:
        lines.append(f"- page states differ in scenes {cap['changed_scenes'][:20]}")
    if "timeline_equal" in report:
        lines.append(f"- timeline: {'equal' if report['timeline_equal'] else 'DIFFERENT'}")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("base", type=Path)
    p.add_argument("head", type=Path)
    p.add_argument("--out", type=Path, help="write report.json, summary.md and frames/ here")
    p.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    add_tolerance_args(p)
    return p.parse_args(argv)


def add_tolerance_args(p: argparse.ArgumentParser) -> None:
    d = Tolerance()
    p.add_argument("--exact", action="store_true", help="any decoded difference fails")
    p.add_argument("--pixel-level", type=int, default=d.pixel_level)
    p.add_argument("--max-changed-pixels", type=int, default=d.max_changed_pixels)
    p.add_argument("--max-mean-abs", type=float, default=d.max_mean_abs)
    p.add_argument("--state-level", type=float, default=d.state_level)
    p.add_argument("--state-max-pixels", type=int, default=d.state_max_pixels)
    p.add_argument("--audio-max-abs", type=int, default=d.audio_max_abs)
    p.add_argument("--audio-tail-seconds", type=float, default=d.audio_tail_seconds)


def tolerance_from(args: argparse.Namespace) -> Tolerance:
    return Tolerance(pixel_level=args.pixel_level, max_changed_pixels=args.max_changed_pixels,
                     max_mean_abs=args.max_mean_abs, state_level=args.state_level,
                     state_max_pixels=args.state_max_pixels, audio_max_abs=args.audio_max_abs,
                     audio_tail_seconds=args.audio_tail_seconds, exact=args.exact)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = compare_dirs(args.base, args.head, tol=tolerance_from(args), ffmpeg=args.ffmpeg, out=args.out)
    text = markdown(report)
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        (args.out / "summary.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    raise SystemExit(main())
