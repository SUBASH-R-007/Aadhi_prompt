"""Phase 20 — the lesson screenplay contract, the test servers' stand-in lesson writer, and traceability.

  parse_screenplay(text)   the JSON in a model's answer, read as /generate-script reads it (a code fence tolerated)
  check_screenplay(value)  the lesson in the page's format (what runGeneration accepts after /generate-script: a JSON object
                           with a non-empty `scenes` list, or a bare list of scenes), normalized and bounded:
                           {subject_name, unit_name, session_number, session_title, concept_map, scenes, companion_sheet};
                           MalformedScreenplay otherwise
  stand_in(text, names)    TEST SERVERS ONLY (AI_FAKE_PROVIDER=1): a deterministic lesson in the page's format, built only
                           from the source text (raw / pasted text, or the Phase 11 lesson input). It never adds a fact:
                           every fact it shows or says is a sentence, list item, formula, code line, table cell or question
                           of the source; only connecting words ("Look at the code on the board") are its own
  trace(scenes, source)    scene.source = {origin: "source" | "ai", refs: [line numbers or block ids], coverage: 0..1} by
                           deterministic word overlap with the source (any writer: the stand-in or a real model). A scene
                           is "source" only when nearly all its content words are in the source; never a source for
                           text that is not in it

The source text is untrusted data: nothing in it is followed as an instruction (the Phase 11 header that tells a model how
to read the prepared input, the known-gaps and accepted-improvements lists are not lesson content and are skipped).
"""
import hashlib
import html
import json
import re
import textwrap

import editor_api
import source_analysis as A

SCENE_TYPES = ("title", "content", "simulation", "visual", "example", "key-takeaway", "summary", "ai_video",
               "p5_simulation", "quiz_checkpoint", "chapter_card", "recap")  # the types the page plays
MAX_SCENES = editor_api.MAX_SCENES
NAME_LIMITS = {"subject_name": 255, "unit_name": 255, "session_number": 50, "session_title": 255}  # the lesson row's columns
TEXT_LIMITS = {**editor_api.SCENE_TEXT_LIMITS, "reveal_narration": 50000, "question": 2000, "explanation": 5000,
               "prompt": 5000, "chapter_label": 200, "visual_reasoning": 20000}
MAX_LESSON_CHARS = editor_api.MAX_BODY
MAX_CONCEPTS = 100
MAX_COMPANION = 200_000
# what the app adds to a scene later (plans, review decisions, editor data, library media, the trace): never taken from a
# model's answer, so a written lesson cannot claim a decision, an approval or a file nobody made
APP_KEYS = frozenset(("edit", "visual_plan", "visual_review", "presenter_plan", "cinematic_plan", "visual_direction", "source",
                      "video_asset_id", "manim_asset_id", "uploaded_image_assets", "video_url", "manim_video_url", "quality"))
PANEL_APP_KEYS = ("video_asset_id", "video_url")


class MalformedScreenplay(A.MalformedOutput):
    """A model's answer that is not a lesson in the page's format."""


# ---- reading a model's answer ---------------------------------------------------------------------------------------


QUANTIFIERS = {"every", "all", "some", "any", "most", "many", "no", "such", "another", "other", "several"}


def _defined_term(sentence, title, found=None):
    """The term a sentence defines (the one Phase 11's analysis found, else its definition finder), without statements about
    "every …" / "all …" (a claim, not a definition: the real video showed "Every green plant" as a defined term and a quiz
    choice)."""
    term = found or A.definition_in(sentence, title)
    return None if term and term.split()[0].lower() in QUANTIFIERS else term

def parse_screenplay(text):
    """The first JSON value in a model's answer (as /generate-script: a ```json fence is tolerated); MalformedScreenplay
    otherwise."""
    if not isinstance(text, str) or not text.strip():
        raise MalformedScreenplay("empty answer")
    raw = text.strip()
    candidates = [raw]
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.S)
    if fence:
        candidates.append(fence.group(1).strip())
    error = "no JSON value"
    for candidate in candidates:
        start = min([i for i in (candidate.find("{"), candidate.find("[")) if i >= 0], default=-1)
        if start < 0:
            continue
        try:
            value, _end = json.JSONDecoder().raw_decode(candidate[start:])
            return value
        except ValueError as e:
            error = str(e)[:160]
    raise MalformedScreenplay(f"not valid JSON ({error})")


def _name(value, limit, default=""):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    text = re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""
    return text[:limit] or default


def _concepts(value):
    if not isinstance(value, list):
        return None
    out = []
    for node in value[:MAX_CONCEPTS]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), (str, int)) or isinstance(node.get("id"), bool):
            continue
        depends = node.get("depends_on") if isinstance(node.get("depends_on"), list) else []
        out.append({"id": str(node["id"])[:80], "title": _name(node.get("title"), 200),
                    "depends_on": [str(d)[:80] for d in depends[:20] if isinstance(d, (str, int)) and not isinstance(d, bool)]})
    return out


