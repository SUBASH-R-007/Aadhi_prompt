"""Deterministic synthesis of schema-valid instances (used by ``FakeLLM``).

Instances are built from the *provider* schema (``to_provider_schema(model, "fake")``) so the fake
sees exactly what a real model would see, then checked with ``model.model_validate``.

Three levels are produced, in this order:

* ``minimal`` - required fields only, lists at ``minItems``;
* ``filled``  - every non-nullable field, lists with at least one item, nullable fields omitted;
* ``rich``    - every field populated (nullable fields take their non-null branch).

Ids and references are derived from the containing list name so cross references line up
(``concept_map[0].id == "concept-1"`` and ``scene.concept_id == "concept-1"``); ``source_refs``
use chunk ids (``c0001``). ``candidates`` returns the levels that validate, de-duplicated; the
fake walks through them on successive re-asks.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from .schema import to_provider_schema

try:  # Python 3.11+
    import re._constants as _sre_c  # type: ignore[import-not-found]
    import re._parser as _sre_parse  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    import sre_constants as _sre_c  # type: ignore[no-redef]
    import sre_parse as _sre_parse  # type: ignore[no-redef]

Level = Literal["minimal", "filled", "rich"]
LEVELS: tuple[Level, ...] = ("minimal", "filled", "rich")
_MAX_DEPTH = 14

_NAME_HINTS: dict[str, str] = {
    "expr": "x^2",
    "latex": "x^2",
    "symbol_latex": "x",
    "code": "print('hello')",
    "p5_code": "function setup() {\n  createCanvas(400, 400);\n}\nfunction draw() {\n  background(30);\n}",
    "color": "#7c3aed",
    "url": "https://example.com",
    "email": "student@example.com",
    "language": "en-IN",
    "command": "echo hello",
}
_CATEGORY_CHARS = {
    "CATEGORY_DIGIT": "0",
    "CATEGORY_NOT_DIGIT": "a",
    "CATEGORY_WORD": "a",
    "CATEGORY_NOT_WORD": "-",
    "CATEGORY_SPACE": " ",
    "CATEGORY_NOT_SPACE": "a",
}


# --- regex example generation ----------------------------------------------------


class _Unsupported(Exception):
    pass


def _set_char(items: list[tuple[Any, Any]]) -> str:
    negate = bool(items) and str(items[0][0]) == "NEGATE"
    if negate:
        banned: set[str] = set()
        for op, av in items[1:]:
            name = str(op)
            if name == "LITERAL":
                banned.add(chr(av))
            elif name == "RANGE":
                banned.update(chr(c) for c in range(av[0], min(av[1], av[0] + 200) + 1))
        for ch in "aA0_-x .":
            if ch not in banned:
                return ch
        raise _Unsupported("negated set")
    for op, av in items:
        name = str(op)
        if name == "LITERAL":
            return chr(av)
        if name == "RANGE":
            lo, hi = av
            # prefer a lowercase letter / digit inside the range for readability
            for ch in "a0A":
                if lo <= ord(ch) <= hi:
                    return ch
            return chr(lo)
        if name == "CATEGORY":
            return _CATEGORY_CHARS.get(str(av), "a")
    raise _Unsupported("empty set")


def _emit(parsed: Any, extra: int, groups: dict[int, str]) -> str:
    out: list[str] = []
    for op, av in parsed:
        name = str(op)
        if name == "LITERAL":
            out.append(chr(av))
        elif name == "NOT_LITERAL":
            out.append("a" if av != ord("a") else "b")
        elif name == "ANY":
            out.append("a")
        elif name == "IN":
            out.append(_set_char(list(av)))
        elif name == "CATEGORY":
            out.append(_CATEGORY_CHARS.get(str(av), "a"))
        elif name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            lo, hi, sub = av
            hi = lo + extra if hi == _sre_c.MAXREPEAT else hi
            count = min(hi, lo + extra) if extra else lo
            out.append("".join(_emit(sub, extra, groups) for _ in range(count)))
        elif name == "SUBPATTERN":
            gid, _add, _del, sub = av
            text = _emit(sub, extra, groups)
            if gid is not None:
                groups[gid] = text
            out.append(text)
        elif name == "ATOMIC_GROUP":
            out.append(_emit(av, extra, groups))
        elif name == "BRANCH":
            out.append(_emit(av[1][0], extra, groups))
        elif name == "GROUPREF":
            out.append(groups.get(av, ""))
        elif name in ("AT", "ASSERT", "ASSERT_NOT"):
            continue
        else:
            raise _Unsupported(name)
    return "".join(out)


def _vary(text: str, pattern: str, index: int) -> str:
    """Make list items distinct by rewriting a trailing digit run with the item number."""
    if index <= 0:
        m = re.search(r"(\d+)(\D*)$", text)
        if m and set(m.group(1)) == {"0"}:
            candidate = text[: m.start(1)] + "1".zfill(len(m.group(1))) + m.group(2)
            return candidate if re.search(pattern, candidate) else text
        return text
    m = re.search(r"(\d+)(\D*)$", text)
    if not m:
        for candidate in (f"{text}{index + 1}", f"{text[:-1]}{index + 1}"):
            if candidate and re.search(pattern, candidate):
                return candidate
        return text
    width = len(m.group(1))
    num = str(index + 1).zfill(width)
    if len(num) > width:
        return text
    candidate = text[: m.start(1)] + num + m.group(2)
    return candidate if re.search(pattern, candidate) else text


def example_for_pattern(pattern: str, *, min_length: int = 0, max_length: int | None = None, index: int = 0) -> str | None:
    """A string matching ``pattern`` (search semantics) within the length bounds, or ``None``."""
    try:
        parsed = _sre_parse.parse(pattern)
        compiled = re.compile(pattern)
    except (re.error, TypeError, ValueError):
        return None
    for extra in (0, 1, 2, 4, 8, 16, 32, 64):
        try:
            text = _emit(parsed, extra, {})
        except (_Unsupported, RecursionError):
            return None
        if len(text) < min_length:
            continue
        if max_length is not None and len(text) > max_length:
            return None
        if compiled.search(text):
            varied = _vary(text, pattern, index)
            return text if max_length is not None and len(varied) > max_length else varied
    return None


# --- schema-driven synthesis -------------------------------------------------------


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _entity_from_container(container: str) -> str:
    base = re.sub(r"_(map|list|items|entries)$", "", container or "item")
    return _singular(base.split("_")[-1] or "item")


def _id_value(name: str, container: str, index: int) -> str | None:
    if name == "id":
        return f"{_entity_from_container(container)}-{index + 1}"
    if name.endswith("_ids"):
        return f"{_singular(name[:-4].split('_')[-1] or 'item')}-{index + 1}"
    if name.endswith("_id"):
        return f"{_singular(name[:-3].split('_')[-1] or 'item')}-{index + 1}"
    return None


def _fit_length(text: str, node: dict[str, Any]) -> str:
    lo = int(node.get("minLength") or 0)
    hi = node.get("maxLength")
    while len(text) < lo:
        text += " sample"
    if hi is not None and len(text) > int(hi):
        text = text[: int(hi)].rstrip() or text[: int(hi)]
    if len(text) < lo:  # rstrip removed padding: pad with letters
        text = text + "x" * (lo - len(text))
    return text


def _string(node: dict[str, Any], name: str, container: str, index: int, in_list: bool) -> str:
    lo = int(node.get("minLength") or 0)
    hi = node.get("maxLength")
    pattern = node.get("pattern")
    if pattern:
        found = example_for_pattern(pattern, min_length=lo, max_length=hi, index=index)
        if found is not None:
            return found
    fmt = node.get("format")
    if fmt == "date-time":
        return "2024-01-01T00:00:00Z"
    if fmt == "date":
        return "2024-01-01"
    if fmt == "time":
        return "00:00:00"
    ident = _id_value(name, container, index)
    if ident is not None:
        return _fit_length(ident, node)
    if name in ("source_refs", "source_ref", "chunk_id", "chunk_ids"):
        return _fit_length(f"c{index + 1:04d}", node)
    hint = _NAME_HINTS.get(name)
    if hint is not None and lo <= len(hint) and (hi is None or len(hint) <= int(hi)):
        return hint
    label = (name or "value").replace("_", " ").strip()
    text = f"Sample {label}" + (f" {index + 1}" if in_list else "")
    return _fit_length(text, node)


def _number(node: dict[str, Any], integer: bool) -> int | float:
    lo, hi = node.get("minimum"), node.get("maximum")
    ex_lo, ex_hi = node.get("exclusiveMinimum"), node.get("exclusiveMaximum")
    if integer:
        if ex_lo is not None:
            lo = max(lo, ex_lo + 1) if lo is not None else ex_lo + 1
        if ex_hi is not None:
            hi = min(hi, ex_hi - 1) if hi is not None else ex_hi - 1
    if lo is not None:
        value = lo
    elif ex_lo is not None:  # float with an exclusive lower bound
        upper = hi if hi is not None else ex_hi
        value = (ex_lo + upper) / 2 if upper is not None else ex_lo + 1
    elif hi is not None and hi < 0:
        value = hi
    elif ex_hi is not None and ex_hi <= 0:
        value = ex_hi - 1
    else:
        value = 0
    if hi is not None and value > hi:
        value = hi
    step = node.get("multipleOf")
    if step:
        value = (int(value / step) + (1 if value % step else 0)) * step
    return int(value) if integer else float(value)


def _value(node: dict[str, Any], *, name: str, container: str, index: int, in_list: bool, level: Level, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        raise ValueError("schema too deep to synthesise")
    if "enum" in node:
        values = node["enum"]
        if not values:
            return None
        return values[index % len(values)] if in_list else values[0]
    if "anyOf" in node:
        branches = node["anyOf"]
        non_null = [b for b in branches if b.get("type") != "null"]
        if level == "rich" and non_null:
            return _value(non_null[0], name=name, container=container, index=index, in_list=in_list,
                          level=level, depth=depth + 1)
        if len(non_null) < len(branches):
            return None
        return _value(non_null[0], name=name, container=container, index=index, in_list=in_list,
                      level=level, depth=depth + 1)
    kind = node.get("type")
    if kind == "string":
        return _string(node, name, container, index, in_list)
    if kind == "integer":
        return _number(node, True)
    if kind == "number":
        return _number(node, False)
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if kind == "array":
        lo = int(node.get("minItems") or 0)
        hi = node.get("maxItems")
        count = lo if level == "minimal" else max(lo, 1)
        if hi is not None:
            count = min(count, int(hi))
        item = node.get("items") or {"type": "string"}
        return [
            _value(item, name=name, container=name, index=i, in_list=True, level=level, depth=depth + 1)
            for i in range(count)
        ]
    if kind == "object" or "properties" in node:
        props: dict[str, Any] = node.get("properties") or {}
        required = set(node.get("required") or [])
        owner = name or container  # list name for list items, property name otherwise
        out: dict[str, Any] = {}
        for prop, sub in props.items():
            nullable = "anyOf" in sub and any(b.get("type") == "null" for b in sub["anyOf"])
            if level == "minimal" and prop not in required:
                continue
            if level == "filled" and prop not in required and nullable:
                continue
            out[prop] = _value(
                sub,
                name=prop,
                container=owner,
                index=index if prop == "id" else 0,
                in_list=False,
                level=level,
                depth=depth + 1,
            )
        return out
    return None  # untyped values never reach here (assert_llm_compatible rejects them)


def synthesize(model: type[BaseModel], level: Level = "minimal") -> dict[str, Any]:
    """Build a JSON-able instance of ``model``'s provider schema at ``level`` (not validated)."""
    schema = to_provider_schema(model, "fake")
    data = _value(schema, name="", container="item", index=0, in_list=False, level=level, depth=0)
    return data if isinstance(data, dict) else {}


