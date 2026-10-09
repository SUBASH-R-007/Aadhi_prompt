"""Each teacher's own media library: the pictures and clips they uploaded or had generated.

Model: ``LibraryItem`` (``library_items``), one row per (user, asset key). The media itself is the shared,
content-addressed ``Asset`` row (``aadhi.storage.assets``): identical bytes or identical generation inputs
are one row for everybody, so nothing per user is ever read from or written to it, and owning an item never
comes from ``Asset.created_by`` or ``Asset.meta``. An item exists only because its user uploaded the bytes
(``POST /api/uploads``, ``POST /api/library``) or a build of one of their lectures generated the media
(``record_generated``): no request can name an asset key into a library.

* Text: titles, descriptions and keywords are tidied and bounded (``clean_title`` …): strict for what a
  teacher types (422 with a readable message), shortened for what the system fills in (file names,
  prompts, AI suggestions). ``search_text`` keeps the item's words and their stems for search (``q``) and
  for the matcher's SQL prefilter.
* Matching (``match``, ``LibraryMatcher``, ``suggestions``): deterministic word overlap between a scene's
  visual request and an item's title, keywords, description and prompt. Words of any script; lower case;
  stop words, generic picture words ("illustration", "photo") and bare numbers dropped; a word after
  "no" / "not" / "without" dropped; a light English stemmer. The score (0..1) mixes how much of the request
  the item covers with how much of the item's title (or of its keywords) the request names, plus a bonus
  for a whole multi-word title or keyword. Bounded: at most ``MATCH_RECENT`` recently used items plus
  ``MATCH_OVERLAP`` older items sharing a request word are loaded, then scored through an inverted index;
  one ``suggestions`` run scores at most ``MATCH_MAX_PAIRS`` (item, scene) pairs, and item profiles are
  cached by their words.
* Automatic use in a build (``auto_match``, ``GenerationOptions.prefer_library_visuals``) is stricter than a
  suggestion: it matches the image prompt alone (never the scene or panel title), needs a score of at least
  ``AUTO_USE_THRESHOLD`` and an item whose words cover at least ``AUTO_USE_MIN_COVERAGE`` of the prompt's.
* Using an item in a lecture (``attach_to_project``) records an ``AssetRef``: that is what authorises the
  key in the lecture's screenplay. Deleting an item never touches asset refs or assets, so lectures that
  use the media keep working and the asset GC is unchanged.
"""

from __future__ import annotations

import datetime as dt
import functools
import logging
import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import Field
from sqlalchemy import and_, delete, exists, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased
from sqlalchemy.orm.attributes import set_committed_value

from .api.errors import ApiException, not_found
from .api.util import iso
from .models import Asset, AssetRef, LibraryItem, Project, UsageEvent, utcnow
from .pipeline.gen_models import GenModel
from .providers.base import ImageInput
from .security.uploads import safe_display_name
from .storage.base import is_public_key

if TYPE_CHECKING:  # pragma: no cover
    from .config import Settings
    from .providers.base import LLMProvider, UsageSink
    from .schemas.screenplay import Screenplay
    from .storage.assets import AssetStore

log = logging.getLogger(__name__)

KINDS = ("image", "video")
SOURCES = ("upload", "generated", "figure")
TITLE_MAX = 120
DESCRIPTION_MAX = 1000
KEYWORDS_MAX = 20
KEYWORD_MAX_CHARS = 40
PROMPT_MAX = 1200
SCENE_ID_MAX = 64
PROVIDER_MAX = 32
MODEL_MAX = 128
SEARCH_TEXT_MAX = 8000
QUERY_MAX_CHARS = 200
QUERY_MAX_TERMS = 8
# Asset kinds whose media an item may hold: pictures and clips served from /media.
ASSET_KINDS = frozenset({"upload", "image", "video", "figure", "manim", "poster"})
MIME_PREFIX = {"image": "image/", "video": "video/"}

# Matching (see the module docstring).
SUGGESTION_THRESHOLD = 0.35  # GET /api/library/suggestions
AUTO_USE_THRESHOLD = 0.6  # GenerationOptions.prefer_library_visuals: use the item instead of generating
AUTO_USE_MIN_COVERAGE = 0.5  # ... and only when the item's words cover at least this share of the prompt's
AUTO_MATCH_CANDIDATES = 50  # best items considered for automatic use (the caller may leave some out)
MATCH_RECENT = 600  # most recently used / edited items always scored
MATCH_OVERLAP = 400  # older items sharing one of the request's longest words
MATCH_LIKE_TERMS = 12  # request words used by the SQL prefilter
MATCH_MAX_REQUEST_TERMS = 48
MATCH_MAX_PAIRS = 20_000  # item scores of one suggestions run, all scenes together (CPU bound per request)
PROFILE_CACHE_SIZE = 4096  # item profiles kept in memory, keyed by the item's words
MATCH_TEXT_MAX = 4000  # characters of one text read for matching
STRENGTH_TITLE = 1.0
STRENGTH_KEYWORD = 1.0
STRENGTH_DESCRIPTION = 0.85
STRENGTH_PROMPT = 0.85
COVERAGE_WEIGHT = 0.6  # how much of the request the item's words cover
SPECIFICITY_WEIGHT = 0.4  # how much of the item's title (or keywords) the request names
PHRASE_BONUS = 0.15  # a whole multi-word title or keyword appears in the request

