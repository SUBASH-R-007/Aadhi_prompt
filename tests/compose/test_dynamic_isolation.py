"""Classic isolation and the Dynamic preview budget (plan B2 §2 "Classic isolation"; b2crit_engineering "direct.py
inside build_timeline").

* A classic build never loads a Dynamic module: ``build_timeline`` / ``preview_timeline`` of a classic lecture (flag on
  or off, or a presentation while the flag is off) and the API's serving rule import nothing under
  ``aadhi.compose.dynamic`` (themes, zones, templates, catalogue, colour_math, faces, icons, textfit, shaping, ...),
  ``aadhi.pipeline.direct``, ``PIL.ImageFont`` or the text fit's shaping libraries (``uharfbuzz``, ``fontTools``,
  ``brotli``: the HarfBuzz step's dependencies). ``brotli`` is also imported, eagerly, by the HTTP clients (httpx,
  urllib3, aiohttp) once it is installed: should one of them ever load in a classic build, ``brotli`` beside it is that
  client's, not a Dynamic leak (``uharfbuzz`` and ``fontTools`` still are). Checked in a fresh interpreter, so modules
  other tests imported cannot hide an import; the same probe then sees a Dynamic build load ``themes`` and
  ``colour_math`` (it can fail), and still none of the shaping libraries (text fit is not wired into a build yet:
  B2d/B2e).
* ``build_timeline`` runs in the API for every Studio preview and every served rebuild, so a 40-scene Dynamic preview
  must build in <= 300 ms (median of 3 after a warm-up, CPU time of this process, with the colour cache cleared before
  each run so the first-request cost is what is measured). This machine needs ~45 ms; the 6x headroom and the CPU clock
  (scheduling noise on a busy CI runner does not count) keep it from flaking. CPU time cannot see a blocking wait (a
  file or font read), so the fastest of the three wall-clock runs must hold the budget too: noise only ever adds time,
  so it would have to hit all three runs, while a blocking wait hits every one.
"""

from __future__ import annotations

import copy
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aadhi.compose.timeline import build_timeline, preview_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay

from .factories import make_manifest, screenplay_dict

ROOT = Path(__file__).resolve().parents[2]
# module prefixes (a name and everything under it); "brotli" also stands for brotlicffi's import name prefix, and
# "_brotli" is brotli's extension module
FORBIDDEN = ("aadhi.compose.dynamic", "aadhi.pipeline.direct", "PIL.ImageFont", "uharfbuzz", "fontTools", "brotli",
             "_brotli")
SHAPING = ("uharfbuzz", "fontTools", "brotli", "_brotli")
BROTLI = ("brotli", "_brotli")
# HTTP clients that import brotli themselves when it is installed (and then advertise "br" in Accept-Encoding)
HTTP_CLIENTS = ("httpx", "urllib3", "aiohttp")


def _leaks(loaded: list[str], http: list[str]) -> list[str]:
    """The forbidden modules a build loaded, less brotli where an HTTP client that imports it is loaded too."""
    return [m for m in loaded if not (http and m.startswith(BROTLI))]
BUDGET_SECONDS = 0.300  # plan B2 §2: the preview build budget
SCENES = 40

PROBE = r"""
import json, sys
from types import SimpleNamespace

from aadhi.api.timelines import current_timeline
from aadhi.compose.timeline import build_timeline, preview_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from tests.compose.factories import make_manifest, screenplay_dict
from tests.schemas.v3_fixtures import PRESENTATION

FORBIDDEN = tuple(sys.argv[1].split(","))
HTTP_CLIENTS = tuple(sys.argv[2].split(","))

def loaded():
    return sorted(m for m in sys.modules if m.startswith(FORBIDDEN))

def http():
    return sorted(m for m in HTTP_CLIENTS if m in sys.modules)

classic = Screenplay.model_validate(screenplay_dict())
data = screenplay_dict()
data["presentation"] = {**PRESENTATION, "format": "landscape", "hook": None, "theme_family": "midnight_indigo",
                        "music": {"source": "procedural", "provider": "procedural"}}
dynamic = Screenplay.model_validate(data)
on, off = Settings(_env_file=None, dynamic_enabled=True), Settings(_env_file=None)
for sp, settings in ((classic, on), (classic, off), (dynamic, off)):
    for build in (build_timeline, preview_timeline):
        tl = build(sp, make_manifest(sp), settings=settings)
        assert tl.presentation is None
stored = build_timeline(classic, make_manifest(classic), settings=on).model_dump(mode="json")
version = SimpleNamespace(timeline=stored, built_revision=1, revision=1, screenplay=classic.model_dump(mode="json"))
assert current_timeline(version, on) is stored
before, before_http = loaded(), http()
assert build_timeline(dynamic, make_manifest(dynamic), settings=on).presentation is not None
print(json.dumps({"classic": before, "classic_http": before_http, "dynamic": loaded(), "dynamic_http": http()}))
"""


