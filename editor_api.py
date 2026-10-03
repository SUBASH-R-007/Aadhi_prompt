"""Phase 19 — the Advanced Video Editor's saving route.

The editor edits the lesson the page already plays (one lesson model: the saved lesson JSON). This route stores an edited
lesson IN PLACE (the same history entry: an edit is not a new version; "Save version" uses /save-history on purpose)
with a revision check, so a stale editor copy never silently overwrites newer changes (a review decision made elsewhere,
a generation that finished meanwhile). The revision token is the lesson row's `updated_at`: every in-place writer
(Visual Review, the composition / direction / style routes, generation completions) changes it.

Validation (nothing a client sends is trusted): at most 200 scenes, each an object with a valid unique scene id, the
per-scene editor data (`scene.edit`) and the lesson-level editor data (`payload.editor`) cleaned to their contract, every
library asset the scenes refer to usable by this user (their own or shared), and no change to the scenes' order or
membership while the lesson has AI generation in progress (generation results are attached by scene position). After a
save the lesson's asset references are recorded again (an asset still used is never deletable); nothing is generated,
nothing is deleted.

Phase 20: every other in-place writer of a lesson writes through `update_lesson` (the same compare-and-set, its change applied
again to the newer lesson when another save landed first), so a background result or a review decision never silently
overwrites an editor save, and an editor save never overwrites them (its copy is then stale: 409).
"""
import copy
import datetime
import hashlib
import json
import math
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import models
from database import get_db

MAX_SCENES = 200
SCENE_ID = re.compile(r"^s-[0-9a-f]{12}$")
ASSET_ID = re.compile(r"^[0-9a-f]{32}$")
EDIT_ORIGINS = ("generated", "inserted", "duplicated", "split")
ORIGINAL_FIELDS = ("title", "subtitle", "narration", "html", "labels")
ORIGINAL_LIMITS = {"title": 500, "subtitle": 800, "narration": 30000, "html": 200000}
MAX_HOLD = 600.0
EDITOR_VERSION = 1
SCENE_TEXT_LIMITS = {"title": 2000, "subtitle": 2000, "narration": 50000, "html": 300000}
MAX_BODY = 8_000_000           # characters of an edited lesson (JSON)
ANY_ASSET = re.compile(r"(?:asset:|/api/assets/)([0-9a-fA-F]{32})")  # library ids anywhere in a scene (any field, any case)


class Invalid(ValueError):
    """Editor data that does not follow the contract."""


def new_scene_id():
    return "s-" + secrets.token_hex(6)


def ensure_ids(scenes, seed=None):
    """Every scene a valid, unique scene id (missing or duplicated ids replaced); returns how many were set. With a seed
    (the lesson's id), a missing id is derived from the seed and the position: an old lesson gets the same ids on every
    load until the first save stores them (a reload before it never makes the editor's edits point nowhere)."""
    seen, changed = set(), 0
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        sid = scene.get("scene_id")
        if not (isinstance(sid, str) and SCENE_ID.fullmatch(sid)) or sid in seen:
            sid = "s-" + hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()[:12] if seed is not None else new_scene_id()
            while sid in seen:
                sid = new_scene_id()
            scene["scene_id"] = sid
            changed += 1
        seen.add(sid)
    return changed


