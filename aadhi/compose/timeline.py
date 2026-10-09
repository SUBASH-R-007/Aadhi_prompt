"""Timeline builder: screenplay + asset manifest -> fully timed ``Timeline``.

``build_timeline`` is pure and deterministic (no I/O, no clock, no randomness): stored timelines
carry asset keys (plus the matching storage keys in ``Timeline.meta["asset_storage_keys"]``) and
every ``url`` is left ``None`` — ``resolve_urls`` fills them at serve time.

Time base (see ``aadhi.schemas.timeline``): scenes are contiguous; inside a scene narration starts
at ``audio_offset`` (``SCENE_LEAD_SECONDS``) and every beat/word/caption/quiz/panel time is scene
relative. A scene lasts ``lead + narration + SCENE_TAIL_SECONDS`` (+ ``QUIZ_REVEAL_HOLD_SECONDS``
for quizzes); silent chapter cards last ``SILENT_SCENE_SECONDS``.

Teacher choices: a ``hidden`` scene is left out (the remaining scenes stay contiguous and their ``index``
counts only the scenes shown, so the captions and chapters leave it out too); a scene shorter than its
``min_seconds`` is held at the end for the difference (``TimedScene.hold_seconds``, part of ``duration``),
and everything inside it (beats, captions, sync cues) is timed as without the hold.

Degradation (never fails on missing assets):

* simulation without media -> board of bullets built from the beats' ``visual_cue``;
* ai_video without footage/still -> board with the title and the visual description;
* scene without narration audio (or audio that no longer covers its beats) -> estimated beat
  durations (0.065 s/char) flagged ``estimated=True``.
"""

from __future__ import annotations

import hashlib
import logging
import random
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from ..config import Settings
from ..schemas.manifest import INTER_BEAT_GAP_SECONDS, AssetManifest, BeatAudio, MediaInfo, SceneAudio, SceneMedia
from ..schemas.screenplay import (
    AIVideoScene,
    Beat,
    BoardItem,
    BoardItemKind,
    BoardScene,
    ChapterCardScene,
    InteractiveScene,
    LexiconEntry,
    QuizScene,
    Screenplay,
    SimulationScene,
    SourceFigure,
)
from ..schemas.timeline import (
    Branding,
    CaptionCue,
    Chapter,
    IntroSpec,
    KenBurns,
    KenBurnsPoint,
    Layout,
    MediaRef,
    QuizTiming,
    ResolvedSidePanel,
    TimedBeat,
    TimedScene,
    TimedWord,
    Timeline,
    TitleCard,
)
from ..storage.assets import AssetStore
from .base import (
    BGM,
    INTRO_CARD_SECONDS,
    INTRO_LOGO_SECONDS,
    LOGO_VIDEO,
    MASCOT_CLIPS,
    QUIZ_REVEAL_HOLD_SECONDS,
    RETIRED_BRANDING_FILES,
    RETIRED_MASCOT_CLIPS,
    SCENE_LEAD_SECONDS,
    SCENE_TAIL_SECONDS,
    SCENE_TRANSITION_SECONDS,
    SILENT_SCENE_SECONDS,
    STAGE_HEIGHT,
    STAGE_WIDTH,
    STATIC_BACKGROUND,
)
from .captions import EST_SECONDS_PER_CHAR, estimate_words, normalize_cues, shift_cues, split_caption
from .chapters import opening_title
from .sync import SyncOptions, scene_sync_cues

log = logging.getLogger(__name__)

STORAGE_KEYS_META = "asset_storage_keys"  # Timeline.meta: asset key -> storage key (pure URL building)
FALLBACKS_META = "fallbacks"  # Timeline.meta: [{"scene_id", "reason"}]
BRANDING_PREFIX = "/branding/"
CAPTION_HOLD_SECONDS = 0.4  # the last caption of a beat stays up a little into the pause
MIN_EST_BEAT_SECONDS = 0.6
FULLSCREEN_TYPES = ("simulation", "ai_video", "interactive")

Phase = Literal["main", "reveal"]


# ---------------------------------------------------------------------------
# Branding / intro
# ---------------------------------------------------------------------------


def branding_url(filename: str) -> str:
    """Stable app path for a file in ``Settings.branding_dir``."""
    return f"{BRANDING_PREFIX}{filename}"


def current_clip_url(url: str) -> str:
    """``/branding/<retired clip>`` -> the clip that replaced it (``RETIRED_MASCOT_CLIPS``); any
    other URL unchanged."""
    if url.startswith(BRANDING_PREFIX):
        replacement = RETIRED_MASCOT_CLIPS.get(url[len(BRANDING_PREFIX):])
        if replacement:
            return branding_url(replacement)
    return url


