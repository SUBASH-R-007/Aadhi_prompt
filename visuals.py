"""Visual router: decides where each scene's visual comes from, before anything is generated.

Every visual a lesson needs becomes a VisualRequest (one per "slot": a scene's main visual, its
side panel, and each explicit asset in its HTML). The router runs a fixed chain of small rules and
the first rule that answers wins:

  0. ReviewDecisionRule  a decision the user made in Visual Review (Phase 6): a visual they chose,
                         their removal of the visual, or an approved asset; never replaced silently
  1. ExplicitAssetRule   an asset the lesson names (video_asset_id, asset:ID, ...) always wins; if
                         it is unusable the slot reports that instead of silently substituting
  2. ExistingMediaRule   media the scene already has (an older URL, a generated file) is reused
  3. PreviousMatchRule   a library asset chosen earlier stays chosen while it is usable, so the
                         preview and the export show the same thing
  4. BuiltInRendererRule the scene carries data for a renderer the app already has (Chart.js,
                         JSXGraph, Three.js, p5, SVG, terminal, ...) or uses GIF search
  5. ManimRule           the scene has Manim code: the existing /render pipeline (local, cached)
  6. NoVisualRule        nothing external is needed (quiz, text, an equation typeset by MathJax)
  7. LibraryMatchRule    a deterministic match against the user's and the shared library assets
  8. AiGenerationRule    an AI image or video, as a plan only: the AI media layer (ai_media.py,
                         Phase 8) names the provider that would make it; the router never names one

Planning never generates: an AI plan says requires_generation and the page decides whether and
when to generate (see visuals.js). AI can be switched off per request or for the whole server
(AI_GENERATION_ENABLED=0), in which case the plan explains what would have been needed.

After routing, AI plans go through the AI media cache (ai_cache.py, Phase 5): an AI visual this
user already generated for the same request is reused as it is (selection "cached", no generation,
even while AI generation is off). Which providers' earlier results count comes from the AI media
layer: the preferred provider's, and a fallback provider's only when that fallback would be used or
nothing can be generated. The cache never takes part in the matching above.

Visual Review (Phase 6): scene.visual_review.<slot> = {status, asset_id?, source?, fingerprint,
reviewed_at} with status approved | changed | removed (no entry = pending). Every main/side plan
carries review_status (and review_stale when the scene's visual request changed after the decision:
an approval then no longer applies; a chosen or removed visual is kept and flagged).

Visual Director (Phase 15): scene.visual_direction.route = {prefer_existing?, preferred_media?, match_terms?}
is a preference for the scene's main/side slots (VisualRequest.preference, validated by route_preference):
the slot's own prefer-existing switch and wanted media for library matching, and a few extra words for it.
It never changes the rule order, a prompt, the AI cache identity or a slot whose media the screenplay fixes,
never names a provider and never switches AI generation on. A slot's fingerprint includes it only when present.

API: POST /api/visuals/plan (with a project_id, the chosen library assets are marked as used by
that saved lesson; nothing else is changed)
     POST /api/visuals/review (records a Visual Review decision in the saved lesson)
"""
import datetime
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import editor_api
import models
from access import make_link_token
from ai_providers import MediaRequest
from database import get_db

MATCH_THRESHOLD = float(os.getenv("VISUAL_MATCH_THRESHOLD") or 0.6)
MAX_SCENES = 400
PROMPT_LIMIT = 20000  # generation prompts are kept whole: the AI cache compares them exactly


def ai_generation_enabled():
    """Server-wide switch for anything that calls an AI media provider (tests set it to 0)."""
    return (os.getenv("AI_GENERATION_ENABLED") or "1").strip().lower() not in ("0", "false", "no", "off")


class VisualSource(str, Enum):
    ASSET = "ASSET"                    # a library asset of the user's (named by the lesson, or matched)
    SYSTEM_ASSET = "SYSTEM_ASSET"      # a shared system asset (Aadhi's clips and stills)
    UPLOADED_ASSET = "UPLOADED_ASSET"  # media the scene already points at by URL (uploaded or generated earlier)
    PROCEDURAL = "PROCEDURAL"          # drawn by a built-in renderer from the scene's own data
    MANIM = "MANIM"                    # rendered in the secure Manim sandbox from the scene's code (Phase 10)
    EXTERNAL_MEDIA = "EXTERNAL_MEDIA"  # found by the existing GIF search (not generated)
    AI_IMAGE = "AI_IMAGE"
    AI_VIDEO = "AI_VIDEO"
    NONE = "NONE"                      # no external visual: not needed, disabled, or unavailable


class MediaType(str, Enum):
    STATIC_IMAGE = "STATIC_IMAGE"
    VIDEO = "VIDEO"
    ANIMATION = "ANIMATION"
    INTERACTIVE = "INTERACTIVE"
    NONE = "NONE"