def clean_edit(edit):
    """A scene's editor data cleaned to its contract (unknown keys dropped); Invalid for wrong types or ranges."""
    if edit is None:
        return None
    if not isinstance(edit, dict):
        raise Invalid("a scene's edit data must be an object")
    out = {}
    if "hidden" in edit:
        if not isinstance(edit["hidden"], bool):
            raise Invalid("hidden must be true or false")
        if edit["hidden"]:
            out["hidden"] = True
    if edit.get("min_seconds") is not None:
        value = edit["min_seconds"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not _finite(value) or not (0 < value <= MAX_HOLD):
            raise Invalid(f"a scene's duration must be between 0 and {int(MAX_HOLD)} seconds")
        out["min_seconds"] = round(float(value), 2)
    if edit.get("captions") is not None:
        if edit["captions"] != "off":
            raise Invalid("captions can only be turned off for a scene")
        out["captions"] = "off"
    if "narration_muted" in edit:
        if not isinstance(edit["narration_muted"], bool):
            raise Invalid("narration_muted must be true or false")
        if edit["narration_muted"]:
            out["narration_muted"] = True
    if edit.get("origin") is not None:
        if edit["origin"] not in EDIT_ORIGINS:
            raise Invalid("unknown scene origin")
        if edit["origin"] != "generated":
            out["origin"] = edit["origin"]
    if edit.get("from") is not None:
        if not (isinstance(edit["from"], str) and SCENE_ID.fullmatch(edit["from"])):
            raise Invalid("from must be a scene id")
        out["from"] = edit["from"]
    original = edit.get("original")
    if original is not None:
        if not isinstance(original, dict):
            raise Invalid("original must be an object")
        kept = {}
        for field, value in original.items():
            if field not in ORIGINAL_FIELDS:
                continue
            if field == "labels":
                if not (isinstance(value, list) and len(value) <= 6 and all(_label_ok(x) for x in value)):
                    raise Invalid("original labels must be up to 6 short texts")
                kept[field] = json.loads(json.dumps(value))
            elif isinstance(value, str) and len(value) <= ORIGINAL_LIMITS[field]:
                kept[field] = value
            else:
                raise Invalid(f"original {field} must be a text")
        if kept:
            out["original"] = kept
    return out or None


def _finite(value):
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError, TypeError):
        return False


def _label_ok(item):
    """A screenplay label: a short text, or {text, at} with at in seconds or {sync: n} (cinematic.clean_composition)."""
    if isinstance(item, str):
        return len(item) <= 120
    if not isinstance(item, dict) or set(item) - {"text", "at"} or not isinstance(item.get("text"), str) or len(item["text"]) > 120:
        return False
    at = item.get("at")
    if at is None:
        return True
    if isinstance(at, bool):
        return False
    if isinstance(at, (int, float)):
        return _finite(at) and 0 <= at <= 3600
    return isinstance(at, dict) and set(at) == {"sync"} and isinstance(at["sync"], int) and not isinstance(at["sync"], bool) and 0 <= at["sync"] <= 100


def clean_editor(editor, scene_ids):
    """The lesson-level editor data cleaned to its contract."""
    if editor is None:
        return None
    if not isinstance(editor, dict):
        raise Invalid("the editor data must be an object")
    out = {"version": EDITOR_VERSION}
    captions = editor.get("captions")
    if captions is not None:
        if not (isinstance(captions, dict) and isinstance(captions.get("visible", True), bool)):
            raise Invalid("captions must be {visible: true|false}")
        out["captions"] = {"visible": captions.get("visible", True)}
    order = editor.get("generated_order")
    if order is not None:
        if not (isinstance(order, list) and len(order) <= 400 and all(isinstance(x, str) and SCENE_ID.fullmatch(x) for x in order)):
            raise Invalid("generated_order must be a list of scene ids")
        out["generated_order"] = list(dict.fromkeys(order))
    return out


def clean_scenes(scenes):
    """The edited scenes validated (ids, edit data); returned as given otherwise (the lesson's own content)."""
    if not isinstance(scenes, list) or not scenes:
        raise Invalid("a lesson needs at least one scene")
    if len(scenes) > MAX_SCENES:
        raise Invalid(f"a lesson can have at most {MAX_SCENES} scenes")
    seen = set()
    for scene in scenes:
        if not isinstance(scene, dict):
            raise Invalid("every scene must be an object")
        sid = scene.get("scene_id")
        if not (isinstance(sid, str) and SCENE_ID.fullmatch(sid)):
            raise Invalid("every scene needs a scene id")
        if sid in seen:
            raise Invalid("two scenes share one scene id")
        seen.add(sid)
        for field, limit in SCENE_TEXT_LIMITS.items():
            if isinstance(scene.get(field), str) and len(scene[field]) > limit:
                raise Invalid(f"a scene's {field} is too long")
        edit = clean_edit(scene.get("edit"))
        if edit:
            scene["edit"] = edit
        else:
            scene.pop("edit", None)
    return scenes


def _revision(project):
    stamp = project.updated_at or project.created_at
    return stamp.isoformat() if stamp else "0"


def revision_of(project):
    """The lesson's revision token (other routes return it so the editor's copy stays current)."""
    return _revision(project) if project is not None else None


