"""Scene intent (Phase 14): what a scene is teaching and what in it deserves attention, from what the lesson
already says. No model is asked to rediscover structured facts.

  screenplay scene (type, title, board HTML, narration, [SYNC] markers, side panel, composition)
  + the Visual Router's plan (what the visual is, its asset's size)            -> scene_intent(...)
  + the Presenter Director's plan (who presents, what they can do)                 purpose, content, density,
                                                                                   media, priorities, weights,
                                                                                   ambiguity, reasons, hash

The intelligent composer (composer.py) turns an intent into a composition decision; Phase 13 (cinematic.py)
turns the decision into the rendered plan.
"""
import hashlib
import json
import re
from html.parser import HTMLParser

from source_analysis import looks_like_formula

INTENT_VERSION = 1
PURPOSES = ("intro", "transition", "definition", "explanation", "example", "diagram", "formula", "code", "process",
            "comparison", "quiz", "summary", "recap", "demonstration")
CONTENT = ("definition", "formula", "example", "code", "diagram", "chart", "image", "video", "animation", "table",
           "process", "quiz", "list")
FULL_CANVAS_TYPES = ("simulation", "visual", "p5_simulation")
# How the educational visual shows (the router's renderer / panel type → a kind the composer reasons about)
VISUAL_KIND = {"chart": "chart", "graph": "chart", "3d_model": "animation", "terminal": "code", "animation": "animation",
               "skill_tree": "map", "quiz": "quiz", "p5": "animation", "svg": "diagram", "gif": "image", "mathjax": "formula",
               "manim": "animation", "image": "image", "video": "video"}
# Default aspect (width / height) when the asset's size is unknown: what the page draws or providers return
DEFAULT_ASPECT = {"chart": 1.4, "animation": 1.33, "diagram": 1.3, "image": 0.67, "video": 1.78, "code": 1.6, "map": 0.8,
                  "quiz": 0.9, "formula": 2.0}
READING_WPS = 3.3   # words per second when a viewer reads on-screen text
STEP_WORDS = re.compile(r"\b(first|second|third|then|next|finally|step \d|stage \d|steps|stages|phases?)\b", re.I)
COMPARE_TITLE = re.compile(r"\b(vs\.?|versus|compared?|comparison|differences?|contrast)\b", re.I)
SUMMARY_TITLE = re.compile(r"\b(summary|recap|conclusion|key takeaways?|wrap[- ]up|review)\b", re.I)
DEFINE = re.compile(r"\b(is defined as|refers to|is called|we define|definition)\b", re.I)
LOOK = re.compile(r"\b(look at|notice|see (?:how|the)|this (?:diagram|figure|chart|graph|picture|image)|shown here|on the (?:left|right))\b", re.I)
TEX = re.compile(r"\\\[.{1,600}?\\\]|\\\(.{1,600}?\\\)|\$\$.{1,600}?\$\$", re.S)  # bounded: linear on crafted boards