# Built-in renderers the page already has, by side-panel type
PANEL_RENDERERS = {
    "chart": ("chart", MediaType.ANIMATION),
    "graph": ("graph", MediaType.INTERACTIVE),
    "3d_model": ("3d_model", MediaType.INTERACTIVE),
    "terminal": ("terminal", MediaType.ANIMATION),
    "animation": ("animation", MediaType.ANIMATION),
    "skill_tree": ("skill_tree", MediaType.STATIC_IMAGE),
    "quiz": ("quiz", MediaType.INTERACTIVE),
}
# Types a screenplay's optional "visual" intent may name, and the media each needs
INTENT_MEDIA = {
    "image": MediaType.STATIC_IMAGE, "photo": MediaType.STATIC_IMAGE, "diagram": MediaType.STATIC_IMAGE,
    "illustration": MediaType.STATIC_IMAGE, "video": MediaType.VIDEO, "animation": MediaType.ANIMATION,
    "chart": MediaType.ANIMATION, "graph": MediaType.INTERACTIVE, "equation": MediaType.NONE,
    "formula": MediaType.NONE, "none": MediaType.NONE,
}
PROCEDURAL_INTENTS = {"chart", "graph"}
EQUATION_INTENTS = {"equation", "formula"}
NO_VISUAL_SCENES = {"quiz_checkpoint", "chapter_card", "title", "recap", "key-takeaway"}
ASSET_ID = re.compile(r"^[0-9a-f]{32}$")
ASSET_MARKER = re.compile(r"asset:([0-9a-f]{32})")
CONTENT_URL = re.compile(r"/api/assets/([0-9a-f]{32})/content")
IMG_SRC = re.compile(r"<img[^>]*?\ssrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
MATH = re.compile(r"\\\[|\\\(|\$\$")


@dataclass
class VisualRequest:
    """One visual a lesson needs, whatever part of the screenplay it came from."""
    slot: str                      # main | side | html:<asset id> | uploaded:<image id>
    scene_index: int
    scene_type: str
    media: MediaType
    concept: str = ""
    description: str = ""
    keywords: list = field(default_factory=list)
    explicit_asset_id: str | None = None
    existing_url: str | None = None
    renderer: str | None = None    # a built-in renderer this slot has data for
    manim_code: bool = False
    manim_source: str = ""         # the code itself (Phase 10: an earlier sandboxed render of it is reused)
    has_math: bool = False
    intent_type: str | None = None
    previous: dict | None = None   # the plan chosen for this slot earlier, if any
    generation_prompt: str = ""
    review: dict | None = None     # the Visual Review decision for this slot, if any (Phase 6)
    fingerprint: str = ""          # what the scene asks this slot to show (changes when the request does)
    preference: dict | None = None  # the Visual Director's routing preference for this slot, validated (Phase 15)


@dataclass
class VisualPlan:
    slot: str
    scene_index: int
    source: VisualSource
    media: MediaType
    selection: str = "auto"        # reviewed | approved | removed | explicit | existing | kept | builtin | matched | planned | cached | none
    asset_id: str | None = None
    url: str | None = None
    renderer: str | None = None
    provider: str | None = None
    model: str | None = None       # the provider's model (planned or cached AI visuals)
    fallback_from: str | None = None  # the preferred provider it replaces (unavailable, or failed when it was made)
    quality_warnings: list = field(default_factory=list)  # what the output checks flagged for a person to look at
    requires_generation: bool = False
    would_require: str | None = None
    score: float | None = None
    reason: str = ""
    error: str | None = None
    cache_hit: bool = False        # an AI visual generated earlier for the same request (Phase 5)
    review_status: str | None = None  # pending | approved | changed | removed (main/side plans, Phase 6)
    review_stale: bool = False     # the scene's visual request changed after the user's decision
    debug: list = field(default_factory=list)

    def to_dict(self, with_debug=False):
        data = asdict(self)
        data["source"] = self.source.value
        data["media"] = self.media.value
        if not with_debug:
            data.pop("debug")
        return {k: v for k, v in data.items() if v not in (None, [], "") or k in ("source", "slot", "scene_index", "media")}


@dataclass
class PlanOptions:
    allow_ai_generation: bool = True
    prefer_existing_assets: bool = True
    prefer_procedural: bool = True
    preferred_media_type: str | None = None  # "image" or "video" for slots that could be either


# ---- requests from a screenplay ---------------------------------------------------------

def _text(value, limit=500):
    return value.strip()[:limit] if isinstance(value, str) else ""


REVIEW_STATUSES = ("approved", "changed", "removed")
# Scene fields that decide what a slot should show (not the media produced for it)
MAIN_REQUEST_FIELDS = ("type", "prompt", "manim_code", "simulation_code", "code", "visual_svg", "svg", "p5_code", "visual")


def slot_fingerprint(scene, slot, preference=None):
    """A short hash of what the scene asks this slot to show. A generated video or a resolved URL does
    not change it; editing the prompt, the side panel or the visual intent does. The Visual Director's
    preference (Phase 15) is left out: it only orders library matches (it is derived from these same fields), and
    counting it would re-open every approval when a lesson switches between the Classic and cinematic styles."""
    if slot == "main":
        wanted = {k: scene.get(k) for k in MAIN_REQUEST_FIELDS}
    else:
        wanted = {"type": scene.get("type"), "title": scene.get("title"), "visual": scene.get("visual"),
                  "side_panel": {k: v for k, v in (scene.get("side_panel") or {}).items()
                                 if k not in ("video_url", "video_asset_id")} if isinstance(scene.get("side_panel"), dict) else None}
    return hashlib.sha256(json.dumps(wanted, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


# The Visual Director's routing preference (Phase 15): scene.visual_direction.route, untrusted client data
ROUTE_VERSION = 1
ROUTE_MEDIA = ("image", "video")
MATCH_TERMS_MAX = 6
MATCH_TERM_LENGTH = 40
FLEXIBLE_INTENTS = (None, "illustration", "diagram")  # side visuals whose intent leaves the media open


def route_preference(scene):
    """The Visual Director's preference for the scene's visuals (scene.visual_direction.route, version 1), validated:
    {"prefer_existing": True?, "preferred_media": "image" | "video"?, "match_terms": [up to 6 strings of up to 40
    characters]?}, or None. It arrives from the page, so a value of the wrong type, outside the enums or too long is
    ignored, and so is every other field. It is a preference only: never a provider, a prompt or a slot."""
    direction = scene.get("visual_direction")
    if not isinstance(direction, dict) or type(direction.get("version")) is not int or direction["version"] != ROUTE_VERSION:
        return None  # no direction, another version or a malformed one (as presenters.direction_interaction reads it)
    route = direction.get("route")
    if not isinstance(route, dict):
        return None
    preference = {}
    if route.get("prefer_existing") is True:
        preference["prefer_existing"] = True
    media = route.get("preferred_media")
    if isinstance(media, str) and media in ROUTE_MEDIA:
        preference["preferred_media"] = media
    raw = route.get("match_terms")
    if isinstance(raw, list):
        found = [t.strip() for t in raw[:MATCH_TERMS_MAX] if isinstance(t, str) and len(t) <= MATCH_TERM_LENGTH and t.strip()]
        if found:
            preference["match_terms"] = list(dict.fromkeys(found))
    return preference or None


def _review_of(scene, slot):
    reviews = scene.get("visual_review") if isinstance(scene.get("visual_review"), dict) else {}
    review = reviews.get(slot)
    return review if isinstance(review, dict) and review.get("status") in REVIEW_STATUSES else None


def requests_from_scenes(scenes):
    """VisualRequests for every visual slot of a lesson, from its current screenplay fields."""
    requests = []
    for i, scene in enumerate(scenes if isinstance(scenes, list) else []):
        if not isinstance(scene, dict):
            continue
        first = len(requests)
        kind = _text(scene.get("type"), 40)
        title = _text(scene.get("title"), 200)
        intent = scene.get("visual") if isinstance(scene.get("visual"), dict) else {}
        intent_type = _text(intent.get("type"), 20).lower() or None
        keywords = [k.strip() for k in intent.get("keywords", []) if isinstance(k, str)][:20] if isinstance(intent.get("keywords"), list) else []
        previous = scene.get("visual_plan") if isinstance(scene.get("visual_plan"), dict) else {}
        html = scene.get("html") if isinstance(scene.get("html"), str) else ""
        base = dict(scene_index=i, scene_type=kind, keywords=keywords, intent_type=intent_type)
        open_media = None  # the one slot whose media the screenplay leaves open (a side visual from a plain intent)

        # Main visual of visual scene types
        if kind == "ai_video":
            requests.append(VisualRequest(
                slot="main", media=MediaType.VIDEO, concept=_text(intent.get("concept")) or title,
                description=_text(intent.get("description")) or _text(scene.get("prompt")),
                explicit_asset_id=scene.get("video_asset_id") or None, existing_url=scene.get("video_url") or None,
                previous=previous.get("main"), generation_prompt=_text(scene.get("prompt"), PROMPT_LIMIT), **base))
        elif kind in ("simulation", "visual"):
            code = scene.get("manim_code") or scene.get("simulation_code") or scene.get("code")
            svg = scene.get("visual_svg") or scene.get("svg")
            requests.append(VisualRequest(
                slot="main", media=MediaType.ANIMATION, concept=_text(intent.get("concept")) or title,
                description=_text(intent.get("description")),
                explicit_asset_id=scene.get("manim_asset_id") or None, existing_url=scene.get("manim_video_url") or None,
                renderer="svg" if (svg and not code) else None, manim_code=bool(code),
                manim_source=code if isinstance(code, str) else "", previous=previous.get("main"), **base))
        elif kind == "p5_simulation":
            requests.append(VisualRequest(slot="main", media=MediaType.INTERACTIVE, concept=title,
                                          renderer="p5" if scene.get("p5_code") else None, **base))

        # Side panel, or the optional visual intent of a scene that has no side panel
        panel = scene.get("side_panel") if isinstance(scene.get("side_panel"), dict) else None
        if panel:
            ptype = _text(panel.get("type"), 30)
            if ptype == "image":
                prompt = _text(panel.get("prompt"), PROMPT_LIMIT) or title
                requests.append(VisualRequest(
                    slot="side", media=MediaType.STATIC_IMAGE, concept=_text(intent.get("concept")) or title,
                    description=_text(intent.get("description")) or prompt[:500], previous=previous.get("side"),
                    generation_prompt=prompt, **base))
            elif ptype == "manim":
                requests.append(VisualRequest(
                    slot="side", media=MediaType.ANIMATION, concept=title,
                    explicit_asset_id=panel.get("video_asset_id") or None, existing_url=panel.get("video_url") or None,
                    manim_code=bool(panel.get("manim_code")), previous=previous.get("side"),
                    manim_source=panel.get("manim_code") if isinstance(panel.get("manim_code"), str) else "", **base))
            elif ptype == "gif":
                requests.append(VisualRequest(slot="side", media=MediaType.ANIMATION, concept=_text(panel.get("query")) or title,
                                              renderer="gif", **base))
            elif ptype in PANEL_RENDERERS:
                renderer, media = PANEL_RENDERERS[ptype]
                requests.append(VisualRequest(slot="side", media=media, concept=title, renderer=renderer, **base))
        elif intent and intent_type != "none":
            media = INTENT_MEDIA.get(intent_type or "", MediaType.STATIC_IMAGE)
            requests.append(VisualRequest(
                slot="side", media=media, concept=_text(intent.get("concept")) or title,
                description=_text(intent.get("description")), has_math=bool(MATH.search(html)),
                previous=previous.get("side"),
                generation_prompt=_text(intent.get("description"), PROMPT_LIMIT) or _text(intent.get("concept"), PROMPT_LIMIT) or title,
                **base))
            if intent_type in FLEXIBLE_INTENTS:
                open_media = requests[-1]

        # Explicit assets inside the scene's HTML and its uploaded board images
        for asset_id in dict.fromkeys(ASSET_MARKER.findall(html)):
            requests.append(VisualRequest(slot=f"html:{asset_id}", media=MediaType.STATIC_IMAGE,
                                          explicit_asset_id=asset_id, **base))
        for src in IMG_SRC.findall(html):
            if not src.startswith("asset:") and not CONTENT_URL.search(src):
                requests.append(VisualRequest(slot=f"html:{src[:80]}", media=MediaType.STATIC_IMAGE, existing_url=src, **base))
        uploaded = scene.get("uploaded_image_assets") if isinstance(scene.get("uploaded_image_assets"), dict) else {}
        for img_id, asset_id in uploaded.items():
            requests.append(VisualRequest(slot=f"uploaded:{img_id}", media=MediaType.STATIC_IMAGE,
                                          explicit_asset_id=asset_id, **base))
        route = route_preference(scene)
        for request in requests[first:]:
            if request.slot in ("main", "side"):
                request.review = _review_of(scene, request.slot)
                if route:
                    # preferred_media only where the media is open: never a panel type, an explicit asset,
                    # existing media, a built-in renderer or a main visual
                    request.preference = {k: v for k, v in route.items()
                                          if k != "preferred_media" or request is open_media} or None
                request.fingerprint = slot_fingerprint(scene, request.slot, request.preference)
    return requests


# ---- asset matching -------------------------------------------------------------------------

STOPWORDS = set("""a an the of and or to in on at for with by from into onto over under about as is are be
this that these those it its their his her our your show shows showing see seen look looking video image
picture photo clip scene visual""".split())
# How strongly an asset "means" a word, by where the word appears in its metadata
STRENGTH = {"keywords": 1.0, "description": 0.85, "prompt": 0.85, "concepts": 1.0, "scene": 0.6, "role": 0.6,
            "placement": 0.5, "source": 0.6, "file_name": 0.35}
# A request word counts by where it came from in the request
REQUEST_WEIGHT = {"concept": 2.0, "keywords": 2.0, "description": 1.0}
PHRASE_BONUS = 0.15


def _stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix) and not word.endswith("ss"):
            return word[: -len(suffix)]
    return word


NEGATIONS = {"no", "not", "non", "without"}


def terms(text):
    """Matching words of a text. A negated word ("no_aadhi", "without people") is left out."""
    words = re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "").lower())
    found, negated = [], False
    for word in words:
        if word in NEGATIONS:
            negated = True
        elif len(word) > 1 and word not in STOPWORDS:
            if not negated:
                found.append(_stem(word))
            negated = False
    return found


