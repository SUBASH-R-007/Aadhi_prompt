"""Transform survival (design §6.7, batch B0): every screenplay transform keeps the fields it does not own.

The maximal Dynamic screenplay ``fixtures/transform_survival_dynamic.json`` uses every v3 field and enum value, every
new board item kind, a clip scene, cuts and overlays, in every scene type, next to every classic field that is left
out at its default (``hidden``, ``min_seconds``, ``variant``, a legend row's ``beat_id``). The schema's own maximal
document (``tests/schemas/fixtures/screenplay_v3_maximal.json``, the canonical dump of ``tests/schemas/v3_fixtures.py``)
is checked the same way. Both are shared with ``web/tests/studio/transformSurvival.test.js``, which runs the editor's
transforms on them (``sceneMerge.js``, ``splitScene.js``, ``convertSceneShape`` ...). Each server-side transform must
give them back exactly as they went in, apart from what the transform owns:

* ``PUT /screenplay`` and ``GET`` (stored and served unchanged; saving what was served changes nothing), and the
  loaders (``validate_stored``);
* export (``export.json``) and import (``POST /api/projects/import``);
* revert of one scene (``aadhi.changes.reverted`` and the endpoint): every other scene and field is kept;
* ``regenerate_scene``'s carry-over (``repair.carry_over`` + ``replace_scenes``): the teacher's choices on the scene
  (``hidden``, ``min_seconds``, ``look``, media overrides ...) and the rest of the lecture are kept;
* ``translate``: only translatable texts, ``language`` and ``board_language`` change; protected fields
  (``icon_hint``, ``value``, ``credit``), cuts and overlays are untouched.

Every check also runs on the classic projection of the document (no v3 field), which must come out classic: no
transform adds a v3 field or moves ``schema_version`` off 2 (B0 has no visible change for classic lectures).
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from aadhi import changes
from aadhi.schemas.features import DYNAMIC_FEATURES, V3_SCHEMA_VERSION, features_used, uses_v3
from aadhi.schemas.jsonsafe import validate_stored
from aadhi.schemas.screenplay import (
    SCHEMA_VERSION,
    V3_BOARD_ITEM_KINDS,
    V3_NARRATIVE_ROLES,
    Scene,
    Screenplay,
)

FIXTURE = Path(__file__).parent / "fixtures" / "transform_survival_dynamic.json"
SCHEMA_MAXIMAL = Path(__file__).resolve().parents[1] / "schemas" / "fixtures" / "screenplay_v3_maximal.json"
SCENE: TypeAdapter[Any] = TypeAdapter(Scene)

BASE_URL = "http://testserver"
BROWSER_HEADERS = {"Origin": BASE_URL, "X-Aadhi-CSRF": "1"}

# Every asset key of ``transform_survival_dynamic.json`` with a stored asset kind and MIME type that fits it:
# ``asset_uses`` must list exactly these (design §6.7, "Asset authorisation"). The test lectures own every key their
# document names (``_asset_rows``), so PUT accepts them all and import keeps them.
ASSETS = {
    "figure-ohm-1": ("figure", "image/png"),
    "upload-panel-1": ("upload", "image/png"),
    "upload-sim-1": ("upload", "video/mp4"),
    "upload-video-1": ("upload", "video/mp4"),
    "upload-poster-1": ("upload", "image/png"),
    "clip-lab-demo": ("clip", "video/mp4"),
    "clip-broll-1": ("clip", "video/mp4"),
    "music-curious-1": ("music", "audio/mpeg"),
    "narration-take-1": ("narration_upload", "audio/wav"),
}

# v3 additions removed by the classic projection (design §6.7 / §8.2).
_V3_SCENE_TYPES = {"clip"}
_V3_SCENE_FIELDS = ("look", "colour_is_content", "sequence")
_V3_BEAT_FIELDS = ("speaker", "answers", "cuts", "overlays", "voice_override_asset_key")
_V3_ITEM_FIELDS = ("value", "unit", "date", "icon_hint", "pins", "level")

# Keys whose string values translation may change (TRANSLATABLE_FIELDS, by their last key). Anything else, including
# ids, references, asset keys, looks, the presentation, stat values, icon hints, credits, and the anchor texts of cuts
# and overlays, must come back unchanged.
_TEXT_KEYS = frozenset({
    "narration", "spoken", "title", "subtitle", "chapter_label", "text", "term", "caption", "justification", "headers",
    "rows", "question", "options", "feedback_wrong", "explanation", "labels", "label", "x_label", "y_label", "output",
    "meaning", "statement", "correction", "summary", "session_title", "name", "description", "definition",
    "misconception", "final_answer", "steps", "hints", "unit", "date",
})


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def _fixture(path: Path = FIXTURE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _classic(doc: dict[str, Any]) -> dict[str, Any]:
    """``doc`` without anything v3: no presentation, clip scene, look, new item kind or field, cut or overlay."""
    d = copy.deepcopy(doc)
    d.pop("presentation", None)
    d["schema_version"] = SCHEMA_VERSION
    gone = {s["id"] for s in d["scenes"] if s["type"] in _V3_SCENE_TYPES}
    d["scenes"] = [s for s in d["scenes"] if s["id"] not in gone]
    for chapter in d["chapters"]:
        chapter.pop("builds_on", None)
        chapter["scene_ids"] = [x for x in chapter["scene_ids"] if x not in gone]
    for scene in d["scenes"]:
        for key in _V3_SCENE_FIELDS:
            scene.pop(key, None)
        if scene.get("side_panel"):
            scene["side_panel"].pop("colour_is_content", None)
        if scene.get("intent"):
            scene["intent"].pop("visual_intent", None)
            if scene["intent"].get("narrative_role") in V3_NARRATIVE_ROLES:
                scene["intent"]["narrative_role"] = "concept"
        dropped = {i["id"] for i in scene.get("board", []) if i["kind"] in V3_BOARD_ITEM_KINDS}
        if "board" in scene:
            scene["board"] = [i for i in scene["board"] if i["id"] not in dropped]
            for item in scene["board"]:
                for key in _V3_ITEM_FIELDS:
                    item.pop(key, None)
        for beat in scene["beats"] + scene.get("reveal_beats", []):
            for key in _V3_BEAT_FIELDS:
                beat.pop(key, None)
            for key in ("board_item_id", "fill_item_id"):
                if beat.get(key) in dropped:
                    beat[key] = None
            beat["highlight_item_ids"] = [h for h in beat.get("highlight_item_ids", []) if h not in dropped]
    return d


def _dump(sp: Screenplay) -> dict[str, Any]:
    return sp.model_dump(mode="json")


def _missing(expected: Any, got: Any, path: str = "") -> list[str]:
    """Where ``got`` lacks or changes a value of ``expected`` (keys only ``got`` has are fine)."""
    if isinstance(expected, dict):
        if not isinstance(got, dict):
            return [f"{path}: {got!r}"]
        out: list[str] = []
        for key, value in expected.items():
            if key not in got:
                out.append(f"{path}.{key}: missing")
            else:
                out += _missing(value, got[key], f"{path}.{key}")
        return out
    if isinstance(expected, list):
        if not isinstance(got, list) or len(got) != len(expected):
            return [f"{path}: {got!r}"]
        return [m for i, (a, b) in enumerate(zip(expected, got, strict=True)) for m in _missing(a, b, f"{path}[{i}]")]
    return [] if expected == got else [f"{path}: {expected!r} -> {got!r}"]


def _differences(a: Any, b: Any, path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], str]]:
    """Every place ``a`` and ``b`` differ: ``(path, "value")`` for a changed leaf, ``(path, "shape")`` for a key or
    list entry only one of them has."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[tuple[tuple[Any, ...], str]] = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(((*path, key), "shape"))
            else:
                out += _differences(a[key], b[key], (*path, key))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [(path, "shape")]
        return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in _differences(x, y, (*path, i))]
    return [] if a == b else [(path, "value")]