def test_a_classic_build_loads_no_dynamic_module() -> None:
    out = subprocess.run([sys.executable, "-c", PROBE, ",".join(FORBIDDEN), ",".join(HTTP_CLIENTS)], cwd=ROOT,
                         capture_output=True, text=True, timeout=300, check=False)
    assert out.returncode == 0, out.stderr[-4000:]
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert _leaks(seen["classic"], seen["classic_http"]) == [], seen
    # the probe can fail: a Dynamic build in the same interpreter loads the colour modules (lazily, at that point)
    # ... and the face table (B2b: presentation.fonts), still with no Pillow font module (textfit measures, not builds)
    assert {"aadhi.compose.dynamic.themes", "aadhi.compose.dynamic.colour_math", "aadhi.compose.dynamic.faces"} <= set(
        seen["dynamic"])
    assert "PIL.ImageFont" not in seen["dynamic"] and "aadhi.compose.dynamic.preflight" not in seen["dynamic"]
    assert not [m for m in _leaks(seen["dynamic"], seen["dynamic_http"]) if m.startswith(SHAPING)
                or m.startswith("aadhi.compose.dynamic.textfit") or m.startswith("aadhi.compose.dynamic.shaping")], seen


def test_brotli_beside_an_http_client_is_the_clients_and_nothing_else_is_excused() -> None:
    """The exemption is only brotli's, and only where an HTTP client is loaded too (review DEP-2: httpx imports brotli
    eagerly once it is installed, so a classic build that loads httpx must not read as a Dynamic leak)."""
    assert _leaks(["_brotli", "brotli", "fontTools.ttLib"], ["httpx"]) == ["fontTools.ttLib"]
    assert _leaks(["brotli", "uharfbuzz"], []) == ["brotli", "uharfbuzz"]
    out = subprocess.run([sys.executable, "-c", "import sys, httpx; print('brotli' in sys.modules)"], cwd=ROOT,
                         capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0 and out.stdout.strip() == "True", (out.stdout, out.stderr[-2000:])


def forty_scene_screenplay() -> Screenplay:
    """The compose factory's scenes (every scene type) repeated to 40, in 5 chapters of 8, with a presentation."""
    from tests.schemas.v3_fixtures import PRESENTATION

    data = screenplay_dict()
    base = data["scenes"]
    scenes: list[dict[str, Any]] = []
    for k in range(SCENES):
        s = copy.deepcopy(base[k % len(base)])
        s["id"] = f"{s['id']}-{k}"
        for b in [*s.get("beats", []), *s.get("reveal_beats", [])]:
            b["id"] = f"{b['id']}-{k}"
        panel = s.get("side_panel")
        if panel and panel.get("show_from_beat_id"):
            panel["show_from_beat_id"] = f"{panel['show_from_beat_id']}-{k}"
        scenes.append(s)
    data["scenes"] = scenes
    data["chapters"] = [{"id": f"c{j}", "title": f"Chapter {j + 1}",
                         "scene_ids": [s["id"] for s in scenes[j * 8:(j + 1) * 8]]} for j in range(5)]
    data["presentation"] = {**PRESENTATION, "format": "landscape", "hook": None, "theme_family": "midnight_indigo",
                            "music": {"source": "procedural", "provider": "procedural"}}
    return Screenplay.model_validate(data)


def test_a_forty_scene_dynamic_preview_builds_within_budget() -> None:
    from aadhi.compose.dynamic import themes

    sp = forty_scene_screenplay()
    manifest = make_manifest(sp)
    settings = Settings(_env_file=None, dynamic_enabled=True)
    tl = preview_timeline(sp, manifest, settings=settings)  # warm-up: imports and the first build
    assert tl.presentation is not None and len(tl.scenes) == SCENES and len(tl.chapters) == 5
    for build in (preview_timeline, build_timeline):
        cpu: list[float] = []
        wall: list[float] = []
        for _ in range(3):
            themes._scene_colours.cache_clear()  # the first request's colour work counts too
            c0, w0 = time.process_time(), time.perf_counter()
            build(sp, manifest, settings=settings)
            cpu.append(time.process_time() - c0)
            wall.append(time.perf_counter() - w0)
        assert statistics.median(cpu) <= BUDGET_SECONDS, (build.__name__, cpu, wall)
        assert min(wall) <= BUDGET_SECONDS, (build.__name__, cpu, wall)