def asset_profile(asset):
    """Word -> strength for one asset, from the metadata the library already keeps."""
    details = json.loads(asset.details) if asset.details else {}
    profile = {}

    def add(words, strength):
        for word in words:
            profile[word] = max(profile.get(word, 0.0), strength)

    keywords = details.get("keywords") if isinstance(details.get("keywords"), list) else []
    add(terms(" ".join(str(k) for k in keywords)), STRENGTH["keywords"])
    for key in ("description", "prompt", "concepts", "scene", "role", "placement"):
        value = details.get(key)
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        if isinstance(value, str):
            add(terms(value), STRENGTH[key])
    if asset.source == "mascot" and details.get("placement") != "hidden":  # the "hidden" still has no Aadhi in it
        add(["aadhi", "mascot", "deer", "blackbuck"], STRENGTH["source"])
    add(terms(os.path.splitext(asset.file_name or "")[0].replace("_", " ").replace("-", " ")), STRENGTH["file_name"])
    phrase_text = " ".join(terms(" ".join([" ".join(map(str, keywords))] + [
        str(details.get(k)) for k in ("description", "prompt", "concepts") if details.get(k)])))
    return profile, phrase_text


def request_profile(request):
    """(word -> weight, concept phrase) for a request; computed once per request, not per asset."""
    weighted = {}
    for source, text in (("concept", request.concept), ("keywords", " ".join(request.keywords)), ("description", request.description)):
        for word in terms(text):
            weighted[word] = max(weighted.get(word, 0.0), REQUEST_WEIGHT[source])
    return weighted, " ".join(terms(request.concept))


MATCH_TERMS_BONUS = 0.1  # the most the Visual Director's match terms add to a match (Phase 15)


def match_terms_words(request, weighted):
    """Words of the Visual Director's match terms that the request does not already carry (once per request).
    Matching only: they are never part of a prompt, the request's concept or keywords, or the AI cache identity."""
    words = []
    for text in (request.preference or {}).get("match_terms") or ():
        for word in terms(text):
            if word not in weighted and word not in words:
                words.append(word)
    return words