def _scene(raw):
    if not isinstance(raw, dict):
        raise MalformedScreenplay("every scene must be an object")
    scene = {k: v for k, v in raw.items() if isinstance(k, str) and k not in APP_KEYS}
    scene["type"] = scene.get("type") if scene.get("type") in SCENE_TYPES else "content"  # an unknown type plays as content
    for field, limit in TEXT_LIMITS.items():
        if field not in scene:
            continue
        value = scene[field]
        if value is None:
            scene[field] = ""
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            scene[field] = str(value)
        elif not isinstance(value, str):
            raise MalformedScreenplay(f"a scene's {field} is not text")
        if len(scene[field]) > limit:
            raise MalformedScreenplay(f"a scene's {field} is too long")
    if "side_panel" in scene:
        if isinstance(scene["side_panel"], dict):
            scene["side_panel"] = {k: v for k, v in scene["side_panel"].items() if k not in PANEL_APP_KEYS}
        else:
            del scene["side_panel"]
    sid = scene.get("scene_id")
    if sid is not None and not (isinstance(sid, str) and editor_api.SCENE_ID.fullmatch(sid)):
        del scene["scene_id"]  # a valid, unique id is given to every scene when the lesson is saved
    if scene["type"] == "quiz_checkpoint":
        options = scene.get("options")
        if not (isinstance(options, list) and 2 <= len(options) <= 8 and all(isinstance(o, str) and len(o) <= 1000 for o in options)
                and isinstance(scene.get("question"), str) and scene["question"].strip()):
            scene["type"] = "content"  # a checkpoint the player cannot ask is kept as a content scene (its narration stays)
        else:
            index = scene.get("correct_index")
            if not (isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(options)):
                scene["correct_index"] = 0
            feedback = scene.get("feedback_wrong")
            if feedback is not None and not (isinstance(feedback, list) and len(feedback) == len(options)
                                             and all(isinstance(f, str) and len(f) <= 2000 for f in feedback)):
                del scene["feedback_wrong"]
            seconds = scene.get("countdown_seconds")
            if seconds is not None and not (isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and 1 <= seconds <= 60):
                del scene["countdown_seconds"]
    return scene