def current_branding_url(url: str) -> str:
    """``/branding/<retired file>`` -> the file that replaced it (``RETIRED_BRANDING_FILES``: the intro logo);
    any other URL unchanged."""
    if url.startswith(BRANDING_PREFIX):
        replacement = RETIRED_BRANDING_FILES.get(url[len(BRANDING_PREFIX):])
        if replacement:
            return branding_url(replacement)
    return url


def default_branding(settings: Settings) -> Branding:
    """Branding with stable ``/branding/...`` paths.

    Tick/ding sound URLs are content-addressed assets (``aadhi.compose.sounds``); they are left
    ``None`` here (stored timelines hold no asset URLs) and filled by :func:`resolve_urls`.
    """
    del settings  # branding files are fixed; kept for interface stability
    return Branding(
        mascot_clips={pos: branding_url(name) for pos, name in MASCOT_CLIPS.items()},
        static_background_url=branding_url(STATIC_BACKGROUND),
        bgm_url=branding_url(BGM),
        bgm_volume=0.06,
        tick_url=None,
        ding_url=None,
    )


def build_intro(screenplay: Screenplay) -> IntroSpec:
    """Logo animation followed by two title cards (subject/unit, session number/title)."""
    cards: list[TitleCard] = []
    t = INTRO_LOGO_SECONDS
    for line1, line2 in ((screenplay.subject_name, screenplay.unit_name),
                         (screenplay.session_number, screenplay.session_title)):
        line1, line2 = (line1 or "").strip(), (line2 or "").strip()
        if not line1 and not line2:
            continue
        if not line1:
            line1, line2 = line2, ""
        cards.append(TitleCard(line1=line1, line2=line2, start=_r(t), duration=INTRO_CARD_SECONDS))
        t += INTRO_CARD_SECONDS
    return IntroSpec(
        logo_video_url=branding_url(LOGO_VIDEO),
        logo_duration=INTRO_LOGO_SECONDS,
        background_url=branding_url(STATIC_BACKGROUND),
        cards=cards,
        duration=_r(t),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _r(x: float) -> float:
    """Millisecond rounding used for every stored time (deterministic, validator friendly)."""
    return round(float(x) + 0.0, 3)


def scene_beats(scene: Any) -> list[tuple[Beat, Phase]]:
    """Beats in combined order (main beats, then quiz reveal beats) with their phase."""
    out: list[tuple[Beat, Phase]] = [(b, "main") for b in scene.beats]
    if isinstance(scene, QuizScene):
        out += [(b, "reveal") for b in scene.reveal_beats]
    return out


def estimate_speech_seconds(text: str) -> float:
    """Estimated spoken duration of ``text`` (0.065 s per character, at least 0.6 s)."""
    return max(MIN_EST_BEAT_SECONDS, len((text or "").strip()) * EST_SECONDS_PER_CHAR)


_RICH_SPECIALS = frozenset("\\$*`[]")  # rich-lite markup characters (web/js/player/richtext.js escapes)


def _plain_rich(text: str, limit: int = 1200) -> str:
    """Escape rich-lite specials so descriptive text renders literally on the board.

    The result is at most ``limit`` characters and never ends inside an escape pair.
    """
    out: list[str] = []
    size = 0
    for ch in (text or "").strip():
        piece = "\\" + ch if ch in _RICH_SPECIALS else ch
        if size + len(piece) > limit:
            break
        out.append(piece)
        size += len(piece)
    return "".join(out).rstrip()


def ken_burns_for(scene_id: str) -> KenBurns:
    """Gentle, deterministic pan/zoom seeded by the scene id."""
    seed = int(hashlib.sha256(scene_id.encode("utf-8")).hexdigest()[:12], 16)
    rnd = random.Random(seed)
    a = KenBurnsPoint(cx=round(rnd.uniform(0.42, 0.58), 3), cy=round(rnd.uniform(0.42, 0.58), 3),
                      scale=round(rnd.uniform(1.0, 1.04), 3))
    b = KenBurnsPoint(cx=round(rnd.uniform(0.40, 0.60), 3), cy=round(rnd.uniform(0.40, 0.60), 3),
                      scale=round(rnd.uniform(1.12, 1.2), 3))
    return KenBurns(start=a, end=b) if rnd.random() < 0.6 else KenBurns(start=b, end=a)


# ---------------------------------------------------------------------------
# Beat layout (narration-relative, i.e. 0 = narration start)
# ---------------------------------------------------------------------------


@dataclass
class _BeatPlan:
    beat: Beat
    phase: Phase
    start: float
    speech: float
    pause: float
    words: list[TimedWord]
    estimated: bool

    @property
    def end(self) -> float:
        return self.start + self.speech + self.pause


@dataclass
class _AudioPlan:
    beats: list[_BeatPlan]
    content_end: float
    audio: SceneAudio | None = None  # set when the scene narration file matches the beats
    countdown_start: float | None = None
    countdown_seconds: int | None = None

    @property
    def estimated(self) -> bool:
        return any(b.estimated for b in self.beats)


def _shift_words(words: Sequence[TimedWord], delta: float) -> list[TimedWord]:
    return [TimedWord(text=w.text, start=w.start + delta, end=w.end + delta) for w in words]


def _plan_from_audio(scene: Any, audio: SceneAudio) -> _AudioPlan | None:
    """Exact layout from the synthesised narration; None if it does not cover every beat."""
    by_id = {ba.beat_id: ba for ba in audio.beats}
    beats = scene_beats(scene)
    if not beats or any(b.id not in by_id for b, _ in beats):
        return None
    plans: list[_BeatPlan] = []
    for beat, phase in beats:
        ba = by_id[beat.id]
        start, speech = max(0.0, ba.offset), max(0.0, ba.speech_duration)
        words = list(ba.words) or estimate_words(beat.narration, start, start + speech)
        plans.append(_BeatPlan(beat, phase, start, speech, max(0.0, ba.pause_after), words, False))
    plan = _AudioPlan(beats=plans, content_end=max([audio.duration] + [p.end for p in plans]), audio=audio)
    if isinstance(scene, QuizScene):
        plan.countdown_seconds = int(audio.countdown_seconds or scene.countdown_seconds)
        if audio.countdown_start is not None:
            plan.countdown_start = max(0.0, audio.countdown_start)
        else:
            mains = [p for p in plans if p.phase == "main"]
            plan.countdown_start = (mains[-1].end + INTER_BEAT_GAP_SECONDS) if mains else 0.0
        plan.content_end = max(plan.content_end, plan.countdown_start + plan.countdown_seconds)
    return plan


def _plan_estimated(scene: Any, reuse: dict[str, BeatAudio] | None = None) -> _AudioPlan:
    """Sequential layout with estimated durations (reusing measured durations from ``reuse``)."""
    reuse = reuse or {}
    plans: list[_BeatPlan] = []
    cursor = 0.0
    countdown_start: float | None = None
    countdown_seconds: int | None = None
    is_quiz = isinstance(scene, QuizScene)
    for beat, phase in scene_beats(scene):
        if is_quiz and phase == "reveal" and countdown_start is None:
            countdown_start = cursor
            countdown_seconds = int(scene.countdown_seconds)
            cursor = countdown_start + countdown_seconds
        ba = reuse.get(beat.id)
        if ba is not None and ba.speech_duration > 0:
            speech = ba.speech_duration
            words = (_shift_words(ba.words, cursor - ba.offset) if ba.words
                     else estimate_words(beat.narration, cursor, cursor + speech))
            estimated = False
        else:
            speech = estimate_speech_seconds(beat.narration)
            words = estimate_words(beat.narration, cursor, cursor + speech)
            estimated = True
        plan = _BeatPlan(beat, phase, cursor, speech, beat.pause_after, words, estimated)
        plans.append(plan)
        cursor = plan.end + INTER_BEAT_GAP_SECONDS
    content_end = plans[-1].end if plans else 0.0
    return _AudioPlan(beats=plans, content_end=content_end, countdown_start=countdown_start,
                      countdown_seconds=countdown_seconds)


def _canon_text(text: str) -> str:
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


@lru_cache(maxsize=1)
def _speech_normalizer() -> Callable[[str, Sequence[LexiconEntry], str], str] | None:
    """The TTS stage's text normalisation (lexicon + speech normaliser), if that module is installed."""
    try:
        from ..providers.tts.normalize import apply_lexicon, normalize_for_speech
    except ImportError:
        return None

    def normalize(text: str, lexicon: Sequence[LexiconEntry], language: str) -> str:
        return normalize_for_speech(apply_lexicon(text, list(lexicon), language), language)

    return normalize


@lru_cache(maxsize=1)
def _scene_hasher() -> Callable[[Any], str] | None:
    try:
        from ..pipeline.assets import scene_hash
    except ImportError:
        return None
    return scene_hash


def _speech_forms(beat: Beat, lexicon: Sequence[LexiconEntry], language: str) -> set[str]:
    """Canonical forms of the text a beat would be synthesised from (with/without lexicon)."""
    src = beat.spoken or beat.narration
    forms = {_canon_text(src), _canon_text(beat.narration)}
    normalize = _speech_normalizer()
    if normalize is not None:
        try:  # same normalisation as the TTS stage
            forms.add(_canon_text(normalize(src, lexicon, language)))
        except Exception as exc:  # noqa: BLE001 - plain comparison still applies
            log.debug("speech normalisation failed: %s", type(exc).__name__)
    forms.discard("")
    return forms


def _beat_matches(beat: Beat, ba: BeatAudio, lexicon: Sequence[LexiconEntry], language: str) -> bool:
    return bool(ba.spoken_text) and _canon_text(ba.spoken_text) in _speech_forms(beat, lexicon, language)


def _audio_still_valid(scene: Any, audio: SceneAudio, manifest: AssetManifest, sp: Screenplay) -> bool:
    """True when the narration file still matches the scene (editor preview)."""
    stored_hash = manifest.scene_hashes.get(scene.id)
    hasher = _scene_hasher() if stored_hash else None
    if hasher is not None:
        try:
            if hasher(scene) == stored_hash:
                return True
        except Exception as exc:  # noqa: BLE001 - fall back to the beat-level comparison
            log.debug("scene_hash failed: %s", type(exc).__name__)
    beats = scene_beats(scene)
    if [b.id for b, _ in beats] != [ba.beat_id for ba in audio.beats]:
        return False
    for (beat, _), ba in zip(beats, audio.beats, strict=True):
        if abs(beat.pause_after - ba.pause_after) > 1e-3 or not _beat_matches(beat, ba, sp.lexicon, audio.language):
            return False
    return not (isinstance(scene, QuizScene) and audio.countdown_seconds not in (None, scene.countdown_seconds))


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


@dataclass
class _Ctx:
    """Mutable accumulator shared by the per-scene builders."""

    figures: dict[str, SourceFigure]
    storage_keys: dict[str, str] = field(default_factory=dict)
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    sync: SyncOptions | None = None  # word-anchored cues (aadhi.compose.sync); None = off

    def note_storage(self, asset_key: str | None, storage_key: str | None) -> None:
        if asset_key and storage_key:
            self.storage_keys[asset_key] = storage_key


def _media_ref(ctx: _Ctx, info: MediaInfo, *, fit: Literal["contain", "cover"] = "contain",
               end_behavior: Literal["loop", "freeze"] | None = None, ken_burns: KenBurns | None = None) -> MediaRef:
    ctx.note_storage(info.asset_key, info.storage_key)
    hotlink = info.asset_key is None and bool(info.external_url)
    if end_behavior is None:
        end_behavior = "freeze" if info.source == "manim" else "loop"
    return MediaRef(
        kind=info.kind,
        asset_key=info.asset_key,
        url=info.external_url if hotlink else None,  # external hotlinks have no asset key
        mime=info.mime,
        duration=info.duration,
        width=info.width,
        height=info.height,
        fit=fit,
        end_behavior=end_behavior,
        ken_burns=ken_burns,
        attribution=info.attribution or ("Powered by GIPHY" if hotlink and info.kind == "gif" else None),
        render_in_mp4=not hotlink,
    )


def _figure_ref(ctx: _Ctx, figure_id: str | None) -> MediaRef | None:
    fig = ctx.figures.get(figure_id or "")
    if fig is None or not fig.asset_key:
        return None
    return MediaRef(kind="image", asset_key=fig.asset_key, width=fig.width, height=fig.height, fit="contain")


def _main_media(ctx: _Ctx, scene: Any, sm: SceneMedia | None) -> MediaRef | None:
    main = sm.main if sm else None
    if isinstance(scene, SimulationScene):
        if main is not None and main.kind in ("video", "image"):
            return _media_ref(ctx, main, fit="contain")
        if scene.override_asset_key:
            return MediaRef(kind="video", asset_key=scene.override_asset_key, fit="contain", end_behavior="loop")
        return None
    if isinstance(scene, AIVideoScene):
        if main is not None and main.kind == "video" and not sm.main_is_fallback:
            return _media_ref(ctx, main, fit="cover", end_behavior="loop")
        if main is not None and main.kind in ("image", "video"):
            still = main.kind == "image"
            return _media_ref(ctx, main, fit="cover", end_behavior="loop",
                              ken_burns=ken_burns_for(scene.id) if still else None)
        fig = _figure_ref(ctx, scene.fallback_figure_id)
        if fig is not None:
            return fig.model_copy(update={"fit": "cover", "ken_burns": ken_burns_for(scene.id)})
        return None
    return None


def _poster(ctx: _Ctx, scene: Any, sm: SceneMedia | None) -> MediaRef | None:
    if not isinstance(scene, InteractiveScene):
        return None
    if sm and sm.poster:
        return _media_ref(ctx, sm.poster, fit="contain")
    if scene.poster_override_asset_key:
        return MediaRef(kind="image", asset_key=scene.poster_override_asset_key, fit="contain")
    return None


def _panel_media(ctx: _Ctx, scene: Any, sm: SceneMedia | None) -> MediaRef | None:
    panel = scene.side_panel
    if panel is None:
        return None
    if sm and sm.side_panel:
        return _media_ref(ctx, sm.side_panel, fit="contain")
    if panel.kind == "figure" and panel.figure_id:
        ref = _figure_ref(ctx, panel.figure_id)
        if ref is not None:
            return ref
    if panel.override_asset_key and panel.kind in ("figure", "image", "manim"):
        kind: Literal["video", "image"] = "video" if panel.kind == "manim" else "image"
        return MediaRef(kind=kind, asset_key=panel.override_asset_key, fit="contain",
                        end_behavior="freeze" if kind == "video" else "loop")
    return None


def _figures(ctx: _Ctx, board: Sequence[BoardItem], sm: SceneMedia | None) -> dict[str, MediaRef]:
    out: dict[str, MediaRef] = {}
    for item in board:
        if item.kind != BoardItemKind.figure:
            continue
        info = sm.figures.get(item.id) if sm else None
        ref = _media_ref(ctx, info, fit="contain") if info else _figure_ref(ctx, item.figure_id)
        if ref is not None:
            out[item.id] = ref
    return out


def _simulation_fallback_board(scene: SimulationScene) -> tuple[list[BoardItem], dict[str, str]]:
    """Bullets from the beats' visual cues; each beat reveals its own bullet."""
    board: list[BoardItem] = []
    reveals: dict[str, str] = {}
    for beat in scene.beats:
        cue = _plain_rich(beat.visual_cue or "")
        if not cue or len(board) >= 12:
            continue
        item = BoardItem(id=f"cue-{len(board) + 1}", kind=BoardItemKind.bullet, text=cue)
        board.append(item)
        reveals[beat.id] = item.id
    return board, reveals


def _ai_video_fallback_board(scene: AIVideoScene) -> list[BoardItem]:
    items: list[BoardItem] = []
    if scene.title.strip():
        items.append(BoardItem(id="fallback-title", kind=BoardItemKind.heading, text=_plain_rich(scene.title)))
    desc = _plain_rich(scene.video_prompt)
    if desc:
        items.append(BoardItem(id="fallback-visual", kind=BoardItemKind.paragraph, text=desc))
    return items


# ---------------------------------------------------------------------------
# Scene assembly
# ---------------------------------------------------------------------------


def _timed_beats(plan: _AudioPlan, reveal_override: dict[str, str]) -> list[TimedBeat]:
    lead = SCENE_LEAD_SECONDS
    out: list[TimedBeat] = []
    for idx, bp in enumerate(plan.beats):
        beat = bp.beat
        start = _r(lead + bp.start)
        speech_end = _r(lead + bp.start + bp.speech)
        end = _r(lead + bp.end)
        words = [TimedWord(text=w.text, start=_r(lead + w.start), end=_r(lead + max(w.end, w.start)))
                 for w in bp.words]
        cues = split_caption(beat.narration, words, start=start, end=speech_end)
        if cues:
            hold = min(end, speech_end + CAPTION_HOLD_SECONDS)
            if hold > cues[-1].end:
                cues[-1] = cues[-1].model_copy(update={"end": _r(hold)})
        out.append(TimedBeat(
            beat_id=beat.id,
            index=idx,
            phase=bp.phase,
            start=start,
            speech_end=speech_end,
            end=end,
            narration=beat.narration,
            board_item_id=reveal_override.get(beat.id, beat.board_item_id),
            fill_item_id=beat.fill_item_id,
            highlight_item_ids=list(beat.highlight_item_ids),
            words=words,
            captions=cues,
            visual_cue=beat.visual_cue,
            estimated=bp.estimated,
        ))
    return out


def _build_scene(scene: Any, index: int, start: float, plan: _AudioPlan, sm: SceneMedia | None,
                 ctx: _Ctx) -> TimedScene:
    stype: str = scene.type
    board: list[BoardItem] = list(scene.board) if isinstance(scene, BoardScene) else []
    reveal_override: dict[str, str] = {}
    media = _main_media(ctx, scene, sm)
    poster = _poster(ctx, scene, sm)
    fullscreen = stype in FULLSCREEN_TYPES
    if isinstance(scene, SimulationScene) and media is None:
        board, reveal_override = _simulation_fallback_board(scene)
        fullscreen = False
        ctx.fallbacks.append({"scene_id": scene.id, "reason": "simulation_without_media"})
    elif isinstance(scene, AIVideoScene) and media is None:
        board = _ai_video_fallback_board(scene)
        fullscreen = False
        ctx.fallbacks.append({"scene_id": scene.id, "reason": "ai_video_without_media"})

    beats = _timed_beats(plan, reveal_override)
    lead = SCENE_LEAD_SECONDS
    if not beats:
        duration = SILENT_SCENE_SECONDS
    else:
        duration = lead + plan.content_end + SCENE_TAIL_SECONDS
        if isinstance(scene, QuizScene):
            duration += QUIZ_REVEAL_HOLD_SECONDS
    duration = _r(max(duration, max([b.end for b in beats], default=0.0), SCENE_TRANSITION_SECONDS + 0.1))
    # The teacher's minimum duration: a hold after the content (the content keeps its timing, sync cues included).
    hold = _r(scene.min_seconds - duration) if scene.min_seconds is not None and scene.min_seconds > duration else 0.0

    quiz: QuizTiming | None = None
    if isinstance(scene, QuizScene):
        cd_start = _r(lead + (plan.countdown_start or 0.0))
        cd_seconds = int(plan.countdown_seconds or scene.countdown_seconds)
        reveal = cd_start + cd_seconds
        first_reveal = next((b.start for b in beats if b.phase == "reveal"), None)
        if first_reveal is not None:
            reveal = max(cd_start, min(reveal, first_reveal))
        quiz = QuizTiming(
            question=scene.question,
            options=list(scene.options),
            correct_index=scene.correct_index,
            feedback_wrong=list(scene.feedback_wrong),
            explanation=scene.explanation,
            countdown_start=cd_start,
            countdown_seconds=cd_seconds,
            reveal_start=_r(reveal),
        )

    side_panel: ResolvedSidePanel | None = None
    if scene.side_panel is not None:
        show_at = 0.0
        if scene.side_panel.show_from_beat_id:
            show_at = next((b.start for b in beats if b.beat_id == scene.side_panel.show_from_beat_id), 0.0)
        side_panel = ResolvedSidePanel(panel=scene.side_panel, media=_panel_media(ctx, scene, sm), show_at=show_at)

    audio = plan.audio
    if audio is not None:
        ctx.note_storage(audio.asset_key, audio.storage_key)
    return TimedScene(
        scene_id=scene.id,
        index=index,
        type=stype,
        concept_id=scene.concept_id,
        chapter_id=scene.chapter_id,
        title=scene.title,
        subtitle=scene.subtitle,
        chapter_label=scene.chapter_label if isinstance(scene, ChapterCardScene) else None,
        start=_r(start),
        duration=_r(duration + hold),
        audio_offset=SCENE_LEAD_SECONDS,
        layout=Layout(mascot_position=scene.mascot_position, show_side_panel=scene.side_panel is not None,
                      fullscreen_media=fullscreen),
        audio_asset_key=audio.asset_key if audio is not None else None,
        audio_duration=_r(audio.duration) if audio is not None else 0.0,
        # narration loudness for the mascot's speech motion (None for older manifests)
        audio_envelope=audio.envelope if audio is not None else None,
        audio_envelope_fps=audio.envelope_fps if audio is not None else 30,
        board=board,
        figures=_figures(ctx, board, sm),
        beats=beats,
        side_panel=side_panel,
        media=media,
        quiz=quiz,
        p5_code=scene.p5_code if isinstance(scene, InteractiveScene) else None,
        poster=poster,
        objective_ids=list(scene.objective_ids),
        sync_cues=scene_sync_cues(beats, board, side_panel, duration, ctx.sync) if ctx.sync else [],
        hold_seconds=hold,
    )


# ---------------------------------------------------------------------------
# Chapters
# ---------------------------------------------------------------------------


def build_chapters(screenplay: Screenplay, scenes: Sequence[TimedScene]) -> list[Chapter]:
    """Chapters from ``Screenplay.chapters``, else chapter cards, else concept changes.

    Each chapter starts at its first scene; an opening chapter at 0:00 is added when the first
    chapter starts later (e.g. after the intro), named "Introduction" unless a real chapter already
    uses that name (then "Opening", "Lesson start", "Opening 2", ...: ``chapters.opening_title``).
    Only the timed scenes count, so a hidden scene never starts a chapter, and a chapter whose scenes are all
    hidden is left out.
    """

    start_of = {s.scene_id: s.start for s in scenes}
    chapters: list[Chapter] = []
    for ch in screenplay.chapters:
        ids = set(ch.scene_ids) | {s.scene_id for s in scenes if s.chapter_id == ch.id}
        starts = [start_of[i] for i in ids if i in start_of]
        if starts:
            chapters.append(Chapter(start=min(starts), title=ch.title))
    if not chapters:
        for s in scenes:
            if s.type == "chapter_card":
                title = s.title.strip() or (s.chapter_label or "").strip() or "Chapter"
                chapters.append(Chapter(start=s.start, title=title))
    if not chapters:
        concept_titles = {c.id: c.title for c in screenplay.concept_map}
        prev: str | None = None
        for s in scenes:
            if s.concept_id and s.concept_id != prev:
                chapters.append(Chapter(start=s.start, title=concept_titles.get(s.concept_id) or s.title or s.concept_id))
            if s.concept_id:
                prev = s.concept_id
    out: list[Chapter] = []
    for ch in sorted(chapters, key=lambda c: c.start):
        title = " ".join(ch.title.split()) or "Chapter"
        if out and (abs(out[-1].start - ch.start) < 1e-3 or out[-1].title == title):
            continue
        out.append(Chapter(start=_r(ch.start), title=title))
    if not out or out[0].start > 1e-3:
        out.insert(0, Chapter(start=0.0, title=opening_title(c.title for c in out)))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _assemble(
    screenplay: Screenplay,
    manifest: AssetManifest | None,
    *,
    settings: Settings,
    version_id: int | None,
    revision: int | None,
    include_intro: bool,
    branding: Branding | None,
    preview: bool,
) -> Timeline:
    ctx = _Ctx(figures={f.id: f for f in screenplay.figures}, sync=SyncOptions.from_settings(settings))
    intro = build_intro(screenplay) if include_intro else None
    t = intro.duration if intro else 0.0
    scenes: list[TimedScene] = []
    shown = [s for s in screenplay.scenes if not s.hidden]  # a hidden scene is in neither renderer
    for index, scene in enumerate(shown):
        audio = manifest.audio.get(scene.id) if manifest else None
        sm = manifest.media.get(scene.id) if manifest else None
        plan: _AudioPlan | None = None
        if audio is not None and audio.asset_key and (not preview or _audio_still_valid(scene, audio, manifest, screenplay)):
            plan = _plan_from_audio(scene, audio)
        if plan is None:
            reuse: dict[str, BeatAudio] = {}
            if audio is not None:
                for beat, _ in scene_beats(scene):
                    ba = next((x for x in audio.beats if x.beat_id == beat.id), None)
                    if ba is not None and _beat_matches(beat, ba, screenplay.lexicon, audio.language):
                        reuse[beat.id] = ba
            plan = _plan_estimated(scene, reuse)
            if scene_beats(scene) and not preview:
                ctx.fallbacks.append({"scene_id": scene.id,
                                      "reason": "audio_mismatch" if audio is not None else "no_audio"})
        timed = _build_scene(scene, index, t, plan, sm, ctx)
        if not settings.mascot_cues:  # the cue bubble beside Aadhi is switched off (player and MP4)
            timed.layout.mascot_cues = False
        scenes.append(timed)
        t = _r(timed.start + timed.duration)

    captions: list[CaptionCue] = []
    for s in scenes:
        for b in s.beats:
            captions.extend(shift_cues(b.captions, s.start))
    estimated = preview or any(b.estimated for s in scenes for b in s.beats)
    meta: dict[str, Any] = {
        "subject_name": screenplay.subject_name,
        "unit_name": screenplay.unit_name,
        "session_number": screenplay.session_number,
        "session_title": screenplay.session_title,
        STORAGE_KEYS_META: dict(sorted(ctx.storage_keys.items())),
    }
    if ctx.fallbacks:
        meta[FALLBACKS_META] = ctx.fallbacks
    return Timeline(
        version_id=version_id,
        screenplay_revision=revision,
        language=screenplay.language,
        board_language=screenplay.board_language or screenplay.language,
        fps=settings.render_fps,
        width=STAGE_WIDTH,
        height=STAGE_HEIGHT,
        meta=meta,
        concept_map=list(screenplay.concept_map),
        learning_objectives=list(screenplay.learning_objectives),
        intro=intro,
        scenes=scenes,
        total_duration=_r(t),
        chapters=build_chapters(screenplay, scenes),
        captions=normalize_cues(captions),
        branding=branding if branding is not None else default_branding(settings),
        transition_seconds=SCENE_TRANSITION_SECONDS,
        estimated=estimated,
    )


def build_timeline(
    screenplay: Screenplay,
    manifest: AssetManifest | None,
    *,
    settings: Settings,
    version_id: int | None = None,
    revision: int | None = None,
    include_intro: bool = True,
    branding: Branding | None = None,
) -> Timeline:
    """Build the timed lecture from the screenplay and the asset stage's manifest (pure).

    Scenes without matching narration fall back to estimated timings (``estimated=True`` beats,
    ``Timeline.estimated``); missing media degrade to board scenes (see the module docstring).
    """
    return _assemble(screenplay, manifest, settings=settings, version_id=version_id, revision=revision,
                     include_intro=include_intro, branding=branding, preview=False)


def preview_timeline(
    screenplay: Screenplay,
    manifest: AssetManifest | None,
    *,
    settings: Settings,
    version_id: int | None = None,
    include_intro: bool = True,
    branding: Branding | None = None,
) -> Timeline:
    """Editor preview of an (unsaved) screenplay. Always ``estimated=True``.

    Scenes whose narration file still matches (scene hash, or every beat id + spoken text +
    pause) keep their audio and exact timings; otherwise measured durations of unchanged beats are
    reused and the rest is estimated (such scenes carry no audio).
    """
    return _assemble(screenplay, manifest, settings=settings, version_id=version_id, revision=None,
                     include_intro=include_intro, branding=branding, preview=True)


# ---------------------------------------------------------------------------
# Serve-time URL resolution
# ---------------------------------------------------------------------------


def iter_media_refs(timeline: Timeline) -> Iterator[MediaRef]:
    """Every MediaRef in the timeline (main media, posters, panel media, board figures)."""
    for s in timeline.scenes:
        if s.media is not None:
            yield s.media
        if s.poster is not None:
            yield s.poster
        if s.side_panel is not None and s.side_panel.media is not None:
            yield s.side_panel.media
        yield from s.figures.values()


def timeline_asset_keys(timeline: Timeline) -> set[str]:
    """Asset keys referenced by the timeline (narration + media)."""
    keys = {s.audio_asset_key for s in timeline.scenes if s.audio_asset_key}
    keys |= {r.asset_key for r in iter_media_refs(timeline) if r.asset_key}
    return {k for k in keys if k}


def resolve_urls(timeline: Timeline, store: AssetStore, *, signed: bool = False) -> Timeline:
    """Return a copy of ``timeline`` with every ``url`` filled (serve time, blocking).

    Storage keys come from ``meta["asset_storage_keys"]``; keys not listed there (e.g. source
    figures, user overrides) are fetched with ONE ``AssetStore.get_many`` query. ``signed=True``
    produces short-lived URLs (S3 presigned GET without CDN). Unknown assets get ``url=None``.
    The storage-key map is not included in the returned copy.
    """
    from .sounds import DING_KEY, TICK_KEY

    tl = timeline.model_copy(deep=True)
    meta = dict(tl.meta)
    storage: dict[str, str] = dict(meta.pop(STORAGE_KEYS_META, None) or {})
    tl.meta = meta
    # Timelines stored before a mascot clip (or the intro logo) was re-encoded name the old file: serve the
    # current one.
    tl.branding.mascot_clips = {pos: current_clip_url(url) for pos, url in tl.branding.mascot_clips.items()}
    if tl.intro is not None and tl.intro.logo_video_url:
        tl.intro.logo_video_url = current_branding_url(tl.intro.logo_video_url)
    keys = timeline_asset_keys(tl)
    want_sounds = tl.branding.tick_url is None or tl.branding.ding_url is None
    if want_sounds:
        keys |= {TICK_KEY, DING_KEY}
    missing = sorted(k for k in keys if k not in storage)
    rows = store.get_many(missing) if missing else {}
    for k, asset in rows.items():
        storage[k] = asset.storage_key
    if want_sounds and (TICK_KEY not in storage or DING_KEY not in storage):
        try:
            from .sounds import ensure_sounds

            for snd in ensure_sounds(store).values():
                storage[snd.asset_key] = snd.storage_key
        except Exception as exc:  # noqa: BLE001 - sounds are optional decoration
            log.warning("could not store UI sounds: %s", type(exc).__name__)

    ttl = 3600
    if signed:
        try:
            from ..config import get_settings

            ttl = get_settings().s3_presign_ttl_seconds
        except Exception:  # noqa: BLE001
            ttl = 3600

    def url_for(asset_key: str | None) -> str | None:
        sk = storage.get(asset_key or "")
        if not sk:
            return None
        return store.storage.signed_url(sk, ttl) if signed else store.url_for(sk)

    unknown: set[str] = set()
    for s in tl.scenes:
        if s.audio_asset_key:
            s.audio_url = url_for(s.audio_asset_key)
            if s.audio_url is None:
                unknown.add(s.audio_asset_key)
    for ref in iter_media_refs(tl):
        if not ref.asset_key:
            continue  # external hotlink: url already set
        ref.url = url_for(ref.asset_key)
        if ref.url is None:
            unknown.add(ref.asset_key)
        row = rows.get(ref.asset_key)
        if row is not None:
            ref.mime = ref.mime or row.mime
            ref.width = ref.width or row.width
            ref.height = ref.height or row.height
            ref.duration = ref.duration if ref.duration is not None else row.duration_s
    if tl.branding.tick_url is None:
        tl.branding.tick_url = url_for(TICK_KEY)
    if tl.branding.ding_url is None:
        tl.branding.ding_url = url_for(DING_KEY)
    if unknown:
        log.warning("timeline references %d unknown asset(s)", len(unknown))
    return tl