def match_terms_bonus(words, profile):
    """0..MATCH_TERMS_BONUS: how much of the match terms an asset covers. Only ever added to a match the request
    already has (never counted against an asset), so a fitting asset never scores lower because of them."""
    if not words:
        return 0.0
    return MATCH_TERMS_BONUS * sum(profile.get(word, 0.0) for word in words) / len(words)


def match_score(wanted, profile, phrase_text):
    """0..1: how much of the request the asset's metadata covers, weighted by where each request
    word came from and where it appears in the asset. Deterministic; no AI involved."""
    weighted, concept = wanted
    if not weighted:
        return 0.0
    total = sum(weighted.values())
    score = sum(weight * profile.get(word, 0.0) for word, weight in weighted.items()) / total
    if concept and len(concept.split()) > 1 and concept in phrase_text:
        score += PHRASE_BONUS
    return round(min(score, 1.0), 3)


KIND_FOR_MEDIA = {MediaType.STATIC_IMAGE: "image", MediaType.VIDEO: "video", MediaType.ANIMATION: "video"}


# ---- routing -------------------------------------------------------------------------------

class RoutingContext:
    """What the rules may consult: the user's usable assets (loaded once per plan) and the library."""

    def __init__(self, db, user, library, options, candidates, ai_media, link):
        self.db = db
        self.user = user
        self.library = library
        self.options = options
        self.candidates = candidates      # usable, ready image/video assets of this user and the system
        self.by_id = {a.id: a for a in candidates}
        self.ai_media = ai_media          # the AI media layer: which provider would make an AI visual
        self.link = link                  # asset -> short-lived content URL
        self._profiles = {}
        self._ai_plans = {}

    def ai_plan(self, media_type, prompt, generation_possible):
        """(lookups, choice, selection) from the AI media layer, once per request in a plan."""
        key = (media_type, prompt, generation_possible)
        if key not in self._ai_plans:
            self._ai_plans[key] = self.ai_media.plan(media_type, prompt, generation_possible)
        return self._ai_plans[key]

    def profile(self, asset):
        if asset.id not in self._profiles:
            self._profiles[asset.id] = asset_profile(asset)
        return self._profiles[asset.id]

    def usable_asset(self, asset_id):
        """(asset, None) if the user can use it now, else (None, why)."""
        if not isinstance(asset_id, str) or not ASSET_ID.match(asset_id):
            return None, "not a valid asset ID"
        asset = self.by_id.get(asset_id) or self.db.get(models.Asset, asset_id)
        if not asset or not self.library.usable_by(asset, self.user.id):
            # Deleted, someone else's, or never existed: the same answer, nothing leaked
            return None, "the asset is not available (deleted, or not in your library)"
        if asset.status != "ready":
            return None, f"the asset cannot be used (status: {asset.status})"
        if not self.library.file_exists(asset):
            self.library.mark_missing(self.db, asset)
            return None, "the asset's file is missing from storage"
        return asset, None


def wanted_media(request, options):
    """The media a slot asks for; preferred_media_type decides for side visuals whose intent names none, and without
    it the Visual Director's preferred media for the slot (Phase 15; only ever set where the media is open)."""
    if request.slot == "side" and request.intent_type in FLEXIBLE_INTENTS and request.media == MediaType.STATIC_IMAGE:
        preferred = options.preferred_media_type if options.preferred_media_type in ROUTE_MEDIA \
            else (request.preference or {}).get("preferred_media")
        if preferred in ROUTE_MEDIA:
            return MediaType.VIDEO if preferred == "video" else MediaType.STATIC_IMAGE
    return request.media


def prefers_existing(request, options):
    """Library visuals (and an earlier match) come before AI generation: the lesson's option, or the Visual Director's
    preference for this slot (Phase 15). It only ever holds AI back; it never switches AI generation on."""
    return options.prefer_existing_assets or (request.preference or {}).get("prefer_existing") is True


def ai_provenance(asset):
    """How an AI-generated asset was made (provider, model, backup, output warnings), from its details."""
    if asset.source not in ("ai-video", "ai-image") or not asset.details:
        return {}
    try:
        made = json.loads(asset.details).get("generation") or {}
    except (TypeError, ValueError):
        return {}
    return {"provider": made.get("provider"), "model": made.get("model"), "fallback_from": made.get("fallback_from"),
            "quality_warnings": list(made.get("warnings") or [])}


def _asset_plan(request, ctx, asset, selection, reason, score=None):
    source = VisualSource.SYSTEM_ASSET if asset.owner_id is None else VisualSource.ASSET
    # The page shows what the file is: a video asset in a side slot plays in the side video panel
    media = request.media
    if asset.kind == "video" and media not in (MediaType.VIDEO, MediaType.ANIMATION):
        media = MediaType.VIDEO
    elif asset.kind == "image":
        media = MediaType.STATIC_IMAGE
    return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=source, media=media,
                      selection=selection, asset_id=asset.id, url=ctx.link(asset), score=score, reason=reason,
                      **ai_provenance(asset))


class ReviewDecisionRule:
    """Visual Review decisions come first: what the user chose is never replaced by automatic routing."""
    name = "visual review"

    def apply(self, request, ctx, trace):
        review = request.review
        if not review:
            trace.append(f"{self.name}: not reviewed")
            return None
        status = review["status"]
        stale = bool(review.get("fingerprint")) and review.get("fingerprint") != request.fingerprint
        if status == "removed":
            trace.append(f"{self.name}: removed by the user")
            return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.NONE,
                              media=MediaType.NONE, selection="removed", reason="you removed the visual for this scene")
        asset_id = review.get("asset_id")
        if status == "approved" and (stale or not asset_id):
            # An approval is for the visual chosen then: it no longer applies after the scene changed,
            # and without an asset the routing below reproduces the approved visual deterministically
            trace.append(f"{self.name}: approved{' for an earlier version of the scene' if stale else ''}; routing as usual")
            return None
        asset, problem = ctx.usable_asset(asset_id)
        if asset:
            trace.append(f"{self.name}: {status} {asset.file_name} ({asset.id[:8]})")
            if status == "changed":
                plan = _asset_plan(request, ctx, asset, "reviewed", "you chose this visual in Visual Review")
            else:
                plan = _asset_plan(request, ctx, asset, "approved", "you approved this visual")
            source = review.get("source")
            if source in (VisualSource.AI_IMAGE.value, VisualSource.AI_VIDEO.value):
                plan.source = VisualSource(source)  # still shown as the AI visual it is
            return plan
        if status == "approved":
            trace.append(f"{self.name}: the approved asset is unusable ({problem}); routing again")
            return None
        trace.append(f"{self.name}: the chosen asset is unusable ({problem}); not substituted")
        return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.NONE, media=request.media,
                          selection="reviewed", asset_id=asset_id, error="asset_unavailable",
                          reason=f"The visual you chose is unavailable: {problem}.")