STOPWORDS = frozenset(
    """a an the of and or nor to in on at for with by from into onto over under about as is are was were be
    been being this that these those it its their theirs his her hers our ours your yours my mine we you they
    he she i me him them us which what who whom whose how why when where there here then than so such can
    could will would shall should may might must do does did done has have had having very more most less
    some any each every all both either neither also just only other others same between through during
    before after above below up down out off again further once while if but because until per via upon
    within without against among across along around behind beyond near like etc using use used one two
    three
    का की के है और में से को पर यह एक
    மற்றும் ஒரு இது அந்த என்ற""".split()
)
NEGATIONS = frozenset({"no", "not", "non", "without"})
# Words that say what kind of picture is wanted but not what it shows (and the image style suffix).
GENERIC_WORDS = frozenset(
    """image images picture pictures photo photos photograph photographs photographic pic pics img imgs
    illustration illustrations illustrated diagram diagrams drawing drawings sketch sketches graphic graphics
    visual visuals video videos clip clips footage scene scenes shot shots view views close closeup up wide
    angle camera cinematic render rendered rendering realistic photorealistic detailed simple clean
    educational consistent soft lighting accent palette style vector flat colorful colourful high quality
    hd 4k background show shows showing shown depict depicts depicting labelled labeled label labels text
    screenshot scan dsc copy final untitled new mov mp4 png jpg jpeg webp gif webm""".split()
)

_WORD_RE = re.compile(
    r"(?:[^\W_]|[̀-ͯऀ-෿᪰-᫿᷀-᷿‌‍⃐-⃿︠-︯])+"
)
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_KEYWORD_SPLIT_RE = re.compile(r"[,;\n\r]+")
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
_DROPPED_CATEGORIES = frozenset({"Cc", "Cs", "Co", "Cn"})


# --- text ----------------------------------------------------------------------------------------


def tidy(value: Any) -> str:
    """NFC text on one line: control, surrogate, private-use and bidi-override characters removed,
    every run of whitespace one space, trimmed."""
    text = unicodedata.normalize("NFC", "" if value is None else str(value))
    kept = (
        " " if ch.isspace() else ch
        for ch in text
        if ch.isspace() or (unicodedata.category(ch) not in _DROPPED_CATEGORIES and ch not in _BIDI_CONTROLS)
    )
    return " ".join("".join(kept).split())


