"""Learner analytics aggregation for the teacher dashboard.

Definitions (per project, optionally per version):

* ``viewers`` — distinct ``viewer_id``; ``sessions`` — ``session_start`` events;
  ``completion_rate`` — viewers with a ``complete`` event / viewers; ``avg_watch_seconds`` — mean
  over viewers of the furthest absolute position reported (``t``, seek events excluded).
* Per scene: ``enters`` / ``completes`` are distinct viewers with ``scene_enter`` /
  ``scene_complete``; ``dropoff_rate`` = share of entering viewers who never completed it.
* Quizzes: only each viewer's *first valid* answer per scene counts (a choice outside the quiz's
  options never uses up a viewer's counted answer). Correctness is recomputed from the
  screenplay's ``correct_index`` (the client flag is used only when the quiz is unknown).
  Selection and counting run in SQL (``min(id)`` per viewer and scene, then ``GROUP BY``), so the
  cost does not grow with the number of rows shipped to Python.
* Flags: quiz accuracy < 0.5, or scene drop-off > 0.3.
* A scene skipped in the video (``hidden``) is still listed, with ``"hidden": true`` (the key is left out
  otherwise): students cannot reach it now, so its numbers only come from before it was hidden.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import ColumnElement, and_, distinct, false, func, or_, select
from sqlalchemy.orm import Session

from ..models import AnalyticsEvent
from ..schemas.screenplay import QuizScene, Screenplay

LOW_ACCURACY = 0.5
HIGH_DROPOFF = 0.3


def _scope(project_id: int, version_id: int | None) -> ColumnElement[bool]:
    cond = AnalyticsEvent.project_id == project_id
    if version_id is not None:
        cond = and_(cond, AnalyticsEvent.version_id == version_id)
    return cond


def _summary(db: Session, scope: ColumnElement[bool]) -> dict[str, Any]:
    viewers = db.execute(select(func.count(distinct(AnalyticsEvent.viewer_id))).where(scope)).scalar_one() or 0
    sessions = (
        db.execute(
            select(func.count()).select_from(AnalyticsEvent).where(scope, AnalyticsEvent.event == "session_start")
        ).scalar_one()
        or 0
    )
    completers = (
        db.execute(
            select(func.count(distinct(AnalyticsEvent.viewer_id))).where(scope, AnalyticsEvent.event == "complete")
        ).scalar_one()
        or 0
    )
    furthest = (
        select(AnalyticsEvent.viewer_id, func.max(AnalyticsEvent.data["t"].as_float()).label("t_max"))
        .where(scope, AnalyticsEvent.event != "seek")
        .group_by(AnalyticsEvent.viewer_id)
        .subquery()
    )
    avg_watch = db.execute(select(func.avg(furthest.c.t_max))).scalar_one()
    return {
        "viewers": int(viewers),
        "sessions": int(sessions),
        "completion_rate": round(completers / viewers, 4) if viewers else 0.0,
        "avg_watch_seconds": round(float(avg_watch), 2) if avg_watch is not None else 0.0,
    }


def _scene_counts(db: Session, scope: ColumnElement[bool]) -> dict[str, dict[str, int]]:
    rows = db.execute(
        select(AnalyticsEvent.scene_id, AnalyticsEvent.event, func.count(distinct(AnalyticsEvent.viewer_id)))
        .where(scope, AnalyticsEvent.scene_id.is_not(None), AnalyticsEvent.event.in_(("scene_enter", "scene_complete")))
        .group_by(AnalyticsEvent.scene_id, AnalyticsEvent.event)
    ).all()
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"enters": 0, "completes": 0})
    for scene_id, event, n in rows:
        counts[scene_id]["enters" if event == "scene_enter" else "completes"] = int(n)
    return counts


# (choice, client "correct" flag, number of viewers) for one quiz scene.
AnswerGroup = tuple[int, bool | None, int]


def _choice_column() -> ColumnElement[int]:
    return AnalyticsEvent.data["choice"].as_integer()


def _valid_choice(quizzes: dict[str, QuizScene | None] | None) -> ColumnElement[bool]:
    """Answers that may count: a choice within the known quiz's options (any choice >= 0 when unknown)."""
    choice = _choice_column()
    if quizzes is None:
        return and_(choice.is_not(None), choice >= 0)
    known = [
        and_(AnalyticsEvent.scene_id == sid, choice >= 0, choice < len(quiz.options))
        for sid, quiz in quizzes.items()
        if quiz is not None
    ]
    return or_(*known) if known else false()


