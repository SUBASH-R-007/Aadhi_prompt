"""Density and pacing across scenes (any language). Both are notes (info): they never trigger a rewrite.

* ``pacing.dense_run`` — two or more dense boards in a row (a board is dense when its items weigh 5 or
  more; a table or a code block weighs 3, any other item 1), with no lighter scene to breathe between.
  Hidden scenes (skipped in the video) are left out of the run: the scenes either side of one play back to back.
* ``board.reading_time`` — a board holds more text than the learner can read while the scene plays
  (reading at about 200 words a minute for English boards, 150 for others, takes more than 1.5 times
  the scene's estimated length, or its minimum duration (``min_seconds``) when that is longer).

A sparse scene (long narration, little on the board) is deliberately not flagged: few words on screen
is what the coherence and redundancy principles ask for.
"""

from __future__ import annotations

from ...schemas.screenplay import BoardItemKind, BoardScene
from ..timing import scene_seconds, word_count
from .extract import Facts, SceneFacts
from .report import Finding, lint_issue
from .text import upper_first

DENSE_WEIGHT = 5
HEAVY_KINDS = frozenset({BoardItemKind.table, BoardItemKind.code})
HEAVY_WEIGHT = 3
READ_WPM = {"en": 200}
READ_WPM_DEFAULT = 150
READING_FACTOR = 1.5
READING_MIN_SECONDS = 8.0


def board_weight(scene: BoardScene) -> int:
    return sum(HEAVY_WEIGHT if item.kind in HEAVY_KINDS else 1 for item in scene.board)


def board_words(f: SceneFacts) -> int:
    """Words a learner reads on the board (text, cells, code tokens; a formula and its legend count a few)."""
    words = sum(word_count(b.plain) for b in f.board if b.path[0] == "board")
    for item in f.scene.board:
        if item.kind == BoardItemKind.code and item.code:
            words += len(item.code[:4000].split())
        if item.kind == BoardItemKind.formula and item.latex:
            words += 3 + len(item.variables) * 3  # a formula and its legend take a moment to read
    return words


def read_wpm(language: str | None) -> int:
    return READ_WPM.get((language or "en").split("-")[0].lower(), READ_WPM_DEFAULT)


def pacing_findings(facts: Facts) -> list[Finding]:
    out: list[Finding] = []
    lesson = facts.lesson
    run: list[int] = []

    def close_run() -> None:
        if len(run) >= 2:
            msg = (f"{upper_first(lesson.where(run))} are dense boards one after another, with no lighter scene in "
                   "between. Give learners a breather: put an example, an animation or a quick question between them, "
                   "or move some points into the narration.")
            out.append(Finding(lint_issue("pacing.dense_run", msg, scene_id=lesson.scene_id(run[1]))))
        run.clear()

    for f in facts.scenes:
        scene = f.scene
        if scene.hidden:
            continue  # left out of the video: the scenes either side of it play back to back
        if isinstance(scene, BoardScene) and scene.type != "title" and board_weight(scene) >= DENSE_WEIGHT:
            run.append(f.index)
        else:
            close_run()
    close_run()

    language = facts.sp.board_language or facts.sp.language
    wpm = read_wpm(language)
    for f in facts.scenes:
        scene = f.scene
        if not isinstance(scene, BoardScene) or not scene.board:
            continue
        reading = board_words(f) / (wpm / 60.0)
        # a teacher's hold (SceneBase.min_seconds) keeps the board on screen at least that long
        playing = max(scene_seconds(scene, facts.sp.language), scene.min_seconds or 0.0)
        if reading >= READING_MIN_SECONDS and reading > READING_FACTOR * playing:
            advice = ("give the scene a longer minimum duration" if scene.min_seconds else
                      "let the narration stay on it longer (a pause after a beat gives reading time)")
            msg = (f"The board of {lesson.label(f.index)} takes about {reading:.0f} s to read, but the scene plays for "
                   f"about {playing:.0f} s. Shorten the board, or {advice}.")
            out.append(Finding(lint_issue("board.reading_time", msg, scene_id=lesson.scene_id(f.index))))
    return out
