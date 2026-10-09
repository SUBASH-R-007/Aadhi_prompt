"""Duration estimates used before TTS exists (plan checks, lint, critic context)."""

from __future__ import annotations

import re
from typing import Any

from ..compose.base import (
    QUIZ_REVEAL_HOLD_SECONDS,
    SCENE_LEAD_SECONDS,
    SCENE_TAIL_SECONDS,
    SILENT_SCENE_SECONDS,
)
from ..schemas.manifest import INTER_BEAT_GAP_SECONDS
from ..schemas.screenplay import Beat, QuizScene, Screenplay

# Approximate narration speed (words per second) of the default neural voices per language.
_WORDS_PER_SECOND = {"en": 2.5, "hi": 2.2, "ta": 1.6, "te": 1.7, "kn": 1.7, "ml": 1.5}
_WORD_RE = re.compile(r"\w+(?:[-'’]\w+)*", re.U)


def word_count(text: str) -> int:
    """Number of words (Unicode-aware; Indic combining marks stay inside words)."""
    return len(_WORD_RE.findall(text or ""))


def words_per_second(language: str) -> float:
    return _WORDS_PER_SECOND.get((language or "en").split("-")[0].lower(), 2.3)


def speech_seconds(text: str, language: str = "en-IN") -> float:
    """Estimated spoken duration of ``text``."""
    words = word_count(text)
    return 0.0 if words == 0 else max(0.6, words / words_per_second(language))


def beat_seconds(beat: Beat, language: str = "en-IN") -> float:
    """Speech + deliberate pause + inter-beat gap."""
    return speech_seconds(beat.narration, language) + beat.pause_after + INTER_BEAT_GAP_SECONDS


def scene_seconds(scene: Any, language: str = "en-IN") -> float:
    """Estimated scene duration including lead-in, tail, quiz countdown and reveal hold."""
    beats = scene.all_beats()
    if not beats:
        return SILENT_SCENE_SECONDS
    total = SCENE_LEAD_SECONDS + SCENE_TAIL_SECONDS + sum(beat_seconds(b, language) for b in beats)
    if isinstance(scene, QuizScene):
        total += scene.countdown_seconds + QUIZ_REVEAL_HOLD_SECONDS
    return total


def lecture_seconds(screenplay: Screenplay) -> float:
    """Estimated total duration of all scenes (no intro)."""
    return sum(scene_seconds(s, screenplay.language) for s in screenplay.scenes)