def _answer_groups(
    db: Session, scope: ColumnElement[bool], quizzes: dict[str, QuizScene | None] | None
) -> dict[str, list[AnswerGroup]]:
    """scene_id -> counts of each viewer's first valid answer, grouped by (choice, client flag).

    ``quizzes`` restricts the scenes and the valid choice range (None: every scene, any choice >= 0).
    """
    first_ids = (
        select(func.min(AnalyticsEvent.id).label("id"))
        .where(
            scope,
            AnalyticsEvent.event == "quiz_answer",
            AnalyticsEvent.scene_id.is_not(None),
            _valid_choice(quizzes),
        )
        .group_by(AnalyticsEvent.viewer_id, AnalyticsEvent.scene_id)
        .subquery()
    )
    firsts = (
        select(
            AnalyticsEvent.scene_id.label("scene_id"),
            _choice_column().label("choice"),
            AnalyticsEvent.data["correct"].as_boolean().label("correct"),
        )
        .join(first_ids, AnalyticsEvent.id == first_ids.c.id)
        .subquery()
    )
    rows = db.execute(
        select(firsts.c.scene_id, firsts.c.choice, firsts.c.correct, func.count()).group_by(
            firsts.c.scene_id, firsts.c.choice, firsts.c.correct
        )
    ).all()
    out: dict[str, list[AnswerGroup]] = defaultdict(list)
    for scene_id, choice, correct, n in rows:
        if choice is None:
            continue
        out[str(scene_id)].append((int(choice), None if correct is None else bool(correct), int(n)))
    return out


def _quiz_entry(scene_id: str, groups: list[AnswerGroup], quiz: QuizScene | None) -> dict[str, Any]:
    if quiz is not None:
        n_options = len(quiz.options)
        correct_index: int | None = quiz.correct_index
        question = quiz.question
    else:
        choices = [choice for choice, _, _ in groups if choice >= 0]
        n_options = (max(choices) + 1) if choices else 0
        correct_index = None
        question = ""
    option_counts = [0] * n_options
    correct = 0
    counted = 0
    for choice, flag, n in groups:
        if not 0 <= choice < n_options:  # already excluded in SQL; defensive
            continue
        option_counts[choice] += n
        counted += n
        if correct_index is not None:
            correct += n if choice == correct_index else 0
        else:
            correct += n if flag is True else 0
    return {
        "scene_id": scene_id,
        "question": question,
        "answers": counted,
        "accuracy": round(correct / counted, 4) if counted else None,
        "option_counts": option_counts,
        "correct_index": correct_index,
    }


def dashboard(db: Session, project_id: int, version_id: int | None, screenplay: Screenplay | None) -> dict[str, Any]:
    """Aggregate analytics for the dashboard (shape of ``GET /api/projects/{id}/analytics``)."""
    scope = _scope(project_id, version_id)
    counts = _scene_counts(db, scope)

    scenes: list[dict[str, Any]] = []
    quizzes: list[dict[str, Any]] = []
    if screenplay is not None:
        ordered = [(i, s.id, s.title or s.id) for i, s in enumerate(screenplay.scenes)]
        hidden = {s.id for s in screenplay.scenes if s.hidden}
        quiz_scenes: dict[str, QuizScene | None] = {s.id: s for s in screenplay.scenes if isinstance(s, QuizScene)}
        answers = _answer_groups(db, scope, quiz_scenes)
    else:
        answers = _answer_groups(db, scope, None)
        hidden = set()
        ids = sorted(set(counts) | set(answers))
        ordered = [(i, sid, sid) for i, sid in enumerate(ids)]
        quiz_scenes = {sid: None for sid in sorted(answers)}
    for index, scene_id, title in ordered:
        c = counts.get(scene_id, {"enters": 0, "completes": 0})
        enters, completes = c["enters"], c["completes"]
        dropoff = max(0.0, (enters - completes) / enters) if enters else 0.0
        row: dict[str, Any] = {
            "scene_id": scene_id,
            "title": title,
            "index": index,
            "enters": enters,
            "completes": completes,
            "dropoff_rate": round(dropoff, 4),
        }
        if scene_id in hidden:
            row["hidden"] = True
        scenes.append(row)
    for scene_id, quiz in quiz_scenes.items():
        quizzes.append(_quiz_entry(scene_id, answers.get(scene_id, []), quiz))

    flags: list[dict[str, Any]] = []
    for q in quizzes:
        if q["answers"] and q["accuracy"] is not None and q["accuracy"] < LOW_ACCURACY:
            flags.append(
                {
                    "scene_id": q["scene_id"],
                    "code": "low_accuracy",
                    "value": q["accuracy"],
                    "reason": f"Only {round(q['accuracy'] * 100)}% of viewers answered this quiz correctly.",
                }
            )
    for s in scenes:
        if s["enters"] and s["dropoff_rate"] > HIGH_DROPOFF:
            flags.append(
                {
                    "scene_id": s["scene_id"],
                    "code": "high_dropoff",
                    "value": s["dropoff_rate"],
                    "reason": f"{round(s['dropoff_rate'] * 100)}% of viewers stopped watching during this scene.",
                }
            )
    return {
        "summary": _summary(db, scope),
        "scenes": scenes,
        "quizzes": quizzes,
        "flags": flags,
        "version_id": version_id,
    }