class _Board(HTMLParser):
    """The board's structure: blocks, lists, tables, code, definitions and formulas (text kept, markup dropped)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []
        self.blocks = 0
        self.items = 0
        self.ordered_items = 0
        self.lists = 0
        self.top_lists = 0
        self.takeaways = False
        self.tables = 0
        self.rows = 0
        self.cells = 0
        self.max_cols = 0
        self._cols = 0
        self.code = []
        self._code = None
        self.definitions = 0
        self.formula_blocks = 0
        self.callouts = 0
        self.images = 0
        # The texts a reader sees in each structure (Phase 15's visual director reads them; counts above are unchanged)
        self.item_texts = []     # top-level list items, in order
        self.headers = []        # the first table row's cells
        self.keywords = []       # <span class="keyword">, <strong>, <b>
        self.paragraphs = []
        self.definition_text = ""
        self.after_code = None   # {"tag", "cls", "text"}: the block right after the first code block
        self._captures = []      # [key, depth, buffer, extra]
        self._code_done = False
        self.marked = []          # <strong> and .keyword in DOM order, not deduplicated (what the page counts, Phase 16)
        self.definition_marked = []
        self._in_definition = 0

    def _capture(self, tag, cls):
        depth = len(self.stack)
        inside_li = any(t == "li" for t, _c in self.stack[:-1])
        if tag == "li" and not inside_li:
            self._captures.append(["item", depth, [], None])
        if tag in ("th", "td") and self.rows == 1:
            self._captures.append(["header", depth, [], None])
        if (tag == "span" and "keyword" in cls) or tag in ("strong", "b"):
            self._captures.append(["keyword", depth, [], None])
        if (tag == "span" and "keyword" in cls) or tag == "strong":
            self._captures.append(["marked", depth, [], bool(any("definition" in c for _t, c in self.stack))])
        if tag == "p":
            self._captures.append(["para", depth, [], None])
        if "definition" in cls and not self.definition_text:
            self._captures.append(["definition", depth, [], None])
        if self._code_done and self.after_code is None and tag in ("p", "pre", "div", "blockquote"):
            self.after_code = {"tag": tag, "cls": cls, "text": ""}
            self._captures.append(["after_code", depth, [], None])

    def _finish(self):
        depth = len(self.stack)
        still = []
        for key, start, buf, _extra in self._captures:
            if start <= depth:
                still.append([key, start, buf, _extra])
                continue
            text = re.sub(r"\s+", " ", "".join(buf)).strip()
            if key == "item" and text:
                self.item_texts.append(text[:160])
            elif key == "header":
                self.headers.append(text[:60])
            elif key == "keyword" and text and len(text) <= 40:
                self.keywords.append(text)
            elif key == "marked":
                self.marked.append(text[:60])
                if _extra:
                    self.definition_marked.append(text[:60])
            elif key == "para" and text:
                self.paragraphs.append(text[:240])
            elif key == "definition":
                self.definition_text = text[:300]
            elif key == "after_code" and self.after_code is not None:
                self.after_code["text"] = text[:240]
        self._captures = still

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "") or ""
        self.stack.append((tag, cls))
        if tag in ("li", "ul", "ol", "p", "div", "br", "tr", "td", "th"):
            for capture in self._captures:
                capture[2].append(" ")
        self._capture(tag, cls)
        if tag in ("p", "li", "h2", "h3", "h4", "div", "pre", "blockquote") and tag != "div" or "definition" in cls or "callout" in cls:
            self.blocks += 1
        if tag in ("ul", "ol"):
            self.lists += 1
            if sum(1 for t, _c in self.stack[:-1] if t in ("ul", "ol")) == 0:
                self.top_lists += 1
            if "takeaway" in cls:
                self.takeaways = True
        if tag == "li":
            self.items += 1
            if any(t == "ol" for t, _c in self.stack[:-1]):
                self.ordered_items += 1
        if tag == "table":
            self.tables += 1
        if tag == "tr":
            self.rows += 1
            self._cols = 0
        if tag in ("td", "th"):
            self.cells += 1
            self._cols += 1
            self.max_cols = max(self.max_cols, self._cols)
        if tag == "pre":
            self._code = []
        if "definition" in cls:
            self.definitions += 1
        if "formula-block" in cls or "math-block" in cls:
            self.formula_blocks += 1
        if "callout" in cls:
            self.callouts += 1
        if tag == "img":
            self.images += 1

    def handle_endtag(self, tag):
        closing_code = tag == "pre" and self._code is not None
        if closing_code:
            self.code.append("".join(self._code))
            self._code = None
        while self.stack:
            t, _c = self.stack.pop()
            if t == tag:
                break
        self._finish()
        if closing_code:
            self._code_done = True

    def handle_data(self, data):
        for capture in self._captures:
            if capture[0] == "after_code" or self._code is None:
                capture[2].append(data)
        if self._code is not None:
            self._code.append(data)  # code is measured by its lines, not as prose
            return
        self.text.append(data)


def board_facts(html):
    """Counts the composer reasons with, from the board's HTML (never its meaning)."""
    parser = _Board()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - a malformed board is still composed (from what could be read)
        pass
    text = re.sub(r"\s+", " ", " ".join(parser.text)).strip()
    tex = TEX.findall(html or "")
    prose = TEX.sub(" ", text)
    words = len(re.findall(r"[\w']+", prose))
    code_lines = sum(len([l for l in block.splitlines() if l.strip()]) for block in parser.code)
    code_width = max((len(l) for block in parser.code for l in block.splitlines()), default=0)
    formulas = len(tex) + (parser.formula_blocks if not tex else 0)
    if not formulas:
        formulas = sum(1 for line in re.split(r"(?<=[.!?])\s+", prose) if looks_like_formula(line) and "=" in line)
    texts = {"item_texts": parser.item_texts[:12], "headers": parser.headers[:6], "keywords": list(dict.fromkeys(parser.keywords))[:10],
             "marked": parser.marked[:20], "definition_marked": parser.definition_marked[:10],
             "paragraphs": parser.paragraphs[:12], "definition_text": parser.definition_text, "after_code": parser.after_code,
             "code_texts": [block[:1200] for block in parser.code[:2]], "tex": [t[:200] for t in tex[:4]], "text": text[:1500]}
    return {**texts, "chars": len(prose), "words": words, "blocks": parser.blocks, "items": parser.items, "ordered_items": parser.ordered_items,
            "lists": parser.lists, "top_lists": parser.top_lists, "takeaways": parser.takeaways, "tables": parser.tables,
            "rows": parser.rows, "cells": parser.cells, "cols": parser.max_cols, "code_blocks": len(parser.code), "code_lines": code_lines,
            "code_width": code_width, "definitions": parser.definitions + (1 if DEFINE.search(prose) else 0), "formulas": formulas,
            "formula_chars": sum(len(t) for t in tex), "callouts": parser.callouts, "images": parser.images}


