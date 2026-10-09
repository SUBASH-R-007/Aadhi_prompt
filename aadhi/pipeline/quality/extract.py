"""What the quality checks read from a typed screenplay, extracted once per lint (bounded, pure).

Positions come from typed fields, not from HTML: rich-lite ``[[keyword]]`` and ``**bold**`` spans
(``keyword``), a definition's ``term`` (``definition``), table ``headers`` (``header``), ``heading``
items (``heading``), figure captions and side-panel labels (``label``), name-like scene titles
(``title``) and concept-map titles (``concept``). Every occurrence on the board records the field it
came from (``path`` inside the scene), so a safe repair can edit exactly that field.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from ...schemas.screenplay import BoardItemKind, BoardScene, ChapterCardScene, QuizScene, Screenplay
from ..richlite import Span, tex_to_plain, tokenize
from .text import MAX_FIELD, MAX_SCENE_TEXT, MAX_TITLE, Lesson, clean_form, cut, group_key, term_ok

Path = tuple[Any, ...]

CASING_POSITIONS = frozenset({"keyword", "definition", "header", "heading"})
HEADING_POSITIONS = frozenset({"header", "heading", "label", "title", "concept"})  # Title Case is a convention here
VARIANT_POSITIONS = frozenset({"keyword", "definition", "header", "heading", "label"})

MAX_FORMS_PER_SCENE = 120
MAX_MARKERS = 40  # rich-lite marker characters per field read as markup (more: read as plain text)
LABEL_SPLIT = re.compile(r"\s{0,4}(?:=|:|→|←|↔|⇒|⟶|->|<-|=>|,|;|\||•)\s{0,4}")
CONTD = re.compile(r"\s{0,4}[(\[]?\s{0,4}\b(?:cont(?:d|inued)?\.?|part\s{1,4}\d{1,3})\s{0,4}[)\]]?\s{0,4}$", re.I)
TITLE_PREFIX = re.compile(r"^(?:what\s{1,4}(?:is|are)\s{1,4}(?:an?\s{1,4}|the\s{1,4})?|introduction\s{1,4}to\s{1,4}(?:the\s{1,4})?|"
                          r"intro\s{1,4}to\s{1,4}|understanding\s{1,4}|defining\s{1,4}|meet\s{1,4}(?:the\s{1,4})?)", re.I)
NOT_A_CONCEPT = re.compile(r"^(?:summary|recap|quiz|introduction|overview|conclusion|key\s{1,4}takeaways?|practice|"
                           r"let'?s\s{1,4}practi[cs]e|quick\s{1,4}check|check|review|example|worked\s{1,4}example|"
                           r"learning\s{1,4}objectives?|objectives?)\b", re.I)


@dataclass(frozen=True)
class Occurrence:
    form: str
    position: str
    scene: int | None  # scene index; None = the concept map
    path: Path | None  # field inside the scene (board / side panel) a repair may edit; None = not editable


@dataclass(frozen=True)
class BoardField:
    """A board-visible rich-lite field, read once: its spans, plain text and reading text (no code / maths)."""

    path: Path
    raw: str
    spans: tuple[Span, ...]

    @property
    def plain(self) -> str:
        return "".join(tex_to_plain(s.text) if s.kind == "math" else s.text for s in self.spans)

    @property
    def reading(self) -> str:
        return "".join(s.text for s in self.spans if s.kind not in ("code", "math"))


@dataclass
class SceneFacts:
    index: int
    scene: Any
    title_name: str | None
    visible: str  # what a learner reads (code and maths left out), bounded
    narration: str  # what the narrator says, bounded
    definitions: list[tuple[str, str]] = field(default_factory=list)  # (term, text) of definition items
    board: list[BoardField] = field(default_factory=list)  # board-visible fields in reading order

    @property
    def fields(self) -> list[tuple[Path, str]]:
        return [(f.path, f.raw) for f in self.board]


@dataclass
class Facts:
    lesson: Lesson
    scenes: list[SceneFacts]
    occurrences: list[Occurrence]
    groups: OrderedDict[str, OrderedDict[str, list[Occurrence]]]  # group key -> form -> occurrences

    @property
    def sp(self) -> Screenplay:
        return self.lesson.sp

    def explain_text(self, f: SceneFacts) -> str:
        """Visible text, narration and definitions of a scene (where an abbreviation may be spelled out)."""
        defs = " \n ".join(f"{t} \n {x}" for t, x in f.definitions)
        return " \n ".join(p for p in (f.visible, f.narration, defs) if p)


def spans_of(text: str) -> tuple[Span, ...]:
    """Rich-lite spans of a field cut to ``MAX_FIELD``. A field crowded with markup characters is read as plain
    text: real board items carry a few markers, and the rich-lite scan costs time per marker."""
    text = (text or "")[:MAX_FIELD]
    markers = text.count("[[") + text.count("*") + text.count("$") + text.count("`")
    if markers > MAX_MARKERS:
        return (Span("text", text),)
    return tuple(tokenize(text))


def plain_without_code(text: str) -> str:
    """Plain rich-lite text without ``code`` and ``$math$`` spans (identifiers and symbols are not words)."""
    return "".join(sp.text for sp in spans_of(text) if sp.kind not in ("code", "math"))


def plain(text: str) -> str:
    """Plain text of a (bounded) rich-lite string."""
    return re.sub(r"[ \t]+", " ", "".join(tex_to_plain(s.text) if s.kind == "math" else s.text for s in spans_of(text))).strip()


def title_name(title: str) -> str | None:
    """A title as a name ('What is photosynthesis?' names 'photosynthesis'), or None when it reads as a sentence."""
    t = CONTD.sub("", cut(plain(title or ""), MAX_TITLE))
    t = TITLE_PREFIX.sub("", t).strip(" ?!.")
    if not t or ":" in t or len(t.split()) > 4 or NOT_A_CONCEPT.match(t):
        return None
    return t


def _board_fields(scene: Any) -> list[BoardField]:
    raw: list[tuple[Path, str]] = []
    if isinstance(scene, BoardScene):
        for i, item in enumerate(scene.board):
            for name in ("text", "term", "caption", "justification"):
                value = getattr(item, name, None)
                if isinstance(value, str) and value.strip():
                    raw.append((("board", i, name), value))
            for j, h in enumerate(item.headers or []):
                if h.strip():
                    raw.append((("board", i, "headers", j), h))
            for r, row in enumerate(item.rows or []):
                for c, cell in enumerate(row):
                    if cell.strip():
                        raw.append((("board", i, "rows", r, c), cell))
    panel = scene.side_panel
    if panel is not None:
        if panel.title:
            raw.append((("side_panel", "title"), panel.title))
        if panel.chart is not None:
            for k, label in enumerate(panel.chart.labels):
                if label.strip():
                    raw.append((("side_panel", "chart", "labels", k), label))
    # rich-lite only on the board; panel titles and chart labels are plain text
    return [BoardField(path, value, spans_of(value) if path[0] == "board" else (Span("text", value[:MAX_FIELD]),))
            for path, value in raw]


def _visible(scene: Any, board: list[BoardField]) -> str:
    parts = [scene.title or "", scene.subtitle or ""]
    if isinstance(scene, ChapterCardScene):
        parts.append(scene.chapter_label)
    size = sum(len(p) for p in parts)
    for f in board:
        if size > MAX_SCENE_TEXT:
            break
        parts.append(f.reading)
        size += len(parts[-1])
    if isinstance(scene, QuizScene):
        parts += [scene.question, *scene.options, scene.explanation]
    if scene.side_panel is not None and scene.side_panel.quiz is not None:
        parts += [scene.side_panel.quiz.question, *scene.side_panel.quiz.options]
    text = " \n ".join(p for p in parts if p)
    return text[:MAX_SCENE_TEXT]


def _scene_occurrences(index: int, scene: Any, board: list[BoardField], name: str | None) -> list[Occurrence]:
    found: list[Occurrence] = []
    seen: set[tuple[str, str, Path | None]] = set()

    def add(value: str, position: str, path: Path | None) -> None:
        form = clean_form(value)
        if form and term_ok(form) and (form, position, path) not in seen and len(found) < MAX_FORMS_PER_SCENE:
            seen.add((form, position, path))
            found.append(Occurrence(form, position, index, path))

    items = scene.board if isinstance(scene, BoardScene) else []
    for f in board:
        path = f.path
        if path[0] == "board":
            item = items[path[1]]
            name_ = path[2]
            if name_ == "term":
                add(f.plain, "definition", path)
                continue
            if name_ == "text" and item.kind == BoardItemKind.heading:
                add(f.plain, "heading", path)
            if name_ == "headers":
                add(f.plain, "header", path)
            if name_ == "caption":
                for part in LABEL_SPLIT.split(f.plain[:MAX_TITLE]):
                    add(part, "label", path)
            for sp in f.spans:
                if sp.kind in ("keyword", "bold"):
                    add(sp.text, "keyword", path)
        else:  # side panel title and chart labels
            add(f.raw, "label", path)
    panel = scene.side_panel
    if panel is not None and panel.chart is not None:
        for value in (panel.chart.x_label, panel.chart.y_label):
            if value:
                add(value, "label", None)
    if name:
        add(name, "title", None)
    return found


def extract(screenplay: Screenplay) -> Facts:
    """Every scene's facts and the lesson's term groups (concept-map titles first)."""
    lesson = Lesson(screenplay)
    scenes: list[SceneFacts] = []
    occurrences: list[Occurrence] = []
    for c in screenplay.concept_map:
        form = clean_form(c.title)
        if form and term_ok(form):
            occurrences.append(Occurrence(form, "concept", None, None))
    for index, scene in enumerate(screenplay.scenes):
        board = _board_fields(scene)
        name = title_name(scene.title) if scene.type != "quiz_checkpoint" else None
        narration = " ".join(b.narration for b in scene.all_beats())[:MAX_SCENE_TEXT]
        defs = []
        if isinstance(scene, BoardScene):
            defs = [(plain(i.term or "")[:240], plain_without_code(i.text)[:600])
                    for i in scene.board if i.kind == BoardItemKind.definition]
        facts = SceneFacts(index=index, scene=scene, title_name=name, visible=_visible(scene, board),
                           narration=narration, definitions=defs, board=board)
        scenes.append(facts)
        occurrences.extend(_scene_occurrences(index, scene, board, name))
    groups: OrderedDict[str, OrderedDict[str, list[Occurrence]]] = OrderedDict()
    for occ in occurrences:
        groups.setdefault(group_key(occ.form), OrderedDict()).setdefault(occ.form, []).append(occ)
    return Facts(lesson=lesson, scenes=scenes, occurrences=occurrences, groups=groups)


def scenes_of(occurrences: list[Occurrence], positions: frozenset[str] | None = None) -> list[int]:
    return sorted({o.scene for o in occurrences if o.scene is not None and (positions is None or o.position in positions)})