class LessonChanged(HTTPException):
    """update_lesson: other saves of the lesson won every attempt; nothing was written (409, plain words)."""

    def __init__(self, revision=None):
        super().__init__(status_code=409, detail={"message": "The lesson kept changing while this was being saved, so nothing was "
                                                             "overwritten: try again.", "revision": revision})


def next_stamp(previous):
    """The `updated_at` of an in-place write: now, but always later than the one it replaces (two writes within one clock
    tick never share a revision, and an earlier revision is never given again)."""
    now = datetime.datetime.utcnow()
    return now if previous is None or now > previous else previous + datetime.timedelta(microseconds=1)


def update_lesson(db, project, mutate, attempts=4):
    """Phase 20 — the in-place write every lesson writer uses (a background result attached, a review decision, the
    style, the direction), so it never silently overwrites a save committed meanwhile (the editor's, another review's).
    The lesson row is read again, `mutate(payload)` applies the writer's change to that fresh payload (in place; truthy
    when it changed something) and the write only lands while the row is still the one read (`updated_at`
    compare-and-set, as the editor's own save). When another save won, the change is applied again to the newer lesson,
    up to `attempts` times; after that LessonChanged (409) and nothing is written. Returns the lesson's new revision, or
    None when mutate changed nothing (nothing written). mutate may raise (an HTTPException: nothing is written); a lesson
    that would hold NaN / Infinity is refused (422, nothing written). Commits; the caller's session must hold no other
    unsaved change (a lost race rolls back)."""
    from sqlalchemy.exc import InvalidRequestError
    for _attempt in range(max(1, attempts)):
        try:
            db.refresh(project)
        except InvalidRequestError:  # the lesson was deleted meanwhile
            raise HTTPException(status_code=404, detail="Lesson not found.")
        seen = project.updated_at
        payload = json.loads(project.json_data or "{}")
        if not mutate(payload):
            return None
        try:
            text = json.dumps(payload, allow_nan=False)  # no NaN / Infinity: the saved lesson stays readable JSON
        except ValueError:
            raise HTTPException(status_code=422, detail="The lesson contains numbers that cannot be saved.")
        same_row = models.Project.updated_at == seen if seen is not None else models.Project.updated_at.is_(None)
        written = db.query(models.Project).filter(models.Project.id == project.id, same_row).update(
            {models.Project.json_data: text, models.Project.updated_at: next_stamp(seen)}, synchronize_session=False)
        if written == 1:
            db.commit()
            db.refresh(project)
            return _revision(project)
        db.rollback()  # another save landed first: apply the change again to the lesson as it is now
    try:
        current = revision_of(project)
    except InvalidRequestError:
        current = None
    raise LessonChanged(current)


def same_scene(saved_scenes, index, scene):
    """A scene sent with a Visual Review decision must be the scene saved at that position (by id): a scene moved or
    removed by an unsaved edit (or in another tab) is never written over another one (409)."""
    from fastapi import HTTPException as _HTTPException
    sent = scene.get("scene_id") if isinstance(scene, dict) else None
    saved = saved_scenes[index].get("scene_id") if 0 <= index < len(saved_scenes) and isinstance(saved_scenes[index], dict) else None
    if isinstance(sent, str) and isinstance(saved, str) and sent != saved:
        raise _HTTPException(status_code=409, detail="The lesson's scenes changed since this page loaded them: save or reload, then try again.")


PAGE_MEDIA_FIELDS = ("video_url", "video_asset_id", "manim_video_url", "manim_asset_id")  # media the page makes / uploads in the preview
SIDE_MEDIA_FIELDS = ("video_url", "video_asset_id", "gif_url")                          # ... for the side panel


def _shows(media):
    return isinstance(media, dict) and bool(media.get("asset_id") or media.get("url"))