def shorten(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters, at a word boundary when one is near the end."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space >= limit * 0.6 else cut).rstrip()


def clean_title(value: Any, *, strict: bool = True) -> str:
    """A title: ``tidy``; longer than ``TITLE_MAX`` is a ``ValueError`` (``strict``) or shortened."""
    text = tidy(value)
    if strict and len(text) > TITLE_MAX:
        raise ValueError(f"The title can have at most {TITLE_MAX} characters.")
    return shorten(text, TITLE_MAX)


def clean_description(value: Any, *, strict: bool = True) -> str:
    """A description: ``tidy``; longer than ``DESCRIPTION_MAX`` is a ``ValueError`` (``strict``) or shortened."""
    text = tidy(value)
    if strict and len(text) > DESCRIPTION_MAX:
        raise ValueError(f"The description can have at most {DESCRIPTION_MAX} characters.")
    return shorten(text, DESCRIPTION_MAX)


def clean_keywords(value: Any, *, strict: bool = True) -> list[str]:
    """Keywords from a list and/or comma-separated text: tidied, empty ones dropped, repeats (ignoring case)
    dropped. More than ``KEYWORDS_MAX`` or one longer than ``KEYWORD_MAX_CHARS`` is a ``ValueError``
    (``strict``); otherwise the extra ones are dropped and long ones shortened."""
    if value is None:
        return []
    parts = [value] if isinstance(value, str) else list(value)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not isinstance(part, str):
            if strict:
                raise ValueError("Keywords must be text.")
            continue
        for raw in _KEYWORD_SPLIT_RE.split(part):
            word = tidy(raw)
            if not word:
                continue
            if len(word) > KEYWORD_MAX_CHARS:
                if strict:
                    raise ValueError(f"A keyword can have at most {KEYWORD_MAX_CHARS} characters.")
                word = shorten(word, KEYWORD_MAX_CHARS)
            folded = word.casefold()
            if folded not in seen:
                seen.add(folded)
                out.append(word)
    if len(out) > KEYWORDS_MAX:
        if strict:
            raise ValueError(f"At most {KEYWORDS_MAX} keywords.")
        out = out[:KEYWORDS_MAX]
    return out


def _optional(value: Any, limit: int) -> str | None:
    text = shorten(tidy(value), limit)
    return text or None


def title_from_filename(filename: str | None, kind: str) -> str:
    """A readable title from an uploaded file's name ("plant_cell-v2.png" -> "plant cell v2")."""
    fallback = "Untitled clip" if kind == "video" else "Untitled picture"
    if not (filename or "").strip():
        return fallback
    name = safe_display_name(filename or "")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return shorten(tidy(re.sub(r"[_\-.]+", " ", stem)), TITLE_MAX) or fallback


def title_from_prompt(prompt: str | None, kind: str) -> str:
    """A short title for generated media: the start of its prompt."""
    text = tidy(prompt)
    first = re.split(r"(?<=[.!?;:])\s", text, maxsplit=1)[0].rstrip(".!?;:,")
    return shorten(first, 60) or ("Generated clip" if kind == "video" else "Generated picture")


def library_kind(mime: str | None) -> str | None:
    """``"image"`` / ``"video"`` for a picture / clip MIME type, else None."""
    mime = (mime or "").lower()
    return next((kind for kind, prefix in MIME_PREFIX.items() if mime.startswith(prefix)), None)


# --- words and stems -----------------------------------------------------------------------------


def stem(word: str) -> str:
    """A light, deterministic English stemmer (plural, -ing, -ed, final e). Other scripts are unchanged."""
    if len(word) <= 3 or not (word.isascii() and word.isalpha()):
        return word
    w = word
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith(("sses", "xes", "zes", "ches", "shes")):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    for suffix in ("ing", "ed"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[: -len(suffix)]
            if w[-1] == w[-2] and w[-1] not in "lsz":
                w = w[:-1]
            break
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


_GENERIC_STEMS = frozenset(stem(w) for w in GENERIC_WORDS)


def words(text: str | None, *, limit: int = MATCH_TEXT_MAX) -> list[str]:
    """Lower-cased words of ``text`` in any script (camelCase split), from its first ``limit`` characters."""
    raw = unicodedata.normalize("NFKC", (text or "")[:limit])
    return [w.casefold() for w in _WORD_RE.findall(_CAMEL_RE.sub(" ", raw))]


def terms(text: str | None, *, limit: int = MATCH_TEXT_MAX) -> list[str]:
    """Matching terms of ``text`` in order (see the module docstring)."""
    out: list[str] = []
    negated = False
    for word in words(text, limit=limit):
        if word in NEGATIONS:
            negated = True
            continue
        if word in STOPWORDS or len(word) < 2 or word.isdigit():
            continue
        if negated:  # "no text", "without people": the negated word is not wanted
            negated = False
            continue
        s = stem(word)
        if word in GENERIC_WORDS or s in _GENERIC_STEMS:
            continue
        out.append(s)
    return out


def search_text(title: str, description: str, keywords: Sequence[str], prompt: str | None) -> str:
    """The words of an item and their stems, space separated with a space at both ends (so ``LIKE '% w%'``
    matches a word prefix)."""
    found: dict[str, None] = {}
    for text in (title, " ".join(keywords), description, prompt or ""):
        for w in words(text):
            found.setdefault(w, None)
            found.setdefault(stem(w), None)
    return f" {' '.join(found)[:SEARCH_TEXT_MAX]} "


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _word_prefix(term: str) -> Any:
    return LibraryItem.search_text.like(f"% {_escape_like(term)}%", escape="\\")


def query_terms(q: str | None) -> list[str]:
    """Search terms of ``q``: stems of its words (stop words left out unless that leaves nothing)."""
    all_words = words((q or "")[:QUERY_MAX_CHARS])
    picked = [w for w in all_words if w not in STOPWORDS] or all_words
    return list(dict.fromkeys(stem(w) for w in picked))[:QUERY_MAX_TERMS]


# --- scoring -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """What an item's words mean for matching (computed once per item)."""

    strength: dict[str, float]  # term -> 0..1, by where the term appears
    title: frozenset[str]
    keywords: tuple[frozenset[str], ...]
    phrases: tuple[str, ...]  # " a b " forms of multi-word titles and keywords


def profile(title: str, description: str, keywords: Sequence[str], prompt: str | None) -> Profile:
    strength: dict[str, float] = {}

    def add(found: Iterable[str], value: float) -> None:
        for t in found:
            if strength.get(t, 0.0) < value:
                strength[t] = value

    title_terms = terms(title)
    keyword_terms = [terms(k) for k in list(keywords)[:KEYWORDS_MAX]]
    add(title_terms, STRENGTH_TITLE)
    for k in keyword_terms:
        add(k, STRENGTH_KEYWORD)
    add(terms(description), STRENGTH_DESCRIPTION)
    add(terms(prompt), STRENGTH_PROMPT)
    phrases = tuple(dict.fromkeys(f" {' '.join(p)} " for p in [title_terms, *keyword_terms] if 2 <= len(p) <= 6))
    return Profile(strength, frozenset(title_terms), tuple(frozenset(k) for k in keyword_terms if k), phrases)


@functools.lru_cache(maxsize=PROFILE_CACHE_SIZE)
def _cached_profile(title: str, description: str, keywords: tuple[str, ...], prompt: str | None) -> Profile:
    return profile(title, description, keywords, prompt)


def item_profile(item: LibraryItem) -> Profile:
    """The item's ``Profile``, cached by its words (a profile depends on nothing else and is never changed)."""
    args = (item.title or "", item.description or "", tuple(item.keywords or ()), item.prompt)
    try:
        return _cached_profile(*args)
    except TypeError:  # a stored keyword that is not text (unhashable): computed without the cache
        return profile(*args)


@dataclass(frozen=True)
class _Request:
    """A request's terms, prepared once for scoring many items."""

    wanted: tuple[str, ...]  # distinct terms in order, at most MATCH_MAX_REQUEST_TERMS
    want: frozenset[str]
    joined: str  # " t1 t2 ... " of every term (for the phrase bonus)


def _request(request: Sequence[str]) -> _Request:
    wanted = tuple(dict.fromkeys(request))[:MATCH_MAX_REQUEST_TERMS]
    return _Request(wanted, frozenset(wanted), f" {' '.join(request)} ")


def _score(req: _Request, prof: Profile) -> float:
    if not req.wanted or not prof.strength:
        return 0.0
    covered = sum(prof.strength.get(t, 0.0) for t in req.wanted)
    if covered <= 0:
        return 0.0
    coverage = covered / len(req.wanted)
    if prof.title or prof.keywords:
        specificity = len(prof.title & req.want) / len(prof.title) if prof.title else 0.0
        if prof.keywords:
            specificity = max(specificity, sum(1 for k in prof.keywords if k <= req.want) / len(prof.keywords))
    else:
        specificity = coverage
    value = COVERAGE_WEIGHT * coverage + SPECIFICITY_WEIGHT * specificity
    if prof.phrases and any(p in req.joined for p in prof.phrases):
        value += PHRASE_BONUS
    return round(min(value, 1.0), 3)


def score(request: Sequence[str], prof: Profile) -> float:
    """0..1: how well an item (``prof``) fits a request (its ``terms``, in order). Deterministic."""
    return _score(_request(request), prof)


def coverage(request: Sequence[str], prof: Profile) -> float:
    """0..1: how much of a request (its ``terms``) an item's words cover, weighted by where they appear."""
    wanted = tuple(dict.fromkeys(request))[:MATCH_MAX_REQUEST_TERMS]
    if not wanted:
        return 0.0
    return sum(prof.strength.get(t, 0.0) for t in wanted) / len(wanted)


def _recency(item: LibraryItem) -> float:
    when = item.last_used_at or item.updated_at or item.created_at
    if when is None:
        return 0.0
    if when.tzinfo is None:  # SQLite returns naive UTC datetimes
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.timestamp()


class LibraryMatcher:
    """Scores a user's items against visual requests. Built once (``load``) and reused for many requests:
    each item's words are read once, and an inverted index scores only the items that share a word."""

    def __init__(self, items: Iterable[LibraryItem]) -> None:
        self.items = list(items)
        self.profiles = [item_profile(it) for it in self.items]
        self._index: dict[str, list[int]] = {}
        for i, prof in enumerate(self.profiles):
            for t in prof.strength:
                self._index.setdefault(t, []).append(i)

    @classmethod
    def load(
        cls, db: Session, user_id: int, *, kinds: Iterable[str] | None = None, texts: Iterable[str] = ()
    ) -> LibraryMatcher:
        """Candidates of ``user_id`` (items whose media is still stored): the ``MATCH_RECENT`` most recently
        used or edited, plus up to ``MATCH_OVERLAP`` older ones that share one of the longest words of
        ``texts`` (one SQL prefilter each, bounded)."""
        base = (
            select(LibraryItem)
            .join(Asset, Asset.key == LibraryItem.asset_key)
            .where(LibraryItem.user_id == user_id)
        )
        wanted_kinds = sorted({k for k in (kinds or ()) if k in KINDS})
        if wanted_kinds:
            base = base.where(LibraryItem.kind.in_(wanted_kinds))
        recency = func.coalesce(LibraryItem.last_used_at, LibraryItem.updated_at)
        recent = list(db.execute(base.order_by(recency.desc(), LibraryItem.id.desc()).limit(MATCH_RECENT)).scalars())
        if len(recent) == MATCH_RECENT:
            hints: dict[str, None] = {}
            for text in texts:
                for t in terms(text):
                    if len(t) >= 3:
                        hints.setdefault(t, None)
            longest = sorted(hints, key=lambda t: (-len(t), t))[:MATCH_LIKE_TERMS]
            if longest:
                seen = [it.id for it in recent]
                older = db.execute(
                    base.where(LibraryItem.id.not_in(seen), _any_word_prefix(longest))
                    .order_by(recency.desc(), LibraryItem.id.desc())
                    .limit(MATCH_OVERLAP)
                ).scalars()
                recent.extend(older)
        return cls(recent)

    def rank(
        self,
        text: str,
        kinds: Iterable[str] | None = None,
        *,
        limit: int = 10,
        threshold: float = 0.0,
        max_candidates: int | None = None,
    ) -> list[tuple[LibraryItem, float]]:
        """Items of ``kinds`` (None = any) scoring above 0 and at least ``threshold`` for ``text``, best first
        (ties: most recently used or edited, then newest). ``max_candidates``: score at most that many of the
        items sharing a word with the request, the most recently used first (the load order)."""
        req = _request(terms(text))
        allowed = set(kinds) if kinds is not None else None
        touched: set[int] = set()
        for t in req.wanted:  # an item sharing none of these words scores 0
            touched.update(self._index.get(t, ()))
        candidates: Iterable[int] = touched
        if max_candidates is not None and len(touched) > max_candidates:
            candidates = sorted(touched)[: max(int(max_candidates), 0)]
        scored: list[tuple[float, float, int, LibraryItem]] = []
        for i in candidates:
            item = self.items[i]
            if allowed is not None and item.kind not in allowed:
                continue
            value = _score(req, self.profiles[i])
            if value > 0 and value >= threshold:
                scored.append((value, _recency(item), item.id, item))
        scored.sort(key=lambda s: (-s[0], -s[1], -s[2]))
        return [(item, value) for value, _, _, item in scored[: max(limit, 0)]]


def _any_word_prefix(found: Sequence[str]) -> Any:
    return or_(*(_word_prefix(t) for t in found))


def match(
    db: Session | None,
    user_id: int,
    text: str,
    kind: str | None,
    *,
    limit: int = 10,
    threshold: float = 0.0,
    matcher: LibraryMatcher | None = None,
) -> list[tuple[LibraryItem, float]]:
    """``user_id``'s items of ``kind`` (None = image or video) that fit ``text``, best first with their score
    (0..1, deterministic). ``matcher``: one already loaded for ``user_id`` and reused for many requests (``db``
    is then not used)."""
    kinds = [kind] if kind else list(KINDS)
    if matcher is None:
        if db is None:
            raise ValueError("match needs a session or a loaded matcher")
        matcher = LibraryMatcher.load(db, user_id, kinds=kinds, texts=[text])
    return matcher.rank(text, kinds, limit=limit, threshold=threshold)


def auto_match(
    db: Session | None,
    user_id: int,
    prompt: str,
    kind: str = "image",
    *,
    exclude: Collection[str] = frozenset(),
    skip: Callable[[LibraryItem], bool] | None = None,
    matcher: LibraryMatcher | None = None,
) -> tuple[LibraryItem, float] | None:
    """The item a build uses automatically instead of generating media for ``prompt`` (the image prompt
    alone, never a title), or None: the best one scoring at least ``AUTO_USE_THRESHOLD`` whose words cover at
    least ``AUTO_USE_MIN_COVERAGE`` of the prompt's, whose asset key is not in ``exclude`` and that ``skip``
    does not leave out."""
    request = terms(prompt)
    if not request:
        return None
    ranked = match(db, user_id, prompt, kind, limit=AUTO_MATCH_CANDIDATES, threshold=AUTO_USE_THRESHOLD,
                   matcher=matcher)
    for item, value in ranked:
        if float(value) < AUTO_USE_THRESHOLD or item.asset_key in exclude or (skip is not None and skip(item)):
            continue
        if coverage(request, item_profile(item)) >= AUTO_USE_MIN_COVERAGE:
            return item, float(value)
    return None


# --- screenplay visual needs ---------------------------------------------------------------------


@dataclass(frozen=True)
class VisualNeed:
    """A scene whose picture or clip could come from the library."""

    scene_id: str
    kinds: tuple[str, ...]  # library kinds that can fill it, preferred first
    text: str  # what the scene asks to show (matched against items)
    overridden: bool  # the teacher already chose media for it (override_asset_key)


def visual_needs(sp: Screenplay) -> list[VisualNeed]:
    """One need per scene that shows a generated picture or clip: an AI video scene (a clip, or a still for
    its fallback), else an image side panel. Charts, figures, Manim and the like are never library needs."""
    from .schemas.screenplay import AIVideoScene

    out: list[VisualNeed] = []
    for scene in sp.scenes:
        panel = scene.side_panel
        if isinstance(scene, AIVideoScene):
            parts = (scene.title, scene.video_prompt, scene.fallback_image_prompt)
            out.append(VisualNeed(scene.id, ("video", "image"), " ".join(p for p in parts if p),
                                  bool(scene.override_asset_key)))
        elif panel is not None and panel.kind == "image":
            parts = (panel.title, scene.title, panel.image_prompt)
            out.append(VisualNeed(scene.id, ("image",), " ".join(p for p in parts if p),
                                  bool(panel.override_asset_key)))
    return out


def suggestions(
    db: Session,
    user_id: int,
    sp: Screenplay,
    *,
    per_scene: int = 3,
    threshold: float = SUGGESTION_THRESHOLD,
    include_overridden: bool = False,
    scene_ids: Collection[str] | None = None,
    shown: Mapping[str, str] | None = None,
) -> list[tuple[VisualNeed, list[tuple[LibraryItem, float]]]]:
    """For every scene with a visual need (``visual_needs``; scenes whose media the teacher already chose
    only with ``include_overridden``): ``user_id``'s best items scoring at least ``threshold``.

    ``scene_ids``: rank only those scenes (the candidates and the per-scene bound stay those of the whole
    screenplay, so a scene gets the same matches either way). ``shown``: {scene id: asset key the scene shows
    now}; that item is not suggested for its own scene. Bounded: at most ``MATCH_MAX_PAIRS`` item scores in all
    (each scene scores the most recently used of the items sharing one of its words)."""
    needs = [n for n in visual_needs(sp) if include_overridden or not n.overridden]
    if not needs:
        return []
    kinds = {k for n in needs for k in n.kinds}
    matcher = LibraryMatcher.load(db, user_id, kinds=kinds, texts=[n.text for n in needs])
    per_need = max(per_scene + 1, MATCH_MAX_PAIRS // len(needs))
    shown = shown or {}
    out: list[tuple[VisualNeed, list[tuple[LibraryItem, float]]]] = []
    for need in needs:
        if scene_ids is not None and need.scene_id not in scene_ids:
            continue
        current = shown.get(need.scene_id)
        ranked = matcher.rank(need.text, need.kinds, limit=per_scene + (1 if current else 0), threshold=threshold,
                              max_candidates=per_need)
        out.append((need, [(item, value) for item, value in ranked if item.asset_key != current][:per_scene]))
    return out


# --- items ---------------------------------------------------------------------------------------


def _insert_ignore(db: Session, values: dict[str, Any]) -> bool:
    """Insert one item unless (user, asset key) exists. True when this call inserted it."""
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:  # pragma: no cover - SQLite and PostgreSQL are the supported databases
        try:
            with db.begin_nested():
                db.execute(insert(LibraryItem).values(**values))
            return True
        except IntegrityError:
            return False
    stmt = dialect_insert(LibraryItem).values(**values).on_conflict_do_nothing(
        index_elements=["user_id", "asset_key"]
    )
    return (db.execute(stmt).rowcount or 0) > 0


def _reload(db: Session, item_id: int) -> LibraryItem:
    return db.execute(
        select(LibraryItem).where(LibraryItem.id == item_id).execution_options(populate_existing=True)
    ).scalar_one()


def add_item(
    db: Session,
    *,
    user_id: int,
    asset_key: str,
    kind: str,
    source: str,
    title: str | None = None,
    description: str | None = None,
    keywords: Sequence[str] | str | None = None,
    origin_project_id: int | None = None,
    origin_scene_id: str | None = None,
    prompt: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    used: bool = False,
) -> LibraryItem:
    """Put ``asset_key`` into ``user_id``'s library (idempotent per user and key; the caller commits).

    The asset must be a stored picture (``kind`` image) or clip (video); else ``ValueError``. An existing
    item keeps every value it has: only empty fields are filled (a teacher's title, description and keywords
    are never overwritten). Values are tidied and shortened, never refused. ``used`` marks the item as just
    used in a lecture (``last_used_at``).
    """
    if kind not in KINDS:
        raise ValueError(f"unknown library kind {kind!r}")
    if source not in SOURCES:
        raise ValueError(f"unknown library source {source!r}")
    asset = db.execute(select(Asset.kind, Asset.mime).where(Asset.key == asset_key)).first()
    if asset is None or asset.kind not in ASSET_KINDS or not str(asset.mime).startswith(MIME_PREFIX[kind]):
        raise ValueError(f"{asset_key!r} is not a stored {kind}")
    clean: dict[str, Any] = {
        "title": clean_title(title, strict=False),
        "description": clean_description(description, strict=False),
        "keywords": clean_keywords(keywords, strict=False),
        "prompt": _optional(prompt, PROMPT_MAX),
        "provider": _optional(provider, PROVIDER_MAX),
        "model": _optional(model, MODEL_MAX),
        "origin_scene_id": _optional(origin_scene_id, SCENE_ID_MAX),
    }
    now = utcnow()
    values = {
        **clean,
        "user_id": user_id,
        "asset_key": asset_key,
        "kind": kind,
        "source": source,
        "origin_project_id": origin_project_id,
        "search_text": search_text(clean["title"], clean["description"], clean["keywords"], clean["prompt"]),
        "created_at": now,
        "updated_at": now,
        "last_used_at": now if used else None,
    }
    inserted = _insert_ignore(db, values)
    item_id = db.execute(
        select(LibraryItem.id).where(LibraryItem.user_id == user_id, LibraryItem.asset_key == asset_key)
    ).scalar_one()
    item = _reload(db, item_id)
    if inserted:
        return item
    fill: dict[str, Any] = {}
    for field in ("title", "description", "keywords", "prompt", "provider", "model", "origin_scene_id"):
        if not getattr(item, field) and clean[field]:
            fill[field] = clean[field]
    if item.origin_project_id is None and origin_project_id is not None:
        fill["origin_project_id"] = origin_project_id
    words_changed = bool({"title", "description", "keywords", "prompt"} & set(fill))
    if words_changed:
        merged = {f: fill.get(f, getattr(item, f)) for f in ("title", "description", "keywords", "prompt")}
        fill["search_text"] = search_text(merged["title"], merged["description"], merged["keywords"] or [],
                                          merged["prompt"])
    if used:
        fill["last_used_at"] = now
    if not fill:
        return item
    fill["updated_at"] = now if words_changed else LibraryItem.updated_at  # use alone is not an edit
    db.execute(update(LibraryItem).where(LibraryItem.id == item.id).values(**fill))
    return _reload(db, item.id)


def get_item(db: Session, user_id: int, item_id: int) -> LibraryItem:
    """``user_id``'s item ``item_id``, else 404 (the same for another user's item: never revealed)."""
    if not (0 < int(item_id) <= 2**63 - 1):
        raise not_found("Library item")
    item = db.execute(
        select(LibraryItem).where(LibraryItem.id == item_id, LibraryItem.user_id == user_id)
    ).scalar_one_or_none()
    if item is None:
        raise not_found("Library item")
    return item


_UNSET: Any = object()


def update_item(
    db: Session, item: LibraryItem, *, title: Any = _UNSET, description: Any = _UNSET, keywords: Any = _UNSET
) -> LibraryItem:
    """The teacher's edit (values already validated with the strict cleaners; the caller commits)."""
    fill: dict[str, Any] = {}
    if title is not _UNSET and title is not None:
        fill["title"] = clean_title(title)
    if description is not _UNSET:
        fill["description"] = clean_description(description)
    if keywords is not _UNSET:
        fill["keywords"] = clean_keywords(keywords)
    if not fill:
        return item
    merged = {f: fill.get(f, getattr(item, f)) for f in ("title", "description", "keywords")}
    fill["search_text"] = search_text(merged["title"], merged["description"], merged["keywords"] or [], item.prompt)
    fill["updated_at"] = utcnow()
    db.execute(update(LibraryItem).where(LibraryItem.id == item.id).values(**fill))
    return _reload(db, item.id)


def delete_item(db: Session, item: LibraryItem) -> None:
    """Remove the library row only (asset refs and assets are untouched; the caller commits)."""
    db.execute(delete(LibraryItem).where(LibraryItem.id == item.id, LibraryItem.user_id == item.user_id))


def list_items(
    db: Session,
    user_id: int,
    *,
    q: str = "",
    kind: str | None = None,
    source: str | Sequence[str] | None = None,
    limit: int = 48,
    offset: int = 0,
) -> tuple[list[tuple[LibraryItem, Asset]], int]:
    """A page of ``user_id``'s items whose media is still stored, newest first, and the total. ``q``: every
    search term must start a word of the item's title, keywords, description or prompt (stems). ``source``:
    one source, or several (items of any of them)."""
    stmt = (
        select(LibraryItem, Asset)
        .join(Asset, Asset.key == LibraryItem.asset_key)
        .where(LibraryItem.user_id == user_id)
    )
    if kind:
        stmt = stmt.where(LibraryItem.kind == kind)
    if isinstance(source, str):
        if source:
            stmt = stmt.where(LibraryItem.source == source)
    elif source:
        stmt = stmt.where(LibraryItem.source.in_(sorted(set(source))))
    found = query_terms(q)
    if found:
        stmt = stmt.where(and_(*(_word_prefix(t) for t in found)))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(LibraryItem.created_at.desc(), LibraryItem.id.desc()).limit(limit).offset(offset)
    ).all()
    return [(row[0], row[1]) for row in rows], int(total)


def used_in(db: Session, user_id: int, asset_keys: Iterable[str]) -> dict[str, int]:
    """asset key -> number of ``user_id``'s own (not deleted) lectures it was added to or made for (their
    ``AssetRef`` rows, which stay when a scene later shows other media). One query."""
    keys = sorted({k for k in asset_keys if k})
    if not keys:
        return {}
    rows = db.execute(
        select(AssetRef.asset_key, func.count(func.distinct(AssetRef.project_id)))
        .join(Project, Project.id == AssetRef.project_id)
        .where(Project.owner_id == user_id, Project.deleted_at.is_(None), AssetRef.asset_key.in_(keys))
        .group_by(AssetRef.asset_key)
    ).all()
    return {str(key): int(count) for key, count in rows}


def item_view(item: LibraryItem, asset: Asset | None, store: AssetStore, uses: int = 0) -> dict[str, Any]:
    """The ``ItemView`` JSON shape (docs/API.md): the item, its media's size and a /media URL."""
    url = None
    if asset is not None and is_public_key(asset.storage_key):
        url = store.url_for(asset.storage_key)
    return {
        "id": item.id,
        "asset_key": item.asset_key,
        "kind": item.kind,
        "title": item.title,
        "description": item.description,
        "keywords": list(item.keywords or []),
        "source": item.source,
        "mime": asset.mime if asset is not None else None,
        "size_bytes": asset.size_bytes if asset is not None else None,
        "width": asset.width if asset is not None else None,
        "height": asset.height if asset is not None else None,
        "duration_s": asset.duration_s if asset is not None else None,
        "url": url,
        "poster_url": None,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
        "last_used_at": iso(item.last_used_at),
        "used_in": int(uses),
        "prompt": item.prompt,
        "provider": item.provider,
        "model": item.model,
    }


def item_views(db: Session, store: AssetStore, user_id: int, items: Sequence[LibraryItem]) -> list[dict[str, Any]]:
    """``item_view`` for several items with two queries (assets, usage counts)."""
    keys = [it.asset_key for it in items]
    assets: dict[str, Asset] = {}
    if keys:
        for a in db.execute(select(Asset).where(Asset.key.in_(sorted(set(keys))))).scalars():
            assets[a.key] = a
    uses = used_in(db, user_id, keys)
    return [item_view(it, assets.get(it.asset_key), store, uses.get(it.asset_key, 0)) for it in items]


# --- use in lectures -----------------------------------------------------------------------------


def attach_to_project(db: Session, item: LibraryItem, project: Project) -> str:
    """Make ``item``'s media usable in ``project`` (records the ``AssetRef`` that authorises its key in the
    screenplay, e.g. as ``override_asset_key``) and mark the item used. Returns the asset key. The caller
    has authorised both (``get_item`` for the acting user, ``load_project``) and commits."""
    from .pipeline.dbops import ensure_asset_refs

    if db.execute(select(Asset.id).where(Asset.key == item.asset_key)).scalar_one_or_none() is None:
        raise ApiException(409, "asset_missing", "The file of this library item is no longer stored.")
    ensure_asset_refs(db, project.id, [item.asset_key])
    now = utcnow()
    db.execute(
        update(LibraryItem)
        .where(LibraryItem.id == item.id)
        .values(last_used_at=now, updated_at=LibraryItem.updated_at)
        .execution_options(synchronize_session=False)
    )
    set_committed_value(item, "last_used_at", now)
    return item.asset_key


def record_generated(
    db: Session,
    *,
    user_id: int | None,
    asset_key: str,
    kind: str,
    project_id: int | None,
    scene_id: str | None,
    prompt: str | None,
    provider: str | None,
    model: str | None,
    title: str | None = None,
    settings: Settings | None = None,
) -> LibraryItem | None:
    """Add media a build generated for ``project_id`` to ``user_id``'s library (the build job's owner, i.e. the
    user who started it; ``LIBRARY_AUTO_SAVE_GENERATED``).

    Cheap (a few indexed statements in the caller's transaction, which the caller commits) and never raises:
    any problem is logged and None returned, so the build goes on. An item the user already has keeps its
    words; a generated one gets a title from ``title`` or the start of its prompt.
    """
    try:
        if settings is None:
            from .config import get_settings

            settings = get_settings()
        if not settings.library_auto_save_generated or not user_id or not asset_key or kind not in KINDS:
            return None
        values = dict(
            user_id=user_id, asset_key=asset_key, kind=kind, source="generated",
            title=title or title_from_prompt(prompt, kind), origin_project_id=project_id,
            origin_scene_id=scene_id, prompt=prompt, provider=provider, model=model, used=True,
        )
        if db.get_bind().dialect.name == "sqlite":  # a failed statement leaves the SQLite transaction usable
            return add_item(db, **values)
        with db.begin_nested():  # PostgreSQL: a failed statement must not abort the caller's transaction
            return add_item(db, **values)
    except Exception as exc:  # noqa: BLE001 - the library is a convenience: a build never fails because of it
        log.warning("library: could not record generated %s %s: %s", kind, asset_key, type(exc).__name__)
        return None


# --- admin statistics (media reuse) --------------------------------------------------------------


def media_cache_stats(db: Session, *, days: int = 30) -> dict[str, Any]:
    """Aggregates only (no prompts, no users): for generated pictures and clips, how many were made and
    billed in the last ``days`` days, how often a lecture reused one an earlier lecture had, and how many are
    stored; plus library sizes by source. Reuse within one lecture (a rebuild) is not recorded anywhere."""
    days = max(1, min(int(days), 366))
    since = utcnow() - dt.timedelta(days=days)
    out: dict[str, Any] = {"days": days, "kinds": {}}
    earlier = aliased(AssetRef)
    for kind in KINDS:
        stored = db.execute(select(func.count(Asset.id)).where(Asset.kind == kind)).scalar_one()
        made = db.execute(
            select(func.count(Asset.id)).where(Asset.kind == kind, Asset.created_at >= since)
        ).scalar_one()
        reused = db.execute(
            select(func.count(AssetRef.id))
            .join(Asset, Asset.key == AssetRef.asset_key)
            .where(
                Asset.kind == kind,
                AssetRef.created_at >= since,
                exists(
                    select(earlier.id).where(
                        earlier.asset_key == AssetRef.asset_key,
                        earlier.project_id != AssetRef.project_id,
                        earlier.created_at < AssetRef.created_at,
                    )
                ),
            )
        ).scalar_one()
        calls, cost = db.execute(
            select(func.count(UsageEvent.id), func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
                UsageEvent.operation == kind, UsageEvent.created_at >= since
            )
        ).one()
        out["kinds"][kind] = {
            "generated": int(made),
            "reused_from_other_lectures": int(reused),
            "provider_calls": int(calls),
            "cost_usd": round(float(cost or 0.0), 4),
            "stored": int(stored),
        }
    by_source = dict(
        db.execute(select(LibraryItem.source, func.count(LibraryItem.id)).group_by(LibraryItem.source)).all()
    )
    out["library"] = {"items": int(sum(by_source.values())), "by_source": {s: int(by_source.get(s, 0)) for s in SOURCES}}
    return out


# --- AI description suggestion (LIBRARY_AI_DESCRIBE_ENABLED) -------------------------------------

DESCRIBE_PROMPTS = ("library_describe",)
DESCRIBE_IMAGE_MAX_SIDE = 1024  # the picture (or clip frame) sent to the model is scaled down to this
DESCRIBE_SOURCE_MAX_BYTES = 40 * 1024 * 1024  # larger media are described from their words only
DESCRIBE_FRAME_AT_SECONDS = 1.0


class GenLibraryDescription(GenModel):
    title: str = Field(default="", description="a short title of at most eight words that names the subject")
    description: str = Field(default="", description="one or two sentences: what the picture or clip shows")
    keywords: list[str] = Field(default_factory=list, description="five to twelve search words or short phrases")


def describe_prompt(item: LibraryItem) -> str:
    """The user prompt: the item's own words as data."""
    from .pipeline.prompting import join_sections, json_section

    data = {
        "kind": "video clip" if item.kind == "video" else "picture",
        "title": item.title,
        "description": item.description,
        "keywords": list(item.keywords or []),
        "generated_from_prompt": item.prompt or "",
    }
    return join_sections(json_section("Item", data), "Respond with JSON that matches the response schema.")


def _scaled_image(data: bytes) -> bytes | None:
    """A JPEG of at most ``DESCRIBE_IMAGE_MAX_SIDE`` pixels a side (first frame of an animation), or None."""
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.seek(0)
            frame = img.convert("RGB")
        frame.thumbnail((DESCRIBE_IMAGE_MAX_SIDE, DESCRIBE_IMAGE_MAX_SIDE))
        out = io.BytesIO()
        frame.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError):
        return None


def _clip_frame(store: AssetStore, asset: Asset, settings: Settings) -> bytes | None:
    """One PNG frame of an MP4 clip (ffmpeg), or None."""
    import shutil
    from pathlib import Path

    from .manim.media import MediaError, extract_frame_sync
    from .scratch import make_temp_dir

    if asset.mime != "video/mp4":  # the frame helper reads MP4 only
        return None
    work = make_temp_dir(settings, "aadhi-describe-")
    try:
        local = store.storage.local_path(asset.storage_key)
        path = local if local is not None and local.is_file() else store.storage.download_to(
            asset.storage_key, Path(work) / "clip.mp4"
        )
        return extract_frame_sync(Path(path), DESCRIBE_FRAME_AT_SECONDS, settings, max_width=DESCRIBE_IMAGE_MAX_SIDE)
    except (MediaError, OSError) as exc:
        log.info("library: no frame for describing clip %s: %s", asset.key, type(exc).__name__)
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def describe_image(store: AssetStore, asset: Asset | None, settings: Settings) -> ImageInput | None:
    """What the model sees of an item: the picture (scaled down) or one frame of the clip; None when the
    media is missing, too large or unreadable (the item is then described from its words). Blocking."""
    if asset is None or (asset.size_bytes or 0) > DESCRIBE_SOURCE_MAX_BYTES:
        return None
    kind = library_kind(asset.mime)
    try:
        if kind == "image":
            scaled = _scaled_image(store.storage.get_bytes(asset.storage_key))
            return None if scaled is None else ImageInput(data=scaled, mime="image/jpeg")
        if kind == "video":
            frame = _clip_frame(store, asset, settings)
            return None if frame is None else ImageInput(data=frame, mime="image/png")
    except Exception as exc:  # noqa: BLE001 - a missing blob or a storage error: describe from the words
        log.info("library: media of %s not readable for a description: %s", asset.key, type(exc).__name__)
    return None


async def describe(
    llm: LLMProvider,
    *,
    model: str,
    item: LibraryItem,
    image: ImageInput | None,
    on_usage: UsageSink | None = None,
) -> dict[str, Any]:
    """A suggested ``{"title", "description", "keywords"}`` for ``item`` (not saved). The model's answer is
    tidied and bounded like any value the system fills in; an empty title keeps the item's own."""
    from .pipeline.prompting import system_prompt

    out: GenLibraryDescription = await llm.generate_json(
        model=model,
        system=system_prompt(*DESCRIBE_PROMPTS),
        prompt=describe_prompt(item),
        schema=GenLibraryDescription,
        images=[image] if image is not None else (),
        temperature=0.2,
        on_usage=on_usage,
        validation_retries=1,
    )
    return {
        "title": clean_title(out.title, strict=False) or item.title,
        "description": clean_description(out.description, strict=False),
        "keywords": clean_keywords(out.keywords, strict=False),
    }