def check_screenplay(value):
    """The lesson a model wrote, normalized to the page's format and bounded (1..200 scenes, known scene types, bounded
    texts, finite numbers only); MalformedScreenplay when it is not a lesson the page could play."""
    if isinstance(value, list):
        data, scenes = {}, value
    elif isinstance(value, dict) and isinstance(value.get("scenes"), list):
        data, scenes = value, value["scenes"]
    else:
        raise MalformedScreenplay("the answer has no list of scenes")
    if not scenes:
        raise MalformedScreenplay("the lesson has no scenes")
    if len(scenes) > MAX_SCENES:
        raise MalformedScreenplay(f"the lesson has more than {MAX_SCENES} scenes")
    try:
        text = json.dumps(scenes, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise MalformedScreenplay("the lesson contains numbers or values that cannot be saved") from None
    if len(text) > MAX_LESSON_CHARS:
        raise MalformedScreenplay("the lesson is too large")
    out = {key: _name(data.get(key), limit) for key, limit in NAME_LIMITS.items()}
    out["subject_name"] = out["subject_name"] or "Subject Overview"  # the page's own defaults for a lesson without them
    out["session_number"] = out["session_number"] or "Session 1"
    out["concept_map"] = _concepts(data.get("concept_map"))
    sheet = data.get("companion_sheet")
    out["companion_sheet"] = sheet if isinstance(sheet, str) and len(sheet) <= MAX_COMPANION else None
    try:
        json.dumps([out["concept_map"], out["companion_sheet"]], allow_nan=False)
    except ValueError:
        raise MalformedScreenplay("the lesson contains numbers that cannot be saved") from None
    out["scenes"] = [_scene(s) for s in json.loads(text)]
    return out


# ---- reading a source -----------------------------------------------------------------------------------------------

MAX_LINES = 30000
PREPARED = "AADHI-READY SOURCE"
META = re.compile(r"^(?:AADHI-READY SOURCE\b|How to use this input:|Language of the source:|(?:Audience|Difficulty):)")
SKIPPED = re.compile(r"^(?:IMPROVEMENTS THE TEACHER ACCEPTED|KNOWN GAPS IN THE SOURCE)\b")  # not lesson content
TAG = re.compile(r"\s*\[(?:SOURCE|USER EDITED|USER|AI SUGGESTION(?:, ACCEPTED)?|ANALYSIS)\b[^\]]*\]")
UNEXPLAINED = re.compile(r"\s*\(symbols not explained in the source:[^)]*\)\s*$")  # the analysis' note, not the source
LABEL = re.compile(r"^(?P<label>Explanation|Formula|Example|Important|Expected output|Definition of (?P<term>.{1,80}?))\s*:\s*(?P<text>.*)$")
SECTION = re.compile(r"^SECTION\s+\d+\s*:\s*(?P<title>.+)$")
SUBTOPIC = re.compile(r"^Subtopic\s+[\d.]+\s*:\s*(?P<title>.+)$")
HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*$")
NUMBERED = re.compile(r"^(?P<n>\d{1,2})[.)]\s+(?P<text>\S.*)$")
BULLET = re.compile(r"^[-*•]\s+(?P<text>\S.*)$")
FENCE = re.compile(r"^```\s*(?P<lang>[\w+#.-]*)\s*$")
CODE_LABEL = re.compile(r"^Code\s*\((?P<lang>[^)]*)\)\s*:\s*$")
PAGE_MARK = re.compile(r"^-{2,}\s*Page\s+\d+\s*-{2,}$", re.I)
VISUAL = re.compile(r"^Visual in the source(?:\s*\([^)]*\))?\s*:\s*(?P<kind>[\w ]+?)(?:\s+-\s+(?P<text>.+))?$")
# a figure the text mentions ("Diagram: a leaf cross section", "Figure 2. The water cycle"): a number or a colon after the word
FIGURE = re.compile(r"^(?P<kind>fig(?:ure)?\.?|diagram|image|illustration|picture|photo|chart|graph)\s*"
                    r"(?:(?P<num>\d+(?:\.\d+)?)\s*[:.\-–—]|:)\s*(?P<text>\S.*)$", re.I)
QUESTION = re.compile(r"^(?:Question|Q)\s*\d*\s*[:.]\s*(?P<q>.+?)(?:\s+(?:Answer|Ans\.?|A)\s*:\s*(?P<a>.+))?$")
ANSWER = re.compile(r"^(?:Answer|Ans\.?|A)\s*:\s*(?P<a>.+)$")
DISPLAY_MATH = re.compile(r"^(?:\$\$.+\$\$|\\\[.+\\\])$", re.S)
SUMMARY_TITLES = re.compile(r"^(?:summary|conclusion|key (?:points|takeaways|ideas)|recap|in summary)\b", re.I)
OBJECTIVE_TITLES = re.compile(r"^(?:learning )?(?:objectives|outcomes|goals)\b", re.I)
TABLE_SEP = re.compile(r"^:?-{3,}:?$")


def _clean_line(line):
    """A line of the prepared input without its provenance tags and the analysis' notes."""
    return UNEXPLAINED.sub("", TAG.sub("", line)).strip()


def _table_rows(cells_rows):
    rows = [r for r in cells_rows if r and not all(TABLE_SEP.match(c) for c in r if c)]
    width = max((len(r) for r in rows), default=0)
    if len(rows) < 2 or width < 2:
        return None
    return [r + [""] * (width - len(r)) for r in rows[:21]]


def _split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _flat_table(line):
    """A Markdown table that a document extractor joined into one line ("| a | b | | --- | --- | | 1 | 2 |"), else None."""
    if line.count("|") < 6 or not re.search(r"\|\s*:?-{3,}:?\s*\|", line):
        return None
    cells = _split_row(line)
    first_dash = next((i for i, c in enumerate(cells) if TABLE_SEP.match(c)), None)
    if not first_dash or cells[first_dash - 1] != "":
        return None
    width = first_dash - 1  # header cells, then the empty cell where two rows were joined
    rows, i = [cells[:width]], width
    while i < len(cells):
        if cells[i] != "" or i + 1 + width > len(cells):
            return None
        rows.append(cells[i + 1:i + 1 + width])
        i += 1 + width
    return _table_rows(rows)


def _plain_heading(lines, i):
    """A heading in plain text: a short line on its own (blank line or start before it), no sentence punctuation at the end."""
    line = lines[i].strip()
    words = line.split()
    if not (1 <= len(words) <= 8) or len(line) > 80 or line[-1] in ".,;:!" or not (line[0].isupper() or line[0].isdigit()):
        return False
    if i > 0 and lines[i - 1].strip():
        return False
    after = lines[i + 1].strip() if i + 1 < len(lines) else ""
    return bool(after) or line.isupper()


def read_source(text):
    """The source as a document: {title, subject, objectives, sections: [{title, line, items}], summary, questions}. Items:
    {kind: paragraph | definition | step | bullet | formula | code | table | image | qa | example | important, line, ...}."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")[:MAX_LINES]
    prepared = lines[0].strip().startswith(PREPARED) if lines else False
    doc = {"title": None, "subject": None, "objectives": [], "sections": [], "summary": [], "questions": []}
    state = {"section": None, "mode": None, "buffer": [], "buffer_line": None, "code_lang": None}

    def section():
        if state["section"] is None:
            state["section"] = {"title": None, "line": None, "items": [], "definitions": {}}
            doc["sections"].append(state["section"])
        return state["section"]

    def add(item):
        mode = state["mode"]
        if mode == "summary" and item["kind"] in ("paragraph", "bullet", "step"):
            doc["summary"].extend({"text": s, "line": item["line"]} for s in A.sentences(item["text"]))
        elif mode == "objectives" and item["kind"] in ("bullet", "step", "paragraph"):
            doc["objectives"].append({"text": item["text"], "line": item["line"]})
        elif mode == "assessment" and item["kind"] in ("bullet", "step", "paragraph"):
            doc["questions"].append({"q": item["text"], "a": None, "line": item["line"]})
        elif mode in ("skip", "prerequisites"):
            return
        else:
            if item["kind"] == "qa":
                doc["questions"].append({"q": item["q"], "a": item["a"], "line": item["line"]})
            section()["items"].append(item)

    def flush():
        if state["buffer"]:
            add({"kind": "paragraph", "text": " ".join(state["buffer"]), "line": state["buffer_line"]})
            state["buffer"], state["buffer_line"] = [], None

    def start_section(title, n):
        flush()
        state["mode"] = "summary" if SUMMARY_TITLES.match(title) else "objectives" if OBJECTIVE_TITLES.match(title) else None
        if state["mode"] is None:
            state["section"] = {"title": title[:200], "line": n, "items": [], "definitions": {}}
            doc["sections"].append(state["section"])

    def text_item(body, n):
        """One line of text: a list item, a formula, a table, a question, a figure, or text of a paragraph."""
        qa = QUESTION.match(body)
        if qa:
            flush()
            answer = qa.group("a")
            if answer is None and n < len(lines):  # the answer on the next line
                following = ANSWER.match(_clean_line(lines[n]) if prepared else lines[n].strip())
                if following:
                    answer = following.group("a")
                    state["skip"] = n + 1
            add({"kind": "qa", "q": qa.group("q").strip(), "a": (answer or "").strip() or None, "line": n})
            return
        if ANSWER.match(body):
            flush()
            return  # an answer whose question was not found: kept out of the lesson's teaching text
        step, bullet = NUMBERED.match(body), BULLET.match(body)
        if step:
            flush()
            add({"kind": "step", "n": int(step.group("n")), "text": step.group("text").strip(), "line": n})
            return
        if bullet:
            flush()
            add({"kind": "bullet", "text": bullet.group("text").strip(), "line": n})
            return
        if DISPLAY_MATH.match(body):
            flush()
            add({"kind": "formula", "tex": body, "line": n})
            return
        table = _flat_table(body)
        if table:
            flush()
            add({"kind": "table", "rows": table, "line": n})
            return
        figure = FIGURE.match(body)
        if figure:
            flush()
            add({"kind": "image", "type": "diagram" if figure.group("kind").lower() in ("diagram", "chart", "graph") else "image",
                 "text": figure.group("text").strip(), "line": n})
            return
        if prepared:
            add({"kind": "paragraph", "text": body, "line": n})
        else:
            state["buffer"].append(body)
            state["buffer_line"] = state["buffer_line"] or n

    i = 0
    while i < len(lines):
        n = i + 1
        raw = lines[i]
        line = _clean_line(raw) if prepared else raw.strip()
        if state.get("skip") == n:
            i += 1
            continue
        fence = FENCE.match(line)
        if fence:  # fenced code, kept exactly (indentation included)
            flush()
            body, j = [], i + 1
            while j < len(lines) and not FENCE.match(lines[j].strip()):
                body.append(lines[j])
                j += 1
            code = textwrap.dedent("\n".join(body)).strip("\n")
            lang = fence.group("lang") or state["code_lang"]
            state["code_lang"] = None
            if code.strip():
                add({"kind": "code", "code": code, "lang": A.code_language(code, lang or None) or "plaintext", "line": n})
            i = j + 1
            continue
        if not line:
            flush()
            i += 1
            continue
        if META.match(line) or PAGE_MARK.match(line):
            i += 1
            continue
        if SKIPPED.match(line):
            flush()
            state["mode"] = "skip"
            i += 1
            continue
        if prepared:
            m = re.match(r"^(Title|Subject):\s*(.+)$", line)
            if m and not state["section"] and state["mode"] is None:
                value = m.group(2).strip()
                if value.lower() != "not provided":
                    doc["title" if m.group(1) == "Title" else "subject"] = value
                i += 1
                continue
            if line.startswith("Prerequisites"):
                state["mode"] = "prerequisites"
                i += 1
                continue
            if line.startswith("Learning objectives"):
                state["mode"] = "objectives"
                i += 1
                continue
            if line == "SUMMARY:":
                flush()
                state["mode"] = "summary"
                i += 1
                continue
            if line.startswith("ASSESSMENT MATERIAL FROM THE SOURCE"):
                flush()
                state["mode"] = "assessment"
                i += 1
                continue
            m = SECTION.match(line)
            if m:
                state["mode"] = None
                start_section(m.group("title").strip(), n)
                i += 1
                continue
            m = SUBTOPIC.match(line)
            if m:
                add({"kind": "subheading", "text": m.group("title").strip(), "line": n})
                i += 1
                continue
            m = CODE_LABEL.match(line)
            if m:
                lang = m.group("lang").strip().lower()
                state["code_lang"] = None if "not stated" in lang else lang
                i += 1
                continue
            m = VISUAL.match(line)
            if m:
                described = (m.group("text") or "").strip()
                figure = FIGURE.match(described)
                described = figure.group("text").strip() if figure else described
                kind = m.group("kind").strip().lower()
                if described and state["mode"] not in ("skip", "prerequisites"):
                    add({"kind": "image", "type": "diagram" if kind in ("diagram", "chart", "graph") else "image", "text": described, "line": n})
                i += 1
                continue
            m = LABEL.match(line)
            if m:
                body = m.group("text").strip()
                label = m.group("label")
                if m.group("term"):
                    if state["mode"] is None and body:
                        section()["definitions"][body] = m.group("term").strip()
                        add({"kind": "definition", "term": m.group("term").strip(), "text": body, "line": n})
                elif label == "Example" and body:
                    add({"kind": "example", "text": body, "line": n})
                elif label == "Important" and body:
                    add({"kind": "important", "text": body, "line": n})
                elif label == "Expected output" and body:
                    add({"kind": "paragraph", "text": f"Expected output: {body}", "line": n})
                elif body:
                    text_item(body, n)
                i += 1
                continue
            if state["mode"] in ("summary", "objectives", "assessment") and BULLET.match(line):
                add({"kind": "bullet", "text": BULLET.match(line).group("text").strip(), "line": n})
                i += 1
                continue
            text_item(line, n)
            i += 1
            continue
        # raw text: Markdown headings, plain headings, tables, indented code, paragraphs
        m = HEADING.match(line)
        if m:
            title = m.group("title").strip()
            if len(m.group("hashes")) == 1 and doc["title"] is None and not doc["sections"]:
                flush()
                doc["title"] = title[:200]
            else:
                start_section(title, n)
            i += 1
            continue
        if line.startswith("|") and line.endswith("|") and line.count("|") >= 3 and _flat_table(line) is None:
            flush()
            rows, j = [], i
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_split_row(lines[j]))
                j += 1
            table = _table_rows(rows)
            if table:
                add({"kind": "table", "rows": table, "line": n})
                i = j
                continue
        if raw[:1] in (" ", "\t") and (i == 0 or not lines[i - 1].strip()) and raw.startswith(("    ", "\t")):
            j = i
            while j < len(lines) and (lines[j].startswith(("    ", "\t")) or not lines[j].strip()):
                j += 1
            block = textwrap.dedent("\n".join(lines[i:j])).strip("\n")
            if A.looks_like_code(block):
                flush()
                add({"kind": "code", "code": block, "lang": A.code_language(block) or "plaintext", "line": n})
                i = j
                continue
        if line == "$$":  # a display formula over several lines
            j = i + 1
            while j < len(lines) and lines[j].strip() != "$$":
                j += 1
            flush()
            add({"kind": "formula", "tex": "$$" + " ".join(x.strip() for x in lines[i + 1:j]) + "$$", "line": n})
            i = j + 1
            continue
        if _plain_heading(lines, i) and not NUMBERED.match(line) and not BULLET.match(line) and not QUESTION.match(line) \
                and not FIGURE.match(line):
            if doc["title"] is None and not doc["sections"] and not state["buffer"]:
                doc["title"] = line[:200]
            else:
                start_section(line, n)
            i += 1
            continue
        text_item(line, n)
        i += 1
    flush()
    doc["sections"] = [s for s in doc["sections"] if s["items"]]
    return doc


# ---- the stand-in lesson writer (test servers only) ------------------------------------------------------------------

MAX_STAND_IN_SCENES = 150
UNITS_PER_SCENE = 4          # the page's chunking rule: at most 3-4 points on a board
MAX_SECTIONS = 60
MAX_SECTION_UNITS = 40
MAX_TAKEAWAYS = 8
INLINE_MATH = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)


def _esc(text):
    return html.escape(text, quote=False)


def _say_math(tex):
    """A formula in words the narrator can say (symbols as words; nothing added)."""
    t = tex.strip().strip("$")
    t = re.sub(r"^\\\[|\\\]$|^\\\(|\\\)$", "", t)
    for _ in range(3):
        t = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"\1 over \2", t)
        t = re.sub(r"\\sqrt\{([^{}]*)\}", r"square root of \1", t)
    words = [(r"\\(?:times|cdot)", " times "), (r"\\div", " divided by "), (r"\\(?:rightarrow|to|longrightarrow)|->|→", " gives "),
             (r"\\(?:leq|le)|≤", " less than or equal to "), (r"\\(?:geq|ge)|≥", " greater than or equal to "),
             (r"\^\{?2\}?", " squared "), (r"\^\{?3\}?", " cubed "), (r"\^\{([^{}]*)\}|\^(\w)", r" to the power \1\2 "),
             (r"=", " equals "), (r"\+", " plus "), (r"(?<=\s)-(?=\s)", " minus "), (r"/", " divided by "), (r"\\([A-Za-z]+)", r" \1 "),
             (r"[_{}]", " ")]
    for pattern, replacement in words:
        t = re.sub(pattern, replacement, t)
    return re.sub(r"\s+", " ", t).strip()


def _spoken(text):
    """Narration text: plain words for text to speech (no Markdown marks; formulas said in words)."""
    text = INLINE_MATH.sub(lambda m: _say_math(next(g for g in m.groups() if g is not None)), text)
    text = re.sub(r"[*_`#]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _sentence(text):
    text = text.strip()
    return text if not text or text[-1] in ".!?…:" else text + "."


def _highlight(sentence, term):
    escaped = _esc(sentence)
    if term:
        pattern = re.compile(re.escape(_esc(term)), re.I)
        escaped = pattern.sub(lambda m: f'<span class="keyword">{m.group(0)}</span>', escaped, count=1)
    return escaped


def _blocks(section):
    """A section's board blocks: {html, says: [(text, pause, sync)], units, kind}. Every revealed element has one [SYNC]."""
    blocks = []
    definitions = section["definitions"]
    defined_text = " ".join(it["text"] for it in section["items"] if it["kind"] == "paragraph")
    items = section["items"]
    i = 0
    while i < len(items):
        it = items[i]
        kind = it["kind"]
        if kind in ("step", "bullet"):
            group = [it]
            while i + 1 < len(items) and items[i + 1]["kind"] == kind:
                i += 1
                group.append(items[i])
            if kind == "step" and len({g["n"] for g in group}) == len(group):
                group.sort(key=lambda g: g["n"])  # the source's own numbering (an analysis may list a step out of place)
            for start in range(0, len(group), UNITS_PER_SCENE):
                part = group[start:start + UNITS_PER_SCENE]
                tag = "ol" if kind == "step" else "ul"
                attrs = " class='process-list'" if kind == "step" else ""
                if kind == "step" and start:
                    attrs += f" start='{start + 1}'"
                blocks.append({"html": f"<{tag}{attrs}>" + "".join(f"<li>{_esc(g['text'])}</li>" for g in part) + f"</{tag}>",
                               "says": [(_sentence(g["text"]), False, True) for g in part], "units": len(part), "kind": kind})
        elif kind == "paragraph":
            for s in A.sentences(it["text"]):
                term = _defined_term(s, section["title"], definitions.get(s))
                if term:
                    blocks.append({"html": f"<div class='definition'><strong>Definition:</strong> {_highlight(s, term)}</div>",
                                   "says": [(s, True, True)], "units": 1, "kind": "definition"})
                else:
                    blocks.append({"html": f"<p>{_esc(s)}</p>", "says": [(s, False, True)], "units": 1, "kind": "text"})
        elif kind == "definition":
            if it["text"] in defined_text:  # (the prepared input lists a definition and its paragraph; shown once)
                pass
            elif _defined_term(it["text"], section["title"], it["term"]):
                blocks.append({"html": f"<div class='definition'><strong>Definition:</strong> {_highlight(it['text'], it['term'])}</div>",
                               "says": [(it["text"], True, True)], "units": 1, "kind": "definition"})
            else:  # a claim about "every …" / "all …": said as it is, not boxed as a definition
                blocks.append({"html": f"<p>{_esc(it['text'])}</p>", "says": [(it["text"], False, True)], "units": 1, "kind": "text"})
        elif kind == "formula":
            blocks.append({"html": f"<div class='formula-block'>{_esc(it['tex'])}</div>", "says": [(_sentence(_say_math(it["tex"])), True, True)],
                           "units": 1, "kind": "formula"})
        elif kind == "code":
            lang = re.sub(r"[^a-z0-9+#-]", "", it["lang"].lower())[:20] or "plaintext"
            blocks.append({"html": f'<pre><code class="language-{lang}">{_esc(it["code"])}</code></pre>',
                           "says": [("Look at the code on the board.", True, True)], "units": 2, "kind": "code"})
        elif kind == "table":
            head, rows = it["rows"][0], it["rows"][1:]
            table = ("<table><thead><tr>" + "".join(f"<th>{_esc(h)}</th>" for h in head) + "</tr></thead><tbody>"
                     + "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")
            said = [(_sentence(", ".join(h for h in head if h)), False, False)]
            said += [(_sentence(f"{r[0]}: " + "; ".join(f"{h} {c}".strip() for h, c in zip(head[1:], r[1:]) if c)), False, False) for r in rows[:8]]
            blocks.append({"html": table, "says": said, "units": UNITS_PER_SCENE, "kind": "table"})
        elif kind in ("example", "important"):
            css = "tip-callout" if kind == "example" else "info-callout"
            blocks.append({"html": f"<div class='{css}'>{_esc(it['text'])}</div>", "says": [(_sentence(it["text"]), False, True)],
                           "units": 1, "kind": kind})
        elif kind == "subheading":
            blocks.append({"html": f"<h2 class='section-heading'>{_esc(it['text'])}</h2>", "says": [(_sentence(it["text"]), False, True)],
                           "units": 1, "kind": "subheading"})
        i += 1
    return blocks


def _narration(blocks, lead=None):
    parts = [lead] if lead else []
    for block in blocks:
        for text, pause, sync in block["says"]:
            spoken = _spoken(text)
            if spoken:
                parts.append(("[SYNC] " if sync else "") + spoken + (" [PAUSE]" if pause else ""))
    return " ".join(parts)[:12000]


def _keywords(text):
    return list(dict.fromkeys(w for w in re.findall(r"[a-z][a-z\-]{2,}", text.lower()) if w not in STOPWORDS))[:6]


def _quiz(qa, concept_id, distractors):
    """A checkpoint from a question and its answer in the source; the other options are other answers or terms of the
    same source (each with what the source says about it)."""
    correct = qa["a"].strip()  # the source's own words
    pool, seen = [], {correct.lower().rstrip(".")}
    for text, why in distractors:
        key = text.lower().rstrip(".")
        if key and key not in seen:
            seen.add(key)
            pool.append((text, why))
        if len(pool) == 3:
            break
    if not pool:
        return None
    at = int(hashlib.sha256(qa["q"].encode("utf-8")).hexdigest(), 16) % (len(pool) + 1)  # deterministic place of the answer
    options = [p[0] for p in pool]
    feedback = [p[1] for p in pool]
    options.insert(at, correct)
    feedback.insert(at, "")
    question = qa["q"].strip()
    explanation = qa.get("why") or correct
    return {"type": "quiz_checkpoint", "concept_id": concept_id, "title": "Checkpoint", "question": question, "options": options,
            "correct_index": at, "feedback_wrong": feedback, "explanation": explanation, "countdown_seconds": 8,
            "narration": f"{_spoken(question)} [PAUSE] Pause the video here and try it yourself!",
            "reveal_narration": f"The answer is {_sentence(_spoken(correct))} {_spoken(explanation) if explanation != correct else ''}".strip(),
            "aadhi_position": "hidden"}


def stand_in(text, names=None):
    """TEST SERVERS ONLY (AI_FAKE_PROVIDER=1, never a real provider): a lesson in the page's exact format, built only from
    the source text — a title scene, the learning objectives when given, one or more content scenes per section (its
    sentences on the board and in the narration with [SYNC] / [PAUSE]; definitions highlighted; numbered steps as a process
    list; formulas kept for MathJax; code as <pre><code class="language-…">; a table as a comparison table; a figure
    mentioned in the text as an image side panel whose prompt and keywords are that text), a presenter-led scene for a
    section of explanation only, a checkpoint from the source's own question and answer, the key takeaways (the section
    titles) and the source's summary. Deterministic: the same text gives the same lesson."""
    names = names if isinstance(names, dict) else {}
    doc = read_source(text)
    sections = doc["sections"][:MAX_SECTIONS]
    if not sections and not doc["summary"]:
        raise MalformedScreenplay("the source has no content to teach")
    title = doc["title"] or _name(names.get("session_title"), 255) or (sections[0]["title"] if sections else None) or "Lesson"
    for s in sections:
        s["title"] = s["title"] or title
    teaching = [s for s in sections if any(it["kind"] != "qa" for it in s["items"])]
    concepts = []
    for s in teaching:
        s["concept_id"] = f"c{len(concepts) + 1}"
        concepts.append({"id": s["concept_id"], "title": s["title"][:60], "depends_on": [concepts[-1]["id"]] if concepts else []})
    if not concepts:
        concepts.append({"id": "c1", "title": title[:60], "depends_on": []})
    first, last = concepts[0]["id"], concepts[-1]["id"]

    # what the source defines (the terms are the checkpoint's other options), and the sentence that best explains an answer
    defined = []
    all_sentences = []
    for s in sections:
        for it in s["items"]:
            if it["kind"] == "paragraph":
                for sentence in A.sentences(it["text"]):
                    all_sentences.append(sentence)
                    term = _defined_term(sentence, s["title"], s["definitions"].get(sentence))
                    if term and term.lower() not in {d[0].lower() for d in defined}:
                        defined.append((term, sentence))
            elif it["kind"] == "definition" and _defined_term(it["text"], s["title"], it["term"]) \
                    and it["term"].lower() not in {d[0].lower() for d in defined}:
                defined.append((it["term"], it["text"]))
    questions = [q for q in doc["questions"] if q["a"]]

    def why(qa):
        wanted = _words(qa["a"])
        context = _words(qa["q"]) | wanted
        best = max((sentence for sentence in all_sentences if wanted and wanted <= _words(sentence) and not QUESTION.match(sentence)),
                   key=lambda sentence: (len(_words(sentence) & context), -len(sentence)), default=None)
        return best

    def checkpoint(qa, concept_id):
        others = [(o["a"].strip(), f"That answers another question: {o['q']}") for o in questions if o is not qa]
        others += [(term[:1].upper() + term[1:], sentence) for term, sentence in defined]
        return _quiz({**qa, "why": why(qa)}, concept_id, others)

    scenes = [{"type": "title", "concept_id": first, "title": title, "subtitle": doc["subject"] or "",
               "narration": f"Welcome! [PAUSE] Today's session: {_sentence(_spoken(title))}", "aadhi_position": "center"}]
    if doc["objectives"]:
        items = doc["objectives"][:6]
        scenes.append({"type": "content", "concept_id": first, "title": "Learning objectives",
                       "html": "<ul>" + "".join(f"<li>{_esc(o['text'])}</li>" for o in items) + "</ul>",
                       "narration": " ".join(f"[SYNC] {_sentence(_spoken(o['text']))}" for o in items),
                       "side_panel": {"type": "skill_tree"}, "aadhi_position": "right"})
    asked, concept_id = set(), first
    for s in sections:
        concept_id = s.get("concept_id") or concept_id  # a questions-only section belongs to the concept before it
        blocks = _blocks(s)[:MAX_SECTION_UNITS]
        images = [it for it in s["items"] if it["kind"] == "image"]
        prose_only = bool(blocks) and all(b["kind"] == "text" for b in blocks) and not images
        chunks, current, units = [], [], 0
        for block in blocks:
            if current and units + block["units"] > UNITS_PER_SCENE:
                chunks.append(current)
                current, units = [], 0
            current.append(block)
            units += block["units"]
        if current:
            chunks.append(current)
        for k, chunk in enumerate(chunks):
            kinds = {b["kind"] for b in chunk}
            scene = {"type": "example" if re.search(r"\bexamples?\b", s["title"], re.I) else "content", "concept_id": concept_id,
                     "title": s["title"] + (" (Contd.)" if k else ""), "html": "".join(b["html"] for b in chunk),
                     "side_panel": {"type": "skill_tree"}, "aadhi_position": "right"}
            lead = None
            if k == 0 and images:  # a figure the source mentions: an image panel that shows what the text describes
                image = images[0]
                scene["side_panel"] = {"type": "image", "prompt": image["text"]}
                scene["visual"] = {"concept": s["title"][:200], "description": image["text"][:500], "keywords": _keywords(image["text"]),
                                   "type": image["type"]}
                lead = f"Look at the {image['type']} on the side: {_sentence(_spoken(image['text']))}"
            if "formula" in kinds:
                scene["composition"] = {"template": "formula_focus"}
                scene["aadhi_position"] = "popup_bottom_right"
            elif "code" in kinds:
                scene["composition"] = {"template": "code_focus"}
                scene["aadhi_position"] = "popup_bottom_right"
            elif "table" in kinds:
                scene["aadhi_position"] = "popup_bottom_right"
            elif prose_only:  # explanation only: the presenter explains it
                scene["composition"] = {"template": "presenter_explanation", "presenter_position": "left"}
                scene["aadhi_position"] = "left"
                scene["mascot_state"] = "explaining"
            scene["narration"] = _narration(chunk, lead)
            scenes.append(scene)
        if not chunks and images:  # a section that only mentions a figure
            image = images[0]
            scenes.append({"type": "content", "concept_id": concept_id, "title": s["title"], "html": f"<p>{_esc(_sentence(image['text']))}</p>",
                           "side_panel": {"type": "image", "prompt": image["text"]},
                           "visual": {"concept": s["title"][:200], "description": image["text"][:500], "keywords": _keywords(image["text"]),
                                      "type": image["type"]},
                           "narration": f"[SYNC] Look at the {image['type']} on the side: {_sentence(_spoken(image['text']))}", "aadhi_position": "right"})
        for it in s["items"]:
            if it["kind"] == "qa" and it["a"]:
                quiz = checkpoint(it, concept_id)
                if quiz:
                    scenes.append(quiz)
                    asked.add(it["q"])
        if len(scenes) >= MAX_STAND_IN_SCENES - 3:
            break
    if not asked and len(defined) >= 2:  # no question in the source: which term the source describes this way
        term, sentence = defined[0]
        described = re.split(r"\s+(?:is|are|refers to|means|denotes)\s+", sentence, maxsplit=1)
        if len(described) == 2:
            others = [(t[:1].upper() + t[1:], d) for t, d in defined[1:]]
            quiz = _quiz({"q": f"Which term is described here: {described[1].rstrip('.')}?", "a": term[:1].upper() + term[1:], "why": sentence},
                         last, others)
            if quiz:
                scenes.append(quiz)
    titles = [s["title"] for s in teaching][:MAX_TAKEAWAYS]
    if titles:
        scenes.append({"type": "key-takeaway", "concept_id": last, "title": "Key takeaways",
                       "html": "<ul class='takeaway-list'>" + "".join(f"<li>{_esc(t)}</li>" for t in titles) + "</ul>",
                       "narration": "Before I show you the summary, pause the video and try to recall the key ideas we covered yourself! "
                                    "[PAUSE:3] Here they come. " + " ".join(f"[SYNC] {_sentence(_spoken(t))} [PAUSE]" for t in titles),
                       "aadhi_position": "right"})
    if doc["summary"]:
        points = doc["summary"][:UNITS_PER_SCENE]
        scenes.append({"type": "content", "concept_id": last, "title": "Summary",
                       "html": "".join(f"<p>{_esc(p['text'])}</p>" for p in points),
                       "narration": " ".join(f"[SYNC] {_spoken(p['text'])}" for p in points),
                       "side_panel": {"type": "skill_tree"}, "aadhi_position": "right"})
    scenes = scenes[:MAX_STAND_IN_SCENES]

    formulas = [it["tex"] for s in sections for it in s["items"] if it["kind"] == "formula"]
    sheet = ["# Key Formulas & Definitions"]
    sheet += [f"- {term}: {sentence}" for term, sentence in defined[:20]] + [f"- {tex}" for tex in formulas[:20]]
    if len(sheet) == 1:
        sheet.append("Not provided in the source.")
    sheet += ["", "# Common Misconceptions", "Not provided in the source.", "", "# Practice Problems"]
    practice = doc["questions"][:10]
    sheet += [f"{k}. {q['q']}" for k, q in enumerate(practice, 1)] or ["Not provided in the source."]
    sheet += ["", "# Answer Key"]
    sheet += [f"{k}. {q['a'] or 'Not provided in the source.'}" for k, q in enumerate(practice, 1)] or ["Not provided in the source."]
    return {"subject_name": _name(names.get("subject_name"), 255) or doc["subject"] or title[:255],
            "unit_name": _name(names.get("unit_name"), 255),
            "session_number": _name(names.get("session_number"), 50) or "Session 1",
            "session_title": _name(names.get("session_title"), 255) or title[:255],
            "concept_map": concepts, "scenes": scenes, "companion_sheet": "\n".join(sheet)}


# ---- traceability -----------------------------------------------------------------------------------------------------

# Words that carry no fact: English function words, and the connecting words of a narrated lesson (a narrator's "look at
# the board", "pause the video", a checkpoint's "the answer is", a formula's symbols said as words). A scene's coverage
# counts only its other words, so connecting words never make a scene look invented, nor sourced.
STOPWORDS = frozenset("""
a an the and or but nor so yet of to in on at by for with from into onto over under about as than then that this these those
it its it's is are was were be been being am do does did done has have had having will would shall should can could may might
must not no yes if when where which who whom whose what why how all any each every both either neither some such only own same
too very just also more most less least much many few other another there here their them they we you your our us he she his
her him i me my mine ours yours theirs one ones up down out off again once further while until because before after above below
between through during without within along across against among around near via per let let's lets
""".split()) | frozenset("""
welcome today today's todays session lesson look looking see seen watch notice board panel side screen picture video pause
paused try yourself yourselves recall remember think thinking guess predict here's come comes coming covered cover key idea ideas
takeaway takeaways summary recap overview checkpoint check quick question questions answer answers correct wrong option options
step steps first second third next finally now right left show shows showing shown definition definitions term terms described
describe describes describing expected output code example examples important note objective objectives learning
equals equal plus minus times over gives divided by squared cubed power square root less greater
""".split())


def _stem(word):
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _words(text):
    """The content words of a text (lower case, plural -s removed), without function and connecting words; a formula's
    one-letter symbols count."""
    out = set()
    for token in re.findall(r"[^\W_]+(?:['’][^\W_]+)?", str(text or "").lower()):
        token = re.sub(r"['’]s$", "", token.replace("’", "'"))
        if token in STOPWORDS:
            continue  # (one-letter words stay: a formula's symbols are its words)
        out.add(_stem(token))
    return out


def _visible_text(value):
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return html.unescape(re.sub(r"\[(?:SYNC|PAUSE(?::[\d.]+)?)\]", " ", text))


def scene_words(scene):
    """What a scene shows and says, as content words: titles, board, narration, a checkpoint's question, options, feedback
    and explanation (not media prompts or code a renderer runs)."""
    if not isinstance(scene, dict):
        return set()
    parts = [scene.get(k) for k in ("title", "subtitle", "html", "narration", "question", "explanation", "reveal_narration",
                                    "chapter_label")]
    for key in ("options", "feedback_wrong"):
        if isinstance(scene.get(key), list):
            parts.extend(scene[key])
    return _words(" ".join(_visible_text(p) for p in parts if isinstance(p, (str, int, float))))


def source_units(source):
    """The source as units with a reference: (line number, text) for a text — its instruction header, the analysis' known
    gaps and accepted improvements left out — or (block id, text) for Phase 11 blocks, or (position, text) for a list."""
    if isinstance(source, list):
        units = []
        for k, block in enumerate(source):
            if isinstance(block, dict):
                text = block.get("text") or " ".join(" ".join(str(c) for c in row) for row in block.get("rows") or [] if isinstance(row, list))
                units.append((block.get("id") or k + 1, str(text or "")))
            elif isinstance(block, str):
                units.append((k + 1, block))
        return units
    units, skipping = [], False
    lines = str(source or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")[:MAX_LINES]
    prepared = bool(lines) and lines[0].strip().startswith(PREPARED)
    for n, raw in enumerate(lines, 1):
        line = _clean_line(raw) if prepared else raw.strip()
        if not line or META.match(line):
            continue
        if prepared and (SKIPPED.match(line) or re.match(r"^(?:SECTION\s+\d+|SUMMARY:|ASSESSMENT MATERIAL)", line)):
            skipping = bool(SKIPPED.match(line))
            if skipping:
                continue
        if skipping or (prepared and line.endswith(": not provided")):
            continue
        if prepared:  # the input's own labels are not the source's words
            line = PREPARED_LABEL.sub("", line)
        units.append((n, line))
    return units


PREPARED_LABEL = re.compile(r"^(?:SECTION\s+\d+|Subtopic\s+[\d.]+|Title|Subject|Visual in the source(?:\s*\([^)]*\))?|Code\s*\([^)]*\)|"
                            r"Explanation|Formula|Example|Important|Expected output|Definition of [^:]{1,80})\s*:\s*")
SOURCE_COVERAGE = 0.75  # a scene is "source" when at least this share of its content words is in the source
MAX_REFS = 8
REF_SHARE = 0.5  # a source line is a ref when the scene uses at least half of its content words


def trace(scenes, source):
    """Marks every scene with where it comes from: scene.source = {origin, refs, coverage}. coverage is the share of the
    scene's content words found in the source; refs the source lines (or blocks) the scene uses (at least half of a line's
    content words, and two words or all of a short line such as a heading), most used first, at most 8, in source order.
    origin is "source" only with coverage >= 0.75 and at least one ref; otherwise "ai" (a writer's own wording, or content
    the source does not have). Returns the scenes."""
    units = [(ref, _words(text)) for ref, text in source_units(source)]
    vocabulary = set().union(*(w for _r, w in units)) if units else set()
    for scene in scenes if isinstance(scenes, list) else []:
        if not isinstance(scene, dict):
            continue
        words = scene_words(scene)
        if not words:
            scene["source"] = {"origin": "ai", "refs": [], "coverage": 0.0}
            continue
        coverage = round(len(words & vocabulary) / len(words), 3)
        scored = []
        for position, (ref, unit) in enumerate(units):
            shared = len(words & unit)
            if shared and (shared >= 2 or shared == len(unit)) and shared / len(unit) >= REF_SHARE:
                scored.append((-shared / len(unit), -shared, position, ref))
        refs = [ref for *_s, _p, ref in sorted(sorted(scored)[:MAX_REFS], key=lambda x: x[2])]
        scene["source"] = {"origin": "source" if coverage >= SOURCE_COVERAGE and refs else "ai", "refs": refs, "coverage": coverage}
    return scenes