def reviewed_scene(saved, sent):
    """Phase 20 — the scene a Visual Review decision (visual, presenter, composition, direction) is made on, when the page
    sends its copy of the scene: the SAVED scene (an editor save made since the page loaded the lesson — a narration fix,
    a retitle, editor data — is never reverted by an older copy), with what the page's copy legitimately adds:
      - the review's own records, per slot (visual_review: each came from the server; a slot only the saved scene has is
        kept) and the plans the page keeps (visual_plan per slot, cinematic_plan, presenter_plan, visual_direction) — except
        that a plan showing nothing never replaces a saved one that shows a picture or clip (e.g. a background result
        attached since);
      - the media the page made or uploaded in the preview (video_url / video_asset_id / manim_video_url / manim_asset_id,
        the side panel's video_url / video_asset_id / gif_url).
    A lesson whose saved scene has no scene id was never saved by the editor: the page's copy is taken as it is (as
    before Phase 20). Returns a new scene; neither argument is changed."""
    if not isinstance(sent, dict) or not isinstance(saved, dict) or not saved.get("scene_id"):
        return copy.deepcopy(sent)
    scene, page = copy.deepcopy(saved), copy.deepcopy(sent)
    if isinstance(page.get("visual_review"), dict):
        scene["visual_review"] = {**(scene["visual_review"] if isinstance(scene.get("visual_review"), dict) else {}), **page["visual_review"]}
    if isinstance(page.get("visual_plan"), dict):
        plans = scene["visual_plan"] if isinstance(scene.get("visual_plan"), dict) else {}
        for slot, plan in page["visual_plan"].items():
            if not (_shows(plans.get(slot)) and not _shows(plan)):
                plans[slot] = plan
        scene["visual_plan"] = plans
    for key in ("presenter_plan", "cinematic_plan", "visual_direction"):
        if isinstance(page.get(key), dict):
            kept = scene.get(key) if isinstance(scene.get(key), dict) else {}
            scene[key] = page[key]
            if key == "presenter_plan" and _shows(kept.get("media")) and not _shows(page[key].get("media")):
                scene[key]["media"] = kept["media"]  # a presenter clip attached since
            if key == "cinematic_plan" and isinstance(kept.get("background"), dict) and kept["background"].get("asset_id") \
                    and not (isinstance(page[key].get("background"), dict) and page[key]["background"].get("asset_id")):
                scene[key]["background"] = kept["background"]  # a generated background attached since
    for key in PAGE_MEDIA_FIELDS:
        if page.get(key):
            scene[key] = page[key]
    side, page_side = scene.get("side_panel"), page.get("side_panel")
    if isinstance(side, dict) and isinstance(page_side, dict):
        for key in SIDE_MEDIA_FIELDS:
            if page_side.get(key):
                side[key] = page_side[key]
    return scene


def _readable(scene):
    """What a scene shows and says (without ids and editor data): an old lesson's first save may only add ids."""
    return json.dumps({k: v for k, v in scene.items() if k not in ("scene_id", "edit")}, sort_keys=True, default=str) if isinstance(scene, dict) else None


def _order(scenes):
    return [s.get("scene_id") for s in scenes if isinstance(s, dict)]


class EditorSaveIn(BaseModel):
    expected_revision: str
    scenes: list
    editor: dict | None = None