def _visual(scene, resolve_asset):
    """The educational visual the router chose, as the composer sees it: kind, aspect, where it shows."""
    plans = scene.get("visual_plan") if isinstance(scene.get("visual_plan"), dict) else {}
    kind = str(scene.get("type") or "")
    panel = scene.get("side_panel") if isinstance(scene.get("side_panel"), dict) else None
    ptype = ""
    if kind in ("ai_video",) + FULL_CANVAS_TYPES:
        main = plans.get("main") if isinstance(plans.get("main"), dict) else {}
        if main.get("selection") == "removed":
            return None
        vkind = "video" if kind == "ai_video" else "animation"
        slot = "main"
        asset_id = main.get("asset_id") or scene.get("video_asset_id") or scene.get("manim_asset_id")
    else:
        side = plans.get("side") if isinstance(plans.get("side"), dict) else None
        if side is not None and (side.get("selection") == "removed" or (side.get("media") in (None, "NONE") and not panel)):
            return None
        if side is not None and side.get("source") == "NONE" and (not panel or panel.get("type") == "image"):
            return None  # the router found nothing to show (cinematic.found_nothing)
        if side is None and not panel:
            return None
        ptype = (panel or {}).get("type") or ""
        renderer = (side or {}).get("renderer") or ptype
        media = (side or {}).get("media")
        vkind = VISUAL_KIND.get(renderer) or VISUAL_KIND.get(ptype) or ("video" if media == "VIDEO" else "image")
        if vkind == "image" and isinstance(scene.get("visual"), dict) and scene["visual"].get("type") == "diagram":
            vkind = "diagram"
        slot = "side"
        asset_id = (side or {}).get("asset_id")
    aspect = None
    if asset_id:
        found = resolve_asset(asset_id) or {}
        if found.get("width") and found.get("height"):
            aspect = round(found["width"] / found["height"], 3)
    return {"slot": slot, "kind": vkind, "aspect": aspect or DEFAULT_ASPECT.get(vkind, 1.33), "aspect_known": bool(aspect),
            "orientation": "landscape" if (aspect or DEFAULT_ASPECT.get(vkind, 1.33)) >= 1.15 else ("portrait" if (aspect or DEFAULT_ASPECT.get(vkind, 1.33)) <= 0.87 else "square"),
            "decorative": vkind in ("map", "quiz") or (ptype == "gif")}


