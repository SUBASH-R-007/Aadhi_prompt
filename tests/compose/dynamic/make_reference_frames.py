"""Write the B2c reference frames for the owner's review (B2 plan §A step 3: "Reference frames produced for owner
review"): each pilot template (formula_focus, media_split, list_build) in both formats and two families (one dark, one
light), drawn by the real render page ``/render-dynamic`` at its semantic instants, into
``docs/plans/b2/b2c_frames/`` with an ``index.html`` contact sheet.

The instants of each scene the template draws (scene time, seconds):

* ``settled``: its first entrance done (the first beat's start + SETTLE);
* ``beat <n>``: each later beat's entrance done (its start + SETTLE);
* ``<kind> <target>``: each ``cue_term`` / ``look_here`` / ``var`` / ``draw`` event at its peak (``t`` + CUE_PEAK: the
  marker swept, the outline drawn, the push in, the tint and brace drawn);
* ``hold``: the scene's last frame (a formula hold, a held list).

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

OUT_DIR = ROOT_DIR / "docs" / "plans" / "b2" / "b2c_frames"
TOKEN = "b2c.reference"
SETTLE = 0.9  # an entrance (450-600 ms, times the personality's scale) and its state change are done
CUE_PEAK = 0.7  # the cue's draw (sweep 300 ms, outline 250 ms, push 600 ms, tint and brace 300 ms) is complete
MIN_GAP = 0.35
MAX_PER_SCENE = 9
QUALITY = 85

# (template, format) -> (dark fixture, light fixture): the typical case of each, and a light family with its own text
# case (Tamil / Hindi / mixed, a wide headline)
CHOSEN: dict[tuple[str, str], tuple[str, str]] = {
    ("formula_focus", "landscape"): ("formula_focus.landscape.json", "formula_focus.landscape.light.json"),
    ("formula_focus", "portrait"): ("formula_focus.portrait.json", "formula_focus.portrait.light.json"),
    ("media_split", "landscape"): ("media_split.landscape.json", "media_split.landscape.light.json"),
    ("media_split", "portrait"): ("media_split.portrait.json", "media_split.portrait.indic.json"),
    ("list_build", "landscape"): ("list_build.landscape.json", "list_build.landscape.tamil.json"),
    ("list_build", "portrait"): ("list_build.portrait.json", "list_build.portrait.hindi.json"),
}


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

    served = json.loads((FIXTURE_DIR / name).read_text("utf-8"))
    tl = Timeline.model_validate(served)
    template = fixture_name(FIXTURE_DIR / name)[0]
    burn = bool(tl.presentation and tl.presentation.captions_burn)
    shots: list[Shot] = []
    async with open_stage(settings, size=(tl.width, tl.height), token=TOKEN, base_url=base,
                          served_timeline=served) as page:
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
                file = f"{Path(name).stem}__{sc.index}__{frame:04d}__{_slug(label)}.jpg"
                img.save(out / file, "JPEG", quality=QUALITY, optimize=True, progressive=True)
                shots.append(Shot(name, sc.index, scene["scene_id"], label, round(t, 2), frame, file))
        if page.errors:
            raise RuntimeError(f"{name}: page errors {page.errors}")
    return shots


def contact_sheet(groups: dict[tuple[str, str], list[tuple[str, str, list[Shot]]]]) -> str:
    """index.html: one section per template, a row per format and family, each frame captioned."""
    parts = ["""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>B2c reference frames</title>
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
<h1>B2c reference frames</h1>
<p>The three pilot templates drawn by the real render page (<code>/render-dynamic</code>, render mode) from the
template fixtures in <code>tests/compose/dynamic/fixtures/templates/</code>, at their semantic instants: entrance
settled, each beat, each cue at its peak, and the hold (the scene's last frame). Both formats, one dark and one light
family each. Frames are halved and saved as JPEG q85; click one for the full file. Regenerate with
<code>python -m tests.compose.dynamic.make_reference_frames</code>.</p>
"""]
    for template in ("formula_focus", "media_split", "list_build"):
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


def _serve_and_capture(out: Path) -> dict[tuple[str, str], list[tuple[str, str, list[Shot]]]]:
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
                    mode = json.loads((FIXTURE_DIR / name).read_text("utf-8"))["presentation"]
                    family = f"{mode['mode']} ({mode.get('family_id')}, {mode.get('personality')})"
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
    args = ap.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.jpg"):
        old.unlink()
    groups = _serve_and_capture(out)
    (out / "index.html").write_text(contact_sheet(groups), "utf-8", newline="\n")
    total = sum(p.stat().st_size for p in out.iterdir())
    print(f"wrote {sum(1 for _ in out.glob('*.jpg'))} frames and index.html to {out} ({total / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