def create_editor_router(*, get_current_user, library):
    router = APIRouter(prefix="/api/editor", tags=["editor"])

    def own_project(db, project_id, user):
        project = db.query(models.Project).filter(models.Project.id == project_id, models.Project.user_id == user.id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Lesson not found.")
        return project

    @router.get("/{project_id}")
    def load(project_id: int, current_user=Depends(get_current_user), db=Depends(get_db)):
        """The lesson for the editor: its scenes (each with a scene id), its editor data and its revision token."""
        project = own_project(db, project_id, current_user)
        payload = json.loads(project.json_data or "{}")
        scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
        new_ids = ensure_ids(scenes, seed=project.id)  # ids for an old lesson (the same on every load; the first save keeps them)
        for scene in scenes:
            if isinstance(scene, dict) and "edit" in scene:
                try:
                    cleaned = clean_edit(scene.get("edit"))
                except Invalid:
                    cleaned = None  # invalid editor data (e.g. a hand-made save) is dropped, never a lesson that cannot be saved
                if cleaned:
                    scene["edit"] = cleaned
                else:
                    scene.pop("edit", None)
        active = db.query(models.AIGenerationRun.id).filter(
            models.AIGenerationRun.project_id == project.id,
            models.AIGenerationRun.status.in_(("queued", "running", "recovering", "cancel_requested"))).count()
        return {"revision": _revision(project), "scenes": scenes, "editor": payload.get("editor") if isinstance(payload.get("editor"), dict) else None,
                "cinematic_style": payload.get("cinematic_style"), "generating": active, "new_ids": new_ids}

    @router.get("/{project_id}/status")
    def status(project_id: int, current_user=Depends(get_current_user), db=Depends(get_db)):
        """The lesson's revision and whether media is being generated (light: polled while the editor is open)."""
        project = own_project(db, project_id, current_user)
        active = db.query(models.AIGenerationRun.id).filter(
            models.AIGenerationRun.project_id == project.id,
            models.AIGenerationRun.status.in_(("queued", "running", "recovering", "cancel_requested"))).count()
        return {"revision": _revision(project), "generating": active}

    @router.put("/{project_id}")
    def save(project_id: int, body: EditorSaveIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """Stores the edited lesson in place (same history entry) when the editor's copy is current; 409 otherwise."""
        project = own_project(db, project_id, current_user)
        if body.expected_revision != _revision(project):
            raise HTTPException(status_code=409, detail={"message": "The lesson changed since the editor loaded it.",
                                                         "revision": _revision(project)})
        try:
            text = json.dumps(body.scenes, allow_nan=False)  # no NaN / Infinity: the saved lesson stays readable JSON
            if len(text) > MAX_BODY:
                raise HTTPException(status_code=413, detail="This lesson is too large to save.")
            scenes = clean_scenes(json.loads(text))
            editor = clean_editor(body.editor, set(_order(scenes)))
        except Invalid as e:
            raise HTTPException(status_code=422, detail=str(e) + ".")
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="The lesson could not be read.")
        # every library asset the scenes refer to (in any field, any form) must be usable by this user (their own or shared)
        refs = {ref.lower() for _field, ref in library.lesson_refs(scenes) if isinstance(ref, str) and ASSET_ID.fullmatch(ref.lower())}
        refs |= {m.lower() for m in ANY_ASSET.findall(text)}
        for scene in scenes:
            media = (scene.get("presenter_plan") or {}) if isinstance(scene.get("presenter_plan"), dict) else {}
            if isinstance(media.get("media_asset_id"), str) and ASSET_ID.fullmatch(media["media_asset_id"].lower()):
                refs.add(media["media_asset_id"].lower())
        for ref in sorted(refs):
            if library.accessible(db, ref, current_user.id) is None:
                raise HTTPException(status_code=422, detail="The lesson refers to a picture or clip that is not in your library.")
        payload = json.loads(project.json_data or "{}")
        saved = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
        if _order(saved) != _order(scenes):
            active = db.query(models.AIGenerationRun.id).filter(
                models.AIGenerationRun.project_id == project.id,
                models.AIGenerationRun.status.in_(("queued", "running", "recovering", "cancel_requested"))).count()
            # a lesson saved before Phase 19 has no ids yet: its first save only adds them (the same scenes, the same order)
            same_scenes = len(saved) == len(scenes) and all(not (isinstance(s, dict) and s.get("scene_id")) for s in saved) \
                and all(_readable(a) == _readable(b) for a, b in zip(saved, scenes))
            if active and not same_scenes:
                raise HTTPException(status_code=409, detail={
                    "message": "Pictures or clips are still being generated for this lesson: move, add or remove scenes when they are ready.",
                    "revision": _revision(project), "generating": active})
        payload["scenes"] = scenes
        if editor is not None:
            payload["editor"] = editor
        expected = project.updated_at
        written = db.query(models.Project).filter(models.Project.id == project.id, models.Project.updated_at == expected).update(
            {models.Project.json_data: json.dumps(payload), models.Project.updated_at: next_stamp(expected)}, synchronize_session=False)
        if written != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail={"message": "The lesson changed while it was being saved.", "revision": None})
        db.commit()
        db.refresh(project)
        try:
            library.record_project_references(db, project, current_user.id)
        except Exception as e:  # noqa: BLE001 - the lesson is saved; references are refreshed on the next save
            db.rollback()
            print(f"[EDITOR] Could not record the assets of project {project.id}: {e}")
        return {"revision": _revision(project)}

    return router


def clean_lesson_editor(value):
    """The editor data a /save-history snapshot keeps (cleaned; None when invalid)."""
    try:
        return clean_editor(value, set()) if value is not None else None
    except Invalid:
        return None