class ExplicitAssetRule:
    name = "explicit asset"

    def apply(self, request, ctx, trace):
        if not request.explicit_asset_id:
            trace.append(f"{self.name}: none")
            return None
        asset, problem = ctx.usable_asset(request.explicit_asset_id)
        if asset:
            trace.append(f"{self.name}: {asset.file_name} ({asset.id[:8]}) is usable")
            return _asset_plan(request, ctx, asset, "explicit", "the lesson names this asset")
        trace.append(f"{self.name}: {request.explicit_asset_id[:8]}… unusable ({problem}); not substituted")
        return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.NONE, media=request.media,
                          selection="explicit", asset_id=request.explicit_asset_id, error="asset_unavailable",
                          reason=f"The asset chosen for this scene is unavailable: {problem}.")


class ExistingMediaRule:
    name = "existing media"

    def apply(self, request, ctx, trace):
        url = request.existing_url
        if not url:
            trace.append(f"{self.name}: none")
            return None
        linked = CONTENT_URL.search(url)
        if linked:  # a URL the library resolved earlier: treat it as that asset
            asset, problem = ctx.usable_asset(linked.group(1))
            if asset:
                trace.append(f"{self.name}: library URL of {asset.file_name}")
                return _asset_plan(request, ctx, asset, "existing", "the scene already uses this library asset")
            trace.append(f"{self.name}: library URL unusable ({problem})")
            return None
        path = url.split("?", 1)[0]
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            try:
                present = "/" not in name and ctx.library.volumes["static"].exists(name)
            except (KeyError, ValueError):
                present = False
            if not present:
                if request.slot.startswith("html:"):
                    # An image inside the scene's text cannot be swapped for another visual
                    trace.append(f"{self.name}: {path} is missing")
                    return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.NONE,
                                      media=request.media, selection="existing", url=url, error="media_missing",
                                      reason="The image this scene's text points at is missing from the server.")
                trace.append(f"{self.name}: {path} is missing; routing on")
                return None
        trace.append(f"{self.name}: {path[:80]}")
        return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.UPLOADED_ASSET,
                          media=request.media, selection="existing", url=url,
                          reason="the scene already has this media (uploaded or generated earlier)")


class PreviousMatchRule:
    name = "earlier choice"

    def apply(self, request, ctx, trace):
        prev = request.previous or {}
        if prev.get("selection") != "matched" or not prev.get("asset_id"):
            trace.append(f"{self.name}: none")
            return None
        if not prefers_existing(request, ctx.options):
            trace.append(f"{self.name}: not kept (prefer_existing_assets is off)")
            return None
        asset, problem = ctx.usable_asset(prev["asset_id"])
        if asset and KIND_FOR_MEDIA.get(wanted_media(request, ctx.options)) == asset.kind:
            trace.append(f"{self.name}: keeping {asset.file_name}")
            return _asset_plan(request, ctx, asset, "matched", "kept from the earlier plan so preview and export match",
                               score=prev.get("score"))
        trace.append(f"{self.name}: {prev['asset_id'][:8]}… no longer usable ({problem or 'wrong type'}); choosing again")
        return None


class BuiltInRendererRule:
    name = "built-in renderer"

    def apply(self, request, ctx, trace):
        renderer = request.renderer
        if renderer is None and ctx.options.prefer_procedural and request.intent_type in PROCEDURAL_INTENTS:
            trace.append(f"{self.name}: a {request.intent_type} is wanted but the scene has no data for it")
        if not renderer:
            trace.append(f"{self.name}: none")
            return None
        source = VisualSource.EXTERNAL_MEDIA if renderer == "gif" else VisualSource.PROCEDURAL
        trace.append(f"{self.name}: {renderer}")
        return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=source, media=request.media,
                          selection="builtin", renderer=renderer,
                          reason="GIF search (existing media, not generated)" if renderer == "gif"
                          else f"drawn by the built-in {renderer} renderer from the scene's own data")


_manim_renders = None  # manim_jobs.ManimService (server.py): earlier sandboxed renders, looked up without rendering


def use_manim_renders(service):
    global _manim_renders
    _manim_renders = service


class ManimRule:
    name = "manim"

    def apply(self, request, ctx, trace):
        if not request.manim_code:
            trace.append(f"{self.name}: no code")
            return None
        asset = None
        if _manim_renders is not None and request.manim_source and ctx.db is not None:
            asset = _manim_renders.cached(ctx.db, ctx.user.id, request.manim_source)
        if asset is not None:
            trace.append(f"{self.name}: this code was rendered before (asset {asset.id[:8]})")
            return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.MANIM,
                              media=request.media, selection="builtin", renderer="manim", asset_id=asset.id,
                              url=ctx.link(asset), cache_hit=True,
                              reason="rendered earlier in the secure Manim sandbox from the same code; reused")
        trace.append(f"{self.name}: scene has Manim code (rendered in the secure sandbox, cached by code, Manim version and profile)")
        return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.MANIM,
                          media=request.media, selection="builtin", renderer="manim",
                          reason="rendered in the secure Manim sandbox from the scene's code")


class NoVisualRule:
    name = "no visual needed"

    def apply(self, request, ctx, trace):
        if request.media == MediaType.NONE or (request.intent_type in EQUATION_INTENTS and ctx.options.prefer_procedural):
            if request.intent_type in EQUATION_INTENTS and request.has_math:
                trace.append(f"{self.name}: equation typeset by MathJax from the scene text")
                return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.PROCEDURAL,
                                  media=MediaType.NONE, selection="builtin", renderer="mathjax",
                                  reason="the equation is typeset by MathJax from the scene text")
            trace.append(f"{self.name}: yes")
            return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.NONE,
                              media=MediaType.NONE, selection="none", reason="no external visual is needed")
        trace.append(f"{self.name}: no")
        return None


class LibraryMatchRule:
    name = "library match"

    def apply(self, request, ctx, trace):
        if not prefers_existing(request, ctx.options):
            trace.append(f"{self.name}: skipped (prefer_existing_assets is off)")
            return None
        if not ctx.options.prefer_existing_assets:
            trace.append(f"{self.name}: the visual direction prefers an existing visual for this scene")
        media = wanted_media(request, ctx.options)
        kind = KIND_FOR_MEDIA.get(media)
        if not kind:
            trace.append(f"{self.name}: nothing to match for {media.value}")
            return None
        scored = []
        wanted = request_profile(request)
        extra = match_terms_words(request, wanted[0])
        for asset in ctx.candidates:
            if asset.kind != kind:
                continue
            profile, phrase_text = ctx.profile(asset)
            score = match_score(wanted, profile, phrase_text)
            bonus = match_terms_bonus(extra, profile) if score > 0 else 0.0  # 0 for every asset without match terms
            if bonus:
                score = round(min(score + bonus, 1.0), 3)
            if score > 0:
                scored.append((score, bonus, asset.owner_id is not None, (asset.width or 0) * (asset.height or 0),
                               asset.created_at.timestamp() if asset.created_at else 0, asset))
        # Technical relevance first (the match terms also settle a tie at full relevance); then own assets over
        # shared, larger, newer
        scored.sort(key=lambda row: row[:5], reverse=True)
        best = scored[0][0] if scored else 0.0
        for score, _bonus, _own, _area, _time, asset in scored:
            if score < MATCH_THRESHOLD:
                break
            usable, problem = ctx.usable_asset(asset.id)
            if usable:
                trace.append(f"{self.name}: {asset.file_name} scored {score:.2f} (threshold {MATCH_THRESHOLD:.2f})")
                return _asset_plan(request, ctx, usable, "matched", f"matched library asset (relevance {score:.2f})", score=score)
            trace.append(f"{self.name}: {asset.file_name} scored {score:.2f} but {problem}; trying the next")
        trace.append(f"{self.name}: best {best:.2f} is below the threshold {MATCH_THRESHOLD:.2f}" if scored
                     else f"{self.name}: no {kind} asset matches")
        return None


