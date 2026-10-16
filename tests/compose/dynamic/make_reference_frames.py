"""Write the reference frames for the owner's review (B2 plan §A step 3: "Reference frames produced for owner
review"): every implemented template in both formats and two families (one dark, one light) - B2c's pilots
formula_focus, media_split and list_build, and the demo phase's chapter_bumper, definition_card, hook_question,
key_terms, recap_retrieval and worked_steps - plus the cases that carry the open owner decisions (B2c review V11:
media_split's long 9:16 phrases with a tall picture, its terminal card and a clip, which is never scaled;
formula_focus's and hook_question's stress scenes with their crossfades), drawn by the real render page
``/render-dynamic`` at its semantic instants, into ``docs/plans/b2/demo_frames/`` with an ``index.html`` contact sheet.
``docs/plans/b2/b2c_frames/`` keeps the earlier B2c-only sheet.

The instants of each scene the template draws (scene time, seconds):

* ``settled``: its first entrance done (the first beat's start + SETTLE);
* ``beat <n>``: each later beat's entrance done (its start + SETTLE);
* ``<kind> <target>``: each ``cue_term`` / ``look_here`` / ``var`` / ``draw`` event at its peak (``t`` + CUE_PEAK: the
  marker swept, the outline drawn, the push in, the tint and brace drawn);
* ``hold``: the scene's last frame (a formula hold, a held list);
* ``crossfade``: the middle of a crossfade into the scene (its ``transition_in``), the outgoing scene painted under it.

A fixture name with ``+clip`` (``media_split.portrait.json+clip``) is drawn with a ``contain_blur`` clip on its side
panel, its frames served as ``tests/determinism/test_template_clips.py`` serves them (``_with_clip``).

Instants closer than MIN_GAP to one already taken are dropped, at most MAX_PER_SCENE per scene. Each frame is the
page's CDP capture (the render's own JPEG path, captions as the fixture burns them), halved and saved as JPEG q85
(about 50 KB each), so the set stays a few MB. Not a test and not run in CI (B2 owner rule: no long measurement in
CI): run ``python -m tests.compose.dynamic.make_reference_frames`` by hand after a template change worth reviewing;
it needs Playwright's Chromium and takes a few minutes. It serves the real app on a free loopback port with a
temporary data directory and database, as ``tests/determinism/conftest.py``'s ``real_app`` does.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import io
import json
import os
import shutil
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aadhi.config import ROOT_DIR
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

OUT_DIR = ROOT_DIR / "docs" / "plans" / "b2" / "demo_frames"
TOKEN = "b2c.reference"
SETTLE = 0.9  # an entrance (450-600 ms, times the personality's scale) and its state change are done
CUE_PEAK = 0.7  # the cue's draw (sweep 300 ms, outline 250 ms, push 600 ms, tint and brace 300 ms) is complete
MIN_GAP = 0.35
MAX_PER_SCENE = 9
QUALITY = 85

# (template, format) -> fixtures: the typical case of each (dark), a light family with its own text case (Tamil /
# Hindi / mixed, a wide headline), and the cases behind the open owner decisions (V11): media_split's terminal card,
# its long 9:16 phrases beside a tall picture (the media share) and a clip (a fade, never a push); formula_focus's
# stress scenes (the lint limit, a long word, Tamil, Hindi; crossfades between them). list_build's Tamil and Hindi
# timelines cross-fade into their second scene.
CHOSEN: dict[tuple[str, str], tuple[str, ...]] = {
    ("formula_focus", "landscape"): ("formula_focus.landscape.json", "formula_focus.landscape.light.json",
                                     "formula_focus.landscape.stress.json"),
    ("formula_focus", "portrait"): ("formula_focus.portrait.json", "formula_focus.portrait.light.json",
                                    "formula_focus.portrait.stress.json"),
    ("media_split", "landscape"): ("media_split.landscape.json", "media_split.landscape.light.json",
                                   "media_split.landscape.terminal.json"),
    ("media_split", "portrait"): ("media_split.portrait.json", "media_split.portrait.indic.json",
                                  "media_split.portrait.long.json", "media_split.portrait.json+clip"),
    ("list_build", "landscape"): ("list_build.landscape.json", "list_build.landscape.tamil.json"),
    ("list_build", "portrait"): ("list_build.portrait.json", "list_build.portrait.hindi.json"),
    # the demo phase's six, each a dark typical case plus its light / Indic / stress case
    ("chapter_bumper", "landscape"): ("chapter_bumper.landscape.json", "chapter_bumper.landscape.light.json"),
    ("chapter_bumper", "portrait"): ("chapter_bumper.portrait.json", "chapter_bumper.portrait.hindi.json"),
    ("definition_card", "landscape"): ("definition_card.landscape.json", "definition_card.landscape.light.json"),
    ("definition_card", "portrait"): ("definition_card.portrait.json", "definition_card.portrait.indic.json"),
    ("hook_question", "landscape"): ("hook_question.landscape.json", "hook_question.landscape.light.json",
                                     "hook_question.landscape.stress.json"),
    ("hook_question", "portrait"): ("hook_question.portrait.json", "hook_question.portrait.light.json",
                                    "hook_question.portrait.stress.json"),
    ("key_terms", "landscape"): ("key_terms.landscape.json", "key_terms.landscape.tamil.json"),
    ("key_terms", "portrait"): ("key_terms.portrait.json", "key_terms.portrait.hindi.json"),
    ("recap_retrieval", "landscape"): ("recap_retrieval.landscape.json", "recap_retrieval.landscape.tamil.json"),
    ("recap_retrieval", "portrait"): ("recap_retrieval.portrait.json", "recap_retrieval.portrait.hindi.json"),
    ("worked_steps", "landscape"): ("worked_steps.landscape.json", "worked_steps.landscape.light.json"),
    ("worked_steps", "portrait"): ("worked_steps.portrait.json", "worked_steps.portrait.light.json"),
    # step 4's four (the templates the showcase's fallback scenes needed), same shape: a dark typical case plus its
    # light / Indic case
    ("animation_stage", "landscape"): ("animation_stage.landscape.json", "animation_stage.landscape.light.json"),
    ("animation_stage", "portrait"): ("animation_stage.portrait.json", "animation_stage.portrait.indic.json"),
    ("check", "landscape"): ("check.landscape.json", "check.landscape.light.json"),
    ("check", "portrait"): ("check.portrait.json", "check.portrait.hindi.json"),
    ("full_bleed_media", "landscape"): ("full_bleed_media.landscape.json",
                                        "full_bleed_media.landscape.light.json"),
    ("full_bleed_media", "portrait"): ("full_bleed_media.portrait.json", "full_bleed_media.portrait.indic.json"),
    ("predict", "landscape"): ("predict.landscape.json", "predict.landscape.tamil.json"),
    ("predict", "portrait"): ("predict.portrait.json", "predict.portrait.hindi.json"),
}
CLIP = "+clip"


@dataclass(frozen=True)
class Shot:
    fixture: str
    scene_index: int
    scene_id: str
    label: str
    t: float  # scene time
    frame: int
    file: str


def instants(scene: dict[str, Any]) -> list[tuple[str, float]]:
    """The semantic instants of one scene (see the module docstring), in time order."""
    duration = float(scene["duration"])
    last = max(0.0, duration - 1 / 30)
    beats = sorted(float(b["start"]) for b in scene.get("beats") or [])  # scene time, as the events
    # in order of priority: the settled entrance and the hold, then the cues, then the beats (a beat within MIN_GAP
    # of a cue is shown by the cue's frame)
    first = [("settled", beats[0] + SETTLE if beats else 1.2), ("hold", last)]
    tin = scene.get("transition_in") or {}
    if tin.get("kind") == "crossfade" and float(tin.get("seconds") or 0) > 0:
        first.append(("crossfade", float(tin["seconds"]) / 2))
    cues = [(f"{e['kind']} {e.get('target') or ''}".strip(), float(e["t"]) + CUE_PEAK)
            for e in scene.get("events") or [] if e["kind"] in ("cue_term", "look_here", "var", "draw")]
    later = [(f"beat {k + 1}", b + SETTLE) for k, b in enumerate(beats[1:], start=1)]
    picked: list[tuple[str, float]] = []
    for label, t in [*first, *cues, *later]:
        t = min(max(0.0, t), last)
        if len(picked) < MAX_PER_SCENE and all(abs(t - u) >= MIN_GAP for _, u in picked):
            picked.append((label, t))
    return sorted(picked, key=lambda x: x[1])


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:40] or "x"


async def _capture(settings: Any, base: str, name: str, out: Path) -> list[Shot]:
    from PIL import Image

    from aadhi.compose.dynamic.stage_page import open_stage
    from aadhi.schemas.timeline import Timeline

    file = name.removesuffix(CLIP)
    template = fixture_name(FIXTURE_DIR / file)[0]
    frames = None
    if name.endswith(CLIP):
        from tests.determinism.test_kenburns_determinism import CLIP_FRAME, CLIP_SIZE, _clip_frame
        from tests.determinism.test_template_clips import _with_clip

        served = _with_clip(file, template, sized=True)

        def frames(path: str) -> bytes | None:  # the worker's extraction, as test_template_clips.py serves it
            m = CLIP_FRAME.match(path)
            if m is None:
                return None
            k = max(int(m[2]) / CLIP_SIZE[0], int(m[3]) / CLIP_SIZE[1])
            return _clip_frame(int(m[4]), 2 * round(CLIP_SIZE[0] * k / 2), 2 * round(CLIP_SIZE[1] * k / 2))
    else:
        served = json.loads((FIXTURE_DIR / file).read_text("utf-8"))
    tl = Timeline.model_validate(served)
    burn = bool(tl.presentation and tl.presentation.captions_burn)
    shots: list[Shot] = []
    async with open_stage(settings, size=(tl.width, tl.height), token=TOKEN, base_url=base,
                          served_timeline=served, frames=frames) as page:
        await page.set_layers(captions=burn, stage=True)
        info = await page.info()
        for sc, scene in zip(info.scenes, served["scenes"], strict=True):
            if (scene.get("look") or {}).get("template") != template:
                continue
            for label, t in instants(scene):
                frame = min(sc.f1 - 1, max(sc.f0, sc.f0 + round(t * info.fps)))
                await page.seek(frame)
                jpeg = await page.capture_jpeg(95)
                img = Image.open(io.BytesIO(jpeg)).convert("RGB")
                img = img.resize((img.width // 2, img.height // 2), Image.Resampling.LANCZOS)
                stem = Path(file).stem + ("_clip" if name.endswith(CLIP) else "")
                jpg = f"{stem}__{sc.index}__{frame:04d}__{_slug(label)}.jpg"
                img.save(out / jpg, "JPEG", quality=QUALITY, optimize=True, progressive=True)
                shots.append(Shot(name, sc.index, scene["scene_id"], label, round(t, 2), frame, jpg))
        if page.errors:
            raise RuntimeError(f"{name}: page errors {page.errors}")
    return shots


def contact_sheet(groups: dict[tuple[str, str], list[tuple[str, str, list[Shot]]]]) -> str:
    """index.html: one section per template CAPTURED (never a hard-coded list: review D3, where the sheet still
    named B2c's three pilots and left the demo phase's six templates out of the owner's size review), in CHOSEN's
    order, with a row per format and family and each frame captioned."""
    names = list(dict.fromkeys(t for t, _ in CHOSEN if any(k[0] == t for k in groups)))
    parts = ["""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>B2 reference frames</title>
<style>
:root { color-scheme: light dark; --bg: #f6f6f4; --ink: #1b1d22; --muted: #5b606b; --card: #ffffff; --line: #d9dbe0; }
@media (prefers-color-scheme: dark) { :root { --bg: #15171c; --ink: #eceef2; --muted: #9aa0ab; --card: #1f2229;
  --line: #2e323b; } }
body { margin: 0; padding: 24px 16px 64px; background: var(--bg); color: var(--ink);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1400px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; } h2 { font-size: 20px; margin: 40px 0 8px; } h3 { font-size: 15px;
  margin: 20px 0 8px; color: var(--muted); font-weight: 600; }
p { color: var(--muted); margin: 0 0 8px; max-width: 80ch; }
.row { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(var(--w), 1fr)); }
.landscape { --w: 300px; } .portrait { --w: 170px; }
figure { margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
figure img { display: block; width: 100%; height: auto; }
figcaption { padding: 6px 8px; font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
figcaption b { color: var(--ink); font-weight: 600; }
</style></head><body><main>
<h1>B2 reference frames</h1>
<p>__TEMPLATES__ drawn by the real render page (<code>/render-dynamic</code>, render mode) from the
template fixtures in <code>tests/compose/dynamic/fixtures/templates/</code>, at their semantic instants: entrance
settled, each beat, each cue at its peak, a crossfade's middle, and the hold (the scene's last frame). Both formats,
a dark and a light family each, and the cases behind the open owner decisions (media_split's terminal card, long 9:16
phrases beside a tall picture and a clip; formula_focus's stress scenes). Frames are saved at half size (960 x 540 or
540 x 960) as JPEG q85; click one to open that file on its own. Regenerate with
<code>python -m tests.compose.dynamic.make_reference_frames</code>.</p>
""".replace("__TEMPLATES__", f"The {len(names)} templates <code>{html.escape(', '.join(names))}</code>")]
    for template in names:
        parts.append(f"<h2>{html.escape(template)}</h2>")
        for fmt in ("landscape", "portrait"):
            for family, fixture, shots in groups.get((template, fmt), []):
                parts.append(f"<h3>{'16:9' if fmt == 'landscape' else '9:16'} &middot; {html.escape(family)} "
                             f"&middot; <code>{html.escape(fixture)}</code></h3>")
                parts.append(f'<div class="row {fmt}">')
                for s in shots:
                    alt = f"{template} {fmt} {family}: scene {s.scene_id}, {s.label} at {s.t:.2f} s"
                    parts.append(
                        f'<figure><a href="{html.escape(s.file)}"><img src="{html.escape(s.file)}" loading="lazy" '
                        f'alt="{html.escape(alt)}"></a><figcaption><b>{html.escape(s.label)}</b><br>'
                        f"{html.escape(s.scene_id)} &middot; t {s.t:.2f} s &middot; frame {s.frame}</figcaption>"
                        "</figure>")
                parts.append("</div>")
    parts.append("</main></body></html>\n")
    return "\n".join(parts)


def _serve_and_capture(out: Path, only: frozenset[str] = frozenset()) -> dict[tuple[str, str],
                                                                              list[tuple[str, str, list[Shot]]]]:
    """The real app on a free loopback port with a temporary data dir and database (``real_app``'s setup)."""
    work = Path(tempfile.mkdtemp(prefix="b2c-frames-"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    os.environ.update({
        "DATA_DIR": str(work / "data"), "DATABASE_URL": f"sqlite:///{(work / 'app.db').as_posix()}",
        "BASE_URL": f"http://127.0.0.1:{port}", "DYNAMIC_ENABLED": "true", "WORKER_MODE": "external",
        "RENDER_WORK_DIR": str(work / "render-work"), "RENDER_WIDTH": "1920", "RENDER_HEIGHT": "1080",
        "RENDER_FPS": "30", "RENDER_BGM": "false",
    })
    from aadhi import config, db, models  # noqa: F401  (models registers the tables)
    from tests.compose.dynamic.helpers import parity_eval

    config.get_settings.cache_clear()
    db.reset_engine_cache()
    settings = config.get_settings()
    db.Base.metadata.create_all(db.get_engine())
    groups: dict[tuple[str, str], list[tuple[str, str, list[Shot]]]] = {}
    try:
        with parity_eval().live_server(settings) as base:
            for (template, fmt), names in CHOSEN.items():
                for name in names:
                    if only and name not in only:
                        continue
                    mode = json.loads((FIXTURE_DIR / name.removesuffix(CLIP)).read_text("utf-8"))["presentation"]
                    family = f"{mode['mode']} ({mode['family']}, {mode['personality']})"
                    shots = asyncio.run(_capture(settings, base, name, out))
                    groups.setdefault((template, fmt), []).append((family, name, shots))
                    print(f"{name}: {len(shots)} frames")
    finally:
        db.get_engine().dispose()
        shutil.rmtree(work, ignore_errors=True)
    return groups


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="output directory (emptied of .jpg files first)")
    ap.add_argument("--only", nargs="*", default=[], help="only these CHOSEN names (a scratch look at a few)")
    args = ap.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.jpg"):
        old.unlink()
    groups = _serve_and_capture(out, frozenset(args.only))
    (out / "index.html").write_text(contact_sheet(groups), "utf-8", newline="\n")
    total = sum(p.stat().st_size for p in out.iterdir())
    print(f"wrote {sum(1 for _ in out.glob('*.jpg'))} frames and index.html to {out} ({total / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
