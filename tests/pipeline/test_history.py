"""``aadhi.pipeline.history`` (B3 plan §1.4, §A B3-0 task 4): what counts, the order and its tie-breaks, the NULL
``Render.job_id`` rule, the classic-scope rule (O21), the entry's fields and the query bound (<= 3 per call)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
import itertools
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import event

from aadhi.models import Asset, Job, Project, ProjectVersion, Render, User
from aadhi.pipeline import history
from aadhi.pipeline.history import HistoryEntry, narrator_of, recent
from tests.schemas.v3_fixtures import PRESENTATION

T0 = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc)
_names = itertools.count(1)


def minutes(n: float) -> dt.datetime:
    return T0 + dt.timedelta(minutes=n)


def presentation(**over: Any) -> dict[str, Any]:
    data = {**PRESENTATION, "format": "landscape", "hook": None, "pins": [], "greeting_id": None,
            "music": {"source": "off"}, "voices": {"narrator": "en-IN-NeerjaNeural"}}
    data.update(over)
    return data


class World:
    """Users, projects, versions, jobs and renders written straight to the test database."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def user(self) -> int:
        u = User(username=f"teacher{next(_names)}", password_hash="x", role="editor")
        self.db.add(u)
        self.db.flush()
        return u.id

    def project(self, owner: int, *, deleted: bool = False) -> int:
        p = Project(owner_id=owner, title="Lesson", deleted_at=minutes(-1) if deleted else None)
        self.db.add(p)
        self.db.flush()
        return p.id

    def version(self, project: int, *, number: int = 1, subject: str = "Physics",
                pres: dict[str, Any] | None | bool = True) -> int:
        doc: dict[str, Any] = {"subject_name": subject, "language": "en-IN", "scenes": []}
        if pres is True:
            doc["presentation"] = presentation()
        elif isinstance(pres, dict):
            doc["presentation"] = pres
        v = ProjectVersion(project_id=project, number=number, status="ready", revision=1, screenplay=doc,
                           generation_meta={})
        self.db.add(v)
        self.db.flush()
        return v.id

    def render(self, version: int, *, at: dt.datetime, status: str = "succeeded", video: str | None = "video-key",
               job: str = "finished", job_at: dt.datetime | None = None) -> int:
        """A render; ``job``: "finished" (a job that finished at ``job_at`` or ``at``), "unfinished" (a job with no
        finish time), "none" (no job: ``Render.job_id`` NULL, as after job cleanup)."""
        job_id = None
        if job != "none":
            j = Job(kind="render_video", status="succeeded" if status == "succeeded" else status, max_attempts=3,
                    created_at=at - dt.timedelta(minutes=5),
                    finished_at=(job_at or at) if job == "finished" else None)
            self.db.add(j)
            self.db.flush()
            job_id = j.id
        r = Render(version_id=version, job_id=job_id, status=status, video_asset_key=video,
                   created_at=at - dt.timedelta(minutes=5) if job == "finished" else at)
        self.db.add(r)
        self.db.flush()
        return r.id

    def lesson(self, owner: int, *, at: dt.datetime, **version_kw: Any) -> tuple[int, int]:
        """A project with one version rendered at ``at``: ``(project_id, version_id)``."""
        p = self.project(owner)
        v = self.version(p, **version_kw)
        self.render(v, at=at)
        return p, v


@pytest.fixture()
def world(db_session):
    w = World(db_session)
    yield w
    db_session.rollback()


@contextmanager
def counted(session: Any):
    statements: list[str] = []
    engine = session.get_bind()

    def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


def ids(entries: list[HistoryEntry]) -> list[int]:
    return [e.version_id for e in entries]


# --- the frozen interface -------------------------------------------------------------------------------------------


def test_the_interface_is_frozen() -> None:
    assert [f.name for f in dataclasses.fields(HistoryEntry)] == [
        "project_id", "version_id", "fmt", "finished_at", "course_key", "narrator_persona", "narrator_speaker",
        "narrator_accent", "narrator_gender", "music_source", "music_asset_key", "music_signature", "theme_family",
        "personality", "display_font", "caption_style", "hook_kind", "greeting_id", "intro_layout", "pins"]
    assert HistoryEntry.__dataclass_params__.frozen  # type: ignore[attr-defined]
    sig = inspect.signature(recent)
    assert list(sig.parameters) == ["session", "user_id", "exclude_project_id", "fmt", "limit"]
    assert all(sig.parameters[n].kind is inspect.Parameter.KEYWORD_ONLY
               for n in ("user_id", "exclude_project_id", "fmt", "limit"))
    assert sig.parameters["limit"].default == 10


# --- what counts ----------------------------------------------------------------------------------------------------


