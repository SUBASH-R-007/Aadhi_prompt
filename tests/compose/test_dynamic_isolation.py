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
* B3 (audio, B3 plan §B3-0 and §3.1 item 6): the probe also runs a classic ``build_assets`` (the providers' fake TTS,
  ffmpeg-free audio) for a classic lecture with the Dynamic mode on and for a lecture WITH a presentation while the
  mode is off, a classic ``assemble_screenplay``, and a whole classic generation through the orchestrator
  (``generate_lecture`` with the providers' fakes, where B3's cast stage and music selection will be wired). A leak from
  ``pipeline/assets.py``, ``pipeline/audio.py``, ``script.py`` or ``orchestrator.py`` is therefore seen too. The
  forbidden list names every B3 module outside ``aadhi.compose.dynamic`` and ``scipy`` (the procedural music engine's,
  worker-only). Importing the API app loads none of them, and a Dynamic ``build_timeline`` / ``preview_timeline``
  loads no ``scipy``, no ``aadhi.providers.music`` and no ``aadhi.compose.bs1770``: the timeline only reads music
  metadata.
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
# B3's modules outside the ``aadhi.compose.dynamic`` prefix and the music engine's scipy (B3 plan §B3-0): none of them
# may load in a classic build, a classic asset build or a classic generation. Named one by one, so the list says
# exactly what B3 adds (a module that does not exist yet is simply never loaded).
B3_FORBIDDEN = ("aadhi.providers.tts.personas", "aadhi.providers.tts.delivery", "aadhi.providers.tts.calibration",
                "aadhi.pipeline.casting", "aadhi.pipeline.history", "aadhi.pipeline.music", "aadhi.pipeline.cast_stage",
                "aadhi.providers.music", "aadhi.compose.bs1770", "scipy")
# ... and what a Dynamic timeline build must not load either: it reads music metadata only (B3d's fit and duck are pure)
DYNAMIC_AUDIO_FORBIDDEN = ("scipy", "aadhi.providers.music", "aadhi.compose.bs1770")
FORBIDDEN = ("aadhi.compose.dynamic", "aadhi.pipeline.direct", "PIL.ImageFont", "uharfbuzz", "fontTools", "brotli",
             "_brotli", *B3_FORBIDDEN)
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
import asyncio, json, os, shutil, sys, tempfile
from types import SimpleNamespace

# a database and data dir of the probe's own, for the classic asset build's clips (never the session's database)
_tmp = tempfile.mkdtemp(prefix="b3-isolation-")
os.environ["DATA_DIR"] = os.path.join(_tmp, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "probe.db").replace(os.sep, "/")

from aadhi.api.timelines import current_timeline
from aadhi.compose.timeline import build_timeline, preview_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from tests.compose.factories import make_manifest, screenplay_dict
from tests.schemas.v3_fixtures import PRESENTATION

FORBIDDEN = tuple(sys.argv[1].split(","))
HTTP_CLIENTS = tuple(sys.argv[2].split(","))
B3_FORBIDDEN = tuple(sys.argv[3].split(","))

def loaded(prefixes=FORBIDDEN):
    return sorted(m for m in sys.modules if m.startswith(prefixes))

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

# B3: a classic asset build (the providers' fake TTS through the real factory; the audio assembly without ffmpeg) of a
# classic lecture with the mode on and of a lecture with a presentation while the mode is off, then a classic
# generation's screenplay assembly.
from aadhi import db
from aadhi.pipeline import audio
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions, IngestResult, LecturePlan
from aadhi.pipeline.script import assemble_screenplay
from aadhi.storage import get_storage
from aadhi.storage.assets import AssetStore
from tests.helpers import FakeJobContext
from tests.pipeline.fakes import dummy_mp3, wav_decode

audio.decode_pcm, audio.encode_mp3 = wav_decode, dummy_mp3
db.Base.metadata.create_all(db.get_engine())
store = AssetStore(get_storage(), db.get_sessionmaker())
narrated = {"language": "en-IN", "scenes": [
    {"id": "a", "type": "content", "title": "A", "beats": [{"id": "a1", "narration": "Current flows round a loop."}]},
    {"id": "q", "type": "quiz_checkpoint", "title": "Q", "question": "Which?", "options": ["x", "y"],
     "correct_index": 0, "countdown_seconds": 4, "beats": [{"id": "q1", "narration": "Which one is right?"}],
     "reveal_beats": [{"id": "q2", "narration": "The first one."}]}]}
