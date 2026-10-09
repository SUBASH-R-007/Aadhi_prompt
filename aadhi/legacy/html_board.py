"""v1 board HTML -> typed v2 board items (rich-lite text, never HTML).

The HTML is parsed with ``html.parser`` into a tiny tolerant tree (no scripts/styles kept). Board
items are produced in document order. v1 revealed "sync-able" elements one per ``[SYNC]`` marker:
``h2, h3, p, li, .math-block, .definition, .formula-block, .info-callout, .warning-callout,
.tip-callout, pre`` that have no sync-able ancestor. ``BoardExtraction.sync_slots`` lists, in that
v1 order, the board item index each sync element became (``None`` when it produced nothing, so the
``[SYNC]`` counting stays aligned with v1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from ..schemas.screenplay import slugify

__all__ = ["BoardExtraction", "Node", "extract_board", "inline_rich", "parse_html", "plain_text", "rich_text"]

VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
DROP_TAGS = frozenset({"script", "style", "template", "noscript", "iframe", "object", "svg", "math", "head", "title"})
_P_CLOSERS = frozenset(
    {"p", "div", "ul", "ol", "table", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "section", "dl", "hr"}
)
_SELF_NESTING = {"li": {"li"}, "p": {"p"}, "tr": {"tr"}, "td": {"td", "th"}, "th": {"td", "th"}, "option": {"option"}}
SYNC_TAGS = frozenset({"h2", "h3", "p", "li", "pre"})
SYNC_CLASSES = frozenset(
    {"math-block", "definition", "formula-block", "info-callout", "warning-callout", "tip-callout"}
)
INLINE_TAGS = frozenset(
    {"strong", "b", "em", "i", "span", "code", "a", "sub", "sup", "br", "u", "mark", "small", "kbd", "tt", "s", "abbr"}
)
CALLOUT_KINDS = {"info-callout": "callout_info", "tip-callout": "callout_tip", "warning-callout": "callout_warning"}

MAX_TEXT = 1200
MAX_TERM = 240
MAX_LATEX = 1200
MAX_CODE = 4000
MAX_CAPTION = 600
MAX_JUSTIFICATION = 800
MAX_TABLE_COLS = 8
MAX_TABLE_ROWS = 20

_DISPLAY_MATH = re.compile(r"^\s*(?:\$\$(?P<a>.+?)\$\$|\\\[(?P<b>.+?)\\\]|\\\((?P<c>.+?)\\\))\s*$", re.DOTALL)
_MATH_SPAN = re.compile(r"(\$\$.+?\$\$|\\\[.+?\\\]|\\\(.+?\\\)|(?<!\\)\$[^$\n]+?(?<!\\)\$)", re.DOTALL)
_MISCONCEPTION_PREFIX = re.compile(
    r"^\s*(?:\*\*)?\s*common\s+misconceptions?\s*(?:\*\*)?\s*[:\-–—]?\s*(?:\*\*)?\s*", re.IGNORECASE
)
_BUT_SPLIT = re.compile(r"\s*[,;:\-–—]*\s*\b(?:but|however)\b,?\s+(?:in\s+fact\s+|actually\s+)?", re.IGNORECASE)
_STEP_RE = re.compile(r"^\s*(?:\*\*)?\s*(?:step\s*\d+|\d+\s*[.)])", re.IGNORECASE)
_BECAUSE_SPLIT = re.compile(r"\s+[-–—]\s+(?=because\b)", re.IGNORECASE)
_FORBIDDEN_TEX = re.compile(
    r"\\(href|url|style|class|cssId|require|data|html|unicode|mmlToken|bbox|special|input|include|def|let|newcommand|renewcommand)\b"
)


# ---------------------------------------------------------------------------
# Tolerant DOM
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """Minimal element node; children are ``Node`` or ``str`` (decoded text)."""

    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)
    parent: Node | None = field(default=None, repr=False)

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def has_class(self, name: str) -> bool:
        return name in self.classes

    def iter(self):
        """Depth-first iteration over descendant element nodes (self included)."""
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed([c for c in node.children if isinstance(c, Node)]))


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack: list[Node] = [self.root]
        self._dropping = 0

    @property
    def current(self) -> Node:
        return self.stack[-1]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._dropping:
            if tag in DROP_TAGS and tag not in VOID_TAGS:
                self._dropping += 1
            return
        if tag in DROP_TAGS:
            self._dropping = 1
            return
        closers = _SELF_NESTING.get(tag, set())
        if self.current.tag in closers or (tag in _P_CLOSERS and self.current.tag == "p"):
            self.stack.pop()
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, parent=self.current)
        self.current.children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS and not self._dropping and self.current.tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._dropping:
            if tag in DROP_TAGS:
                self._dropping -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._dropping or not data:
            return
        self.current.children.append(data)


def parse_html(html: str) -> Node:
    """Parse v1 board HTML into a tolerant tree (unknown/unbalanced markup is tolerated)."""
    # v1 fixed double-escaped MathJax delimiters before rendering (\\[ -> \[ ...); do the same.
    html = (html or "").replace("\\\\[", "\\[").replace("\\\\]", "\\]").replace("\\\\(", "\\(").replace("\\\\)", "\\)")
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


# ---------------------------------------------------------------------------
# Text conversion
# ---------------------------------------------------------------------------


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def raw_text(node: Node | str) -> str:
    """Concatenated text content (whitespace preserved; ``<br>`` -> newline)."""
    if isinstance(node, str):
        return node
    if node.tag == "br":
        return "\n"
    return "".join(raw_text(c) for c in node.children)


def plain_text(node: Node | str) -> str:
    """Text content with collapsed whitespace."""
    return _collapse(raw_text(node))


def _math_to_inline(segment: str) -> str:
    m = re.match(r"^(?:\$\$(.+)\$\$|\\\[(.+)\\\]|\\\((.+)\\\)|\$(.+)\$)$", segment, re.DOTALL)
    inner = next((g for g in (m.groups() if m else ()) if g is not None), segment)
    return f"${_collapse(inner)}$"


def _escape_text(text: str) -> str:
    """Escape rich-lite specials in plain text, keeping math spans (normalised to ``$..$``)."""
    out: list[str] = []
    for i, part in enumerate(_MATH_SPAN.split(text)):
        if not part:
            continue
        if i % 2 == 1:
            out.append(_math_to_inline(part))
        else:
            out.append(part.replace("*", "\\*").replace("`", "'"))
    return "".join(out)


def _wrap(marker_open: str, inner: str, marker_close: str | None = None) -> str:
    if not inner.strip():
        return inner
    lead = " " if inner[:1].isspace() else ""
    trail = " " if inner[-1:].isspace() else ""
    return f"{lead}{marker_open}{inner.strip()}{marker_close or marker_open}{trail}"


def _rich_raw(node: Node, warnings: list[str] | None, where: str) -> str:
    """Rich-lite for the children of ``node`` with surrounding whitespace preserved."""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(_escape_text(child))
            continue
        tag = child.tag
        if tag in ("strong", "b"):
            parts.append(_wrap("**", _rich_raw(child, warnings, where)))
        elif tag in ("em", "i"):
            parts.append(_wrap("*", _rich_raw(child, warnings, where)))
        elif tag == "span" and child.has_class("keyword"):
            inner = raw_text(child)
            parts.append(_wrap("[[", inner.replace("[", "(").replace("]", ")"), "]]"))
        elif tag in ("code", "kbd", "tt") or (tag == "span" and child.has_class("inline-code")):
            parts.append(_wrap("`", raw_text(child).replace("`", "'")))
        elif tag == "br":
            parts.append(" ")
        elif tag == "img":
            if warnings is not None:
                what = "animated GIF" if child.has_class("context-gif") else "inline image"
                warnings.append(f"{where}: {what} dropped (not supported on the v2 board)")
        elif child.has_class("board-image-placeholder"):
            continue  # emitted as a separate figure item by the extractor
        else:
            parts.append(_rich_raw(child, warnings, where))
    return "".join(parts)


def rich_text(node: Node, warnings: list[str] | None = None, *, where: str = "") -> str:
    """Inline HTML -> rich-lite: bold ``**b**``, italic ``*i*``, ``[[keyword]]``, backtick code, ``$math$``."""
    return _collapse(_rich_raw(node, warnings, where))


def inline_rich(node: Node, warnings: list[str] | None = None, *, where: str = "") -> str:
    """Rich-lite for ``node`` itself (e.g. a loose ``<b>``), surrounding whitespace preserved."""
    return _rich_raw(Node("#inline", children=[node]), warnings, where)


def _truncate(text: str | None, limit: int, warnings: list[str], where: str, what: str) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    warnings.append(f"{where}: {what} shortened to {limit} characters")
    cut = text[: limit - 1].rstrip()
    # Do not leave a dangling backslash escape behind.
    cut = cut.removesuffix("\\")
    return cut + "…"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


@dataclass
class BoardExtraction:
    """Board items (BoardItem kwargs without ``id``) + v1 sync order + side products."""

    items: list[dict[str, Any]] = field(default_factory=list)
    sync_slots: list[int | None] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)  # SourceFigure kwargs
    misconceptions: list[dict[str, str]] = field(default_factory=list)  # {"statement", "correction", "item"}

    def readable_text(self, item_index: int | None) -> str:
        """Plain text suitable for narrating an item ('' for formulas/code/tables/figures)."""
        if item_index is None:
            return ""
        item = self.items[item_index]
        if item["kind"] in ("formula", "code", "table", "figure"):
            return ""
        text = " ".join(x for x in (item.get("term"), item.get("text")) if x)
        text = re.sub(r"\$[^$]*\$", "", text)
        text = re.sub(r"\\([*$])", r"\1", text)
        return _collapse(re.sub(r"\*\*|\[\[|\]\]|[*`]", "", text))


def _is_sync(node: Node) -> bool:
    return node.tag in SYNC_TAGS or bool(node.classes & SYNC_CLASSES)


def _strip_delimiters(text: str) -> str:
    t = text.strip()
    for pattern in (r"^\$\$(.*)\$\$$", r"^\\\[(.*)\\\]$", r"^\\\((.*)\\\)$", r"^\$(.*)\$$"):
        m = re.match(pattern, t, re.DOTALL)
        if m:
            return m.group(1).strip()
    return t


class _Extractor:
    def __init__(self, scene_type: str, warnings: list[str], where: str) -> None:
        self.scene_type = scene_type
        self.warnings = warnings
        self.where = where
        self.out = BoardExtraction()
        self._loose: list[str] = []

    # -- emit helpers -------------------------------------------------------
    def _emit(self, item: dict[str, Any] | None, *, sync: bool) -> None:
        index: int | None = None
        if item is not None:
            self.out.items.append(item)
            index = len(self.out.items) - 1
        if sync:
            self.out.sync_slots.append(index)

    def _text(self, text: str, what: str = "text", limit: int = MAX_TEXT) -> str:
        return _truncate(text, limit, self.warnings, self.where, what) or ""

    def _formula_item(self, latex: str) -> dict[str, Any] | None:
        latex = latex.strip()
        if not latex:
            return None
        if _FORBIDDEN_TEX.search(latex):
            self.warnings.append(f"{self.where}: formula uses a forbidden TeX macro; kept as code text")
            return {"kind": "paragraph", "text": self._text("`" + latex.replace("`", "'") + "`")}
        return {"kind": "formula", "latex": self._text(latex, "formula", MAX_LATEX)}

    def _flush_loose(self) -> None:
        text = _collapse("".join(self._loose))
        self._loose = []
        if not text:
            return
        m = _DISPLAY_MATH.match(text)
        if m:
            self._emit(self._formula_item(next(g for g in m.groups() if g is not None)), sync=False)
        else:
            self._emit({"kind": "paragraph", "text": self._text(text)}, sync=False)

    def _figures_in(self, node: Node) -> None:
        for el in node.iter():
            if el is not node and el.has_class("board-image-placeholder"):
                self._emit(self._figure(el), sync=False)

    def _figure(self, node: Node) -> dict[str, Any] | None:
        img_id = (node.attrs.get("data-img-id") or "").strip()
        if not img_id:
            self.warnings.append(f"{self.where}: image placeholder without data-img-id dropped")
            return None
        fid = slugify(img_id, "figure")
        caption = _collapse(img_id.replace("_", " "))
        self.out.figures.append({"id": fid, "caption": caption[:MAX_CAPTION]})
        return {"kind": "figure", "figure_id": fid, "caption": caption[:MAX_CAPTION]}

    # -- element conversion -------------------------------------------------
    def _sync_item(self, node: Node, *, in_takeaway_list: bool) -> dict[str, Any] | None:
        classes = node.classes
        if classes & {"formula-block", "math-block"}:
            return self._formula_item(_strip_delimiters(raw_text(node)))
        if "definition" in classes:
            return self._definition(node)
        for cls, kind in CALLOUT_KINDS.items():
            if cls in classes:
                return self._callout(node, kind)
        tag = node.tag
        if tag == "pre":
            return self._code(node)
        text = rich_text(node, self.warnings, where=self.where)
        if not text:
            return None
        if tag in ("h2", "h3"):
            return {"kind": "heading", "text": self._text(text)}
        if tag == "li":
            kind = "takeaway" if in_takeaway_list or self.scene_type == "key_takeaway" else "bullet"
            return {"kind": kind, "text": self._text(text)}
        # <p>
        m = _DISPLAY_MATH.match(raw_text(node))
        if m:
            return self._formula_item(next(g for g in m.groups() if g is not None))
        if self.scene_type == "example" and _STEP_RE.match(text):
            parts = _BECAUSE_SPLIT.split(text, maxsplit=1)
            item: dict[str, Any] = {"kind": "example_step", "text": self._text(parts[0].strip())}
            if len(parts) == 2 and parts[1].strip():
                just = parts[1].strip()
                item["justification"] = self._text(just[:1].upper() + just[1:], "justification", MAX_JUSTIFICATION)
            return item
        return {"kind": "paragraph", "text": self._text(text)}

    def _definition(self, node: Node) -> dict[str, Any] | None:
        children = [c for c in node.children if not (isinstance(c, str) and not c.strip())]
        term: str | None = None
        rest = node
        if children and isinstance(children[0], Node) and children[0].tag in ("strong", "b"):
            term = plain_text(children[0])
            rest = Node("#rest", children=[c for c in node.children if c is not children[0]])
        text = rich_text(rest, self.warnings, where=self.where)
        if term is not None:
            if term.endswith(":"):
                term = term[:-1].strip()
            text = re.sub(r"^\s*[:\-–—]\s*", "", text)
        else:
            m = re.match(r"^([^:]{1,80}?):\s+(.+)$", text, re.DOTALL)
            if m and "$" not in m.group(1):
                term, text = re.sub(r"\*\*|\[\[|\]\]", "", m.group(1)).strip(), m.group(2).strip()
        if not term and not text:
            return None
        item: dict[str, Any] = {"kind": "definition", "text": self._text(text)}
        if term:
            item["term"] = self._text(term, "term", MAX_TERM)
        return item

    def _callout(self, node: Node, kind: str) -> dict[str, Any] | None:
        text = rich_text(node, self.warnings, where=self.where)
        if not text:
            return None
        if kind == "callout_warning" and _MISCONCEPTION_PREFIX.match(text):
            body = _MISCONCEPTION_PREFIX.sub("", text, count=1).strip()
            body = re.sub(r"^\*\*\s*", "", body)
            statement, correction = body, ""
            m = _BUT_SPLIT.search(body, 10)
            if m:
                statement = body[: m.start()].strip(" ,;:-–—")
                correction = body[m.end() :].strip()
                correction = correction[:1].upper() + correction[1:]
            item: dict[str, Any] = {"kind": "misconception", "text": self._text(statement or body)}
            if correction:
                item["justification"] = self._text(correction, "correction", MAX_JUSTIFICATION)
                self.out.misconceptions.append(
                    {"statement": item["text"], "correction": item["justification"], "item": len(self.out.items)}
                )
            return item
        return {"kind": kind, "text": self._text(text)}

    def _code(self, node: Node) -> dict[str, Any] | None:
        code = raw_text(node).strip("\n")
        if not code.strip():
            return None
        language = "text"
        for el in node.iter():
            for cls in el.classes:
                m = re.match(r"^(?:language|lang)-([A-Za-z0-9+#-]{1,20})$", cls)
                if m:
                    language = m.group(1).lower()
                    break
        if not re.match(r"^[a-z0-9+#-]{1,20}$", language):
            language = "text"
        return {"kind": "code", "language": language, "code": self._text(code, "code", MAX_CODE)}

    def _table(self, node: Node) -> dict[str, Any] | None:
        rows: list[tuple[bool, list[str]]] = []
        for tr in node.iter():
            if tr.tag != "tr":
                continue
            cells = [c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]
            if not cells:
                continue
            all_th = all(c.tag == "th" for c in cells)
            in_head = tr.parent is not None and tr.parent.tag == "thead"
            rows.append((all_th or in_head, [rich_text(c, self.warnings, where=self.where) for c in cells]))
        if not rows:
            return None
        if rows[0][0] or len(rows) > 1:
            headers, body = rows[0][1], [r for _, r in rows[1:]]
        else:
            headers, body = rows[0][1], []
        if not body:
            return (
                {"kind": "paragraph", "text": self._text(" | ".join(h for h in headers if h))} if any(headers) else None
            )
        width = max(len(headers), *(len(r) for r in body))
        if width > MAX_TABLE_COLS:
            self.warnings.append(f"{self.where}: table cut to {MAX_TABLE_COLS} columns")
            width = MAX_TABLE_COLS
        if len(body) > MAX_TABLE_ROWS:
            self.warnings.append(f"{self.where}: table cut to {MAX_TABLE_ROWS} rows")
            body = body[:MAX_TABLE_ROWS]

        def fit(row: list[str]) -> list[str]:
            return (row + [""] * width)[:width]

        return {"kind": "table", "headers": fit(headers), "rows": [fit(r) for r in body]}

    # -- walk -----------------------------------------------------------------
    def walk(self, node: Node, *, in_takeaway_list: bool = False) -> None:
        for child in node.children:
            if isinstance(child, str):
                self._loose.append(_escape_text(child))
                continue
            tag = child.tag
            if _is_sync(child):
                self._flush_loose()
                self._emit(self._sync_item(child, in_takeaway_list=in_takeaway_list), sync=True)
                self._figures_in(child)
            elif tag == "table":
                self._flush_loose()
                self._emit(self._table(child), sync=False)
            elif child.has_class("board-image-placeholder"):
                self._flush_loose()
                self._emit(self._figure(child), sync=False)
            elif tag == "img":
                what = "animated GIF" if child.has_class("context-gif") else "inline image"
                self.warnings.append(f"{self.where}: {what} dropped (not supported on the v2 board)")
            elif tag in ("h1", "h4", "h5", "h6"):
                self._flush_loose()
                text = rich_text(child, self.warnings, where=self.where)
                self._emit({"kind": "heading", "text": self._text(text)} if text else None, sync=False)
            elif tag in INLINE_TAGS:
                self._loose.append(inline_rich(child, self.warnings, where=self.where))
            elif tag == "hr":
                self._flush_loose()
            else:
                self._flush_loose()
                takeaway = in_takeaway_list or (tag in ("ul", "ol") and child.has_class("takeaway-list"))
                self.walk(child, in_takeaway_list=takeaway)
                self._flush_loose()


def extract_board(html: str, *, scene_type: str, warnings: list[str], where: str) -> BoardExtraction:
    """Convert v1 board ``html`` into board items (see module docstring)."""
    extractor = _Extractor(scene_type, warnings, where)
    if html and html.strip():
        extractor.walk(parse_html(html))
        extractor._flush_loose()
    return extractor.out