def test_only_completed_renders_with_a_video_count(world) -> None:
    me = world.user()
    _, ok = world.lesson(me, at=minutes(10))
    for status, video in (("failed", None), ("cancelled", None), ("running", None), ("queued", None),
                          ("succeeded", None), ("succeeded", "")):
        p = world.project(me)
        world.render(world.version(p), at=minutes(50), status=status, video=video)
    world.version(world.project(me))  # a draft: never rendered
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None)) == [ok]


def test_other_teachers_deleted_and_excluded_projects_never_count(world) -> None:
    me, other = world.user(), world.user()
    _, mine = world.lesson(me, at=minutes(10))
    world.lesson(other, at=minutes(20))
    gone = world.project(me, deleted=True)
    world.render(world.version(gone), at=minutes(30))
    this, _ = world.lesson(me, at=minutes(40))
    assert ids(recent(world.db, user_id=me, exclude_project_id=this, fmt=None)) == [mine]
    assert len(recent(world.db, user_id=me, exclude_project_id=None, fmt=None)) == 2


def test_classic_lessons_never_enter_rotation(world) -> None:
    """O21's default: a version without a presentation (a classic lesson) is not in the history."""
    me = world.user()
    _, dyn = world.lesson(me, at=minutes(10))
    world.lesson(me, at=minutes(20), pres=None)
    world.lesson(me, at=minutes(30), pres={"broken": True})  # an unreadable presentation is skipped, not raised
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None)) == [dyn]


def test_a_lesson_is_its_projects_newest_rendered_version(world) -> None:
    """One entry per project; an unrendered duplicate (a newer version without a render) never counts."""
    me = world.user()
    p = world.project(me)
    v1 = world.version(p, number=1)
    world.render(v1, at=minutes(10))
    world.version(p, number=2)  # the duplicate, never rendered
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None)) == [v1]
    v3 = world.version(p, number=3)
    world.render(v3, at=minutes(20))
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None)) == [v3]


def test_the_format_filter(world) -> None:
    me = world.user()
    _, land = world.lesson(me, at=minutes(10))
    _, reel = world.lesson(me, at=minutes(20), pres=presentation(format="portrait"))
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt="landscape")) == [land]
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt="portrait")) == [reel]
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None)) == [reel, land]


# --- order --------------------------------------------------------------------------------------------------------


def test_order_is_render_completion_newest_first_with_the_null_job_rule(world) -> None:
    me = world.user()
    p1, p2, p3, p4 = (world.project(me) for _ in range(4))
    a = world.version(p1)
    world.render(a, at=minutes(10), job_at=minutes(100))  # its job finished last of all
    b = world.version(p2)
    world.render(b, at=minutes(90), job="none")  # job cleaned up: Render.created_at (90) rules
    c = world.version(p3)
    world.render(c, at=minutes(95), job="unfinished")  # a job without a finish time: Render.created_at (95)
    d = world.version(p4)
    world.render(d, at=minutes(50))
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None)) == [a, c, b, d]


def test_ties_are_broken_by_version_id_descending(world) -> None:
    me = world.user()
    versions = []
    for _ in range(3):
        versions.append(world.lesson(me, at=minutes(10))[1])
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None)) == sorted(versions, reverse=True)


def test_a_version_rendered_twice_counts_at_its_latest_completion(world) -> None:
    me = world.user()
    _, early = world.lesson(me, at=minutes(10))
    _, middle = world.lesson(me, at=minutes(20))
    world.render(early, at=minutes(30))  # rendered again, after "middle"
    entries = recent(world.db, user_id=me, exclude_project_id=None, fmt=None)
    assert ids(entries) == [early, middle]
    assert entries[0].finished_at == minutes(30) and entries[0].finished_at.tzinfo is not None


def test_limit_and_the_candidate_bound(world) -> None:
    me = world.user()
    lessons = [world.lesson(me, at=minutes(k))[1] for k in range(6)]
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None, limit=3)) == lessons[::-1][:3]
    assert recent(world.db, user_id=me, exclude_project_id=None, fmt=None, limit=0) == []
    # the newest 2 x limit projects are read: classic ones among them leave fewer entries (the cost rule)
    for k in range(6, 12):
        world.lesson(me, at=minutes(k), pres=None)
    assert recent(world.db, user_id=me, exclude_project_id=None, fmt=None, limit=3) == []
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None, limit=5)) == lessons[::-1][:4]


# --- the entry --------------------------------------------------------------------------------------------------------