_STRING_ALTERNATIVES = (
    "sample.value", "sample_value", "sample-value", "Sample value text", "x", "a1", "x^2", "en-IN", "c0001",
    "https://example.com", "2024-01-01", "#7c3aed", "1",
)
_MAX_REPAIR_ROUNDS = len(_STRING_ALTERNATIVES) + 2


def _locate(data: Any, loc: tuple[Any, ...]) -> tuple[Any, Any] | None:
    """(container, key) addressed by a pydantic error location, or ``None``."""
    cur = data
    for part in loc[:-1]:
        if isinstance(cur, dict) and isinstance(part, str) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and isinstance(part, int) and 0 <= part < len(cur):
            cur = cur[part]
        else:
            return None
    last = loc[-1] if loc else None
    if isinstance(cur, dict) and isinstance(last, str) and last in cur:
        return cur, last
    if isinstance(cur, list) and isinstance(last, int) and 0 <= last < len(cur):
        return cur, last
    return None


def _fixed_number(value: float, err: dict[str, Any]) -> float | None:
    ctx = err.get("ctx") or {}
    kind = err.get("type", "")
    integer = isinstance(value, int)
    if kind == "greater_than" and "gt" in ctx:
        return ctx["gt"] + 1 if integer else float(ctx["gt"]) + 0.5
    if kind == "greater_than_equal" and "ge" in ctx:
        return ctx["ge"]
    if kind == "less_than" and "lt" in ctx:
        return ctx["lt"] - 1 if integer else float(ctx["lt"]) - 0.5
    if kind == "less_than_equal" and "le" in ctx:
        return ctx["le"]
    return None