def _scene(doc: dict[str, Any], scene_id: str) -> dict[str, Any]:
    return next(s for s in doc["scenes"] if s["id"] == scene_id)


def _with_scene(doc: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    out["scenes"] = [scene if s["id"] == scene["id"] else s for s in out["scenes"]]
    return out


_MIME = {"image/": "image/png", "video/": "video/mp4", "audio/": "audio/mpeg"}


def _asset_rows(doc: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Asset key -> (kind, MIME type) of a stored asset that fits every field naming the key (asset_uses)."""
    from aadhi.api.screenplay_service import asset_uses

    fits: dict[str, tuple[set[str], set[str]]] = {}
    for use in asset_uses(Screenplay.model_validate(doc)):
        kinds, prefixes = fits.get(use.key, (set(use.kinds), set(use.mime_prefixes)))
        fits[use.key] = (kinds & use.kinds, prefixes & set(use.mime_prefixes))
    return {key: (min(kinds), _MIME[min(prefixes)]) for key, (kinds, prefixes) in fits.items()}


@pytest.fixture(params=["classic", "dynamic", "schema_maximal"])
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture()
def document(kind: str) -> dict[str, Any]:
    """The canonical document (as stored and served) of a maximal screenplay or of its classic projection."""
    raw = {"classic": lambda: _classic(_fixture()), "dynamic": _fixture,
           "schema_maximal": lambda: _fixture(SCHEMA_MAXIMAL)}[kind]()
    return _dump(Screenplay.model_validate(raw))


@pytest.fixture(autouse=True)
def _every_feature(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """DYNAMIC_FEATURES on (the write gates are tested elsewhere); autouse, so it is set before ``app_env``."""
    monkeypatch.setenv("DYNAMIC_FEATURES", ",".join(DYNAMIC_FEATURES))
    changes.clear_cache()
    yield
    changes.clear_cache()


# ---------------------------------------------------------------------------
# The documents themselves
# ---------------------------------------------------------------------------


def test_the_fixture_is_fully_modelled_and_maximal():
    raw = _fixture()
    sp = Screenplay.model_validate(raw)
    # Nothing in the fixture is dropped or changed by the schema (an unknown key would be ignored silently).
    assert _missing(raw, _dump(sp)) == []
    assert sp.schema_version == V3_SCHEMA_VERSION
    # It uses every feature (the editor test checks each field and kind one by one).
    assert sorted(features_used(sp)) == sorted(DYNAMIC_FEATURES)
    # Every asset key it names is authorised through asset_uses (design §6.7), with the kinds listed above.
    from aadhi.api.screenplay_service import asset_uses

    uses = asset_uses(sp)
    assert {u.key for u in uses} == set(ASSETS)
    for use in uses:
        asset_kind, mime = ASSETS[use.key]
        assert asset_kind in use.kinds and mime.startswith(use.mime_prefixes), use.loc


def test_the_schema_maximal_document_is_canonical_and_maximal():
    raw = _fixture(SCHEMA_MAXIMAL)
    sp = Screenplay.model_validate(raw)
    assert _dump(sp) == raw
    assert sorted(features_used(sp)) == sorted(DYNAMIC_FEATURES)


def test_the_classic_projection_is_classic():
    raw = _classic(_fixture())
    sp = Screenplay.model_validate(raw)
    assert _missing(raw, _dump(sp)) == []
    assert not uses_v3(sp) and _dump(sp)["schema_version"] == SCHEMA_VERSION == 2


def test_validation_and_the_loaders_give_the_document_back(document):
    assert _dump(Screenplay.model_validate(document)) == document
    assert _dump(validate_stored(Screenplay, copy.deepcopy(document))) == document  # API, workers (safe_screenplay)
    assert _dump(Screenplay.model_validate_json(json.dumps(document))) == document


# ---------------------------------------------------------------------------
# API: PUT / GET, export / import, revert
# ---------------------------------------------------------------------------


@pytest.fixture()
def lecture(app_env, asset_store, document) -> Iterator[dict[str, Any]]:
    """A signed-in teacher with a lecture (revision 1, classic sample screenplay) that owns every asset ``document``
    names."""
    from fastapi.testclient import TestClient

    from aadhi.db import get_sessionmaker
    from aadhi.main import create_app
    from aadhi.models import ProjectVersion
    from tests.api.factories import PASSWORD, add_asset_ref, add_project, add_user

    assert set(app_env.dynamic_features) == set(DYNAMIC_FEATURES)
    with get_sessionmaker()() as db:
        user = add_user(db, "alice")
        project, version = add_project(db, user, built=False)
        for key, (asset_kind, mime) in _asset_rows(document).items():
            add_asset_ref(db, project, key, kind=asset_kind, mime=mime)
        project_id, version_id = project.id, version.id
    app = create_app(app_env)
    with TestClient(app, base_url=BASE_URL, headers=BROWSER_HEADERS) as client:
        r = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        assert r.status_code == 200, r.text

        def stored() -> Any:
            with get_sessionmaker()() as db:
                return db.get(ProjectVersion, version_id).screenplay

        def set_snapshot(doc: dict[str, Any]) -> None:
            with get_sessionmaker()() as db:
                v = db.get(ProjectVersion, version_id)
                v.generation_meta = {changes.SNAPSHOT_META_KEY: changes.store_snapshot(asset_store, doc)}
                db.commit()

        yield {"client": client, "project_id": project_id, "vid": version_id, "stored": stored,
               "set_snapshot": set_snapshot}


def _put(c: Any, vid: int, doc: dict[str, Any], revision: int) -> dict[str, Any]:
    r = c.put(f"/api/versions/{vid}/screenplay", json={"screenplay": doc, "revision": revision})
    assert r.status_code == 200, r.text
    return r.json()


def _get(c: Any, vid: int) -> dict[str, Any]:
    r = c.get(f"/api/versions/{vid}")
    assert r.status_code == 200, r.text
    return r.json()


def test_put_and_get_keep_every_field(lecture, document):
    c, vid = lecture["client"], lecture["vid"]
    _put(c, vid, document, 1)
    assert lecture["stored"]() == document
    served = _get(c, vid)["screenplay"]
    assert served == document
    _put(c, vid, served, 2)  # the editor saves what it was served: nothing changes
    assert lecture["stored"]() == document
    assert _get(c, vid)["revision"] == 3


def test_export_and_import_keep_every_field(lecture, document):
    c, vid = lecture["client"], lecture["vid"]
    _put(c, vid, document, 1)
    r = c.get(f"/api/versions/{vid}/export.json")
    assert r.status_code == 200, r.text
    assert r.json() == document
    r = c.post("/api/projects/import", files={"file": ("lecture.json", r.content, "application/json")})
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] == []  # every asset key is the teacher's own: none stripped
    imported = _get(c, r.json()["version"]["id"])
    assert imported["screenplay"] == document


def test_revert_endpoint_puts_one_scene_back_and_keeps_everything_else(lecture, document):
    c, vid = lecture["client"], lecture["vid"]
    _put(c, vid, document, 1)
    lecture["set_snapshot"](document)  # "what Aadhi wrote" is the maximal document
    edited = copy.deepcopy(document)
    target, other = edited["scenes"][1], edited["scenes"][-1]
    target["title"] = "Measuring"
    target["beats"][0]["narration"] = "First, connect the cell."
    if "look" in target:
        target["look"]["palette_slot"] = 9
    other["notes"] = "Slow down here."
    _put(c, vid, edited, 2)
    r = c.post(f"/api/versions/{vid}/scenes/{target['id']}/revert", json={"revision": 3, "to": "generated"})
    assert r.status_code == 200, r.text
    expected = _with_scene(edited, copy.deepcopy(_scene(document, target["id"])))
    assert _get(c, vid)["screenplay"] == expected
    assert lecture["stored"]() == expected


def test_reverted_keeps_every_other_scene_and_field(document):
    generated = Screenplay.model_validate(document)
    for scene in document["scenes"]:
        edited = copy.deepcopy(scene)
        edited["title"] = f"{scene['title']} (edited)"[:240]
        current = Screenplay.model_validate(_with_scene(document, edited))
        target = changes.target_scene(generated, None, scene["id"], "generated")
        assert _dump(changes.reverted(current, generated, target)) == document, scene["id"]
    # a removed scene is put back where it was, in its chapter, with all its fields
    restored = 0
    for scene in document["scenes"]:
        without = copy.deepcopy(document)
        without["scenes"] = [s for s in without["scenes"] if s["id"] != scene["id"]]
        for chapter in without["chapters"]:
            chapter["scene_ids"] = [x for x in chapter["scene_ids"] if x != scene["id"]]
        try:
            current = Screenplay.model_validate(without)
        except ValueError:
            continue  # another scene's answer points into it: it cannot be removed on its own
        target = changes.target_scene(generated, None, scene["id"], "generated")
        assert _dump(changes.reverted(current, generated, target)) == document, scene["id"]
        restored += 1
    assert restored >= len(document["scenes"]) - 2


def test_changes_compare_every_field(document):
    """The "Generated vs edited" comparison: an unchanged maximal lecture has no edits; a look edit is one."""
    sp = Screenplay.model_validate(document)
    result = changes.changes(sp, sp, {})
    assert {row["status"] for row in result["scenes"]} == {"unchanged"}
    edited = copy.deepcopy(document)
    edited["scenes"][1]["notes"] = "Edited."
    if "look" in edited["scenes"][1]:
        edited["scenes"][1]["look"]["palette_slot"] = 9
    rows = changes.changes(sp, Screenplay.model_validate(edited), {})["scenes"]
    expected = ["look", "notes"] if "look" in edited["scenes"][1] else ["notes"]
    assert sorted(rows[1]["fields_changed"]) == expected


# ---------------------------------------------------------------------------
# regenerate_scene: carry-over
# ---------------------------------------------------------------------------

# What the teacher (or Visual Review) set on a scene: kept when the scene is rewritten (repair.carry_over).
_TEACHER_FIELDS = (
    "chapter_id", "hidden", "min_seconds", "look", "mascot_position", "notes", "override_asset_key",
    "poster_override_asset_key", "variant",
)


def _rewrite(scene: dict[str, Any]) -> dict[str, Any]:
    """What a scene writer returns for ``scene``: new narration and none of the teacher's choices (the writer never
    sets hidden, min_seconds, a look, media overrides, visual variants, cuts, overlays or recorded narration)."""
    new = copy.deepcopy(scene)
    for key in ("hidden", "min_seconds", "look", "variant"):
        new.pop(key, None)
    new["notes"] = ""
    new["chapter_id"] = None
    new["mascot_position"] = "right" if scene["mascot_position"] != "right" else "left"
    for key in ("override_asset_key", "poster_override_asset_key"):
        if key in new:
            new[key] = None
    if new.get("side_panel"):
        new["side_panel"].pop("variant", None)
        new["side_panel"]["override_asset_key"] = None
    for beat in new["beats"] + new.get("reveal_beats", []):
        for key in ("cuts", "overlays", "voice_override_asset_key"):
            beat.pop(key, None)
    if new["beats"]:
        new["beats"][0]["narration"] = f"In other words: {new['beats'][0]['narration']}"
    return new


def test_regenerate_carry_over_keeps_the_teachers_choices_and_the_rest_of_the_lecture(document):
    from aadhi.pipeline.repair import carry_over
    from aadhi.pipeline.screenplay_ops import replace_scenes

    sp = Screenplay.model_validate(document)
    for before in document["scenes"]:
        sid = before["id"]
        rewrite = SCENE.validate_python(_rewrite(before))
        kept = carry_over(sp.scene_by_id(sid), rewrite)
        out = kept.model_dump(mode="json")
        for key in _TEACHER_FIELDS:
            if key in before:
                assert out.get(key) == before[key], f"{sid}: {key} not carried over"
        if before.get("side_panel"):
            for key in ("override_asset_key", "variant"):
                assert out["side_panel"].get(key) == before["side_panel"].get(key), f"{sid}: side_panel.{key}"
        # the rewrite's content is used
        assert out["beats"] == rewrite.model_dump(mode="json")["beats"], sid
        # and the rest of the lecture is untouched
        assert _dump(replace_scenes(sp, {sid: kept})) == _with_scene(document, out), sid


# ---------------------------------------------------------------------------
# translate
# ---------------------------------------------------------------------------


def _translatable(path: tuple[Any, ...]) -> bool:
    if path in (("language",), ("board_language",)):
        return True
    if "cuts" in path or "overlays" in path:  # anchor texts are the source words: never translated
        return False
    keys = [p for p in path if isinstance(p, str)]
    return bool(keys) and keys[-1] in _TEXT_KEYS


def test_translate_changes_only_the_texts(document, job_ctx, monkeypatch):
    from aadhi.pipeline.translate import translate_screenplay
    from tests.pipeline.fakes import install_providers

    install_providers(monkeypatch)
    for board in (False, True):
        res = asyncio.run(translate_screenplay(job_ctx, Screenplay.model_validate(document), "ta-IN",
                                               translate_board=board))
        assert res.issues == []
        out = _dump(res.screenplay)
        diffs = _differences(document, out)
        assert [d for d in diffs if d[1] == "shape" or not _translatable(d[0])] == [], f"translate_board={board}"
        assert out["language"] == "ta-IN"
        assert out["scenes"][0]["beats"][0]["narration"] != document["scenes"][0]["beats"][0]["narration"]
        assert out["schema_version"] == document["schema_version"]