def test_the_entry_fields(world) -> None:
    me = world.user()
    world.db.add(Asset(key="music-bed1", kind="music", storage_key="private/music/x.wav", mime="audio/wav",
                       meta={"mood": "calm_focus", "key": "D minor", "bpm": 78.4, "licence": "procedural"}))
    world.db.add(Asset(key="music-bed2", kind="music", storage_key="private/music/y.wav", mime="audio/wav",
                       meta={"licence": "upload"}))
    pres = presentation(
        voices={"narrator": "openai:coral@en-IN", "calibration": {"openai:coral@en-IN": {"natural_wpm": 160,
                                                                                       "control": "wpm=155"}}},
        music={"source": "procedural", "provider": "procedural", "mood": "calm_focus", "asset_key": "music-bed1",
               "mode": "bed"},
        pins=["voice", "music", "theme"], greeting_id="vanakkam-2", intro_layout="card_left", hook_kind="mystery")
    p, v = world.lesson(me, at=minutes(10), pres=pres, subject="  Basic Electrical ENGINEERING ")
    _, v2 = world.lesson(me, at=minutes(5), pres=presentation(music={"source": "upload", "asset_key": "music-bed2"}))
    world.db.flush()
    e, e2 = recent(world.db, user_id=me, exclude_project_id=None, fmt=None)
    assert (e.project_id, e.version_id, e.fmt, e.finished_at) == (p, v, "landscape", minutes(10))
    assert e.course_key == "basic electrical engineering"
    assert (e.narrator_persona, e.narrator_speaker, e.narrator_accent, e.narrator_gender) == (
        "openai:coral@en-IN", "openai:coral", "en-IN", "female")
    assert (e.music_source, e.music_asset_key, e.music_signature) == ("procedural", "music-bed1",
                                                                      ("calm_focus", "D minor", 78))
    assert (e.theme_family, e.personality, e.display_font, e.caption_style, e.hook_kind) == (
        PRESENTATION["theme_family"], PRESENTATION["personality"], PRESENTATION["display_font"],
        PRESENTATION["caption_style"], "mystery")
    assert (e.greeting_id, e.intro_layout) == ("vanakkam-2", "card_left")
    assert dict(e.pins) == {"voice": "openai:coral@en-IN", "music": "music-bed1",
                            "theme": PRESENTATION["theme_family"]}
    with pytest.raises(TypeError):
        e.pins["voice"] = "x"  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.course_key = "x"  # type: ignore[misc]
    # a bed without the analysis, and the B2-era placeholder: no signature
    assert e2.version_id == v2 and e2.music_signature is None and e2.music_asset_key == "music-bed2"


@pytest.mark.parametrize(("raw", "persona", "speaker", "accent", "gender"), [
    ("en-IN-NeerjaNeural", "edge:en-IN-NeerjaNeural", "ms:neerja", "en-IN", "female"),  # B2-era bare ids
    ("en-IN-NeerjaExpressiveNeural", "edge:en-IN-NeerjaExpressiveNeural", "ms:neerja", "en-IN", "female"),
    ("en-US-AndrewMultilingualNeural", "edge:en-US-AndrewMultilingualNeural", "ms:andrew", "en-US", "male"),
    ("nova", "openai:nova", "openai:nova", "en-US", "female"),
    ("fable", "openai:fable", "openai:fable", "en-GB", "male"),
    ("Charon", "gemini:Charon", "gemini:Charon", None, "male"),
    ("edge:en-IN-PrabhatNeural", "edge:en-IN-PrabhatNeural", "ms:prabhat", "en-IN", "male"),  # persona ids
    ("openai:marin", "openai:marin", "openai:marin", "en-US", None),  # not in today's catalogue
    ("openai:coral@en-IN", "openai:coral@en-IN", "openai:coral", "en-IN", "female"),
    ("mystery-voice", "mystery-voice", "mystery-voice", None, None),
])
def test_narrator_of(raw: str, persona: str, speaker: str, accent: str | None, gender: str | None) -> None:
    n = narrator_of(raw)
    assert (n.persona, n.speaker, n.accent, n.gender) == (persona, speaker, accent, gender)


def test_no_bare_catalogue_id_is_ambiguous_today() -> None:
    """Every bare id has one owner today, so the provider hint changes no entry yet; it matters once two providers
    share an id (Azure's catalogue would share every Edge name)."""
    from aadhi.providers.tts.voices import CATALOGUE

    owners: dict[str, set[str]] = {}
    for name, voices in CATALOGUE.items():
        for v in voices:
            if name != "fake":
                owners.setdefault(v["id"], set()).add(name)
    assert not {i: o for i, o in owners.items() if len(o) > 1}


@pytest.fixture()
def shared_nova(monkeypatch):
    """A catalogue where Gemini also offers a (male) voice with OpenAI's id ``nova``."""
    from aadhi.providers.tts import voices

    catalogue = {k: list(v) for k, v in voices.CATALOGUE.items()}
    catalogue["gemini"].append({"id": "nova", "label": "Nova", "language": "multi", "gender": "Male"})
    monkeypatch.setattr(voices, "CATALOGUE", catalogue)