class AiGenerationRule:
    name = "AI generation"

    def apply(self, request, ctx, trace):
        media = wanted_media(request, ctx.options)
        if request.slot == "side" and media == MediaType.VIDEO:
            # The page generates side-panel visuals as images only; AI videos are for ai_video scenes
            trace.append(f"{self.name}: side panels get a generated still image, not an AI video")
            media = MediaType.STATIC_IMAGE
        if media == MediaType.VIDEO:
            source, media_type = VisualSource.AI_VIDEO, "video"
        elif media == MediaType.STATIC_IMAGE:
            source, media_type = VisualSource.AI_IMAGE, "image"
        else:
            trace.append(f"{self.name}: not applicable to {media.value}")
            return None
        if not request.generation_prompt:
            trace.append(f"{self.name}: no description to generate from")
            return None
        if not ai_generation_enabled() or not ctx.options.allow_ai_generation:
            why = "AI generation is turned off on this server" if not ai_generation_enabled() else "AI generation is turned off"
            trace.append(f"{self.name}: would be needed ({source.value}) but {why}")
            return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.NONE, media=media,
                              selection="none", would_require=source.value, reason=f"{why}; no reusable visual was found")
        # Which provider would make it is the AI media layer's decision (preference, capabilities, availability)
        _lookups, choice, selection = ctx.ai_plan(media_type, request.generation_prompt, True)
        for candidate in selection.candidates:
            trace.append(f"{self.name}: provider {candidate.name}: " + ("usable" if candidate.usable else f"not used ({candidate.reason})"))
        if choice is None:
            why = "; ".join(f"{c.name}: {c.reason}" for c in selection.candidates if c.reason) or "none is set up"
            return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.NONE, media=media,
                              selection="none", would_require=source.value,
                              reason=f"no AI {media_type} provider can be used now ({why}); no reusable visual was found")
        preferred = selection.candidates[0].name
        fallback_from = preferred if preferred != choice.name else None
        model = choice.provider.settings_for(MediaRequest(media_type=media_type, prompt=request.generation_prompt)).get("model")
        trace.append(f"{self.name}: planned {source.value} via {choice.name} (not generated yet)")
        reason = "no reusable asset or built-in visual fits, so it has to be generated"
        if fallback_from:
            reason += f" ({fallback_from} cannot be used now, so {choice.name} will make it)"
        return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=source, media=media,
                          selection="planned", provider=choice.name, model=model, fallback_from=fallback_from,
                          requires_generation=True, reason=reason)


class VisualRouter:
    RULES = (ReviewDecisionRule(), ExplicitAssetRule(), ExistingMediaRule(), PreviousMatchRule(), BuiltInRendererRule(),
             ManimRule(), NoVisualRule(), LibraryMatchRule(), AiGenerationRule())

    def plan(self, request, ctx):
        trace = []
        for rule in self.RULES:
            result = rule.apply(request, ctx, trace)
            if result is not None:
                result.debug = trace
                return result
        trace.append("fallback: no visual")
        return VisualPlan(slot=request.slot, scene_index=request.scene_index, source=VisualSource.NONE,
                          media=request.media, selection="none", reason="no visual source applies", debug=trace)


def load_candidates(db, user_id):
    """Every ready image/video asset this user may use, in one query."""
    return db.query(models.Asset).filter(
        models.Asset.status == "ready", models.Asset.kind.in_(("image", "video")),
        models.Asset.source.notin_(("narration", "export")),  # narration audio and whole lesson videos never fit a scene
        (models.Asset.owner_id == user_id) | (models.Asset.owner_id.is_(None))).all()


def plan_lesson(db, user, library, scenes, options, link, ai_cache=None, ai_media=None):
    ctx = RoutingContext(db, user, library, options, load_candidates(db, user.id), ai_media, link)
    router = VisualRouter()
    requests = requests_from_scenes(scenes)
    plans = [router.plan(request, ctx) for request in requests]
    if ai_cache is not None:
        reuse_cached_generations(db, user, requests, plans, ai_cache, ctx, link)
    annotate_reviews(requests, plans)
    return plans


def annotate_reviews(requests, plans):
    """review_status on every main/side plan: the user's decision while it still applies, else pending."""
    for request, plan in zip(requests, plans):
        if request.slot not in STORED_SLOTS:
            continue
        review = request.review or {}
        status = review.get("status")
        stale = bool(review.get("fingerprint")) and review.get("fingerprint") != request.fingerprint
        if status in ("changed", "removed"):
            plan.review_status, plan.review_stale = status, stale
        elif status == "approved" and not stale and (not review.get("asset_id") or plan.asset_id == review.get("asset_id")):
            plan.review_status = "approved"
        else:
            plan.review_status = "pending"
            plan.review_stale = status == "approved" and stale


AI_SOURCES = {VisualSource.AI_VIDEO.value: ("video", MediaType.VIDEO, VisualSource.AI_VIDEO),
              VisualSource.AI_IMAGE.value: ("image", MediaType.STATIC_IMAGE, VisualSource.AI_IMAGE)}


def reuse_cached_generations(db, user, requests, plans, ai_cache, ctx, link):
    """The AI cache step after routing: a planned AI visual (or one that AI generation being off, or no
    usable provider, would have needed) that this user already generated for the same request is reused.
    The AI media layer says whose earlier results count (the preferred provider first). One query."""
    if ctx.ai_media is None:
        return
    wanted, names = {}, {}
    for i, (request, plan) in enumerate(zip(requests, plans)):
        needed = plan.source.value if plan.requires_generation else plan.would_require
        if needed not in AI_SOURCES or not request.generation_prompt or plan.selection in ("removed", "reviewed", "approved"):
            continue
        lookups, _choice, _selection = ctx.ai_plan(AI_SOURCES[needed][0], request.generation_prompt, plan.requires_generation)
        names[i] = [candidate.name for candidate, _identity in lookups]
        for k, (_candidate, identity) in enumerate(lookups):
            wanted[(i, k)] = identity
    best = {}
    for (i, k), hit in ai_cache.lookup_many(db, user.id, wanted).items():
        if i not in best or k < best[i][0]:
            best[i] = (k, hit)
    for i, (k, hit) in best.items():
        old = plans[i]
        _, media, source = AI_SOURCES[old.source.value if old.requires_generation else old.would_require]
        made = (json.loads(hit.asset.details).get("generation") or {}) if hit.asset.details else {}
        fallback_from = names[i][0] if k > 0 else made.get("fallback_from")
        plans[i] = VisualPlan(slot=old.slot, scene_index=old.scene_index, source=source, media=media,
                              selection="cached", asset_id=hit.asset.id, url=link(hit.asset), provider=hit.entry.provider,
                              model=hit.entry.model, fallback_from=fallback_from, cache_hit=True,
                              quality_warnings=list(made.get("warnings") or []),
                              reason="generated earlier for the same request; reused without calling the AI provider",
                              debug=old.debug + [f"AI cache: hit ({hit.entry.provider}, asset {hit.asset.id[:8]})"])