def _presenter(scene, settings):
    """Who presents and what that means for composition (the Phase 12 plan, or Aadhi placed by the lesson)."""
    plan = scene.get("presenter_plan") if isinstance(scene.get("presenter_plan"), dict) else None
    legacy = settings.get("presenter_legacy", True)
    if not legacy and plan and plan.get("presenter_id") == settings.get("presenter_id"):
        ptype = plan.get("type") or "mascot"
        media = plan.get("media") if isinstance(plan.get("media"), dict) else None
        return {"type": ptype, "available": bool(plan.get("enabled")) and (ptype not in ("ai_avatar", "custom") or bool(media)),
                "enabled": bool(plan.get("enabled")), "transparent": ptype == "illustrated", "clip": bool(media),
                "director_role": plan.get("role")}
    pos = str(scene.get("aadhi_position") or "").lower()
    return {"type": "mascot", "available": pos != "hidden", "enabled": pos != "hidden", "transparent": False, "clip": False,
            "director_role": None}


def _sync_mentions(narration, pattern):
    """The 1-based [SYNC] reveal whose sentence matches the pattern (0: before the first reveal), or None."""
    parts = str(narration or "").split("[SYNC]")
    for i, part in enumerate(parts):
        if pattern.search(part):
            return i
    return None


def classify(scene, index, board, visual):
    """(content flags, purpose, whether a real visual teaches) of a scene, by an ordered rule list (the order is part
    of the explanation). `visual` is the router's visual as the composer sees it, or (Phase 15's visual director) the
    visual the screenplay asks for, in the same shape: {kind, orientation, decorative}."""
    kind = str(scene.get("type") or "content")
    title = str(scene.get("title") or "")
    html = scene.get("html") if isinstance(scene.get("html"), str) else ""
    narration = str(scene.get("narration") or "")
    content = {k: False for k in CONTENT}
    content["definition"] = board["definitions"] > 0
    content["formula"] = board["formulas"] > 0
    content["code"] = board["code_blocks"] > 0 or (visual or {}).get("kind") == "code"
    content["table"] = board["tables"] > 0
    content["list"] = board["items"] > 0
    content["process"] = board["ordered_items"] >= 3 or (board["items"] >= 3 and len(STEP_WORDS.findall(narration)) >= 2)
    content["example"] = kind == "example" or bool(re.search(r"\b(example|for instance|e\.g\.)\b", title + " " + html, re.I))
    content["quiz"] = kind == "quiz_checkpoint" or (visual or {}).get("kind") == "quiz"
    if visual and not visual["decorative"]:
        vk = visual["kind"]
        content["chart"] = vk == "chart"
        content["diagram"] = vk == "diagram" or (vk == "image" and (visual["orientation"] != "portrait" or content["definition"]))
        content["image"] = vk == "image"
        content["video"] = vk == "video"
        content["animation"] = vk == "animation"
    real_visual = bool(visual and not visual["decorative"])

    # Purpose: the first rule that fits (the order matters and is part of the explanation)
    two_lists = board["top_lists"] == 2 and board["tables"] == 0
    if kind == "quiz_checkpoint":
        purpose = "quiz"
    elif kind in ("title", "chapter_card"):
        purpose = "intro" if index == 0 else "transition"
    elif kind in FULL_CANVAS_TYPES or kind == "ai_video":
        purpose = "demonstration"
    elif kind == "recap":
        purpose = "recap"
    elif kind in ("summary", "key-takeaway") or board["takeaways"] or SUMMARY_TITLE.search(title):
        purpose = "summary"
    elif index == 0:
        purpose = "intro"
    elif content["code"] and board["code_blocks"]:
        purpose = "code"
    elif COMPARE_TITLE.search(title) or (content["table"] and board["cols"] >= 2) or two_lists:
        purpose = "comparison"
    elif content["formula"]:
        purpose = "formula"
    elif content["definition"]:
        purpose = "definition"
    elif content["process"]:
        purpose = "process"
    elif kind == "example":
        purpose = "example"
    elif real_visual and board["words"] <= 30:
        purpose = "diagram"
    else:
        purpose = "explanation"
    return content, purpose, real_visual