with_presentation = {**narrated, "presentation": data["presentation"]}
for doc, settings in ((narrated, on), (with_presentation, off)):
    s = settings.model_copy(update={"tts_provider": "fake", "image_provider": "none", "video_provider": "none"})
    ctx = FakeJobContext(settings=s, assets=store)
    res = asyncio.run(build_assets_detailed(ctx, Screenplay.model_validate(doc), GenerationOptions()))
    assert res.manifest.audio["q"].asset_key and not [i for i in res.issues if i.severity == "error"], res.issues
generated = assemble_screenplay(LecturePlan(), list(classic.scenes), IngestResult(markdown="x"), GenerationOptions(),
                                [], settings=off)
assert generated.presentation is None

# ... and a whole classic generation through the orchestrator (generate_lecture: ingest, plan, script, assets and the
# timeline, with the providers' fakes), where B3's cast stage and music selection will be wired: a module-level or an
# unconditional import of a B3 module there is a leak into every classic generation.
import pytest
from aadhi.pipeline import orchestrator
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import install_providers
from tests.pipeline.sources import SAMPLE_MARKDOWN

patches = pytest.MonkeyPatch()
install_providers(patches, real_timeline=True)
seeded = seed_project(store.storage, SAMPLE_MARKDOWN.encode("utf-8"), "text/markdown", "ohm.md",
                      options={"target_minutes": 6})
ctx = FakeJobContext(settings=off.model_copy(update={"tts_provider": "fake", "image_provider": "fake",
                                                     "video_provider": "none"}), assets=store)
ctx.project_id, ctx.version_id, ctx.kind = seeded.project_id, seeded.version_id, "generate_lecture"
ctx.payload = {"source_document_id": seeded.source_id, "options": {"target_minutes": 6}, "base_revision": 1}
result = asyncio.run(orchestrator.generate_lecture(ctx))
assert result["issue_counts"]["error"] == 0, result
version = get_version(seeded.version_id)
assert version.screenplay and "presentation" not in version.screenplay and version.timeline
patches.undo()
before, before_http = loaded(), http()

from aadhi.main import create_app

create_app(off)
api_b3 = loaded(B3_FORBIDDEN)

assert build_timeline(dynamic, make_manifest(dynamic), settings=on).presentation is not None
assert preview_timeline(dynamic, make_manifest(dynamic), settings=on).presentation is not None
db.get_engine().dispose()
shutil.rmtree(_tmp, ignore_errors=True)
print(json.dumps({"classic": before, "classic_http": before_http, "api_b3": api_b3, "dynamic": loaded(),
                  "dynamic_http": http()}))
"""


def test_a_classic_build_loads_no_dynamic_module() -> None:
    out = subprocess.run([sys.executable, "-c", PROBE, ",".join(FORBIDDEN), ",".join(HTTP_CLIENTS),
                          ",".join(B3_FORBIDDEN)], cwd=ROOT, capture_output=True, text=True, timeout=300, check=False)
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
    # B3: the API process loads no B3 module and no scipy (B3 plan §3.1 item 6; a later step that must load one there
    # names that module here as an explicit exception); a Dynamic timeline build loads no music engine, no meter and
    # no scipy
    assert seen["api_b3"] == [], seen
    assert not [m for m in seen["dynamic"] if m.startswith(DYNAMIC_AUDIO_FORBIDDEN)], seen


def test_the_probe_names_every_b3_module_outside_the_dynamic_prefix() -> None:
    """The B3 plan's FORBIDDEN gains (§B3-0) are all in the classic list, and the Dynamic build's audio list in it."""
    assert set(B3_FORBIDDEN) <= set(FORBIDDEN)
    assert not [m for m in B3_FORBIDDEN if m.startswith("aadhi.compose.dynamic")]
    assert {"aadhi.providers.tts.personas", "aadhi.providers.tts.delivery", "aadhi.providers.tts.calibration",
            "aadhi.pipeline.casting", "aadhi.pipeline.history", "aadhi.pipeline.music", "aadhi.pipeline.cast_stage",
            "aadhi.providers.music", "aadhi.compose.bs1770", "scipy"} == set(B3_FORBIDDEN)
    assert set(DYNAMIC_AUDIO_FORBIDDEN) <= set(B3_FORBIDDEN)


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
    # Aadhi's synthesised intro opens it (step 5: every Dynamic video opens with Aadhi), inside the first chapter
    assert tl.presentation is not None and len(tl.scenes) == SCENES + 1 and len(tl.chapters) == 5
    assert tl.scenes[0].look is not None and tl.scenes[0].look.template == "aadhi_intro"
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