STORED_SLOTS = ("main", "side")  # the plans the page keeps in scene.visual_plan


def record_plan_references(db, project_id, plans):
    """Marks the library assets chosen for a saved lesson as used, so they cannot be deleted while
    it shows them: one reference per scene slot (scenes[i].visual_plan.<slot>), replacing the
    slot's earlier choice. Saving the lesson later rescans it and keeps the same references."""
    chosen = {f"scenes[{p.scene_index}].visual_plan.{p.slot}": p.asset_id
              for p in plans if p.slot in STORED_SLOTS and p.asset_id and not p.error}
    existing = db.query(models.AssetReference).filter(
        models.AssetReference.project_id == project_id, models.AssetReference.field.like("%.visual_plan.%")).all()
    kept = set()
    for ref in existing:
        if chosen.get(ref.field) == ref.asset_id and ref.field not in kept:
            kept.add(ref.field)
        else:
            db.delete(ref)
    for where, asset_id in chosen.items():
        if where not in kept:
            db.add(models.AssetReference(asset_id=asset_id, project_id=project_id, field=where))
    db.commit()
    return len(chosen)


def run_request(run):
    """What a background run was asked for (its request), with the scene it is for: `scene_index` (the run's own
    column) and, when it was recorded (Phase 20), `scene_id` (in the request, or in the run's detail)."""
    def read(text):
        try:
            value = json.loads(text or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
    request = read(getattr(run, "request", None))
    if not request.get("scene_id"):
        scene_id = read(getattr(run, "detail", None)).get("scene_id")
        if scene_id:
            request["scene_id"] = scene_id
    if getattr(run, "scene_index", None) is not None:
        request["scene_index"] = run.scene_index
    return request


def scene_for_run(scenes, request):
    """Where the scene a background result is for is now (Phase 20): found by its scene id when the request carries one
    (None when no scene has that id any more: it was removed), else at the position it was requested for (runs made
    before scene ids, and lessons whose scenes have no ids yet). None when there is no such scene."""
    scenes = scenes if isinstance(scenes, list) else []
    request = request if isinstance(request, dict) else {}
    scene_id = request.get("scene_id")
    if isinstance(scene_id, str) and scene_id:
        found = next((i for i, s in enumerate(scenes) if isinstance(s, dict) and s.get("scene_id") == scene_id), None)
        if found is not None or any(isinstance(s, dict) and s.get("scene_id") for s in scenes):
            return found
    index = request.get("scene_index")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(scenes) and isinstance(scenes[index], dict):
        return index
    return None


def store_scene_plans(db, user, library, project, scene_index, link, ai_cache=None, ai_media=None, scene_id=None, fill_slot=None):
    """Re-plans one scene of a saved lesson and keeps its main/side plans in the lesson (as the review does),
    e.g. after a background generation for it finished (Phase 9). Visual Review decisions stay first (the
    router applies them before anything else). Returns the scene's plans, or None if the scene is gone.
    Phase 20: written with editor_api.update_lesson, so a save committed meanwhile (an editor edit, a Visual Review
    decision) is kept: the scene is planned again on the lesson as it is then. With `scene_id` the scene is found by
    its id (a scene the editor moved still gets its result; a removed one gets nothing); with `fill_slot` the scene is
    only planned while that slot shows nothing yet (checked on the lesson being written; None returned otherwise)."""
    wanted = {"scene_id": scene_id, "scene_index": scene_index}
    out = {"plans": None}

    def apply(payload):
        out["plans"] = None
        scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
        index = scene_for_run(scenes, wanted)
        if index is None:
            return False
        scene = scenes[index]
        if not wanted["scene_id"] and isinstance(scene.get("scene_id"), str) and scene["scene_id"]:
            wanted["scene_id"] = scene["scene_id"]  # a retry finds this same scene, even if it was moved meanwhile
        stored = dict(scene.get("visual_plan") or {}) if isinstance(scene.get("visual_plan"), dict) else {}
        shown = stored.get(fill_slot) if fill_slot else None
        if isinstance(shown, dict) and (shown.get("asset_id") or shown.get("url")):
            return False  # the lesson already shows something for this slot
        before = json.dumps(scene.get("visual_plan"), sort_keys=True, default=str)
        plans = [p for p in plan_lesson(db, user, library, scenes, PlanOptions(), link, ai_cache, ai_media)
                 if p.scene_index == index and p.slot in STORED_SLOTS]
        for plan in plans:
            stored[plan.slot] = {k: v for k, v in plan.to_dict().items() if k not in ("slot", "scene_index")}
        if stored:
            scene["visual_plan"] = stored
        out["plans"] = plans
        return json.dumps(scene.get("visual_plan"), sort_keys=True, default=str) != before

    editor_api.update_lesson(db, project, apply)
    if out["plans"] is None:
        return None
    library.record_project_references(db, project, user.id)
    return out["plans"]


def still_wanted(db, run):
    """Whether a generation continued without the user (recovery, lesson batch) is still wanted by its lesson:
    (True, "") or (False, why). Visual Review decisions win: a removed visual, or another visual chosen, is not
    generated; a New AI Version the user asked for is generated unless the visual was removed. A run not tied
    to a scene is always wanted. Phase 20: the scene is found by its id when the run recorded one (scene_for_run)."""
    request = run_request(run)
    if run.project_id is None or (run.scene_index is None and not request.get("scene_id")) or run.slot not in STORED_SLOTS:
        return True, ""
    project = db.query(models.Project).filter(models.Project.id == run.project_id, models.Project.user_id == run.user_id).first()
    if project is None:
        return False, "The lesson this was for no longer exists, so it was not generated."
    scenes = json.loads(project.json_data or "{}").get("scenes") or []
    index = scene_for_run(scenes, request)
    if index is None:
        return False, "The scene this was for no longer exists, so it was not generated."
    review = _review_of(scenes[index], run.slot) or {}
    if review.get("status") == "removed":
        return False, "The visual was removed in Visual Review, so it was not generated."
    if not run.forced and review.get("status") in ("changed", "approved") and review.get("asset_id"):
        return False, "Another visual was chosen in Visual Review, so this one was not generated."
    wanted = next((r for r in requests_from_scenes(scenes) if r.scene_index == index and r.slot == run.slot), None)
    requested = request.get("prompt") or ""
    if run.kind == "manim":  # a render: the scene must still have exactly this code
        from manim_security import fix_code
        if wanted is None or not wanted.manim_source or fix_code(wanted.manim_source) != requested:
            return False, "The scene's animation code changed after this was requested, so it was not rendered."
        return True, ""
    if wanted is None or " ".join((wanted.generation_prompt or "").split()) != " ".join(requested.split()):
        return False, "The scene changed after this was requested, so it was not generated."
    return True, ""


class PlanRequest(BaseModel):
    scenes: list
    allow_ai_generation: bool = True
    prefer_existing_assets: bool = True
    prefer_procedural: bool = True
    preferred_media_type: str | None = None
    project_id: int | None = None  # the saved lesson these scenes belong to: its chosen assets are marked as used
    debug: bool = False


class ReviewRequest(BaseModel):
    project_id: int
    scene_index: int
    slot: str                        # main | side
    action: str                      # keep | choose | remove | reset
    asset_id: str | None = None      # choose: the visual picked; keep: an asset just generated for the planned visual
    scene: dict | None = None        # the page's current copy of the scene (e.g. with a video generated in the preview)
    allow_ai_generation: bool = True


REVIEW_ACTIONS = ("keep", "choose", "remove", "reset")


def create_visuals_router(library, get_current_user, secret_key, algorithm, link_ttl, ai_cache=None, ai_media=None):
    router = APIRouter(prefix="/api/visuals", tags=["visuals"])

    def linker(user):
        def link(asset):
            token = make_link_token(secret_key, algorithm, user.username, "asset", asset.id, link_ttl)
            return f"/api/assets/{asset.id}/content?token={token}"
        return link

    @router.post("/review")
    def review_visual(body: ReviewRequest, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        """Records a Visual Review decision for one visual of a saved lesson and returns that scene's new
        plans. Never generates anything; the chosen asset must be the user's own or a shared one."""
        if body.slot not in STORED_SLOTS:
            raise HTTPException(status_code=422, detail="slot must be main or side.")
        if body.action not in REVIEW_ACTIONS:
            raise HTTPException(status_code=422, detail="action must be keep, choose, remove or reset.")
        project = db.query(models.Project).filter(models.Project.id == body.project_id, models.Project.user_id == user.id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Lesson not found.")
        options = PlanOptions(allow_ai_generation=body.allow_ai_generation)
        link = linker(user)

        def usable(asset_id):
            asset = library.accessible(db, asset_id, user.id)  # own or shared; others' look like unknown IDs
            if not asset:
                raise HTTPException(status_code=404, detail="Asset not found.")
            if asset.status != "ready" or not library.file_exists(asset):
                raise HTTPException(status_code=409, detail="This asset cannot be used right now (its file is missing or failed).")
            if asset.kind not in ("image", "video") or (body.slot == "main" and asset.kind != "video"):
                raise HTTPException(status_code=422, detail="Choose a video for this scene." if body.slot == "main"
                                    else "Choose a picture or a video.")
            return asset

        out = {}

        def apply(payload):
            """The decision applied to the lesson as it is now (Phase 20: again, if another save landed first)."""
            scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
            if not 0 <= body.scene_index < len(scenes):
                raise HTTPException(status_code=404, detail="Scene not found.")
            if body.scene is not None:
                editor_api.same_scene(scenes, body.scene_index, body.scene)  # Phase 19: the same scene, by id
                scenes[body.scene_index] = editor_api.reviewed_scene(scenes[body.scene_index], body.scene)  # Phase 20: never an older copy
            scene = scenes[body.scene_index]
            if not isinstance(scene, dict):
                raise HTTPException(status_code=422, detail="This scene cannot be reviewed.")
            request = next((r for r in requests_from_scenes(scenes)
                            if r.scene_index == body.scene_index and r.slot == body.slot), None)
            if request is None:
                raise HTTPException(status_code=404, detail="This scene has no such visual.")
            reviews = scene.get("visual_review") if isinstance(scene.get("visual_review"), dict) else {}
            review = {"fingerprint": request.fingerprint,
                      "reviewed_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()}
            if body.action == "choose":
                if not body.asset_id:
                    raise HTTPException(status_code=422, detail="asset_id is required to choose a visual.")
                asset = usable(body.asset_id)
                review.update(status="changed", asset_id=asset.id)
                if asset.source in ("ai-video", "ai-image"):  # a generated visual keeps its "AI" label
                    review.update(source=(VisualSource.AI_VIDEO if asset.kind == "video" else VisualSource.AI_IMAGE).value)
            elif body.action == "remove":
                review.update(status="removed")
            elif body.action == "keep":
                review.update(status="approved")
                if body.asset_id:
                    asset = usable(body.asset_id)
                    review.update(asset_id=asset.id, source=(VisualSource.AI_VIDEO if asset.kind == "video" else VisualSource.AI_IMAGE).value
                                  if asset.source in ("ai-video", "ai-image") else None)
                else:
                    # Approve what the scene shows now (its current plan, including an earlier decision)
                    current = next(p for p in plan_lesson(db, user, library, scenes, options, link, ai_cache, ai_media)
                                   if p.scene_index == body.scene_index and p.slot == body.slot)
                    if current.selection == "removed":
                        review.update(status="removed")
                    elif current.asset_id and not current.error:
                        review.update(asset_id=current.asset_id, source=current.source.value)
                review = {k: v for k, v in review.items() if v is not None}
            if body.action == "reset":
                reviews.pop(body.slot, None)
            else:
                reviews[body.slot] = review
            if reviews:
                scene["visual_review"] = reviews
            else:
                scene.pop("visual_review", None)

            # The scene's plans with the decision applied, kept in the saved lesson like the page keeps them
            plans = [p for p in plan_lesson(db, user, library, scenes, options, link, ai_cache, ai_media)
                     if p.scene_index == body.scene_index and p.slot in STORED_SLOTS]
            stored = {p.slot: {k: v for k, v in p.to_dict().items() if k not in ("slot", "scene_index")} for p in plans}
            if stored:
                scene["visual_plan"] = stored
            else:
                scene.pop("visual_plan", None)
            out.update(review=reviews.get(body.slot), plans=plans)
            return True

        revision = editor_api.update_lesson(db, project, apply)
        library.record_project_references(db, project, user.id)  # the reviewed asset is marked as used
        return {"review": out["review"], "plans": {p.slot: p.to_dict() for p in out["plans"]}, "revision": revision}

    @router.post("/plan")
    def plan_visuals(body: PlanRequest, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
        """Plans every visual of a lesson. Never generates anything."""
        if len(body.scenes) > MAX_SCENES:
            raise HTTPException(status_code=413, detail=f"A lesson can have at most {MAX_SCENES} scenes.")
        if body.preferred_media_type not in (None, "image", "video"):
            raise HTTPException(status_code=422, detail="preferred_media_type must be image or video.")
        if body.project_id is not None and not db.query(models.Project.id).filter(
                models.Project.id == body.project_id, models.Project.user_id == user.id).first():
            raise HTTPException(status_code=404, detail="Lesson not found.")
        options = PlanOptions(body.allow_ai_generation, body.prefer_existing_assets, body.prefer_procedural,
                              body.preferred_media_type)
        plans = plan_lesson(db, user, library, body.scenes, options, linker(user), ai_cache, ai_media)
        if body.project_id is not None:
            record_plan_references(db, body.project_id, plans)
        summary = {}
        for plan in plans:
            summary[plan.source.value] = summary.get(plan.source.value, 0) + 1
        return {"plans": [p.to_dict(with_debug=body.debug) for p in plans], "summary": summary,
                "ai_generation_enabled": ai_generation_enabled() and body.allow_ai_generation,
                "match_threshold": MATCH_THRESHOLD}

    return router