def scene_intent(scene, index, count, settings=None, resolve_asset=lambda _id: None):
    """The normalized intent of one scene (see the module docstring)."""
    settings = settings or {}
    scene = scene if isinstance(scene, dict) else {}
    kind = str(scene.get("type") or "content")
    title = str(scene.get("title") or "")
    html = scene.get("html") if isinstance(scene.get("html"), str) else ""
    narration = str(scene.get("narration") or "")
    board = board_facts(html)
    visual = _visual(scene, resolve_asset)
    presenter = _presenter(scene, settings)
    reasons = []

    content, purpose, real_visual = classify(scene, index, board, visual)
    reasons.append(f"{purpose} scene")

    # Density: how much the viewer must read at once
    load = board["words"] + board["code_lines"] * 4 + board["cells"] * 2 + board["formulas"] * 6
    density = "low" if load <= 28 else ("medium" if load <= 75 else "high")
    reading = round(board["words"] / READING_WPS + board["code_lines"] * 1.2 + board["formulas"] * 2.5 + board["cells"] * 0.6, 1)

    # Educational priority: the elements in the order they deserve attention
    order = {
        "intro": ["title", "presenter", "visual", "text"], "transition": ["title", "presenter"],
        "definition": ["text", "visual", "presenter"], "explanation": ["visual", "text", "presenter"] if real_visual else ["text", "presenter"],
        "example": ["text", "visual", "presenter"], "diagram": ["visual", "text", "presenter"], "formula": ["formula", "visual", "text", "presenter"],
        "code": ["code", "text", "presenter"], "process": ["text", "visual", "presenter"], "comparison": ["table" if content["table"] else "text", "visual", "presenter"],
        "quiz": ["question", "presenter", "answer"], "summary": ["text", "presenter", "visual"], "recap": ["text", "presenter"],
        "demonstration": ["visual", "text"],
    }[purpose]
    if not real_visual:
        order = [e for e in order if e != "visual"]
    if not presenter["available"]:
        order = [e for e in order if e != "presenter"]
    weights = {"visual": 0.0, "presenter": 0.0, "text": 0.0}
    for rank, element in enumerate(order):
        key = "visual" if element == "visual" else ("presenter" if element == "presenter" else ("text" if element != "title" else "presenter"))
        weights[key] = max(weights[key], round(1.0 - rank * 0.25, 2))
    if purpose == "intro":
        weights["presenter"] = 0.9
    if density == "high":
        weights["presenter"] = round(weights["presenter"] * 0.6, 2)

    # Ambiguity: scenes where two elements compete for the stage (where an AI suggestion may help)
    primaries = [k for k in ("formula", "code", "table") if content[k]] + (["visual"] if real_visual and visual["kind"] in ("diagram", "chart", "image") else [])
    ambiguous = len(primaries) >= 2 or (purpose == "comparison" and real_visual) or (density == "high" and real_visual and presenter["available"])
    if ambiguous:
        reasons.append("several elements compete for the stage: " + ", ".join(primaries or ["text", "visual"]))

    cues = {"look": _sync_mentions(narration, LOOK), "steps": len(STEP_WORDS.findall(narration)),
            "syncs": narration.count("[SYNC]")}
    intent = {"version": INTENT_VERSION, "purpose": purpose, "type": kind, "content": content, "density": density,
              "reading_seconds": reading, "board": {k: board[k] for k in ("words", "chars", "blocks", "items", "ordered_items", "top_lists", "tables",
                                                                          "rows", "cols", "cells", "code_lines", "code_width", "formulas",
                                                                          "formula_chars", "definitions", "callouts")},
              "visual": visual, "presenter": presenter, "priority": order, "weights": weights, "ambiguous": ambiguous,
              "cues": cues, "title_chars": len(title), "reasons": reasons}
    intent["hash"] = hashlib.sha256(json.dumps(intent, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return intent