def _repair(model: type[BaseModel], data: dict[str, Any]) -> dict[str, Any] | None:
    """Nudge values that field validators reject (e.g. ``code`` must look like ``area.problem``).

    Uses the ``ValidationError`` locations: failing strings cycle through common shapes,
    failing numbers move inside the reported bound. Returns ``None`` when nothing helps
    (typically a cross-field model validator).
    """
    data = copy.deepcopy(data)
    tried: dict[tuple[Any, ...], int] = {}
    for _ in range(_MAX_REPAIR_ROUNDS):
        try:
            model.model_validate(data)
            return data
        except ValidationError as exc:
            changed = False
            for err in exc.errors(include_url=False):
                loc = tuple(err.get("loc") or ())
                spot = _locate(data, loc)
                if spot is None:
                    continue
                holder, key = spot
                value = holder[key]
                if isinstance(value, bool):
                    continue
                if isinstance(value, str):
                    n = tried.get(loc, 0)
                    if n < len(_STRING_ALTERNATIVES):
                        holder[key] = _STRING_ALTERNATIVES[n]
                        tried[loc] = n + 1
                        changed = True
                elif isinstance(value, (int, float)):
                    fixed = _fixed_number(value, err)
                    if fixed is not None and fixed != value:
                        holder[key] = fixed
                        changed = True
            if not changed:
                return None
    return None


@lru_cache(maxsize=256)
def _candidates_json(model: type[BaseModel]) -> tuple[str, ...]:
    valid: list[str] = []
    first = ""
    for level in LEVELS:
        try:
            data = synthesize(model, level)
        except ValueError:
            continue
        first = first or json.dumps(data, sort_keys=True, ensure_ascii=False)
        repaired = _repair(model, data)
        if repaired is None:
            continue
        text = json.dumps(repaired, sort_keys=True, ensure_ascii=False)
        if text not in valid:
            valid.append(text)
    return tuple(valid) or ((first,) if first else ("{}",))


def candidates(model: type[BaseModel]) -> list[dict[str, Any]]:
    """Schema-valid instances ordered minimal -> rich (never empty; falls back to ``minimal``)."""
    return [copy.deepcopy(json.loads(t)) for t in _candidates_json(model)]