def test_a_bare_id_resolves_within_the_provider_hint(shared_nova) -> None:
    """B3 plan §1.2 rule 2: within the provider that narrated the lesson, else the unique owner, else no provider."""
    assert (narrator_of("nova", "openai").persona, narrator_of("nova", "openai").gender) == ("openai:nova", "female")
    assert (narrator_of("nova", "gemini").persona, narrator_of("nova", "gemini").gender) == ("gemini:nova", "male")
    ambiguous = narrator_of("nova")
    assert (ambiguous.persona, ambiguous.speaker, ambiguous.gender) == ("nova", "nova", None)
    assert narrator_of("nova", "edge").persona == "nova"  # a hint that lacks the voice does not decide
    assert narrator_of("fable", "gemini").persona == "openai:fable"  # the unique owner, whatever the hint
    assert narrator_of("openai:nova", "gemini").persona == "openai:nova"  # a persona id names its provider


def test_history_reads_the_bare_narrator_within_the_stored_provider(world, shared_nova) -> None:
    """The hint is the version's stored ``tts_provider``: its ``generation_meta["options"]``, else the project's
    settings (as the orchestrator's ``version_options`` reads them); with neither, the id stays ambiguous."""
    me = world.user()
    bare = presentation(voices={"narrator": "nova"})
    p1, v1 = world.lesson(me, at=minutes(30), pres=bare)
    world.db.get(ProjectVersion, v1).generation_meta = {"options": {"tts_provider": "openai"}}
    world.db.get(Project, p1).settings = {"tts_provider": "gemini"}  # the version's own options win
    p2, _ = world.lesson(me, at=minutes(20), pres=bare)
    world.db.get(Project, p2).settings = {"tts_provider": "gemini"}
    world.lesson(me, at=minutes(10), pres=bare)
    world.db.flush()
    with counted(world.db) as statements:
        entries = recent(world.db, user_id=me, exclude_project_id=None, fmt=None)
    assert [(e.narrator_persona, e.narrator_gender) for e in entries] == [
        ("openai:nova", "female"), ("gemini:nova", "male"), ("nova", None)]
    assert len(statements) <= 3


# --- cost ---------------------------------------------------------------------------------------------------------


def test_at_most_three_queries_per_call(world) -> None:
    me = world.user()
    world.db.add(Asset(key="music-bed1", kind="music", storage_key="private/music/x.wav", mime="audio/wav",
                       meta={"mood": "calm_focus", "key": "C", "bpm": 72}))
    for k in range(25):
        music = {"source": "procedural", "asset_key": "music-bed1"} if k % 2 else {"source": "off"}
        world.lesson(me, at=minutes(k), pres=presentation(music=music) if k % 5 else None)
    world.db.flush()
    for fmt in (None, "landscape", "portrait"):
        with counted(world.db) as statements:
            entries = recent(world.db, user_id=me, exclude_project_id=None, fmt=fmt)
        assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) <= 3, statements
        assert len(statements) <= 3
        assert len(entries) == (10 if fmt != "portrait" else 0)
    with counted(world.db) as statements:
        assert recent(world.db, user_id=world.user(), exclude_project_id=None, fmt=None) == []
    assert len(statements) <= 2  # the flush of the new user, then one query: nothing to load


def test_the_render_rows_read_are_bounded(world, monkeypatch) -> None:
    """The first query reads at most the newest ``ROWS_PER_LIMIT * limit`` rendered versions, not the teacher's whole
    history: a project rendered more often than that hides older projects (the cost rule)."""
    me = world.user()
    _, older = world.lesson(me, at=minutes(1))
    p = world.project(me)
    versions = [world.version(p, number=k + 1) for k in range(4)]
    for k, v in enumerate(versions):
        world.render(v, at=minutes(10 + k))
    world.db.flush()
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None, limit=1)) == [versions[-1]]
    assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None, limit=2)) == [versions[-1], older]
    monkeypatch.setattr(history, "ROWS_PER_LIMIT", 2)  # 4 rows for limit 2: all the busy project's versions
    with counted(world.db) as statements:
        assert ids(recent(world.db, user_id=me, exclude_project_id=None, fmt=None, limit=2)) == [versions[-1]]
    assert " LIMIT " in statements[0].upper()


def test_history_reads_only(world) -> None:
    me = world.user()
    world.lesson(me, at=minutes(1))
    world.db.flush()
    with counted(world.db) as statements:
        recent(world.db, user_id=me, exclude_project_id=None, fmt=None)
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert not world.db.new and not world.db.dirty and not world.db.deleted
    assert history.COMPLETED == "succeeded"
